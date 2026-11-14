"""FastAPI interface for the MLOps agent.

    uvicorn api.main:app --port 8001
    POST /chat              {"message": "..."}                -> answer + tool evidence + pending actions
    POST /actions/confirm   {"token": "...", "approve": true}  -> executes / rejects a pending state change
    GET  /tools             -> tool catalogue with permissions
    GET  /health
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from agent import __version__
from agent.logging_utils import configure_logging, get_logger, log_event, new_request_id
from agent.orchestrator import MLOpsAgent
from agent.schemas import ChatRequest, ChatResponse, ConfirmRequest, ToolResult

configure_logging()
logger = get_logger("api")
app = FastAPI(title="MLOps AI Agent", version=__version__)


@lru_cache(maxsize=1)
def get_agent() -> MLOpsAgent:
    return MLOpsAgent()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret protection. Enabled when AGENT_API_KEY is set."""
    expected = os.getenv("AGENT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log_event(logger, "unhandled_error", error=type(exc).__name__, path=request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/tools", dependencies=[Depends(require_api_key)])
def list_tools(agent: MLOpsAgent = Depends(get_agent)) -> list[dict]:
    return [
        {"name": spec.name, "module": spec.module, "mutating": spec.mutating,
         "requires_confirmation": spec.mutating, "description": spec.description}
        for spec in (agent.registry.get(n) for n in agent.registry.names)
    ]


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
def chat(req: ChatRequest, agent: MLOpsAgent = Depends(get_agent),
         x_request_id: str | None = Header(default=None)) -> ChatResponse:
    request_id = (x_request_id or new_request_id())[:32]
    return agent.run(req.message, request_id=request_id)


@app.post("/actions/confirm", response_model=ToolResult, dependencies=[Depends(require_api_key)])
def confirm(req: ConfirmRequest, agent: MLOpsAgent = Depends(get_agent)) -> ToolResult:
    return agent.confirm(req.token, approve=req.approve)
