"""Run one agent conversation and save it as evidence (question, every tool call with its full result, answer).

    python scripts/record_conversation.py evidence/01_dataset_provenance.md "Which dataset version ..."
    python scripts/record_conversation.py --offline evidence/x.md "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import MLOpsAgent, make_llm  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("question")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--approve", action="store_true",
                        help="after the answer, approve pending actions (the human step of /actions/confirm)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    llm = make_llm("offline" if args.offline else None)
    agent = MLOpsAgent(llm=llm)
    captured: list[dict] = []
    original = agent.registry.execute

    def recording_execute(name, tool_args=None, **kw):
        result = original(name, tool_args, **kw)
        captured.append({"tool": name, "input": tool_args, "result": result.model_dump(mode="json")})
        return result

    agent.registry.execute = recording_execute  # type: ignore[method-assign]
    resp = agent.run(args.question)

    lines = [
        f"# {Path(args.out).stem}",
        "",
        f"- Recorded: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- LLM backend: `{type(llm).__name__}`",
        f"- Request id: `{resp.request_id}`",
        "",
        "## Question",
        "",
        f"> {args.question}",
        "",
        "## Tool calls",
        "",
        "| # | Tool | Status | Duration (ms) |",
        "|---|---|---|---|",
    ]
    lines += [f"| {i} | `{c.tool}` | {c.status.value} | {c.duration_ms} |" for i, c in enumerate(resp.tool_calls, 1)]
    lines += ["", "## Agent answer", "", resp.answer, ""]
    if resp.pending_actions:
        lines += ["## Pending actions (require human confirmation)", ""]
        lines += [f"- `{p.tool}`: {p.description}" for p in resp.pending_actions]
        lines += [""]
        if args.approve:
            lines += ["## Human approval (equivalent to POST /actions/confirm)", ""]
            for p in resp.pending_actions:
                result = agent.confirm(p.token, approve=True)
                lines += [f"Approved `{p.tool}` -> **{result.status.value}**", "", "```json",
                          json.dumps(result.model_dump(mode="json"), indent=2, default=str), "```", ""]
                print(f"approved {p.tool}: {result.status.value} {result.data}")
    lines += ["## Raw tool evidence", ""]
    for i, c in enumerate(captured, 1):
        lines += [f"<details><summary>{i}. {c['tool']} ({c['result']['status']})</summary>", "", "```json",
                  json.dumps(c, indent=2, default=str)[:12000], "```", "", "</details>", ""]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(resp.answer)
    print(f"\n[saved {args.out}: {len(resp.tool_calls)} tool calls]")


if __name__ == "__main__":
    main()
