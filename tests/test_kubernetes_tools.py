"""Kubernetes tools: diagnosis and the namespace / deployment permission policy."""

from __future__ import annotations

from agent.schemas import ToolStatus
from agent.tools import build_registry, kubernetes_tools
from tests.conftest import FakeApps, FakeCore


def test_unauthorized_kubernetes_modifications_are_rejected(fake_services):
    registry = build_registry()
    other_ns = registry.execute("k8s_restart_deployment", {"deployment": "prediction-api", "namespace": "kube-system"})
    other_dep = registry.execute("k8s_scale_deployment", {"deployment": "coredns", "replicas": 3})
    too_many = registry.execute("k8s_scale_deployment", {"deployment": "prediction-api", "replicas": 50})

    assert other_ns.status == ToolStatus.REJECTED and "not approved" in other_ns.error
    assert other_dep.status == ToolStatus.REJECTED and "not modifiable" in other_dep.error
    assert too_many.status == ToolStatus.INVALID_INPUT
    # no confirmation token is issued for an action that the policy forbids
    assert other_ns.confirmation_token is None and other_dep.confirmation_token is None
    assert fake_services.apps.patches == []


def test_even_confirmed_calls_cannot_escape_the_namespace(fake_services):
    registry = build_registry()
    result = registry.execute("k8s_restart_deployment", {"deployment": "prediction-api", "namespace": "default"},
                              confirmed=True)
    assert result.status == ToolStatus.REJECTED
    assert fake_services.apps.patches == []


def test_reads_are_limited_to_approved_namespaces(fake_services):
    result = build_registry().execute("k8s_list_pods", {"namespace": "kube-system"})
    assert result.status == ToolStatus.REJECTED


def test_healthy_deployment_report(fake_services):
    result = build_registry().execute("k8s_diagnose_deployment", {})
    assert result.ok
    assert result.data["deployment"]["ready_replicas"] == 2
    assert result.data["findings"][0].startswith("no problems detected")


def test_diagnosis_of_crashlooping_pods_uses_events_and_logs():
    kubernetes_tools.set_apis(FakeCore(healthy=False), FakeApps(healthy=False))
    result = build_registry().execute("k8s_diagnose_deployment", {})
    assert result.ok
    findings = " ".join(result.data["findings"])
    assert "0/2 replicas ready" in findings
    assert "CrashLoopBackOff" in findings
    assert "NO ready endpoints" in findings
    assert result.data["warning_events"][0]["reason"] == "BackOff"
    assert any("model file not found" in ln for ln in result.data["logs_of_first_unhealthy_pod"]["error_lines"])
