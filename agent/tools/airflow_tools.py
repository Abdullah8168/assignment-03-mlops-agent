"""Apache Airflow 3 workflow tools (REST API v2, JWT authentication)."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import quote

import requests
from pydantic import Field, field_validator

from agent.config import get_settings
from agent.schemas import NoInput, StrictInput
from agent.tools.base import PermissionDeniedError, ServiceUnavailableError, ToolError, ToolSpec

HTTP_TIMEOUT_S = 10
ALLOWED_CONF_KEYS = {"data_path", "f1_threshold", "C", "max_iter", "inject_failure"}
TERMINAL_STATES = {"success", "failed"}


class AirflowClient:
    """Thin authenticated client. Token obtained from POST /auth/token and cached until near expiry."""

    def __init__(self, base_url: str, username: str, password: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self._username, self._password = username, password
        self._session = session or requests.Session()
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    def _auth_header(self) -> dict[str, str]:
        with self._lock:
            if not self._token or time.time() > self._token_expiry:
                if not self._password:
                    raise ServiceUnavailableError("AIRFLOW_PASSWORD is not configured")
                resp = self._request_raw(
                    "POST", "/auth/token", json={"username": self._username, "password": self._password}
                )
                if resp.status_code not in (200, 201):
                    raise ToolError(f"Airflow authentication failed (HTTP {resp.status_code})")
                self._token = resp.json()["access_token"]
                self._token_expiry = time.time() + 20 * 60
        return {"Authorization": f"Bearer {self._token}"}

    def _request_raw(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return self._session.request(method, f"{self.base_url}{path}", timeout=HTTP_TIMEOUT_S, **kwargs)
        except requests.Timeout as exc:
            raise TimeoutError(f"Airflow API timed out ({path})") from exc
        except requests.ConnectionError as exc:
            raise ServiceUnavailableError(f"Airflow API unreachable at {self.base_url}") from exc

    def request(self, method: str, path: str, **kwargs) -> Any:
        headers = {"Accept": "application/json", **self._auth_header()}
        resp = self._request_raw(method, f"/api/v2{path}", headers=headers, **kwargs)
        if resp.status_code == 401:  # token expired early -> retry once
            self._token = None
            headers.update(self._auth_header())
            resp = self._request_raw(method, f"/api/v2{path}", headers=headers, **kwargs)
        if resp.status_code == 404:
            raise ToolError(f"Airflow: not found ({path})")
        if resp.status_code >= 500:
            raise ServiceUnavailableError(f"Airflow API error HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ToolError(f"Airflow API HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}


_client_override: AirflowClient | None = None


def set_client(client: Any) -> None:
    global _client_override
    _client_override = client


def _client() -> AirflowClient:
    if _client_override is not None:
        return _client_override
    s = get_settings()
    return AirflowClient(s.airflow_url, s.airflow_username, s.airflow_password)


def _q(value: str) -> str:
    return quote(value, safe="")


def _latest_run_id(dag_id: str, state: str | None = None) -> str:
    params: dict[str, Any] = {"limit": 1, "order_by": "-run_after"}
    if state:
        params["state"] = state
    runs = _client().request("GET", f"/dags/{_q(dag_id)}/dagRuns", params=params).get("dag_runs", [])
    if not runs:
        raise ToolError(f"no {state + ' ' if state else ''}runs found for DAG '{dag_id}'")
    return runs[0]["dag_run_id"]


def _run_view(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in ("dag_run_id", "state", "run_type", "start_date", "end_date", "logical_date", "conf")}


# ------------------------------------------------------------------ inputs
DAG_ID = Field(default="ml_training_pipeline", pattern=r"^[A-Za-z0-9_.\-]+$", max_length=250)


class DagInput(StrictInput):
    dag_id: str = DAG_ID
    limit: int = Field(default=5, ge=1, le=50)


class DagRunInput(StrictInput):
    dag_id: str = DAG_ID
    dag_run_id: str | None = Field(default=None, max_length=250, description="Run id; omitted = latest run.")


class TaskLogInput(StrictInput):
    dag_id: str = DAG_ID
    dag_run_id: str | None = Field(default=None, max_length=250, description="Run id; omitted = latest run.")
    task_id: str = Field(pattern=r"^[A-Za-z0-9_.\-]+$", max_length=250)
    try_number: int | None = Field(default=None, ge=1, le=20)
    tail_lines: int = Field(default=40, ge=5, le=200)


class TriggerInput(StrictInput):
    dag_id: str = DAG_ID
    conf: dict[str, Any] = Field(default_factory=dict, description=f"Optional run config. Allowed keys: "
                                 f"{sorted(ALLOWED_CONF_KEYS)}")

    @field_validator("conf")
    @classmethod
    def _conf(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = set(v) - ALLOWED_CONF_KEYS
        if bad:
            raise ValueError(f"conf keys not allowed: {sorted(bad)}")
        return v


# ------------------------------------------------------------------ tools
def list_dags(_: NoInput) -> dict[str, Any]:
    dags = _client().request("GET", "/dags", params={"limit": 100}).get("dags", [])
    approved = set(get_settings().approved_dags)
    return {
        "dags": [
            {
                "dag_id": d["dag_id"],
                "is_paused": d.get("is_paused"),
                "tags": [t.get("name") for t in d.get("tags", [])],
                "approved_for_trigger": d["dag_id"] in approved,
            }
            for d in dags
        ]
    }


def get_dag_runs(params: DagInput) -> dict[str, Any]:
    runs = _client().request(
        "GET", f"/dags/{_q(params.dag_id)}/dagRuns", params={"limit": params.limit, "order_by": "-run_after"}
    ).get("dag_runs", [])
    states: dict[str, int] = {}
    for r in runs:
        states[r.get("state")] = states.get(r.get("state"), 0) + 1
    return {"dag_id": params.dag_id, "runs": [_run_view(r) for r in runs], "state_counts": states}


def get_dag_run_status(params: DagRunInput) -> dict[str, Any]:
    client = _client()
    run_id = params.dag_run_id or _latest_run_id(params.dag_id)
    run = client.request("GET", f"/dags/{_q(params.dag_id)}/dagRuns/{_q(run_id)}")
    tis = client.request("GET", f"/dags/{_q(params.dag_id)}/dagRuns/{_q(run_id)}/taskInstances").get(
        "task_instances", []
    )
    return {
        "dag_id": params.dag_id,
        "run": _run_view(run),
        "tasks": [
            {k: t.get(k) for k in ("task_id", "state", "try_number", "start_date", "end_date", "duration")}
            for t in tis
        ],
        "finished": run.get("state") in TERMINAL_STATES,
    }


def get_failed_tasks(params: DagRunInput) -> dict[str, Any]:
    run_id = params.dag_run_id or _latest_run_id(params.dag_id, state="failed")
    status = get_dag_run_status(DagRunInput(dag_id=params.dag_id, dag_run_id=run_id))
    failed = [t for t in status["tasks"] if t["state"] in ("failed", "upstream_failed", "up_for_retry")]
    return {
        "dag_id": params.dag_id,
        "dag_run_id": run_id,
        "run_state": status["run"]["state"],
        "failed_tasks": failed,
        "root_cause_task": next((t["task_id"] for t in failed if t["state"] == "failed"), None),
    }


def _flatten_log(content: Any) -> list[str]:
    if isinstance(content, str):
        return content.splitlines()
    lines: list[str] = []
    for item in content or []:
        if isinstance(item, dict):
            lines.append(" ".join(str(item.get(k, "")) for k in ("timestamp", "level", "event") if item.get(k)))
        elif isinstance(item, list | tuple):  # Airflow 2 style [(host, text)]
            lines.extend(str(item[-1]).splitlines())
        else:
            lines.append(str(item))
    return lines


def get_task_logs(params: TaskLogInput) -> dict[str, Any]:
    client = _client()
    run_id = params.dag_run_id or _latest_run_id(params.dag_id)
    try_number = params.try_number
    if try_number is None:
        ti = client.request(
            "GET", f"/dags/{_q(params.dag_id)}/dagRuns/{_q(run_id)}/taskInstances/{_q(params.task_id)}"
        )
        try_number = max(int(ti.get("try_number") or 1), 1)
    log = client.request(
        "GET",
        f"/dags/{_q(params.dag_id)}/dagRuns/{_q(run_id)}/taskInstances/{_q(params.task_id)}/logs/{try_number}",
        params={"full_content": "true"},
    )
    lines = [ln for ln in _flatten_log(log.get("content")) if ln.strip()]
    error_lines = [ln for ln in lines if any(k in ln for k in ("ERROR", "Error", "Exception", "FAILED", "Traceback"))]
    return {
        "dag_id": params.dag_id,
        "dag_run_id": run_id,
        "task_id": params.task_id,
        "try_number": try_number,
        "error_lines": error_lines[-15:],
        "tail": lines[-params.tail_lines:],
    }


def trigger_dag(params: TriggerInput) -> dict[str, Any]:
    _trigger_precheck(params)
    client = _client()
    dag = client.request("GET", f"/dags/{_q(params.dag_id)}")
    run = client.request(
        "POST", f"/dags/{_q(params.dag_id)}/dagRuns", json={"logical_date": None, "conf": params.conf}
    )
    return {
        "triggered": True,
        "dag_id": params.dag_id,
        "dag_run_id": run.get("dag_run_id"),
        "state": run.get("state"),
        "warning": "DAG is paused - run will stay queued until unpaused" if dag.get("is_paused") else None,
        "next_step": "poll airflow_get_dag_run_status with this dag_run_id",
    }


def _trigger_precheck(params: TriggerInput) -> None:
    if params.dag_id not in get_settings().approved_dags:
        raise PermissionDeniedError(
            f"DAG '{params.dag_id}' is not in the approved list {get_settings().approved_dags}"
        )


trigger_dag.precheck = _trigger_precheck  # type: ignore[attr-defined]


TOOLS = [
    ToolSpec("airflow_list_dags", "List Airflow DAGs, whether they are paused and whether the agent may "
             "trigger them.", "airflow", NoInput, list_dags),
    ToolSpec("airflow_get_dag_runs", "List the most recent runs of a DAG with their state.", "airflow",
             DagInput, get_dag_runs),
    ToolSpec("airflow_get_dag_run_status", "Get a DAG run's state and every task's state / tries / duration "
             "(latest run if dag_run_id omitted).", "airflow", DagRunInput, get_dag_run_status),
    ToolSpec("airflow_get_failed_tasks", "Identify failed / upstream_failed tasks in a DAG run (latest failed "
             "run if dag_run_id omitted).", "airflow", DagRunInput, get_failed_tasks),
    ToolSpec("airflow_get_task_logs", "Fetch a task's log tail and extracted error lines for failure "
             "diagnosis.", "airflow", TaskLogInput, get_task_logs),
    ToolSpec("airflow_trigger_dag", "Trigger an approved training DAG run with optional conf.", "airflow",
             TriggerInput, trigger_dag, mutating=True),
]
