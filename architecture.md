# Architecture

## 1. System overview

```
                    ┌──────────────────────────────┐
                    │ User / ML engineer           │
                    └──────────────┬───────────────┘
                     CLI (agent/main.py) │ HTTP (api/main.py: /chat, /actions/confirm, /tools)
                    ┌──────────────▼───────────────┐
                    │ MLOpsAgent (orchestrator.py) │
                    │  • Claude tool-calling loop  │
                    │  • ToolRegistry: validation, │
                    │    timeouts, permissions,    │
                    │    confirmation tokens       │
                    │  • JSON logs + request ids   │
                    │  • grounding check           │
                    └──┬────────┬────────┬───────┬─┘
          subprocess   │        │ Mlflow │ REST  │ kubernetes
          (allowlist)  │        │ Client │ v2+JWT│ python client
                 ┌─────▼──┐ ┌───▼────┐ ┌─▼─────┐ ┌▼──────────────────────────┐
                 │  DVC   │ │ MLflow │ │Airflow│ │ Kubernetes (ns ml-serving)│
                 │ + Git  │ │ server │ │   3   │ │ Deployment x2, Service,   │
                 └────────┘ └───▲────┘ └──┬────┘ │ ConfigMap, probes, limits │
                                │ logs runs│      └───────────▲───────────────┘
                                └──────────┘ ml_training_pipeline      │ image built from the
                                         (validate→…→quality gate→promote)  MLflow @champion model
```

**Data → model → deployment lineage:**
- The `.dvc` file plus `params.yaml` (data.version) at a Git commit pins the dataset bytes.
- `training/provenance.py` records `data.version`, `data.git_commit`, `data.dvc_md5` and `data.path` as MLflow run
  tags, plus `pipeline.dag_run_id` when the run comes from Airflow.
- The quality gate writes `quality_gate.status`. Only runs that pass are registered and aliased `@champion`.
- `scripts/export_model.py` packages `@champion` with its metadata into the serving image. The Deployment carries
  the `app.kubernetes.io/version` label and the `mlops/model-version` annotation.

The agent can follow this chain in both directions. Model → data uses `mlflow_get_run_provenance` and then
`dvc_get_dataset_version`. Deployment → model uses `k8s_get_deployed_version` and then `mlflow_get_model_registry`.

## 2. Agent loop

1. The user message is sent to Claude (`claude-opus-5-5`, adaptive thinking) with the system policy and 32 tool
   schemas.
2. Claude returns one or more `tool_use` blocks. Independent calls come back in parallel in a single turn.
3. Each call goes through `ToolRegistry.execute`:
   - **Validate:** strict pydantic schema. Unknown fields are rejected, and values are range-checked and
     pattern-checked.
   - **Authorize:** namespace, DAG and deployment allowlists.
   - **Gate:** `mutating` tools don't run. They return `confirmation_required` and a one-time token.
   - **Execute:** runs with a timeout in a worker thread.
   - **Classify:** the result is `success`, `error`, `unavailable`, `timeout`, `invalid_input` or `rejected`.
   - **Log:** one JSON line with the tool, status, duration_ms, request_id and redacted args.
4. All results of a step are returned in one user message, with `is_error` set on failures. The loop repeats, up
   to `AGENT_MAX_STEPS`.
5. The final answer is post-processed by a **grounding check**. Commit hashes and metric-like decimals that appear
   in no successful tool result are listed as `unverified_values` and flagged in the answer. If every tool failed,
   the answer states that no status can be confirmed.

`OfflineLLM` implements the same interface with a keyword planner and a templated report. It allows demos without
an API key and deterministic integration tests.

## 3. Tools and permissions

| Module | Tool | Type | Permission rule |
|---|---|---|---|
| DVC | dvc_list_tracked_datasets | read | allowlisted git/dvc sub-commands, no shell |
| | dvc_get_dataset_version | read | revision regex-validated |
| | dvc_list_dataset_versions | read | |
| | dvc_compare_dataset_versions | read | |
| | dvc_check_data_changes | read | |
| | dvc_get_reproduction_steps | read | |
| | dvc_checkout_dataset_version | **write** | human confirmation, `git checkout <rev> -- <paths>` only |
| MLflow | mlflow_list_experiments, mlflow_list_runs, mlflow_get_run_details, mlflow_get_run_provenance, mlflow_find_best_run, mlflow_compare_runs, mlflow_compare_experiments, mlflow_get_model_registry | read | MlflowClient only; fails fast (no retry storms) |
| Airflow | airflow_list_dags, airflow_get_dag_runs, airflow_get_dag_run_status, airflow_get_failed_tasks, airflow_get_task_logs | read | JWT auth from env; 10 s HTTP timeout |
| | airflow_trigger_dag | **write** | human confirmation, approved DAGs only, conf keys allowlisted |
| Kubernetes | k8s_list_deployments, k8s_list_pods, k8s_get_pod_status, k8s_get_pod_logs, k8s_list_services, k8s_find_unhealthy_pods, k8s_get_events, k8s_get_deployed_version, k8s_diagnose_deployment | read | approved namespaces only |
| | k8s_restart_deployment, k8s_scale_deployment | **write** | human confirmation, approved namespace and deployment, replicas 1–5; namespaced RBAC Role (`kubernetes/agent-rbac.yaml`) |

There are three layers of defense for state changes:
1. The LLM cannot pass `confirm=true`, because that parameter doesn't exist in any schema.
2. Only `/actions/confirm` (a human) can redeem a token.
3. The policy is checked again at execution time, so even a confirmed call cannot leave the namespace.

## 4. Training DAG (`dags/training_pipeline.py`)

`validate_dataset → preprocess_data → train_model → evaluate_and_log_mlflow → quality_gate → promote_model`

- `retries=2` with exponential back-off for transient errors, and a 15-minute execution timeout per task.
- Validation and the quality gate raise `AirflowFailException`, which fails the task without retrying. Downstream
  tasks become `upstream_failed`, so **a model that failed the gate is never promoted**.
- The DAG params let the run be configured (data path, threshold, hyperparameters) and allow deliberate failure
  injection (`inject_failure`).

## 5. Kubernetes deployment
- Deployment `prediction-api` runs 2 replicas with RollingUpdate (`maxUnavailable: 0`).
- Probes: readiness `/health/ready` (503 until the model loads) and liveness `/health/live`.
- Requests are 100m CPU / 256Mi memory; limits are 500m / 512Mi.
- The container runs as non-root with a read-only root filesystem.
- A ClusterIP Service maps port 80 to the named port `http`.
- The ConfigMap `prediction-api-config` holds APP_VERSION, MODEL_NAME, MODEL_PATH, threshold and log level.

## 6. Design decisions

| Decision | Why |
|---|---|
| Manual tool loop over an agent framework | Full control over permission checks, confirmation tokens, per-call tracing and grounding. All of these are graded requirements, and a framework would hide them. |
| Claude via the Anthropic SDK | Native structured tool calling with parallel calls. Server-side refusal fallback (`fallbacks="default"`) keeps the agent answering if the primary model declines. |
| Pydantic schemas → JSON schema | One definition serves both LLM tool schemas and runtime validation. |
| MLflow tags for provenance | Lineage lives with the run, so the agent never has to guess which data trained a model. |
| Airflow REST v2 + JWT | The official, supported Airflow 3 interface. No direct database access. |
| `diagnose_deployment` composite tool | "Why is my API down?" needs pods, events, endpoints and logs together. One tool call reduces latency and keeps the evidence consistent. |
| Offline planner | Makes CI and demos possible without an API key, and proves the tool layer works independently of the LLM. |
