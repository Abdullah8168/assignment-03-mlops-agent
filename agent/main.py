"""Interactive command-line interface for the MLOps agent.

    python -m agent.main                       # interactive chat
    python -m agent.main "Which dataset ..."   # single question
    python -m agent.main --offline "..."       # no LLM API key needed
"""

from __future__ import annotations

import argparse
import json
import sys

from agent.orchestrator import MLOpsAgent, make_llm


def _print_response(resp) -> None:
    print(f"\n[request {resp.request_id}]")
    for call in resp.tool_calls:
        print(f"  tool {call.tool:<34} {call.status.value:<22} {call.duration_ms:>8.1f} ms"
              + (f"  ({call.error})" if call.error else ""))
    print("\n" + resp.answer + "\n")
    for p in resp.pending_actions:
        print(f"  PENDING ACTION: {p.description}\n  confirm with:  /confirm {p.token}   or   /reject {p.token}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MLOps AI agent")
    parser.add_argument("question", nargs="*", help="question to ask (omit for interactive mode)")
    parser.add_argument("--offline", action="store_true", help="use the rule-based planner instead of Claude")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    agent = MLOpsAgent(llm=make_llm("offline" if args.offline else None))
    if args.question:
        _print_response(agent.run(" ".join(args.question)))
        return 0

    print("MLOps agent ready. Type a question, '/confirm <token>', '/reject <token>' or 'exit'.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not line:
            continue
        if line in ("exit", "quit"):
            return 0
        if line.startswith(("/confirm ", "/reject ")):
            cmd, token = line.split(maxsplit=1)
            result = agent.confirm(token.strip(), approve=cmd == "/confirm")
            print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
            continue
        _print_response(agent.run(line))


if __name__ == "__main__":
    sys.exit(main())
