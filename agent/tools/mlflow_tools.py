"""MLflow experiment-intelligence tools (MlflowClient API only, read-only)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from agent.config import get_settings
from agent.schemas import NoInput, StrictInput
from agent.tools.base import ServiceUnavailableError, ToolError, ToolSpec

# Fail fast instead of MLflow's default multi-minute retry loop when the server is down.
os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")

PROVENANCE_TAGS = (
    "data.version", "data.git_commit", "data.git_tag", "data.dvc_md5", "data.path", "data.provenance_check",
    "code.git_commit",
    "pipeline.dag_id", "pipeline.dag_run_id", "quality_gate.status", "quality_gate.reason",
)
_client_override: Any = None


def set_client(client: Any) -> None:
    """Inject a fake client (tests)."""
    global _client_override
    _client_override = client


def _client() -> Any:
    if _client_override is not None:
        return _client_override
    try:
        from mlflow.tracking import MlflowClient
    except ImportError as exc:
        raise ServiceUnavailableError("mlflow package not installed (pip install -e .[mlops])") from exc
    return MlflowClient(tracking_uri=get_settings().mlflow_tracking_uri)


def _call(fn, *args, **kwargs):
    """Translate MLflow connection failures into ServiceUnavailableError."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if any(s in text for s in ("Max retries", "Connection", "connect", "timed out", "NameResolution")):
            raise ServiceUnavailableError(
                f"MLflow tracking server unreachable at {get_settings().mlflow_tracking_uri}"
            ) from exc
        if "RESOURCE_DOES_NOT_EXIST" in text or "not found" in text.lower():
            raise ToolError(text[:300]) from exc
        raise


