"""Central configuration, read from environment variables (never hard-coded secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # LLM
    llm_backend: str = field(default_factory=lambda: os.getenv("AGENT_LLM_BACKEND", "claude"))
    llm_model: str = field(default_factory=lambda: os.getenv("AGENT_LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: os.getenv("AGENT_LLM_EFFORT", "medium"))
    max_agent_steps: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_STEPS", "12")))

    # Tool execution
    tool_timeout_s: float = field(default_factory=lambda: float(os.getenv("TOOL_TIMEOUT_SECONDS", "20")))

    # DVC / Git
    repo_path: Path = field(default_factory=lambda: Path(os.getenv("REPO_PATH", str(PROJECT_ROOT))))

    # MLflow
    mlflow_tracking_uri: str = field(
        default_factory=lambda: os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    )
    mlflow_experiment: str = field(
        default_factory=lambda: os.getenv("MLFLOW_EXPERIMENT_NAME", "breast-cancer-classifier")
    )
    registered_model_name: str = field(
        default_factory=lambda: os.getenv("REGISTERED_MODEL_NAME", "breast-cancer-classifier")
    )

    # Airflow (Airflow 3 REST API v2, JWT auth)
    airflow_url: str = field(default_factory=lambda: os.getenv("AIRFLOW_URL", "http://localhost:8080"))
    airflow_username: str = field(default_factory=lambda: os.getenv("AIRFLOW_USERNAME", "admin"))
    airflow_password: str = field(default_factory=lambda: os.getenv("AIRFLOW_PASSWORD", ""), repr=False)
    approved_dags: list[str] = field(default_factory=lambda: _csv("AIRFLOW_APPROVED_DAGS", "ml_training_pipeline"))

    # Kubernetes
    k8s_approved_namespaces: list[str] = field(
        default_factory=lambda: _csv("K8S_APPROVED_NAMESPACES", "ml-serving")
    )
    k8s_default_namespace: str = field(default_factory=lambda: os.getenv("K8S_NAMESPACE", "ml-serving"))
    k8s_app_label: str = field(default_factory=lambda: os.getenv("K8S_APP_LABEL", "app=prediction-api"))
    k8s_mutable_deployments: list[str] = field(
        default_factory=lambda: _csv("K8S_MUTABLE_DEPLOYMENTS", "prediction-api")
    )

    # Policy
    model_freshness_days: int = field(default_factory=lambda: int(os.getenv("MODEL_FRESHNESS_DAYS", "30")))
    f1_quality_threshold: float = field(default_factory=lambda: float(os.getenv("F1_QUALITY_THRESHOLD", "0.90")))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
