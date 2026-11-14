"""Tool framework: registration, input validation, timeouts, error classification and tracing.

Every tool the LLM can call goes through `ToolRegistry.execute`, which guarantees:
  * inputs are validated against a strict pydantic schema (unknown fields rejected),
  * execution is bounded by a timeout,
  * failures are classified (unavailable / timeout / error / rejected) instead of crashing the agent,
  * state-changing tools never run directly - they return a confirmation token that only a
    human can redeem through the API (`/actions/confirm`),
  * each call emits one structured log line (tool, status, duration, request id; secrets redacted).
"""

from __future__ import annotations

import concurrent.futures
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.config import get_settings
from agent.logging_utils import get_logger, log_event
from agent.schemas import ToolResult, ToolStatus

logger = get_logger("tools")


# ------------------------------------------------------------------ errors
class ToolError(Exception):
    """Service answered but the operation failed (e.g. 404, bad state)."""


class ServiceUnavailableError(ToolError):
    """Backend service unreachable, not installed or not configured."""


class PermissionDeniedError(ToolError):
    """Operation blocked by the agent's safety policy."""


# ------------------------------------------------------------------ specs
@dataclass
class ToolSpec:
    name: str
    description: str
    module: str  # dvc | mlflow | airflow | kubernetes
    input_model: type[BaseModel]
    func: Callable[..., Any]
    mutating: bool = False  # changes external state -> requires human confirmation

    def anthropic_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        description = self.description
        if self.mutating:
            description += (
                " STATE-CHANGING: calling this only requests the action; a human must confirm it "
                "before anything is executed."
            )
        return {"name": self.name, "description": description, "input_schema": schema}


@dataclass
class PendingCall:
    token: str
    tool: str
    args: dict[str, Any]
    created: float = field(default_factory=time.time)


class ToolRegistry:
    CONFIRMATION_TTL_S = 15 * 60

    def __init__(self, timeout_s: float | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._pending: dict[str, PendingCall] = {}
        self.timeout_s = timeout_s if timeout_s is not None else get_settings().tool_timeout_s

    # registration -----------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool name {spec.name}")
        self._tools[spec.name] = spec

    def register_many(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [self._tools[name].anthropic_schema() for name in self.names]

    # execution ----------------------------------------------------------------
    def execute(self, name: str, args: dict[str, Any] | None, *, confirmed: bool = False) -> ToolResult:
        start = time.perf_counter()
        args = dict(args or {})
        spec = self._tools.get(name)

        def finish(status: ToolStatus, data: Any = None, error: str | None = None, token: str | None = None):
            duration = round((time.perf_counter() - start) * 1000, 2)
            log_event(
                logger,
                "tool_call",
                logging.INFO if status == ToolStatus.SUCCESS else logging.WARNING,
                tool=name,
                module=spec.module if spec else None,
                status=status.value,
                duration_ms=duration,
                args=args,
                error=error,
            )
            return ToolResult(
                tool=name, status=status, data=data, error=error, duration_ms=duration, confirmation_token=token
            )

        if spec is None:
            return finish(ToolStatus.INVALID_INPUT, error=f"unknown tool '{name}'")

        try:
            params = spec.input_model.model_validate(args)
        except ValidationError as exc:
            errors = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            return finish(ToolStatus.INVALID_INPUT, error=errors)

        if spec.mutating and not confirmed:
            # Validate the permission policy *before* issuing a token so the human is never asked
            # to approve something that would be rejected anyway.
            precheck = getattr(spec.func, "precheck", None)
            if precheck is not None:
                try:
                    precheck(params)
                except PermissionDeniedError as exc:
                    return finish(ToolStatus.REJECTED, error=str(exc))
            token = secrets.token_urlsafe(12)
            self._purge_expired()
            self._pending[token] = PendingCall(token=token, tool=name, args=params.model_dump())
            return finish(
                ToolStatus.CONFIRMATION_REQUIRED,
                data={
                    "message": "Action NOT executed. Explicit human confirmation is required.",
                    "action": name,
                    "input": params.model_dump(),
                },
                token=token,
            )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(spec.func, params)
        try:
            data = future.result(timeout=self.timeout_s)
            return finish(ToolStatus.SUCCESS, data=data)
        except concurrent.futures.TimeoutError:
            return finish(ToolStatus.TIMEOUT, error=f"tool exceeded {self.timeout_s:.0f}s timeout")
        except PermissionDeniedError as exc:
            return finish(ToolStatus.REJECTED, error=str(exc))
        except ServiceUnavailableError as exc:
            return finish(ToolStatus.UNAVAILABLE, error=str(exc))
        except ToolError as exc:
            return finish(ToolStatus.ERROR, error=str(exc))
        except (ConnectionError, OSError) as exc:
            return finish(ToolStatus.UNAVAILABLE, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a tool crash the agent
            return finish(ToolStatus.ERROR, error=f"{type(exc).__name__}: {exc}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    # human confirmation -------------------------------------------------------
    def pending(self, token: str) -> PendingCall | None:
        self._purge_expired()
        return self._pending.get(token)

    def confirm(self, token: str) -> ToolResult:
        call = self._pending.pop(token, None)
        if call is None:
            return ToolResult(tool="?", status=ToolStatus.INVALID_INPUT, error="unknown or expired token")
        return self.execute(call.tool, call.args, confirmed=True)

    def reject(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def _purge_expired(self) -> None:
        now = time.time()
        for token in [t for t, c in self._pending.items() if now - c.created > self.CONFIRMATION_TTL_S]:
            self._pending.pop(token, None)
