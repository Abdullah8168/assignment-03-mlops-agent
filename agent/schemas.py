"""Structured inputs/outputs shared by tools, the orchestrator and the API."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Git revisions / names accepted from the LLM. Blocks option injection ("-x") and shell metacharacters.
SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,99}$")
SAFE_NAME = re.compile(r"^[a-z0-9]([a-z0-9.\-]{0,251}[a-z0-9])?$")


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"                      # tool ran but the service returned an error
    UNAVAILABLE = "unavailable"          # service unreachable / not configured
    TIMEOUT = "timeout"
    INVALID_INPUT = "invalid_input"
    REJECTED = "rejected"                # blocked by a safety / permission policy
    CONFIRMATION_REQUIRED = "confirmation_required"


class ToolResult(BaseModel):
    tool: str
    status: ToolStatus
    data: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    confirmation_token: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.SUCCESS


class StrictInput(BaseModel):
    """Base class for tool inputs: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class NoInput(StrictInput):
    pass


def validate_git_ref(value: str) -> str:
    if not SAFE_REF.match(value) or ".." in value:
        raise ValueError(f"unsafe git revision: {value!r}")
    return value


class K8sNamespaced(StrictInput):
    namespace: str | None = Field(default=None, description="Kubernetes namespace (must be approved).")

    @field_validator("namespace")
    @classmethod
    def _ns(cls, v: str | None) -> str | None:
        if v is not None and not SAFE_NAME.match(v):
            raise ValueError(f"invalid namespace name: {v!r}")
        return v


# ---------------------------------------------------------------- API models
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)


class ToolCallRecord(BaseModel):
    tool: str
    input: dict[str, Any]
    status: ToolStatus
    duration_ms: float
    error: str | None = None


class PendingAction(BaseModel):
    token: str
    tool: str
    input: dict[str, Any]
    description: str


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    tool_calls: list[ToolCallRecord] = []
    pending_actions: list[PendingAction] = []
    unverified_values: list[str] = []


class ConfirmRequest(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    approve: bool = True
