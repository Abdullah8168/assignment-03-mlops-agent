"""Training pipeline stages. Used by the Airflow DAG, `training/train.py` and `dvc repro`.

validate_dataset -> preprocess -> train_model -> evaluate_and_log (MLflow) -> quality_gate -> promote_model
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("training")

TARGET = "target"
MIN_ROWS = 100
MAX_NULL_FRACTION = 0.05
EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT_NAME", "breast-cancer-classifier")
MODEL_NAME = os.getenv("REGISTERED_MODEL_NAME", "breast-cancer-classifier")


class DataValidationError(Exception):
    """Dataset failed validation - downstream stages must not run."""


class QualityGateError(Exception):
    """Model failed the quality gate - it must not be promoted."""


# ------------------------------------------------------------------ 1. validation
def validate_dataset(path: str | Path) -> dict[str, Any]:
    import pandas as pd

    path = Path(path)
    if not path.exists():
        raise DataValidationError(f"dataset not found: {path} (did you run `dvc pull`?)")
    df = pd.read_csv(path)
    problems = []
    if TARGET not in df.columns:
        problems.append(f"missing target column '{TARGET}'")
    if len(df) < MIN_ROWS:
        problems.append(f"only {len(df)} rows (< {MIN_ROWS})")
    null_fraction = float(df.isna().mean().max()) if len(df) else 1.0
    if null_fraction > MAX_NULL_FRACTION:
        problems.append(f"column null fraction {null_fraction:.2%} exceeds {MAX_NULL_FRACTION:.0%}")
    if TARGET in df.columns and not set(df[TARGET].dropna().unique()) <= {0, 1}:
        problems.append("target is not binary {0,1}")
    report = {"path": str(path), "rows": len(df), "columns": len(df.columns), "max_null_fraction": null_fraction,
              "problems": problems}
    log.info("validation report: %s", json.dumps(report))
    if problems:
        raise DataValidationError("; ".join(problems))
    return report


# ------------------------------------------------------------------ 2. preprocessing
def preprocess(path: str | Path, out_dir: str | Path, test_size: float = 0.2, seed: int = 42) -> dict[str, str]:
    import pandas as pd
    from sklearn.model_selection import train_test_split

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(path).drop_duplicates().dropna()
    train, test = train_test_split(df, test_size=test_size, random_state=seed, stratify=df[TARGET])
    paths = {"train": str(out_dir / "train.csv"), "test": str(out_dir / "test.csv")}
    train.to_csv(paths["train"], index=False)
    test.to_csv(paths["test"], index=False)
    log.info("preprocessed %d rows -> train=%d test=%d", len(df), len(train), len(test))
    return paths


# ------------------------------------------------------------------ 3. training
def train_model(train_path: str | Path, out_dir: str | Path, C: float = 1.0, max_iter: int = 500) -> str:
    import joblib
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    df = pd.read_csv(train_path)
    X, y = df.drop(columns=[TARGET]), df[TARGET]
    model = Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(C=C, max_iter=max_iter))])
    model.fit(X, y)
    model_path = Path(out_dir) / "model.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    log.info("trained LogisticRegression(C=%s, max_iter=%s) on %d rows", C, max_iter, len(df))
    return str(model_path)


# ------------------------------------------------------------------ 4. evaluation + MLflow
def compute_metrics(model: Any, test_path: str | Path) -> dict[str, float]:
    import pandas as pd
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

    df = pd.read_csv(test_path)
    X, y = df.drop(columns=[TARGET]), df[TARGET]
    pred = model.predict(X)
    proba = model.predict_proba(X)[:, 1]
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred)),
        "recall": float(recall_score(y, pred)),
        "f1": float(f1_score(y, pred)),
        "roc_auc": float(roc_auc_score(y, proba)),
        "test_rows": float(len(df)),
    }


def evaluate_and_log(model_path: str | Path, test_path: str | Path, provenance: dict[str, str],
                     params: dict[str, Any], extra_tags: dict[str, str] | None = None) -> dict[str, Any]:
    import joblib
    import mlflow
    import pandas as pd

    mlflow.set_experiment(EXPERIMENT)
    exp = mlflow.get_experiment_by_name(EXPERIMENT)
    run_number = len(mlflow.search_runs([exp.experiment_id], output_format="list", max_results=1000)) + 1
    model = joblib.load(model_path)
    metrics = compute_metrics(model, test_path)
    with mlflow.start_run(run_name=f"train-{run_number}") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        mlflow.set_tags({**provenance, **(extra_tags or {}), "model.type": "LogisticRegression",
                         "quality_gate.status": "pending"})
        sample = pd.read_csv(test_path).drop(columns=[TARGET]).head(5)
        mlflow.sklearn.log_model(model, name="model", input_example=sample)
        report_path = Path(model_path).parent / "metrics.json"
        report_path.write_text(json.dumps(metrics, indent=2))
        mlflow.log_artifact(str(report_path))
    log.info("logged run %s (train-%d): %s", run.info.run_id, run_number, metrics)
    return {"run_id": run.info.run_id, "run_name": f"train-{run_number}", "metrics": metrics}


# ------------------------------------------------------------------ 5. quality gate
def check_quality_gate(metrics: dict[str, float], f1_threshold: float = 0.90,
                       min_recall: float = 0.85) -> tuple[bool, str]:
    reasons = []
    if metrics.get("f1", 0.0) < f1_threshold:
        reasons.append(f"f1 {metrics.get('f1', 0):.4f} < {f1_threshold}")
    if metrics.get("recall", 0.0) < min_recall:
        reasons.append(f"recall {metrics.get('recall', 0):.4f} < {min_recall}")
    return (not reasons, "; ".join(reasons) or "all thresholds met")


def quality_gate(run_id: str, metrics: dict[str, float], f1_threshold: float = 0.90) -> str:
    from mlflow.tracking import MlflowClient

    passed, reason = check_quality_gate(metrics, f1_threshold)
    client = MlflowClient()
    client.set_tag(run_id, "quality_gate.status", "passed" if passed else "failed")
    client.set_tag(run_id, "quality_gate.reason", reason)
    client.set_tag(run_id, "quality_gate.f1_threshold", str(f1_threshold))
    if not passed:
        raise QualityGateError(f"quality gate failed for run {run_id}: {reason}")
    log.info("quality gate passed for %s: %s", run_id, reason)
    return reason


# ------------------------------------------------------------------ 6. promotion (only after the gate)
def promote_model(run_id: str, alias: str = "champion") -> dict[str, str]:
    import mlflow
    from mlflow.tracking import MlflowClient

    version = mlflow.register_model(f"runs:/{run_id}/model", MODEL_NAME)
    MlflowClient().set_registered_model_alias(MODEL_NAME, alias, version.version)
    log.info("registered %s v%s as @%s", MODEL_NAME, version.version, alias)
    return {"model": MODEL_NAME, "version": str(version.version), "alias": alias}
