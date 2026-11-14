"""Prediction API - the containerized FastAPI inference service deployed to Kubernetes.

Configuration comes from the ConfigMap (env vars): APP_VERSION, MODEL_NAME, MODEL_PATH,
PREDICTION_THRESHOLD, LOG_LEVEL.

  GET  /health/live   liveness  - process is up
  GET  /health/ready  readiness - model loaded (503 otherwise, so the Pod gets no traffic)
  GET  /version       app + model version (read by the agent)
  POST /predict       {"instances": [{feature: value, ...}, ...]}
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
log = logging.getLogger("prediction-api")

APP_VERSION = os.getenv("APP_VERSION", "dev")
MODEL_NAME = os.getenv("MODEL_NAME", "breast-cancer-classifier")
MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/model/model.joblib"))
THRESHOLD = float(os.getenv("PREDICTION_THRESHOLD", "0.5"))

app = FastAPI(title="prediction-api", version=APP_VERSION)
state: dict[str, Any] = {"model": None, "meta": {}, "error": None, "predictions": 0, "started": time.time()}


def load_model() -> None:
    try:
        import joblib

        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"model file not found at {MODEL_PATH} (check MODEL_PATH in ConfigMap)")
        state["model"] = joblib.load(MODEL_PATH)
        meta_path = MODEL_PATH.with_name("model_meta.json")
        state["meta"] = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        state["error"] = None
        log.info("model loaded from %s meta=%s", MODEL_PATH, state["meta"])
    except Exception as exc:  # noqa: BLE001
        state["model"], state["error"] = None, f"{type(exc).__name__}: {exc}"
        log.error("ERROR loading model: %s", state["error"])


@app.on_event("startup")
def _startup() -> None:
    log.info("starting prediction-api version=%s", APP_VERSION)
    load_model()


class PredictRequest(BaseModel):
    instances: list[dict[str, float]] = Field(min_length=1, max_length=1000)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    if state["model"] is None:
        log.error("readiness check failed: %s", state["error"])
        raise HTTPException(status_code=503, detail=f"model not loaded: {state['error']}")
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, Any]:
    return {"app_version": APP_VERSION, "model_name": MODEL_NAME, "model": state["meta"],
            "uptime_s": round(time.time() - state["started"])}


@app.post("/predict")
def predict(req: PredictRequest) -> dict[str, Any]:
    model = state["model"]
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    import pandas as pd

    df = pd.DataFrame(req.instances)
    expected = list(getattr(model, "feature_names_in_", df.columns))
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"missing features: {missing[:5]}")
    proba = model.predict_proba(df[expected])[:, 1]
    state["predictions"] += len(df)
    return {"predictions": [int(p >= THRESHOLD) for p in proba], "probabilities": [round(float(p), 4) for p in proba],
            "model_version": state["meta"].get("version"), "app_version": APP_VERSION}
