"""FastAPI interface: request validation, chat and confirmation endpoints."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from agent.llm import OfflineLLM  # noqa: E402
from agent.orchestrator import MLOpsAgent  # noqa: E402
from api.main import app, get_agent  # noqa: E402


@pytest.fixture
def client(fake_services):
    agent = MLOpsAgent(llm=OfflineLLM())
    app.dependency_overrides[get_agent] = lambda: agent
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_invalid_requests_are_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={}).status_code == 422
    assert client.post("/actions/confirm", json={"token": "x"}).status_code == 422


def test_chat_returns_answer_with_evidence(client):
    resp = client.post("/chat", json={"message": "Is the prediction API running on kubernetes?"},
                       headers={"X-Request-ID": "demo-req-1"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["request_id"] == "demo-req-1"
    assert body["tool_calls"][0]["tool"] == "k8s_diagnose_deployment"
    assert "2/2 replicas ready" in body["answer"]


def test_trigger_flow_requires_confirm_endpoint(client, fake_services):
    body = client.post("/chat", json={"message": "Trigger the model training pipeline"}).json()
    assert body["pending_actions"], body
    assert ("POST", "/dags/ml_training_pipeline/dagRuns") not in fake_services.airflow.calls
    token = body["pending_actions"][0]["token"]
    confirmed = client.post("/actions/confirm", json={"token": token, "approve": True}).json()
    assert confirmed["status"] == "success"
    assert ("POST", "/dags/ml_training_pipeline/dagRuns") in fake_services.airflow.calls


def test_tools_catalogue_marks_mutating_tools(client):
    tools = {t["name"]: t for t in client.get("/tools").json()}
    assert tools["k8s_restart_deployment"]["requires_confirmation"] is True
    assert tools["k8s_list_pods"]["requires_confirmation"] is False
