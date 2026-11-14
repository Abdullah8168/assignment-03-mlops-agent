"""Kubernetes operations tools.

Safety model:
  * every tool (read or write) is restricted to K8S_APPROVED_NAMESPACES,
  * write tools (restart / scale) are `mutating=True`: the registry never executes them without a
    human confirmation token, and the precheck rejects non-approved namespaces / deployments up front,
  * the agent uses the user's kubeconfig context; in-cluster use should bind a namespaced Role
    (see kubernetes/agent-rbac.yaml) rather than cluster-admin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from agent.config import get_settings
from agent.schemas import K8sNamespaced
from agent.tools.base import PermissionDeniedError, ServiceUnavailableError, ToolError, ToolSpec

_api_override: dict[str, Any] | None = None
REQUEST_TIMEOUT_S = 10


def set_apis(core: Any, apps: Any) -> None:
    """Inject fake CoreV1Api / AppsV1Api (tests)."""
    global _api_override
    _api_override = {"core": core, "apps": apps}


def _apis() -> tuple[Any, Any]:
    if _api_override is not None:
        return _api_override["core"], _api_override["apps"]
    try:
        from kubernetes import client, config
    except ImportError as exc:
        raise ServiceUnavailableError("kubernetes package not installed (pip install -e .[mlops])") from exc
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001
        try:
            config.load_kube_config()
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError(f"no usable kubeconfig: {exc}") from exc
    return client.CoreV1Api(), client.AppsV1Api()


def _call(fn, *args, **kwargs):
    kwargs.setdefault("_request_timeout", REQUEST_TIMEOUT_S)
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status", None)
        if status == 404:
            raise ToolError(f"Kubernetes resource not found: {getattr(exc, 'reason', exc)}") from exc
        if status == 403:
            raise PermissionDeniedError(f"Kubernetes RBAC denied the request: {getattr(exc, 'reason', exc)}") from exc
        if status is None and any(s in str(exc) for s in ("Max retries", "Connection", "refused", "timed out")):
            raise ServiceUnavailableError("Kubernetes API server unreachable") from exc
        raise


def _namespace(ns: str | None) -> str:
    s = get_settings()
    ns = ns or s.k8s_default_namespace
    if ns not in s.k8s_approved_namespaces:
        raise PermissionDeniedError(f"namespace '{ns}' is not approved (allowed: {s.k8s_approved_namespaces})")
    return ns


def _age(ts: Any) -> str | None:
    if not ts:
        return None
    seconds = int((datetime.now(UTC) - ts).total_seconds())
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60}s"


def _container_state(cs: Any) -> dict[str, Any]:
    state, last = cs.state, cs.last_state
    current = "unknown"
    reason = message = None
    if state and state.running:
        current = "running"
    elif state and state.waiting:
        current, reason, message = "waiting", state.waiting.reason, state.waiting.message
    elif state and state.terminated:
        current, reason, message = "terminated", state.terminated.reason, state.terminated.message
    last_term = last.terminated if last else None
    return {
        "name": cs.name,
        "image": cs.image,
        "ready": cs.ready,
        "restart_count": cs.restart_count,
        "state": current,
        "reason": reason,
        "message": (message or "")[:300] or None,
        "last_termination_reason": last_term.reason if last_term else None,
        "last_exit_code": last_term.exit_code if last_term else None,
    }


def _pod_view(p: Any) -> dict[str, Any]:
    containers = [_container_state(cs) for cs in (p.status.container_statuses or [])]
    conditions = {c.type: c.status for c in (p.status.conditions or [])}
    unhealthy_reasons = [c["reason"] for c in containers if c["reason"]]
    if p.status.phase not in ("Running", "Succeeded"):
        unhealthy_reasons.append(f"phase={p.status.phase}")
    if conditions.get("Ready") != "True" and p.status.phase == "Running":
        unhealthy_reasons.append("not Ready (readiness probe failing?)")
    return {
        "name": p.metadata.name,
        "phase": p.status.phase,
        "ready": conditions.get("Ready") == "True",
        "restarts": sum(c["restart_count"] for c in containers),
        "age": _age(p.metadata.creation_timestamp),
        "node": p.spec.node_name,
        "version_label": (p.metadata.labels or {}).get("app.kubernetes.io/version"),
        "containers": containers,
        "unhealthy_reasons": unhealthy_reasons,
    }


# ------------------------------------------------------------------ inputs
class PodsInput(K8sNamespaced):
    label_selector: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-/=,!]*$",
                                       description="Label selector; default is the ML application's label.")


class PodRef(K8sNamespaced):
    pod_name: str = Field(pattern=r"^[a-z0-9]([a-z0-9.\-]*[a-z0-9])?$", max_length=253)


class PodLogsInput(PodRef):
    tail_lines: int = Field(default=50, ge=1, le=500)
    previous: bool = Field(default=False, description="Logs of the previous (crashed) container instance.")


class EventsInput(K8sNamespaced):
    warnings_only: bool = True
    limit: int = Field(default=20, ge=1, le=100)


class DeploymentRef(K8sNamespaced):
    deployment: str = Field(default="prediction-api", pattern=r"^[a-z0-9]([a-z0-9.\-]*[a-z0-9])?$", max_length=253)


class ScaleInput(DeploymentRef):
    replicas: int = Field(ge=1, le=5, description="Desired replicas (bounded 1-5 by policy).")


# ------------------------------------------------------------------ read tools
def list_deployments(params: K8sNamespaced) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    _, apps = _apis()
    items = _call(apps.list_namespaced_deployment, ns).items
    return {
        "namespace": ns,
        "deployments": [
            {
                "name": d.metadata.name,
                "desired": d.spec.replicas,
                "ready": d.status.ready_replicas or 0,
                "available": d.status.available_replicas or 0,
                "updated": d.status.updated_replicas or 0,
                "images": [c.image for c in d.spec.template.spec.containers],
                "version_label": (d.metadata.labels or {}).get("app.kubernetes.io/version"),
            }
            for d in items
        ],
    }


def list_pods(params: PodsInput) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    core, _ = _apis()
    selector = params.label_selector if params.label_selector is not None else get_settings().k8s_app_label
    pods = _call(core.list_namespaced_pod, ns, label_selector=selector).items
    return {"namespace": ns, "label_selector": selector, "pods": [_pod_view(p) for p in pods]}


def get_pod_status(params: PodRef) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    core, _ = _apis()
    pod = _call(core.read_namespaced_pod, params.pod_name, ns)
    view = _pod_view(pod)
    probes = []
    for c in pod.spec.containers:
        for kind in ("readiness_probe", "liveness_probe"):
            probe = getattr(c, kind)
            if probe and probe.http_get:
                probes.append({"container": c.name, "type": kind, "path": probe.http_get.path,
                               "port": str(probe.http_get.port)})
    view["probes"] = probes
    view["conditions"] = [
        {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
        for c in (pod.status.conditions or [])
    ]
    return view


def get_pod_logs(params: PodLogsInput) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    core, _ = _apis()
    text = _call(core.read_namespaced_pod_log, params.pod_name, ns, tail_lines=params.tail_lines,
                 previous=params.previous)
    lines = (text or "").splitlines()
    errors = [ln for ln in lines if any(k in ln for k in ("ERROR", "Error", "Exception", "Traceback", "CRITICAL"))]
    return {"pod": params.pod_name, "namespace": ns, "previous": params.previous, "error_lines": errors[-10:],
            "lines": lines}


def list_services(params: K8sNamespaced) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    core, _ = _apis()
    services = _call(core.list_namespaced_service, ns).items
    out = []
    for s in services:
        ready_endpoints = 0
        try:
            ep = _call(core.read_namespaced_endpoints, s.metadata.name, ns)
            ready_endpoints = sum(len(sub.addresses or []) for sub in (ep.subsets or []))
        except ToolError:
            pass
        out.append(
            {
                "name": s.metadata.name,
                "type": s.spec.type,
                "cluster_ip": s.spec.cluster_ip,
                "ports": [f"{p.port}->{p.target_port}/{p.protocol}" for p in (s.spec.ports or [])],
                "selector": s.spec.selector,
                "ready_endpoints": ready_endpoints,
            }
        )
    return {"namespace": ns, "services": out}


def find_unhealthy_pods(params: K8sNamespaced) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    pods = list_pods(PodsInput(namespace=ns, label_selector=""))["pods"]
    unhealthy = [p for p in pods if p["unhealthy_reasons"] or not p["ready"]]
    restarted = [{"pod": p["name"], "restarts": p["restarts"]} for p in pods if p["restarts"] > 0]
    return {"namespace": ns, "total_pods": len(pods), "unhealthy_pods": unhealthy, "pods_with_restarts": restarted}


def get_events(params: EventsInput) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    core, _ = _apis()
    kwargs = {"field_selector": "type=Warning"} if params.warnings_only else {}
    events = _call(core.list_namespaced_event, ns, **kwargs).items
    events = sorted(events, key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime.min.replace(
        tzinfo=UTC), reverse=True)[: params.limit]
    return {
        "namespace": ns,
        "events": [
            {
                "type": e.type,
                "reason": e.reason,
                "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "message": (e.message or "")[:300],
                "count": e.count,
                "last_seen": _age(e.last_timestamp or e.metadata.creation_timestamp),
            }
            for e in events
        ],
    }


def get_deployed_version(params: DeploymentRef) -> dict[str, Any]:
    ns = _namespace(params.namespace)
    _, apps = _apis()
    d = _call(apps.read_namespaced_deployment, params.deployment, ns)
    env: dict[str, str] = {}
    for c in d.spec.template.spec.containers:
        for e in c.env or []:
            if e.name in ("APP_VERSION", "MODEL_VERSION", "MODEL_NAME") and e.value:
                env[e.name] = e.value
    return {
        "deployment": params.deployment,
        "namespace": ns,
        "version_label": (d.metadata.labels or {}).get("app.kubernetes.io/version"),
        "images": [c.image for c in d.spec.template.spec.containers],
        "model_version_annotation": (d.spec.template.metadata.annotations or {}).get("mlops/model-version"),
        "env_versions": env,
        "revision": (d.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
        "ready_replicas": d.status.ready_replicas or 0,
        "desired_replicas": d.spec.replicas,
    }


def diagnose_deployment(params: DeploymentRef) -> dict[str, Any]:
    """Collect all evidence needed to answer 'why is my API unavailable?' in one call."""
    ns = _namespace(params.namespace)
    version = get_deployed_version(params)
    pods = list_pods(PodsInput(namespace=ns))["pods"]
    services = list_services(K8sNamespaced(namespace=ns))["services"]
    events = get_events(EventsInput(namespace=ns, limit=15))["events"]
    bad = [p for p in pods if p["unhealthy_reasons"] or not p["ready"]]
    log_evidence = None
    if bad:
        pod = bad[0]
        crashed = any(c["last_termination_reason"] for c in pod["containers"])
        try:
            log_evidence = get_pod_logs(PodLogsInput(namespace=ns, pod_name=pod["name"], tail_lines=30,
                                                     previous=crashed))
        except ToolError as exc:
            log_evidence = {"error": str(exc)}
    findings = []
    if version["ready_replicas"] < (version["desired_replicas"] or 0):
        findings.append(f"only {version['ready_replicas']}/{version['desired_replicas']} replicas ready")
    for p in bad:
        findings.append(f"pod {p['name']}: {', '.join(p['unhealthy_reasons']) or 'not ready'}")
    for s in services:
        if s["ready_endpoints"] == 0:
            findings.append(f"service {s['name']} has NO ready endpoints - traffic cannot be served")
    return {
        "deployment": version,
        "pods": pods,
        "services": services,
        "warning_events": events,
        "logs_of_first_unhealthy_pod": log_evidence,
        "findings": findings or ["no problems detected: all replicas ready and service has endpoints"],
    }


# ------------------------------------------------------------------ write tools (confirmation-gated)
def _mutation_precheck(params: DeploymentRef) -> None:
    _namespace(params.namespace)
    allowed = get_settings().k8s_mutable_deployments
    if params.deployment not in allowed:
        raise PermissionDeniedError(f"deployment '{params.deployment}' is not modifiable (allowed: {allowed})")


def restart_deployment(params: DeploymentRef) -> dict[str, Any]:
    _mutation_precheck(params)
    ns = _namespace(params.namespace)
    _, apps = _apis()
    now = datetime.now(UTC).isoformat()
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
    _call(apps.patch_namespaced_deployment, params.deployment, ns, body)
    return {"restarted": params.deployment, "namespace": ns, "restarted_at": now}


def scale_deployment(params: ScaleInput) -> dict[str, Any]:
    _mutation_precheck(params)
    ns = _namespace(params.namespace)
    _, apps = _apis()
    _call(apps.patch_namespaced_deployment_scale, params.deployment, ns, {"spec": {"replicas": params.replicas}})
    return {"scaled": params.deployment, "namespace": ns, "replicas": params.replicas}


restart_deployment.precheck = _mutation_precheck  # type: ignore[attr-defined]
scale_deployment.precheck = _mutation_precheck  # type: ignore[attr-defined]


TOOLS = [
    ToolSpec("k8s_list_deployments", "List Deployments with desired/ready replicas, images and version.",
             "kubernetes", K8sNamespaced, list_deployments),
    ToolSpec("k8s_list_pods", "List application Pods with phase, readiness, restarts and unhealthy reasons.",
             "kubernetes", PodsInput, list_pods),
    ToolSpec("k8s_get_pod_status", "Detailed status of one Pod: container states, probes and conditions.",
             "kubernetes", PodRef, get_pod_status),
    ToolSpec("k8s_get_pod_logs", "Get recent logs of a Pod (previous=true for the crashed instance).",
             "kubernetes", PodLogsInput, get_pod_logs),
    ToolSpec("k8s_list_services", "List Services with type, ports, selector and number of ready endpoints.",
             "kubernetes", K8sNamespaced, list_services),
    ToolSpec("k8s_find_unhealthy_pods", "Find Pods that are not ready / failing and Pods that restarted.",
             "kubernetes", K8sNamespaced, find_unhealthy_pods),
    ToolSpec("k8s_get_events", "Recent namespace events (Warnings by default): probe failures, image pull "
             "errors, OOMKills, scheduling problems.", "kubernetes", EventsInput, get_events),
    ToolSpec("k8s_get_deployed_version", "Report the currently deployed application/model version of a "
             "Deployment.", "kubernetes", DeploymentRef, get_deployed_version),
    ToolSpec("k8s_diagnose_deployment", "One-shot health diagnosis of a Deployment: replicas, pods, service "
             "endpoints, warning events and logs of the first unhealthy pod.", "kubernetes", DeploymentRef,
             diagnose_deployment),
    ToolSpec("k8s_restart_deployment", "Rolling-restart a Deployment in an approved namespace.", "kubernetes",
             DeploymentRef, restart_deployment, mutating=True),
    ToolSpec("k8s_scale_deployment", "Scale a Deployment (1-5 replicas) in an approved namespace.", "kubernetes",
             ScaleInput, scale_deployment, mutating=True),
]
