"""LLM backends.

Every backend exposes `create(system, messages, tools) -> LLMTurn` so the orchestrator loop is backend
agnostic. Messages use the Anthropic Messages API format.

* ClaudeLLM   - real tool calling through the Anthropic SDK (default).
* OfflineLLM  - deterministic keyword planner + templated report; lets the agent run in demos/CI
                without an API key. It still only reports what the tools returned.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.config import get_settings


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class LLMTurn:
    stop_reason: str                      # "tool_use" | "end_turn" | "refusal" | "max_tokens"
    content: list[Any]                    # blocks to append verbatim to the history
    tool_calls: list[ToolUseBlock] = field(default_factory=list)
    text: str = ""


class LLMBackend(Protocol):
    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMTurn: ...


# ====================================================================== Claude
class ClaudeLLM:
    def __init__(self, client: Any = None, model: str | None = None, effort: str | None = None):
        import anthropic

        s = get_settings()
        self.client = client or anthropic.Anthropic(timeout=120.0, max_retries=2)
        self.model = model or s.llm_model
        self.effort = effort or s.llm_effort

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMTurn:
        # Server-side fallback: if the primary model declines, the API retries on a fallback model
        # inside the same call, so operational questions are not left unanswered.
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        tool_calls = [
            ToolUseBlock(id=b.id, name=b.name, input=dict(b.input)) for b in response.content if b.type == "tool_use"
        ]
        text = "\n".join(b.text for b in response.content if b.type == "text")
        stop = response.stop_reason
        if stop == "pause_turn":
            stop = "tool_use" if tool_calls else "end_turn"
        # response.content is appended unchanged (thinking blocks must be passed back as-is).
        return LLMTurn(stop_reason=stop, content=list(response.content), tool_calls=tool_calls, text=text)


# ====================================================================== Offline planner
INTENTS: dict[str, str] = {
    "dataset": r"\b(dataset|data version|dvc|provenance|reproduc\w*)\b",
    "model": r"\b(best|models?|experiments?|runs?|f1|metrics?|compare|candidates?)\b",
    "pipeline": r"\b(pipelines?|dags?|airflow|training|retrain\w*|trigger)\b",
    "deploy": r"\b(deploy\w*|kubernetes|k8s|pods?|api|unavailable|running|services?|health\w*)\b",
}


def _intents(text: str) -> set[str]:
    t = text.lower()
    found = {name for name, pattern in INTENTS.items() if re.search(pattern, t)}
    return found or {"model", "dataset", "pipeline", "deploy"}


class OfflineLLM:
    """Rule-based stand-in for the LLM. Plans tool calls by keyword, then writes a templated report."""

    def __init__(self) -> None:
        self._counter = 0

    def _call(self, name: str, args: dict[str, Any] | None = None) -> ToolUseBlock:
        self._counter += 1
        return ToolUseBlock(id=f"offline_{self._counter}", name=name, input=args or {})

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMTurn:
        question = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        results = _collect_results(messages)
        intents = _intents(question)
        q = question.lower()
        # "pipeline run" / "dag run" is about Airflow, not about MLflow runs
        if re.search(r"\b(pipeline|dag) runs?\b", q) and not re.search(r"\b(best|f1|compare|models?)\b", q):
            intents.discard("model")
        # "training runs" are MLflow runs, not the Airflow pipeline
        if re.search(r"\btraining runs?\b", q) and not re.search(r"\b(pipelines?|dags?|airflow|retrain\w*)\b", q):
            intents.discard("pipeline")
        available = {t["name"] for t in tools}
        calls: list[ToolUseBlock] = []

        if not results:  # ---- first step: plan
            run_ref = re.search(r"(?:experiment|run)\s+#?(\d+)", q)
            if "trigger" in q and "pipeline" in intents:
                # an explicit action request: only request the action (it still needs human approval)
                calls.append(self._call("airflow_trigger_dag", {}))
                intents = set()
            elif "pipeline" in intents and re.search(r"\b(status|succeed\w*|latest|last)\b", q) \
                    and not re.search(r"\b(fail\w*|error|why)\b", q):
                calls.append(self._call("airflow_get_dag_run_status", {}))
                calls.append(self._call("airflow_get_dag_runs", {"limit": 5}))
            elif "pipeline" in intents:
                calls.append(self._call("airflow_get_dag_runs", {"limit": 5}))
                if any(k in q for k in ("fail", "error", "why")):
                    calls.append(self._call("airflow_get_failed_tasks", {}))
            if run_ref and "dataset" in intents:
                calls.append(self._call("mlflow_get_run_provenance", {"run": run_ref.group(1)}))
            elif "model" in intents:
                metric = "f1" if "f1" in q or "best" in q else "f1"
                if "compare" in q:
                    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10}
                    n = re.search(r"last\s+(\d+|two|three|four|five|six|ten)", q)
                    last_n = (int(n.group(1)) if n.group(1).isdigit() else words[n.group(1)]) if n else 5
                    calls.append(self._call("mlflow_compare_runs", {"last_n": last_n, "metric": metric}))
                    calls.append(self._call("mlflow_get_model_registry", {}))
                else:
                    calls.append(self._call("mlflow_find_best_run", {"metric": metric}))
            elif "dataset" in intents:
                calls.append(self._call("dvc_get_dataset_version", {}))
                calls.append(self._call("dvc_check_data_changes", {}))
            if "deploy" in intents:
                calls.append(self._call("k8s_diagnose_deployment", {}))
            if "retrain" in q and not any(c.name.startswith("airflow") for c in calls):
                calls.append(self._call("airflow_get_dag_runs", {"limit": 5}))
        else:  # ---- follow-up step: chain provenance after best-run lookup
            best = results.get("mlflow_find_best_run", {}).get("data") or {}
            if best and "dataset" in intents and "mlflow_get_run_provenance" not in results:
                calls.append(self._call("mlflow_get_run_provenance", {"run": best["best_run"]["run_id"]}))
            failed = results.get("airflow_get_failed_tasks", {}).get("data") or {}
            if failed.get("root_cause_task") and "airflow_get_task_logs" not in results:
                calls.append(self._call("airflow_get_task_logs", {"task_id": failed["root_cause_task"],
                                                                  "dag_run_id": failed["dag_run_id"]}))
            prov = (results.get("mlflow_get_run_provenance", {}).get("data") or {}).get("provenance") or {}
            if prov.get("data.git_commit") and "dvc_get_dataset_version" not in results:
                calls.append(self._call("dvc_get_dataset_version", {"rev": prov["data.git_commit"]}))

        calls = [c for c in calls if c.name in available]
        if calls:
            return LLMTurn(stop_reason="tool_use", content=list(calls), tool_calls=calls)
        text = _offline_report(question, results)
        return LLMTurn(stop_reason="end_turn", content=[TextBlock(text)], text=text)


def _collect_results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map tool name -> parsed ToolResult dict, from the tool_result blocks in the history."""
    names: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    for m in messages:
        if not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
            if btype == "tool_use":
                names[b.id if not isinstance(b, dict) else b["id"]] = b.name if not isinstance(b, dict) else b["name"]
            elif btype == "tool_result":
                try:
                    results[names.get(b["tool_use_id"], "?")] = json.loads(b["content"])
                except (json.JSONDecodeError, TypeError):
                    pass
    return results


