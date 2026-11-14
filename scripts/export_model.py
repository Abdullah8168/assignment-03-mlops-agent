"""Export the model to serve into serving/model/ before `docker build`.

    python scripts/export_model.py                 # MLflow registry: models:/<name>@champion
    python scripts/export_model.py --alias candidate
    python scripts/export_model.py --local         # train locally from params.yaml (no MLflow needed)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "serving" / "model"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    import joblib

    name = os.getenv("REGISTERED_MODEL_NAME", "breast-cancer-classifier")
    if args.local:
        from training import pipeline
        from training.provenance import get_data_provenance

        data = ROOT / "data" / "raw" / "dataset.csv"
        split = pipeline.preprocess(data, ROOT / "artifacts" / "export")
        model_path = pipeline.train_model(split["train"], ROOT / "artifacts" / "export")
        model = joblib.load(model_path)
        meta = {"name": name, "version": "local", "source": "local-train", **get_data_provenance(data)}
    else:
        import mlflow
        from mlflow.tracking import MlflowClient

        mv = MlflowClient().get_model_version_by_alias(name, args.alias)
        model = mlflow.sklearn.load_model(f"models:/{name}@{args.alias}")
        run = MlflowClient().get_run(mv.run_id)
        meta = {"name": name, "version": mv.version, "alias": args.alias, "run_id": mv.run_id,
                "data.version": run.data.tags.get("data.version"),
                "data.git_commit": run.data.tags.get("data.git_commit")}
    joblib.dump(model, OUT / "model.joblib")
    (OUT / "model_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"exported model -> {OUT}: {meta}")


if __name__ == "__main__":
    main()
