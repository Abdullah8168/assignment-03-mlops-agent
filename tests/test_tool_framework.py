"""Tool execution framework: validation, timeouts, failure classification, confirmation, structured logs."""

from __future__ import annotations

import json
import logging

from agent.schemas import NoInput, ToolStatus
from agent.tools import build_registry
from agent.tools.base import ServiceUnavailableError, ToolRegistry, ToolSpec


def test_registry_has_at_least_three_tools_per_module():
    registry = build_registry()
    modules: dict[str, int] = {}
    for name in registry.names:
        modules[registry.get(name).module] = modules.get(registry.get(name).module, 0) + 1
    assert set(modules) == {"dvc", "mlflow", "airflow", "kubernetes"}
    assert all(count >= 3 for count in modules.values()), modules
    # every schema is valid for the Anthropic tools API
    for tool in registry.anthropic_tools():
        assert tool["input_schema"]["type"] == "object"
        assert tool["description"]


def test_invalid_input_is_rejected_without_calling_the_service(fake_services):
    registry = build_registry()
    bad_type = registry.execute("mlflow_list_runs", {"max_results": "lots"})
    unknown_field = registry.execute("mlflow_list_runs", {"drop_table": True})
    unknown_tool = registry.execute("rm_rf_everything", {})
    assert bad_type.status == ToolStatus.INVALID_INPUT and "max_results" in bad_type.error
    assert unknown_field.status == ToolStatus.INVALID_INPUT
    assert unknown_tool.status == ToolStatus.INVALID_INPUT


def test_tool_timeout_is_reported_not_raised(slow_func):
    registry = ToolRegistry(timeout_s=0.2)
    registry.register(ToolSpec("slow_tool", "sleeps", "mlflow", NoInput, slow_func))
    result = registry.execute("slow_tool", {})
    assert result.status == ToolStatus.TIMEOUT
    assert result.data is None
    assert result.duration_ms < 1500


def test_service_failures_are_classified():
    def down(_):
        raise ServiceUnavailableError("MLflow tracking server unreachable")

    def boom(_):
        raise RuntimeError("unexpected")

    registry = ToolRegistry(timeout_s=5)
    registry.register(ToolSpec("down", "x", "mlflow", NoInput, down))
    registry.register(ToolSpec("boom", "x", "mlflow", NoInput, boom))
    assert registry.execute("down", {}).status == ToolStatus.UNAVAILABLE
    err = registry.execute("boom", {})
    assert err.status == ToolStatus.ERROR and "RuntimeError" in err.error


def test_mutating_tool_needs_human_confirmation(fake_services):
    registry = build_registry()
    pending = registry.execute("k8s_restart_deployment", {"deployment": "prediction-api"})
    assert pending.status == ToolStatus.CONFIRMATION_REQUIRED
    assert fake_services.apps.patches == []          # nothing executed yet
    done = registry.confirm(pending.confirmation_token)
    assert done.status == ToolStatus.SUCCESS
    assert fake_services.apps.patches == [("patch", "prediction-api", "ml-serving")]
    # tokens are single-use
    assert registry.confirm(pending.confirmation_token).status == ToolStatus.INVALID_INPUT


def test_rejected_confirmation_never_executes(fake_services):
    registry = build_registry()
    pending = registry.execute("airflow_trigger_dag", {"dag_id": "ml_training_pipeline"})
    assert registry.reject(pending.confirmation_token)
    assert registry.confirm(pending.confirmation_token).status == ToolStatus.INVALID_INPUT
    assert ("POST", "/dags/ml_training_pipeline/dagRuns") not in fake_services.airflow.calls


def test_structured_logs_have_tool_status_duration_request_id_and_no_secrets(fake_services, caplog):
    from agent.logging_utils import JsonFormatter, request_id_var

    registry = build_registry()
    token = request_id_var.set("req-test-123")
    try:
        with caplog.at_level(logging.INFO, logger="mlops_agent"):
            registry.execute("mlflow_list_runs", {"max_results": 2})
            # a tool argument that looks like a secret must be redacted in logs
            registry.execute("airflow_trigger_dag", {"dag_id": "ml_training_pipeline",
                                                     "conf": {"api_key": "sk-ant-abc123"}})
    finally:
        request_id_var.reset(token)
    lines = [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.getMessage() == "tool_call"]
    assert lines, "no tool_call log records"
    first = lines[0]
    assert first["tool"] == "mlflow_list_runs"
    assert first["status"] == "success"
    assert isinstance(first["duration_ms"], float)
    assert first["request_id"] == "req-test-123"
    serialized = json.dumps(lines)
    assert "sk-ant-abc123" not in serialized
    assert "super-secret-password" not in serialized