def _offline_report(question: str, results: dict[str, dict[str, Any]]) -> str:
    s = get_settings()
    facts: list[str] = []
    unknowns: list[str] = []
    actions: list[str] = []
    retrain_reasons: list[str] = []

    for name, r in results.items():
        if r.get("status") == "confirmation_required":
            data = r.get("data") or {}
            facts.append(f"`{name}` was requested but NOT executed: it changes system state and needs human approval "
                         f"(input {data.get('input')}).")
            actions.append(f"Approve or reject the pending action with token `{r.get('confirmation_token')}` "
                           "(POST /actions/confirm). After approval, ask for the latest pipeline run status.")
        elif r.get("status") != "success":
            unknowns.append(f"`{name}` returned **{r.get('status')}**: {r.get('error')}. No data from it is reported.")

    def ok(name: str) -> dict[str, Any] | None:
        r = results.get(name)
        return r["data"] if r and r.get("status") == "success" else None

    if best := ok("mlflow_find_best_run"):
        b = best["best_run"]
        facts.append(
            f"Best run by `{best['metric']}` among {best['considered_runs']} recent runs: **{b['run_name']}** "
            f"(`{b['run_id']}`) with metrics {b['metrics']}, quality gate: {b['quality_gate']}."
        )
        unknowns.extend(best.get("caveats", []))
        if b.get("quality_gate") == "failed":
            retrain_reasons.append("the best run did not pass the quality gate")
        if b.get("start_time"):
            from datetime import UTC, datetime

            age = (datetime.now(UTC) - datetime.fromisoformat(b["start_time"])).days
            facts.append(f"That model was trained {age} day(s) ago (freshness policy: {s.model_freshness_days} days).")
            if age > s.model_freshness_days:
                retrain_reasons.append(f"model age {age}d exceeds the {s.model_freshness_days}-day freshness policy")
    if cmp_ := ok("mlflow_compare_runs"):
        metric = cmp_["metric"]
        rows = [r for r in cmp_["runs"] if metric in r["metrics"]]
        facts.append(f"Compared {len(cmp_['runs'])} runs on `{metric}`:")
        for r in sorted(rows, key=lambda r: r["metrics"][metric], reverse=True):
            facts.append(f"  - {r['run_name']}: {metric}={r['metrics'][metric]}, recall={r['metrics'].get('recall')}, "
                         f"data={r['data_version']}, test_rows={r['metrics'].get('test_rows')}, "
                         f"gate={r['quality_gate']}, params={r['params']}")
        versions = sorted({r["data_version"] for r in rows if r["data_version"]})
        eligible = [r for r in rows if r["quality_gate"] == "passed"]
        if len(versions) > 1:
            latest = versions[-1]
            unknowns.append(f"Runs were evaluated on different dataset versions {versions} (different test sets), "
                            f"so their {metric} values are NOT directly comparable.")
            eligible = [r for r in eligible if r["data_version"] == latest] or eligible
        if eligible:
            pick = max(eligible, key=lambda r: r["metrics"][metric])
            top = max(rows, key=lambda r: r["metrics"][metric])
            why = (f"highest {metric} among runs that passed the quality gate on the latest data "
                   f"({pick['data_version']})")
            if top["run_name"] != pick["run_name"]:
                why += (f"; {top['run_name']} has a higher raw {metric} ({top['metrics'][metric]}) but on "
                        f"{top['data_version']} data / {top['metrics'].get('test_rows')} test rows")
            actions.append(f"Recommended candidate: **{pick['run_name']}** ({metric}={pick['metrics'][metric]}) - "
                           f"{why}. Before promoting it, validate it on a held-out set and check latency/drift: a "
                           "high offline metric alone does not make a model production-ready.")
    if reg := ok("mlflow_get_model_registry"):
        champ = reg["aliases"].get("champion")
        src = next((v["run_id"] for v in reg["versions"] if v["version"] == champ), None)
        facts.append(f"Registry: `{reg['model_name']}` has {len(reg['versions'])} version(s); @champion = v{champ} "
                     f"(source run `{src}`).")
    if prov := ok("mlflow_get_run_provenance"):
        p = prov["provenance"]
        facts.append(f"Run {prov['run_name']} was trained on dataset version **{p.get('data.version')}**, git commit "
                     f"`{p.get('data.git_commit')}`, DVC md5 `{p.get('data.dvc_md5')}`.")
        if p.get("data.provenance_check"):
            facts.append(f"Provenance check recorded at training time: {p['data.provenance_check']}.")
        if prov["missing_provenance_fields"]:
            unknowns.append(f"Missing provenance fields: {prov['missing_provenance_fields']}")
    if dv := ok("dvc_get_dataset_version"):
        facts.append(f"DVC at `{dv['git_commit_short']}` (tags {dv['tags'] or 'none'}): data version "
                     f"**{dv['data_version']}**, files {[(d['path'], d['md5']) for d in dv['datasets']]}.")
        if prov:
            run_md5 = prov["provenance"].get("data.dvc_md5")
            commit_md5s = {d["md5"] for d in dv["datasets"]}
            if run_md5 and commit_md5s and run_md5 not in commit_md5s:
                unknowns.append(
                    f"PROVENANCE MISMATCH: the run recorded data hash `{run_md5}`, but commit "
                    f"`{dv['git_commit_short']}` contains `{', '.join(sorted(commit_md5s))}`. The recorded commit "
                    "does not reproduce this run's data; find the commit that contains the recorded hash "
                    "(dvc_list_dataset_versions).")
            elif run_md5 in commit_md5s:
                facts.append("Cross-check passed: the run's recorded data hash matches the .dvc file at that commit "
                             "- the dataset is reproducible from it.")
                actions.append(f"Reproduce with: git checkout {dv['git_commit_short']} -- "
                               f"{' '.join(d['dvc_file'] for d in dv['datasets'])} params.yaml && dvc pull && "
                               "dvc checkout")
    if ch := ok("dvc_check_data_changes"):
        facts.append("Workspace data matches the committed .dvc files." if ch["workspace_matches_dvc_files"]
                     else f"Tracked data has CHANGED vs the committed version: {ch['dvc_status']}")
    if runs := ok("airflow_get_dag_runs"):
        latest = runs["runs"][0] if runs["runs"] else None
        latest_txt = f"{latest['dag_run_id']} = {latest['state']}" if latest else "none"
        facts.append(f"Airflow `{runs['dag_id']}` recent runs: {runs['state_counts']}; latest: {latest_txt}.")
        if runs["runs"] and runs["runs"][0]["state"] == "failed":
            retrain_reasons.append("the latest training pipeline run failed")
    if failed := ok("airflow_get_failed_tasks"):
        facts.append(f"Failed run {failed['dag_run_id']}: root-cause task `{failed['root_cause_task']}`, failed tasks "
                     f"{[t['task_id'] + ':' + t['state'] for t in failed['failed_tasks']]}.")
        logs = ok("airflow_get_task_logs")
        if logs and logs.get("error_lines"):
            facts.append(f"Log of `{logs['task_id']}` (try {logs['try_number']}): "
                         + " | ".join(logs["error_lines"][-3:]))
            actions.append(f"Fix the error in `{logs['task_id']}` shown in its log, then re-trigger the pipeline.")
        else:
            actions.append(f"Inspect logs with airflow_get_task_logs(task_id='{failed['root_cause_task']}').")
    if diag := ok("k8s_diagnose_deployment"):
        d = diag["deployment"]
        facts.append(f"Deployment `{d['deployment']}` runs {d['images']} (version {d['version_label']}), "
                     f"{d['ready_replicas']}/{d['desired_replicas']} replicas ready.")
        facts.extend(f"K8s finding: {f}" for f in diag["findings"])
        for e in diag["warning_events"][:3]:
            facts.append(f"Event {e['reason']} on {e['object']}: {e['message']}")
        logs = diag.get("logs_of_first_unhealthy_pod") or {}
        if logs.get("error_lines"):
            facts.append(f"Pod log errors: {logs['error_lines'][-2:]}")
        if not diag["findings"][0].startswith("no problems"):
            cause = " ".join(logs.get("error_lines") or [])
            if "MODEL_PATH" in cause or "model file not found" in cause:
                actions.append("Root cause: the ConfigMap sets a MODEL_PATH that does not exist in the image, so the "
                               "model never loads and /health/ready returns 503. Fix: restore MODEL_PATH="
                               "/app/model/model.joblib (kubectl apply -f kubernetes/configmap.yaml), then restart "
                               "the deployment via k8s_restart_deployment (requires your confirmation).")
            elif any("ImagePull" in f or "ErrImage" in f for f in diag["findings"]):
                actions.append("Root cause: the image tag cannot be pulled. Fix: set the image back to a tag "
                               "loaded in the cluster (kubectl apply -f kubernetes/deployment.yaml).")
            else:
                actions.append("Investigate the failing pods' logs and events listed above, fix the cause, then "
                               "restart the deployment (requires confirmation).")
    if trig := ok("airflow_trigger_dag"):
        facts.append(f"Triggered DAG run {trig['dag_run_id']} (state {trig['state']}).")

    if "retrain" in question.lower():
        if retrain_reasons:
            actions.append("Retraining is recommended because " + "; ".join(retrain_reasons)
                           + ". It was NOT started automatically - trigger `ml_training_pipeline` after approval.")
        else:
            actions.append("No evidence requires retraining (quality gate passed, model within freshness policy, "
                           "no degradation results supplied). Retraining NOT recommended now.")

    if not facts:
        facts.append("No operational data could be retrieved - every tool call failed, so no status is claimed.")
    sections = ["### Known facts (from tool evidence)", *[f"- {f}" for f in facts]]
    sections += ["", "### Uncertainties", *([f"- {u}" for u in unknowns] or ["- None identified."])]
    sections += ["", "### Recommended actions", *([f"- {a}" for a in actions] or ["- No action required."])]
    return "\n".join(sections)
