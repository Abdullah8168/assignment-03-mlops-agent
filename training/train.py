"""Run the full training pipeline locally (same stages as the Airflow DAG).

    python -m training.train                         # uses params.yaml
    python -m training.train --C 0.1 --max-iter 300  # hyper-parameter override
    python -m training.train --no-mlflow             # offline (used by `dvc repro`)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from training import pipeline
from training.provenance import PROJECT_ROOT, get_data_provenance


def main(argv: list[str] | None = None) -> int:
    # MLflow prints emoji; the default Windows console encoding (cp1252) cannot encode them
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    params =yaml.safe_load((PROJECT_ROOT / "params.yaml").read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=params["data"]["path"])
    parser.add_argument("--C", type=float, default=params["train"]["C"])
    parser.add_argument("--max-iter", type=int, default=params["train"]["max_iter"])
    parser.add_argument("--f1-threshold", type=float, default=params["quality_gate"]["f1_threshold"])
    parser.add_argument("--out", default="artifacts/local")
    parser.add_argument("--no-mlflow", action="store_true", help="train + evaluate without MLflow logging")
    parser.add_argument("--promote", action="store_true", help="register model if quality gate passes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    data_path = PROJECT_ROOT / args.data
    out = PROJECT_ROOT / args.out
    pipeline.validate_dataset(data_path)
    split = pipeline.preprocess(data_path, out / "data")
    model_path = pipeline.train_model(split["train"], out, C=args.C, max_iter=args.max_iter)

    if args.no_mlflow:
        import joblib

        metrics = pipeline.compute_metrics(joblib.load(model_path), split["test"])
        Path(out / "metrics.json").write_text(json.dumps(metrics, indent=2))
        passed, reason = pipeline.check_quality_gate(metrics, args.f1_threshold)
        print(json.dumps({"metrics": metrics, "quality_gate": reason}, indent=2))
        return 0 if passed else 1

    result = pipeline.evaluate_and_log(
        model_path, split["test"], get_data_provenance(data_path),
        {"C": args.C, "max_iter": args.max_iter, "f1_threshold": args.f1_threshold},
        {"pipeline.runner": "local-cli"},
    )
    try:
        pipeline.quality_gate(result["run_id"], result["metrics"], args.f1_threshold)
    except pipeline.QualityGateError as exc:
        print(f"QUALITY GATE FAILED: {exc}")
        return 1
    if args.promote:
        print(pipeline.promote_model(result["run_id"]))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
