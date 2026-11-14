"""The MLOps agent: LLM tool-selection loop + permissions + tracing + evidence grounding."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from agent.config import get_settings
from agent.llm import ClaudeLLM, LLMBackend, OfflineLLM
from agent.logging_utils import configure_logging, get_logger, log_event, new_request_id, request_id_var
from agent.schemas import ChatResponse, PendingAction, ToolCallRecord, ToolResult, ToolStatus
from agent.tools import build_registry
from agent.tools.base import ToolRegistry

logger = get_logger("orchestrator")

SYSTEM_PROMPT = """You are an MLOps operations agent for a machine-learning team. You can inspect and operate \
real infrastructure through tools: DVC (dataset versions), MLflow (experiments, runs, model registry), \
Apache Airflow (training pipeline `ml_training_pipeline`) and Kubernetes (the `prediction-api` inference service \
in namespace `ml-serving`).

Rules - follow them strictly:
1. Evidence only. Every operational claim (status, metric, version, commit, pod state) must come from a tool \
result in this conversation. Never fill gaps from general knowledge or guess values.
2. If a tool returns unavailable / timeout / error, say plainly which system could not be reached and that its \
status is unknown. Do not describe what it "probably" shows.
3. Use as many tools as the question needs. Call independent tools in parallel. For provenance questions read \
the MLflow run's recorded data tags, then confirm them with DVC.
4. Highest metric is not the same as production-ready. When recommending a model, mention quality-gate status, \
registry status and other caveats.
5. State-changing tools (trigger DAG, restart/scale deployment, dataset checkout) only create a pending request \
that a human must approve. Never claim such an action was executed unless a tool result says so. Never trigger \
retraining as a side effect of a question about whether retraining is needed.
6. Recommend retraining only with explicit evidence: a failed quality gate, model older than the freshness \
policy ({freshness} days), a failed recent training pipeline, or supplied degradation results. Otherwise say \
retraining is not justified by the available evidence.
7. Diagnose failures by correlating evidence across layers (data -> pipeline -> model -> deployment): name the \
failing component, the evidence, the likely root cause and a concrete fix.

Answer format (markdown):
### Known facts (from tool evidence)
### Uncertainties
### Recommended actions
Keep it concise; cite run names, commits, task ids and pod names exactly as tools returned them."""

# Values that look like operational facts and therefore must appear in tool evidence.
_HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_DECIMAL_RE = re.compile(r"\b0\.\d{3,4}\b")


def _norm_decimal(v: str) -> str:
    return f"{float(v):.4f}".rstrip("0")


def find_unverified_values(answer: str, evidence: str) -> list[str]:
    """Return commit-like hashes and metric-like decimals in the answer that no tool result contains."""
    unverified = [h for h in set(_HEX_RE.findall(answer)) if h not in evidence and not h.isdigit()]
    ev_decimals = {_norm_decimal(d) for d in re.findall(r"0\.\d+", evidence)}
    # metrics are rounded to 4 dp by the tools; accept the answer's value if it is a rounding of evidence
    ev_rounded = ev_decimals | {_norm_decimal(f"{float(d):.3f}") for d in re.findall(r"0\.\d+", evidence)}
    unverified += [d for d in set(_DECIMAL_RE.findall(answer)) if _norm_decimal(d) not in ev_rounded]
    return sorted(unverified)


def _describe_pending(result: ToolResult) -> str:
    data = result.data or {}
    return f"{data.get('action', result.tool)} with {json.dumps(data.get('input', {}), default=str)}"


def make_llm(backend: str | None = None) -> LLMBackend:
    backend = (backend or get_settings().llm_backend).lower()
    if backend == "offline":
        return OfflineLLM()
    return ClaudeLLM()


class MLOpsAgent:
    def __init__(self, llm: LLMBackend | None = None, registry: ToolRegistry | None = None,
                 max_steps: int | None = None) -> None:
        configure_logging()
        self.registry = registry or build_registry()
        self.llm = llm or make_llm()
        self.max_steps = max_steps or get_settings().max_agent_steps
        self.system = SYSTEM_PROMPT.format(freshness=get_settings().model_freshness_days)

    def run(self, message: str, request_id: str | None = None) -> ChatResponse:
        request_id = request_id or new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            return self._run(message, request_id)
        finally:
            log_event(logger, "request_complete", duration_ms=round((time.perf_counter() - started) * 1000, 1))
            request_id_var.reset(token)

    def _run(self, message: str, request_id: str) -> ChatResponse:
        log_event(logger, "request_received", chars=len(message))
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        tools = self.registry.anthropic_tools()
        records: list[ToolCallRecord] = []
        pending: list[PendingAction] = []
        evidence: list[str] = []
        answer = ""

        for step in range(1, self.max_steps + 1):
            try:
                turn = self.llm.create(self.system, messages, tools)
            except Exception as exc:  # noqa: BLE001
                log_event(logger, "llm_error", error=f"{type(exc).__name__}: {exc}")
                answer = (f"The language model could not be reached ({type(exc).__name__}). No operational "
                          "result is reported. Tool evidence gathered so far is listed in tool_calls.")
                break

            if turn.stop_reason == "refusal":
                answer = "The model declined this request. No action was taken."
                break
            if turn.stop_reason != "tool_use" or not turn.tool_calls:
                answer = turn.text or "(no answer produced)"
                break

            messages.append({"role": "assistant", "content": turn.content})
            tool_results = []
            for call in turn.tool_calls:
                result = self.registry.execute(call.name, call.input)
                records.append(ToolCallRecord(tool=call.name, input=call.input, status=result.status,
                                              duration_ms=result.duration_ms, error=result.error))
                if result.status == ToolStatus.CONFIRMATION_REQUIRED and result.confirmation_token:
                    pending.append(PendingAction(token=result.confirmation_token, tool=call.name, input=call.input,
                                                 description=_describe_pending(result)))
                payload = result.model_dump_json()
                if result.ok:
                    evidence.append(payload)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": payload[:20000],
                    "is_error": not result.ok,
                })
            # all results of one step go back in a single user message
            messages.append({"role": "user", "content": tool_results})
            log_event(logger, "agent_step", step=step, tools=[c.name for c in turn.tool_calls])
        else:
            answer = (f"Stopped after {self.max_steps} tool-calling steps without a final answer. "
                      "Partial evidence is in tool_calls.")

        unverified = find_unverified_values(answer, "\n".join(evidence))
        if not evidence and records:
            answer += ("\n\n> Note: no tool returned data for this request, so no operational status "
                       "can be confirmed.")
        if unverified:
            answer += ("\n\n> Grounding check: these values in the answer do not appear in any tool result and "
                       f"should be treated as unverified: {', '.join(unverified)}")
            log_event(logger, "grounding_warning", values=unverified)
        return ChatResponse(request_id=request_id, answer=answer, tool_calls=records, pending_actions=pending,
                            unverified_values=unverified)

    # human-in-the-loop -----------------------------------------------------------
    def confirm(self, token: str, approve: bool = True) -> ToolResult:
        if not approve:
            removed = self.registry.reject(token)
            return ToolResult(tool="?", status=ToolStatus.REJECTED if removed else ToolStatus.INVALID_INPUT,
                              error="action rejected by user" if removed else "unknown or expired token")
        return self.registry.confirm(token)
