"""Agent loop: tool selection, multi-tool integration, safety, and no invented results."""

from __future__ import annotations

from agent.llm import LLMTurn, OfflineLLM, TextBlock, ToolUseBlock
from agent.orchestrator import MLOpsAgent, find_unverified_values
from agent.schemas import ToolStatus
from agent.tools import kubernetes_tools, mlflow_tools
from tests.conftest import FakeApps, FakeCore, FakeMlflowClient

INTEGRATION_QUESTION = ("Find the best recent model, identify the dataset version used to train it, check the "
                        "deployment status, and explain whether retraining is necessary.")


class ScriptedLLM:
    """Replays a fixed list of turns; records the messages it was given."""

    def __init__(self, turns: list[LLMTurn]):
        self.turns = list(turns)
        self.seen: list[list] = []

    def create(self, system, messages, tools):
        self.seen.append(list(messages))
        return self.turns.pop(0)


def _tool_turn(*calls: tuple[str, dict]) -> LLMTurn:
    blocks = [ToolUseBlock(id=f"t{i}", name=n, input=a) for i, (n, a) in enumerate(calls)]
    return LLMTurn(stop_reason="tool_use", content=blocks, tool_calls=blocks)


def _text_turn(text: str) -> LLMTurn:
    return LLMTurn(stop_reason="end_turn", content=[TextBlock(text)], text=text)


def test_selected_tool_is_executed_and_result_returned_to_llm(fake_services):
    llm = ScriptedLLM([_tool_turn(("mlflow_find_best_run", {"metric": "f1"})),
                       _text_turn("Best run is train-25 with f1 0.9731.")])
    resp = MLOpsAgent(llm=llm).run("Which run has the best F1?")
    assert [c.tool for c in resp.tool_calls] == ["mlflow_find_best_run"]
    assert resp.tool_calls[0].status == ToolStatus.SUCCESS
    tool_result_msg = llm.seen[1][-1]["content"][0]
    assert tool_result_msg["type"] == "tool_result" and tool_result_msg["tool_use_id"] == "t0"
    assert "train-25" in tool_result_msg["content"]
    assert resp.unverified_values == []          # 0.9731 is in the evidence


def test_mandatory_integration_scenario_uses_all_four_technologies(fake_services, monkeypatch):
    # DVC lookup of the provenance commit is mocked (repo-independent)
    from agent.tools import dvc_tools

    monkeypatch.setattr(dvc_tools, "_resolve_commit", lambda rev: "a4c92e1f" + "0" * 32)
    monkeypatch.setattr(dvc_tools, "run_approved", lambda cmd, **kw: "2026-11-01T00:00:00+00:00|x|data v2"
                        if cmd[1] == "log" else "")
    monkeypatch.setattr(dvc_tools, "_tracked_dvc_files", lambda rev="HEAD": [])
    monkeypatch.setattr(dvc_tools, "_data_version_label", lambda rev: "v2")

    resp = MLOpsAgent(llm=OfflineLLM()).run(INTEGRATION_QUESTION)
    tools = [c.tool for c in resp.tool_calls]
    assert {"mlflow_find_best_run", "mlflow_get_run_provenance", "dvc_get_dataset_version",
            "k8s_diagnose_deployment", "airflow_get_dag_runs"} <= set(tools)
    assert "airflow_trigger_dag" not in tools               # retraining never started automatically
    assert "### Known facts" in resp.answer and "### Uncertainties" in resp.answer
    assert "v2" in resp.answer and "train-25" in resp.answer
    # latest Airflow run failed -> retraining recommended with evidence, but not executed
    assert "latest training pipeline run failed" in resp.answer
    assert "NOT started automatically" in resp.answer


def test_agent_does_not_invent_results_when_tool_unavailable(fake_services):
    mlflow_tools.set_client(FakeMlflowClient(fail=ConnectionError("Max retries exceeded")))
    resp = MLOpsAgent(llm=OfflineLLM()).run("What is the best model by F1?")
    assert resp.tool_calls[0].status == ToolStatus.UNAVAILABLE
    assert "unavailable" in resp.answer
    assert "train-25" not in resp.answer and "0.97" not in resp.answer


def test_hallucinated_values_are_flagged_by_grounding_check(fake_services):
    mlflow_tools.set_client(FakeMlflowClient(fail=ConnectionError("Max retries exceeded")))
    llm = ScriptedLLM([_tool_turn(("mlflow_find_best_run", {})),
                       _text_turn("The best run has F1 0.9812 and used commit deadbee1.")])
    resp = MLOpsAgent(llm=llm).run("best model?")
    assert set(resp.unverified_values) == {"0.9812", "deadbee1"}
    assert "no tool returned data" in resp.answer


def test_llm_requested_restart_becomes_pending_action_only():
    apps = FakeApps(healthy=False)
    kubernetes_tools.set_apis(FakeCore(healthy=False), apps)
    llm = ScriptedLLM([_tool_turn(("k8s_restart_deployment", {"deployment": "prediction-api"})),
                       _text_turn("I requested a restart; it needs your confirmation.")])
    agent = MLOpsAgent(llm=llm)
    resp = agent.run("restart the api")
    assert resp.tool_calls[0].status == ToolStatus.CONFIRMATION_REQUIRED
    assert len(resp.pending_actions) == 1 and apps.patches == []
    assert agent.confirm(resp.pending_actions[0].token).ok
    assert apps.patches == [("patch", "prediction-api", "ml-serving")]


def test_llm_outage_is_reported_honestly(fake_services):
    class DownLLM:
        def create(self, *a):
            raise ConnectionError("api down")

    resp = MLOpsAgent(llm=DownLLM()).run("status?")
    assert "could not be reached" in resp.answer and resp.tool_calls == []


def test_unverified_value_detection():
    evidence = '{"f1": 0.9731, "git_commit": "a4c92e1f0000"}'
    assert find_unverified_values("f1 0.973 commit a4c92e1", evidence) == []
    assert find_unverified_values("f1 0.9999", evidence) == ["0.9999"]
