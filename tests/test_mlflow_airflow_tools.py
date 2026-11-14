"""MLflow and Airflow tools against mocked services."""

from __future__ import annotations

from agent.schemas import ToolStatus
from agent.tools import airflow_tools, build_registry, mlflow_tools
from agent.tools.base import ServiceUnavailableError
from tests.conftest import FakeAirflow, FakeMlflowClient


def test_best_run_selection_with_caveats(fake_services):
    result = build_registry().execute("mlflow_find_best_run", {"metric": "f1"})
    assert result.ok
    assert result.data["best_run"]["run_name"] == "train-25"
    assert [r["run_name"] for r in result.data["ranking"]] == ["train-25", "train-24", "train-23"]
    assert any("production-ready" in c for c in result.data["caveats"])


def test_run_provenance_by_run_number(fake_services):
    result = build_registry().execute("mlflow_get_run_provenance", {"run": "25"})
    assert result.ok
    assert result.data["provenance"]["data.version"] == "v2"
    assert result.data["provenance"]["data.git_commit"].startswith("a4c92e1")
    assert result.data["missing_provenance_fields"] == []


def test_compare_last_runs(fake_services):
    result = build_registry().execute("mlflow_compare_runs", {"last_n": 3, "metric": "f1"})
    assert result.ok and result.data["recommended_candidate"] == "train-25"


def test_mlflow_server_down_is_unavailable():
    mlflow_tools.set_client(FakeMlflowClient(fail=ConnectionError("Max retries exceeded with url")))
    result = build_registry().execute("mlflow_find_best_run", {})
    assert result.status == ToolStatus.UNAVAILABLE
    assert result.data is None


def test_airflow_failed_task_identification(fake_services):
    result = build_registry().execute("airflow_get_failed_tasks", {})
    assert result.ok
    assert result.data["root_cause_task"] == "quality_gate"
    assert {t["task_id"] for t in result.data["failed_tasks"]} == {"quality_gate", "promote_model"}


def test_trigger_only_approved_dag_with_allowed_conf(fake_services):
    registry = build_registry()
    not_approved = registry.execute("airflow_trigger_dag", {"dag_id": "drop_production_db"})
    bad_conf = registry.execute("airflow_trigger_dag", {"conf": {"bash_command": "rm -rf /"}})
    ok = registry.execute("airflow_trigger_dag", {"conf": {"f1_threshold": 0.92}})
    assert not_approved.status == ToolStatus.REJECTED
    assert bad_conf.status == ToolStatus.INVALID_INPUT
    assert ok.status == ToolStatus.CONFIRMATION_REQUIRED
    executed = registry.confirm(ok.confirmation_token)
    assert executed.ok and executed.data["dag_run_id"] == "manual__new"


def test_airflow_unreachable():
    airflow_tools.set_client(FakeAirflow(fail=ServiceUnavailableError("Airflow API unreachable")))
    assert build_registry().execute("airflow_list_dags", {}).status == ToolStatus.UNAVAILABLE
