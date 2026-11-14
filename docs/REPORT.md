# Technical Report – MLOps AI Agent (Assignment 03)

> **How to complete this report:** the sections marked **📸 EVIDENCE** must be filled in with *real* output
> from your running system. The assignment requires actual tool evidence, so do not paste invented values. Save
> screenshots in `screenshots/` and paste agent transcripts (CLI output, or the `/chat` JSON with `tool_calls`).

## 1. System architecture
See [architecture.md](../architecture.md): component diagram, agent loop, lineage chain, DAG and Kubernetes design.

Summary: a FastAPI/CLI front-end feeds a Claude tool-calling loop. 32 typed tools across DVC, MLflow, Airflow and
Kubernetes run through a registry that validates inputs, enforces allowlists, gates state changes behind human
confirmation, applies timeouts, classifies failures and writes structured JSON logs. Answers are checked against
tool evidence before they are returned.

## 2. Tool definitions and permissions
See the table in [architecture.md §3](../architecture.md#3-tools-and-permissions). `GET /tools` returns the live
catalogue.

## 3. Evidence from each technology 📸 EVIDENCE

| Technology | What to capture | File |
|---|---|---|
| DVC | `git tag -l "data-v*"`, `dvc status`, `cat data/raw/dataset.csv.dvc`, `git show data-v1:data/raw/dataset.csv.dvc` | screenshots/dvc_*.png |
| MLflow | Experiment page with ≥5 runs and metrics; one run's **Tags** tab showing data.version / data.git_commit / quality_gate.status; Models page with `@champion` | screenshots/mlflow_*.png |
| Airflow | DAG graph view; a successful run; a failed run (quality gate) with `promote_model` upstream_failed; task log | screenshots/airflow_*.png |
| Kubernetes | `kubectl -n ml-serving get deploy,pods,svc,cm`; `kubectl describe pod` during an injected failure | screenshots/k8s_*.png |

Already verified while building the repository (real output):
```
dvc_list_dataset_versions → data-v1 (commit 7cd893a, md5 1bd0667c…, 85,940 B)
                            data-v2 (commit 15f5465, md5 bfa7a203…, 121,955 B)
dvc_compare_dataset_versions(data-v1, data-v2) → changed=True, size_delta=+36,015 bytes
dvc repro (data v2) → accuracy 0.9825, precision 0.9861, recall 0.9861, F1 0.9861, ROC-AUC 0.9954, gate passed
```

## 4. Five tested agent conversations 📸 EVIDENCE
For each conversation, paste the question, the `tool_calls` (tool, status, duration) and the answer.

### 4.1 Dataset provenance
**Q:** "Which dataset version was used for experiment 3, and how do I reproduce it?"
Expected tools: `mlflow_get_run_provenance` → `dvc_get_dataset_version` → `dvc_get_reproduction_steps`

### 4.2 Model selection
**Q:** "Compare the last five training runs and recommend the best candidate based on F1 score."
Expected tools: `mlflow_compare_runs` / `mlflow_find_best_run`, `mlflow_get_model_registry`.
The answer should rank the runs and mention caveats: gate status, small metric gaps, and that the top metric is
not proof of production readiness.

### 4.3 Pipeline execution
**Q:** "Trigger the model training pipeline and report whether execution succeeded."
The agent returns a pending action. Run `/confirm <token>` and the run is created. Then ask "What is the status of
the latest pipeline run?", which calls `airflow_get_dag_run_status` and shows per-task states.

### 4.4 Deployment diagnosis
**Q:** "Why is my prediction API unavailable?" (after `scripts/inject_k8s_failure.sh bad-config`)
Expected tools: `k8s_diagnose_deployment`, possibly `k8s_get_pod_logs` / `k8s_get_events`.

### 4.5 Multi-tool reasoning (mandatory scenario)
**Q:** "Find the best recent model, identify the dataset version used to train it, check the deployment status,
and explain whether retraining is necessary."
Expected tools: MLflow → DVC → Kubernetes → Airflow. The answer has *Known facts / Uncertainties / Recommended
actions*, and no `airflow_trigger_dag` call is made.

## 5. Failure investigations 📸 EVIDENCE

### Failure 1: Quality gate blocks promotion (Airflow / model layer)
- **Injection:** trigger `ml_training_pipeline` with conf `{"inject_failure": "quality_gate"}`. This raises the F1
  threshold to 0.999.
- **Symptom:** the DAG run is `failed`. `quality_gate` is failed and `promote_model` is upstream_failed.
- **Agent diagnosis:** "Why did the last training pipeline fail?" runs:
  - `airflow_get_failed_tasks`, which gives root_cause_task = `quality_gate`;
  - `airflow_get_task_logs`, which returns the error line `quality gate failed for run …: f1 0.98xx < 0.999`;
  - `mlflow_get_run_details`, which shows the run tag `quality_gate.status=failed`.
- **Root cause:** the model's F1 is below the configured threshold.
- **Resolution:** this is the intended safety behavior, and the model was not registered. Either improve the model
  or use the correct threshold (0.90) and re-trigger.

### Failure 2: Prediction API not ready (Kubernetes / deployment layer)
- **Injection:** `bash scripts/inject_k8s_failure.sh bad-config`. The ConfigMap `MODEL_PATH` now points to a
  missing file.
- **Symptom:** the new Pods run but never become Ready, and the rollout stalls. Because `maxUnavailable: 0`, the old
  Pods keep serving; if every Pod is new, the Service has 0 endpoints.
- **Agent diagnosis:** `k8s_diagnose_deployment` finds:
  - Pods `not Ready (readiness probe failing?)`;
  - `Readiness probe failed: HTTP probe failed with statuscode: 503` warning events;
  - pod logs `ERROR loading model: FileNotFoundError: model file not found at /app/model/missing-model.joblib`.
- **Root cause:** a misconfigured `MODEL_PATH` in the ConfigMap.
- **Resolution:** restore the ConfigMap (`kubectl apply -f kubernetes/configmap.yaml`), then restart the
  Deployment through the agent: a pending action, then `/confirm`.

(Optional third failure: `inject_failure=validation`. Here `validate_dataset` fails with "missing target column
'target'; column null fraction 20%". The pipeline stops before training.)

### Unexpected failure found during development
`dvc_get_dataset_version` initially returned an **integer** md5 for an all-digit hash, because YAML parses it as a
number. The test `test_current_dataset_version_and_commit` caught it, and the parser now always casts hashes to
strings (`agent/tools/dvc_tools.py::_parse_dvc_file`).

## 6. Testing, reliability and safety
37 pytest tests with all services mocked (`tests/`):

| Requirement | Tests |
|---|---|
| ≥ 3 tools per module | `test_registry_has_at_least_three_tools_per_module` |
| Tool selection and execution | `test_selected_tool_is_executed_and_result_returned_to_llm`, `test_mandatory_integration_scenario_uses_all_four_technologies` |
| Timeouts | `test_tool_timeout_is_reported_not_raised` |
| Invalid requests | `test_invalid_input_is_rejected_without_calling_the_service`, `test_invalid_requests_are_rejected`, `test_unsafe_revisions_and_commands_are_blocked` |
| Service failures | `test_service_failures_are_classified`, `test_mlflow_server_down_is_unavailable`, `test_airflow_unreachable`, `test_llm_outage_is_reported_honestly` |
| No invented results | `test_agent_does_not_invent_results_when_tool_unavailable`, `test_hallucinated_values_are_flagged_by_grounding_check` |
| Unauthorized k8s changes rejected | `test_unauthorized_kubernetes_modifications_are_rejected`, `test_even_confirmed_calls_cannot_escape_the_namespace` |
| Structured logs without secrets | `test_structured_logs_have_tool_status_duration_request_id_and_no_secrets` |

## 7. Known limitations and proposed improvements
- **Pending confirmations are in memory.** They are lost on restart and not shared across API replicas. Move them
  to Redis with TTLs.
- **No user authentication or RBAC per user.** There is an optional shared `X-API-Key`. Add OIDC and map users to
  allowed actions.
- **The grounding check is heuristic** (hashes and decimals). Add citation of tool-result ids per claim.
- **Run numbering** (`train-N`) is derived from the run count and could race under concurrent training. Use an
  MLflow-side sequence or the DAG run id.
- **No drift or degradation monitoring.** Retraining decisions rely on the freshness policy, the quality gate and
  pipeline state. Add Evidently/Prometheus metrics as another tool.
- **The DVC remote is a local folder.** Use S3/GCS/Azure with scoped credentials in a team setting.
- **The Airflow container installs packages at start-up** (`_PIP_ADDITIONAL_REQUIREMENTS`). Bake a custom image for
  production.
