"""Shared fakes: every external system (MLflow, Airflow, Kubernetes, LLM) is mocked so the suite runs in CI."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from typing import Any

import pytest

# Deterministic settings for tests regardless of a developer's local .env
os.environ.update({
    "K8S_APPROVED_NAMESPACES": "ml-serving",
    "K8S_NAMESPACE": "ml-serving",
    "K8S_MUTABLE_DEPLOYMENTS": "prediction-api",
    "AIRFLOW_APPROVED_DAGS": "ml_training_pipeline",
    "AIRFLOW_PASSWORD": "super-secret-password",
    "MODEL_FRESHNESS_DAYS": "30",
    "AGENT_LLM_BACKEND": "offline",
})

from agent.config import get_settings  # noqa: E402
from agent.tools import airflow_tools, kubernetes_tools, mlflow_tools  # noqa: E402

get_settings.cache_clear()


# ============================================================ MLflow
def make_run(run_id: str, name: str, f1: float, *, days_ago: int = 1, gate: str = "passed",
             data_version: str = "v2", commit: str = "a4c92e1f00000000000000000000000000000000") -> Any:
    start = int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)
    return NS(
        info=NS(run_id=run_id, run_name=name, status="FINISHED", start_time=start, end_time=start + 5000),
        data=NS(
            metrics={"f1": f1, "accuracy": f1 + 0.01, "precision": 0.95, "recall": 0.96},
            params={"C": "1.0", "max_iter": "500"},
            tags={"mlflow.runName": name, "data.version": data_version, "data.git_commit": commit,
                  "data.dvc_md5": "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "quality_gate.status": gate,
                  "pipeline.dag_run_id": "manual__2026-11-10"},
        ),
    )


class FakeMlflowClient:
    def __init__(self, runs: list[Any] | None = None, fail: Exception | None = None):
        self.runs = runs if runs is not None else [
            make_run("a" * 32, "train-25", 0.9731),
            make_run("b" * 32, "train-24", 0.9612, days_ago=3, data_version="v1",
                     commit="b5d03f20000000000000000000000000000000ff"),
            make_run("c" * 32, "train-23", 0.8810, days_ago=5, gate="failed"),
        ]
        self.fail = fail

    def _check(self):
        if self.fail:
            raise self.fail

    def get_experiment_by_name(self, name):
        self._check()
        return NS(experiment_id="1", name=name, lifecycle_stage="active")

    def search_experiments(self):
        self._check()
        return [NS(experiment_id="1", name="breast-cancer-classifier", lifecycle_stage="active")]

    def search_runs(self, exp_ids, filter_string="", order_by=None, max_results=100):
        self._check()
        runs = list(self.runs)
        if "runName" in (filter_string or ""):
            wanted = filter_string.split("'")[1]
            runs = [r for r in runs if r.data.tags["mlflow.runName"] == wanted]
        return runs[:max_results]

    def get_run(self, run_id):
        self._check()
        return next(r for r in self.runs if r.info.run_id == run_id)

    def list_artifacts(self, run_id):
        return [NS(path="model"), NS(path="metrics.json")]

    def search_model_versions(self, flt):
        self._check()
        return [NS(version="3", run_id="a" * 32, status="READY", creation_timestamp=None, tags={})]

    def get_registered_model(self, name):
        return NS(aliases={"champion": "3"})


# ============================================================ Airflow
class FakeAirflow:
    """Mimics AirflowClient.request(method, path, **kwargs)."""

    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def request(self, method: str, path: str, **kwargs) -> Any:
        self.calls.append((method, path))
        if self.fail:
            raise self.fail
        if path == "/dags":
            return {"dags": [{"dag_id": "ml_training_pipeline", "is_paused": False, "tags": [{"name": "ml"}]}]}
        if path.endswith("/dagRuns") and method == "GET":
            state = (kwargs.get("params") or {}).get("state")
            runs = [
                {"dag_run_id": "manual__2026-11-14", "state": "failed", "run_type": "manual"},
                {"dag_run_id": "manual__2026-11-10", "state": "success", "run_type": "manual"},
            ]
            return {"dag_runs": [r for r in runs if not state or r["state"] == state]}
        if path.endswith("/dagRuns") and method == "POST":
            return {"dag_run_id": "manual__new", "state": "queued"}
        if path.endswith("/taskInstances"):
            return {"task_instances": [
                {"task_id": "validate_dataset", "state": "success", "try_number": 1},
                {"task_id": "preprocess_data", "state": "success", "try_number": 1},
                {"task_id": "train_model", "state": "success", "try_number": 1},
                {"task_id": "evaluate_and_log_mlflow", "state": "success", "try_number": 1},
                {"task_id": "quality_gate", "state": "failed", "try_number": 1},
                {"task_id": "promote_model", "state": "upstream_failed", "try_number": 0},
            ]}
        if "/dagRuns/" in path and path.count("/") == 4:
            return {"dag_run_id": path.rsplit("/", 1)[-1], "state": "failed"}
        if path.startswith("/dags/") and path.count("/") == 2:
            return {"dag_id": path.split("/")[2], "is_paused": False}
        return {}


# ============================================================ Kubernetes
def _container(ready=True, restarts=0, waiting=None, last_reason=None):
    state = NS(running=None if waiting else NS(), waiting=NS(reason=waiting, message="back-off") if waiting else None,
               terminated=None)
    last = NS(terminated=NS(reason=last_reason, exit_code=1) if last_reason else None)
    return NS(name="prediction-api", image="prediction-api:1.0.0", ready=ready, restart_count=restarts,
              state=state, last_state=last)


def _pod(name, ready=True, **kw):
    return NS(
        metadata=NS(name=name, creation_timestamp=datetime.now(UTC) - timedelta(minutes=30),
                    labels={"app": "prediction-api", "app.kubernetes.io/version": "1.0.0"}),
        spec=NS(node_name="kind-control-plane", containers=[]),
        status=NS(phase="Running", container_statuses=[_container(ready=ready, **kw)],
                  conditions=[NS(type="Ready", status="True" if ready else "False", reason=None, message=None)]),
    )


class FakeCore:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy

    def list_namespaced_pod(self, ns, label_selector=None, _request_timeout=None):
        if self.healthy:
            return NS(items=[_pod("prediction-api-abc"), _pod("prediction-api-def")])
        return NS(items=[_pod("prediction-api-abc", ready=False, restarts=5, waiting="CrashLoopBackOff",
                              last_reason="Error"),
                         _pod("prediction-api-def", ready=False, restarts=4, waiting="CrashLoopBackOff",
                              last_reason="Error")])

    def list_namespaced_service(self, ns, _request_timeout=None):
        return NS(items=[NS(metadata=NS(name="prediction-api"),
                            spec=NS(type="ClusterIP", cluster_ip="10.96.0.10", selector={"app": "prediction-api"},
                                    ports=[NS(port=80, target_port="http", protocol="TCP")]))])

    def read_namespaced_endpoints(self, name, ns, _request_timeout=None):
        addrs = [NS(), NS()] if self.healthy else []
        return NS(subsets=[NS(addresses=addrs)])

    def list_namespaced_event(self, ns, _request_timeout=None, **kw):
        if self.healthy:
            return NS(items=[])
        return NS(items=[NS(type="Warning", reason="BackOff", message="Back-off restarting failed container",
                            count=12, last_timestamp=datetime.now(UTC),
                            involved_object=NS(kind="Pod", name="prediction-api-abc"),
                            metadata=NS(creation_timestamp=datetime.now(UTC)))])

    def read_namespaced_pod_log(self, name, ns, tail_lines=50, previous=False, _request_timeout=None):
        return "INFO starting\nERROR loading model: FileNotFoundError: model file not found\n"


class FakeApps:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy
        self.patches: list[tuple] = []

    def _dep(self):
        return NS(
            metadata=NS(name="prediction-api", labels={"app.kubernetes.io/version": "1.0.0"},
                        annotations={"deployment.kubernetes.io/revision": "3"}),
            spec=NS(replicas=2, template=NS(metadata=NS(annotations={"mlops/model-version": "champion"}),
                                            spec=NS(containers=[NS(image="prediction-api:1.0.0", env=[])]))),
            status=NS(ready_replicas=2 if self.healthy else 0, available_replicas=2 if self.healthy else 0,
                      updated_replicas=2),
        )

    def list_namespaced_deployment(self, ns, _request_timeout=None):
        return NS(items=[self._dep()])

    def read_namespaced_deployment(self, name, ns, _request_timeout=None):
        return self._dep()

    def patch_namespaced_deployment(self, name, ns, body, _request_timeout=None):
        self.patches.append(("patch", name, ns))

    def patch_namespaced_deployment_scale(self, name, ns, body, _request_timeout=None):
        self.patches.append(("scale", name, ns))


# ============================================================ fixtures
@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    mlflow_tools.set_client(None)
    airflow_tools.set_client(None)
    kubernetes_tools._api_override = None


@pytest.fixture
def fake_services():
    """All four backends healthy (DVC uses the real git repo of a temp project when needed)."""
    ml, af, core, apps = FakeMlflowClient(), FakeAirflow(), FakeCore(), FakeApps()
    mlflow_tools.set_client(ml)
    airflow_tools.set_client(af)
    kubernetes_tools.set_apis(core, apps)
    return NS(mlflow=ml, airflow=af, core=core, apps=apps)


@pytest.fixture
def slow_func():
    def _slow(_params):
        time.sleep(2)
        return {"never": "returned"}
    return _slow