def _ts(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds") if ms else None


def _experiment(name: str | None) -> Any:
    name = name or get_settings().mlflow_experiment
    exp = _call(_client().get_experiment_by_name, name)
    if exp is None:
        raise ToolError(f"experiment '{name}' not found")
    return exp


def _summarize(run: Any, metrics: list[str] | None = None) -> dict[str, Any]:
    tags = run.data.tags
    m = run.data.metrics
    return {
        "run_id": run.info.run_id,
        "run_name": tags.get("mlflow.runName") or getattr(run.info, "run_name", None),
        "status": run.info.status,
        "start_time": _ts(run.info.start_time),
        "metrics": {k: round(v, 4) for k, v in m.items() if metrics is None or k in metrics},
        "data_version": tags.get("data.version"),
        "data_git_commit": tags.get("data.git_commit"),
        "quality_gate": tags.get("quality_gate.status"),
    }


def _resolve_run(ref: str, experiment_name: str | None = None) -> Any:
    """Accept a run id, an exact run name, or a run number ('25' -> 'train-25')."""
    client = _client()
    if len(ref) == 32 and all(c in "0123456789abcdef" for c in ref):
        return _call(client.get_run, ref)
    exp = _experiment(experiment_name)
    candidates = [ref, f"train-{ref}"] if ref.isdigit() else [ref]
    for name in candidates:
        runs = _call(
            client.search_runs, [exp.experiment_id], filter_string=f"tags.mlflow.runName = '{name}'", max_results=1
        )
        if runs:
            return runs[0]
    raise ToolError(f"no run found with id or name '{ref}' in experiment '{exp.name}'")


# ------------------------------------------------------------------ inputs
class ListRunsInput(StrictInput):
    experiment_name: str | None = Field(default=None, description="Experiment name (default: project experiment).")
    max_results: int = Field(default=10, ge=1, le=100)


class RunRef(StrictInput):
    run: str = Field(min_length=1, max_length=64, description="Run id, run name (e.g. train-25) or run number (25).")
    experiment_name: str | None = None


class BestRunInput(StrictInput):
    metric: str = Field(default="f1", pattern=r"^[A-Za-z0-9_.\-]+$")
    experiment_name: str | None = None
    last_n: int = Field(default=20, ge=1, le=200, description="Only consider the N most recent finished runs.")
    higher_is_better: bool = True


class CompareRunsInput(StrictInput):
    runs: list[str] = Field(default_factory=list, max_length=10, description="Run ids/names. Empty = use last_n.")
    last_n: int = Field(default=5, ge=2, le=20)
    metric: str = Field(default="f1", pattern=r"^[A-Za-z0-9_.\-]+$", description="Metric used for ranking.")
    experiment_name: str | None = None


class CompareExperimentsInput(StrictInput):
    experiment_names: list[str] = Field(min_length=2, max_length=5)
    metric: str = Field(default="f1", pattern=r"^[A-Za-z0-9_.\-]+$")


class RegistryInput(StrictInput):
    model_name: str | None = Field(default=None, description="Registered model name (default: project model).")


# ------------------------------------------------------------------ tools
def list_experiments(_: NoInput) -> dict[str, Any]:
    exps = _call(_client().search_experiments)
    return {
        "experiments": [
            {"experiment_id": e.experiment_id, "name": e.name, "lifecycle_stage": e.lifecycle_stage} for e in exps
        ]
    }


def list_runs(params: ListRunsInput) -> dict[str, Any]:
    exp = _experiment(params.experiment_name)
    runs = _call(
        _client().search_runs, [exp.experiment_id], order_by=["attributes.start_time DESC"],
        max_results=params.max_results,
    )
    return {"experiment": exp.name, "count": len(runs), "runs": [_summarize(r) for r in runs]}


def get_run_details(params: RunRef) -> dict[str, Any]:
    run = _resolve_run(params.run, params.experiment_name)
    try:
        artifacts = [a.path for a in _call(_client().list_artifacts, run.info.run_id)]
    except ToolError:
        artifacts = []
    summary = _summarize(run)
    summary.update(
        {
            "params": dict(run.data.params),
            "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
            "artifacts": artifacts,
            "end_time": _ts(run.info.end_time),
        }
    )
    return summary


def get_run_provenance(params: RunRef) -> dict[str, Any]:
    run = _resolve_run(params.run, params.experiment_name)
    tags = run.data.tags
    provenance = {k: tags.get(k) for k in PROVENANCE_TAGS}
    missing = [k for k in ("data.version", "data.git_commit", "data.dvc_md5") if not provenance.get(k)]
    return {
        "run_id": run.info.run_id,
        "run_name": tags.get("mlflow.runName"),
        "provenance": provenance,
        "missing_provenance_fields": missing,
        "reproduce_with": (
            f"dvc_get_reproduction_steps(rev='{provenance['data.git_commit']}')"
            if provenance.get("data.git_commit") else None
        ),
    }


def find_best_run(params: BestRunInput) -> dict[str, Any]:
    exp = _experiment(params.experiment_name)
    runs = _call(
        _client().search_runs, [exp.experiment_id], filter_string="attributes.status = 'FINISHED'",
        order_by=["attributes.start_time DESC"], max_results=params.last_n,
    )
    scored = [r for r in runs if params.metric in r.data.metrics]
    if not scored:
        raise ToolError(f"no finished runs with metric '{params.metric}' among the last {params.last_n}")
    ranked = sorted(scored, key=lambda r: r.data.metrics[params.metric], reverse=params.higher_is_better)
    best = ranked[0]
    caveats = []
    if best.data.tags.get("quality_gate.status") != "passed":
        caveats.append(f"quality gate status is '{best.data.tags.get('quality_gate.status', 'unknown')}'")
    if len(ranked) > 1:
        gap = abs(ranked[0].data.metrics[params.metric] - ranked[1].data.metrics[params.metric])
        if gap < 0.005:
            caveats.append(f"top-2 {params.metric} differ by only {gap:.4f} - difference may not be significant")
    caveats.append("best metric alone does not make a model production-ready (check registry, latency, drift)")
    return {
        "experiment": exp.name,
        "metric": params.metric,
        "considered_runs": len(scored),
        "best_run": _summarize(best),
        "ranking": [_summarize(r, [params.metric, "accuracy", "precision", "recall"]) for r in ranked[:10]],
        "caveats": caveats,
    }


def compare_runs(params: CompareRunsInput) -> dict[str, Any]:
    if params.runs:
        runs = [_resolve_run(r, params.experiment_name) for r in params.runs]
    else:
        exp = _experiment(params.experiment_name)
        runs = _call(
            _client().search_runs, [exp.experiment_id], filter_string="attributes.status = 'FINISHED'",
            order_by=["attributes.start_time DESC"], max_results=params.last_n,
        )
    if len(runs) < 2:
        raise ToolError("need at least two runs to compare")
    rows = []
    for r in runs:
        row = _summarize(r)
        row["params"] = dict(r.data.params)
        rows.append(row)
    with_metric = [r for r in rows if params.metric in r["metrics"]]
    ranked = sorted(with_metric, key=lambda r: r["metrics"][params.metric], reverse=True)
    return {
        "metric": params.metric,
        "runs": rows,
        "ranking_by_metric": [{"run_name": r["run_name"], params.metric: r["metrics"][params.metric]} for r in ranked],
        "recommended_candidate": ranked[0]["run_name"] if ranked else None,
    }


def compare_experiments(params: CompareExperimentsInput) -> dict[str, Any]:
    out = []
    for name in params.experiment_names:
        exp = _experiment(name)
        runs = _call(
            _client().search_runs, [exp.experiment_id], filter_string="attributes.status = 'FINISHED'", max_results=200
        )
        values = [r.data.metrics[params.metric] for r in runs if params.metric in r.data.metrics]
        out.append(
            {
                "experiment": name,
                "finished_runs": len(runs),
                f"best_{params.metric}": round(max(values), 4) if values else None,
                f"mean_{params.metric}": round(sum(values) / len(values), 4) if values else None,
            }
        )
    return {"metric": params.metric, "experiments": out}


def get_model_registry(params: RegistryInput) -> dict[str, Any]:
    name = params.model_name or get_settings().registered_model_name
    client = _client()
    versions = _call(client.search_model_versions, f"name = '{name}'")
    aliases: dict[str, str] = {}
    try:
        model = _call(client.get_registered_model, name)
        aliases = dict(getattr(model, "aliases", {}) or {})
    except ToolError:
        pass
    return {
        "model_name": name,
        "aliases": aliases,
        "versions": [
            {
                "version": v.version,
                "run_id": v.run_id,
                "status": v.status,
                "created": _ts(v.creation_timestamp),
                "tags": dict(getattr(v, "tags", {}) or {}),
            }
            for v in sorted(versions, key=lambda v: int(v.version), reverse=True)
        ],
    }


TOOLS = [
    ToolSpec("mlflow_list_experiments", "List MLflow experiments.", "mlflow", NoInput, list_experiments),
    ToolSpec("mlflow_list_runs", "List recent runs of an experiment with key metrics and data version.",
             "mlflow", ListRunsInput, list_runs),
    ToolSpec("mlflow_get_run_details", "Get metrics, parameters, tags and artifacts of one run (by id, name or "
             "number).", "mlflow", RunRef, get_run_details),
    ToolSpec("mlflow_get_run_provenance", "Get the recorded dataset provenance of a run: DVC data version, git "
             "commit, md5, Airflow DAG run and quality-gate result.", "mlflow", RunRef, get_run_provenance),
    ToolSpec("mlflow_find_best_run", "Find the best recent finished run by a metric, with ranking and caveats.",
             "mlflow", BestRunInput, find_best_run),
    ToolSpec("mlflow_compare_runs", "Compare specific runs, or the last N runs, by metrics and parameters.",
             "mlflow", CompareRunsInput, compare_runs),
    ToolSpec("mlflow_compare_experiments", "Compare two or more experiments by best and mean metric.",
             "mlflow", CompareExperimentsInput, compare_experiments),
    ToolSpec("mlflow_get_model_registry", "Get registered model versions, aliases (e.g. champion) and source runs.",
             "mlflow", RegistryInput, get_model_registry),
]
