"""Training pipeline stages (skipped in CI where the heavy ML stack is not installed)."""

from __future__ import annotations

import pytest

from training.pipeline import check_quality_gate


def test_quality_gate_blocks_weak_models():
    assert check_quality_gate({"f1": 0.95, "recall": 0.96}, 0.90)[0] is True
    passed, reason = check_quality_gate({"f1": 0.82, "recall": 0.96}, 0.90)
    assert passed is False and "f1" in reason
    assert check_quality_gate({"f1": 0.95, "recall": 0.50}, 0.90)[0] is False


def test_validation_rejects_corrupted_dataset(tmp_path):
    pd = pytest.importorskip("pandas")
    from training.pipeline import DataValidationError, validate_dataset

    good = pd.DataFrame({"a": range(150), "target": [0, 1] * 75})
    good.to_csv(tmp_path / "good.csv", index=False)
    assert validate_dataset(tmp_path / "good.csv")["rows"] == 150

    pd.DataFrame({"a": [None] * 150}).to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(DataValidationError, match="missing target column"):
        validate_dataset(tmp_path / "bad.csv")
    with pytest.raises(DataValidationError, match="not found"):
        validate_dataset(tmp_path / "missing.csv")


def test_dag_structure():
    pytest.importorskip("airflow")
    from airflow.models import DagBag

    bag = DagBag(dag_folder="dags", include_examples=False)
    assert not bag.import_errors
    dag = bag.get_dag("ml_training_pipeline")
    order = ["validate_dataset", "preprocess_data", "train_model", "evaluate_and_log_mlflow", "quality_gate",
             "promote_model"]
    for upstream, downstream in zip(order, order[1:], strict=False):
        assert downstream in dag.get_task(upstream).downstream_task_ids
    assert dag.default_args["retries"] >= 1
