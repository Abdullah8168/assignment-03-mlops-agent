"""Structured JSON logging with secret redaction and request-id propagation."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

SECRET_KEY_PATTERN = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|credential)", re.I)
SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),            # Anthropic keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),      # bearer tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWTs
]
REDACTED = "***REDACTED***"


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def redact(value: Any) -> Any:
    """Recursively redact secrets from dicts / lists / strings."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if SECRET_KEY_PATTERN.search(str(k)) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for pattern in SECRET_VALUE_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("mlops_agent")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = True  # lets pytest's caplog capture records
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mlops_agent.{name}")


def log_event(logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, message, extra={"fields": redact(fields), "request_id": request_id_var.get()})
