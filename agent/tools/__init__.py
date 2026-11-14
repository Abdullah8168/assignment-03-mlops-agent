"""Tool modules. `build_registry()` assembles every tool the agent may call."""

from agent.tools import airflow_tools, dvc_tools, kubernetes_tools, mlflow_tools
from agent.tools.base import ToolRegistry


def build_registry(timeout_s: float | None = None) -> ToolRegistry:
    registry = ToolRegistry(timeout_s=timeout_s)
    for module in (dvc_tools, mlflow_tools, airflow_tools, kubernetes_tools):
        registry.register_many(module.TOOLS)
    return registry
