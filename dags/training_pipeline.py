"""ml_training_pipeline - Airflow 3 training DAG.

validate_dataset -> preprocess_data -> train_model -> evaluate_and_log_mlflow -> quality_gate -> promote_model

* retries with back-off on transient failures (MLflow / filesystem hiccups),
* data validation and quality gate raise AirflowFailException: they fail immediately (no retries,
  retrying bad data or a weak model is pointless) and every downstream task becomes upstream_failed,
  so a model that failed the gate can never be promoted,
* every task logs what it did; run metadata (DAG id / run id) is written to MLflow tags for lineage.

Trigger conf (all optional): {"data_path": "...", "f1_threshold": 0.9, "C": 1.0, "max_iter": 500,
                              "inject_failure": "validation" | "quality_gate"}
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow.exceptions import AirflowFailException
from airflow.sdk import Param, dag, get_current_context, task

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/opt/project"))
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

log = logging.getLogger("airflow.task")

DEFAULT_ARGS = {
    "owner": "mlops",
    "retries": 2,
    "retry_delay": timedelta(seconds=15),
    "retry_exponential_backoff": True,
    "execution_timeout": timedelta(minutes=15),
}


def _ctx() -> tuple[dict, str, Path]:
    ctx = get_current_context()
    params = ctx["params"]
    run_id = ctx["dag_run"].run_id
    work = PROJECT_DIR / "artifacts" / "airflow" / re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)
    work.mkdir(parents=True, exist_ok=True)
    return params, run_id, work


@dag(
    dag_id="ml_training_pipeline",
    description="Validate -> preprocess -> train -> evaluate/MLflow -> quality gate -> promote",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["ml", "training", "approved"],
    params={
        "data_path": Param("data/raw/dataset.csv", type="string"),
        "f1_threshold": Param(0.90, type="number", minimum=0, maximum=1),
        "C": Param(1.0, type="number", exclusiveMinimum=0),
        "max_iter": Param(500, type="integer", minimum=50),
        "inject_failure": Param("none", type="string", enum=["none", "validation", "quality_gate"]),
    },
)
def ml_training_pipeline():
    @task
    def validate_dataset() -> dict:
        from training.pipeline import DataValidationError
        from training.pipeline import validate_dataset as _validate

        params, _, _ = _ctx()
        data_path = params["data_path"]
        if params["inject_failure"] == "validation":
            data_path = "data/raw/corrupted.csv"
            log.warning("FAILURE INJECTION: validating corrupted dataset %s", data_path)
        try:
            report = _validate(PROJECT_DIR / data_path)
        except DataValidationError as exc:
            log.error("Data validation failed: %s", exc)
            raise AirflowFailException(f"Data validation failed: {exc}") from exc
        log.info("Dataset valid: %s rows, %s columns", report["rows"], report["columns"])
        return {"data_path": data_path, "rows": report["rows"]}

    @task
    def preprocess_data(validation: dict) -> dict:
        from training.pipeline import preprocess

        _, _, work = _ctx()
        paths = preprocess(PROJECT_DIR / validation["data_path"], work / "data")
        log.info("Wrote train/test splits to %s", work / "data")
        return {**paths, "data_path": validation["data_path"]}

    @task
    def train_model(split: dict) -> dict:
        from training.pipeline import train_model as _train

        params, _, work = _ctx()
        model_path = _train(split["train"], work, C=float(params["C"]), max_iter=int(params["max_iter"]))
        return {**split, "model_path": model_path}

    @task
    def evaluate_and_log_mlflow(trained: dict) -> dict:
        from training.pipeline import evaluate_and_log
        from training.provenance import get_data_provenance

        params, run_id, _ = _ctx()
        result = evaluate_and_log(
            trained["model_path"],
            trained["test"],
            get_data_provenance(PROJECT_DIR / trained["data_path"], root=PROJECT_DIR),
            {"C": params["C"], "max_iter": params["max_iter"], "f1_threshold": params["f1_threshold"]},
            {"pipeline.dag_id": "ml_training_pipeline", "pipeline.dag_run_id": run_id,
             "pipeline.runner": "airflow"},
        )
        log.info("MLflow run %s metrics %s", result["run_name"], result["metrics"])
        return result

    @task(retries=0)
    def quality_gate(evaluation: dict) -> dict:
        from training.pipeline import QualityGateError
        from training.pipeline import quality_gate as _gate

        params, _, _ = _ctx()
        threshold = float(params["f1_threshold"])
        if params["inject_failure"] == "quality_gate":
            threshold = 0.999
            log.warning("FAILURE INJECTION: raising F1 threshold to %s", threshold)
        try:
            reason = _gate(evaluation["run_id"], evaluation["metrics"], threshold)
        except QualityGateError as exc:
            log.error("%s - model will NOT be promoted", exc)
            raise AirflowFailException(str(exc)) from exc
        return {**evaluation, "gate": reason}

    @task
    def promote_model(gated: dict) -> dict:
        from training.pipeline import promote_model as _promote

        result = _promote(gated["run_id"])
        log.info("Promoted %s", result)
        return result

    validated = validate_dataset()
    split = preprocess_data(validated)
    trained = train_model(split)
    evaluation = evaluate_and_log_mlflow(trained)
    gated = quality_gate(evaluation)
    promote_model(gated)


ml_training_pipeline()
