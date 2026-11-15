# Technical Report – MLOps AI Agent (Assignment 03)

All results below come from the **real running system** (Docker: MLflow 3.18 + Airflow 3.1 + Postgres; kind
Kubernetes v1.37; DVC 3.67). Full transcripts, including every tool call and its raw JSON result, are in
[`evidence/`](../evidence/).

> The conversations were recorded with the agent's `offline` planner (no LLM API key was available on the
> recording machine). The tool layer, safety policy and evidence are identical in Claude mode
> (`AGENT_LLM_BACKEND=claude`); only the planning/wording component differs.

## 1. System architecture
See [architecture.md](../architecture.md): component diagram, agent loop, lineage chain, DAG and Kubernetes design.

A FastAPI/CLI front-end feeds an LLM tool-calling loop. There are 32 typed tools across DVC, MLflow, Airflow and
Kubernetes. They run through a registry that:
- validates inputs and enforces allowlists;
- gates state changes behind human confirmation tokens;
- applies timeouts and classifies failures;
- writes structured JSON logs with request ids and redacted secrets.

Answers are grounded: the agent cross-checks values against tool results.

## 2. Tool definitions and permissions
See [architecture.md §3](../architecture.md#3-tools-and-permissions). `GET /tools` returns the live catalogue.

## 3. Evidence from each technology
Raw outputs are in [evidence/raw_system_evidence.md](../evidence/raw_system_evidence.md) (DVC tags/status/.dvc files,
`kubectl get/describe`, docker services).

**DVC:**

| Tag | Commit | Rows | md5 | Size |
|---|---|---|---|---|
| `data-v1` | `7cd893a` | 400 | `1bd0667c…` | 85,940 B |
| `data-v2` | `15f5465` | 569 | `bfa7a203…` | 121,955 B |

`dvc status` reports that the workspace matches.

**MLflow:** experiment `breast-cancer-classifier`, 11 runs:

| Run | Runner | Data | F1 | Gate |
|---|---|---|---|---|
| train-1..4 | CLI | v2, C ∈ {1, 0.1, 0.01, 10} | 0.9861 / 0.9793 / 0.9595 / 0.9722 | passed |
| train-5 | CLI | v1 | 0.9901 | passed (*wrong commit, see failure 3*) |
| train-6..8 | Airflow | v2 | – | **FAILED** (failure 1) |
| train-9 | Airflow | v2 | 0.9861 | passed → registered **v2 @champion** |
| train-10 | CLI | v1 | 0.9901 | passed (correct provenance, commit `7cd893a`) |
| train-11 | Airflow | v2 | 0.9861 | **failed** (injected gate, failure 2) |

**Airflow:** DAG `ml_training_pipeline`, 3 real runs:

| Run | State | Why |
|---|---|---|
| `…14:03…_5xWeAaVf` | failed | missing dependency |
| `…14:33…_R3TnlSJs` | success | all 6 tasks; model promoted |
| `…17:35…_RikZayL0` | failed | injected quality gate; `promote_model` = upstream_failed |

**Kubernetes:**
- Namespace `ml-serving`: Deployment `prediction-api` with 2/2 replicas, ClusterIP Service (port 80 → 8000), ConfigMap
  `prediction-api-config`, readiness and liveness probes, requests 100m/256Mi and limits 500m/512Mi.
- `GET /version` returns champion **v2**, trained on data **v2**.
- `/predict` returns predictions.

## 4. Five tested agent conversations

| # | Scenario | Evidence | Tools used | Result |
|---|---|---|---|---|
| 1 | Dataset provenance | [01_dataset_provenance.md](../evidence/01_dataset_provenance.md) | mlflow_get_run_provenance → dvc_get_dataset_version | train-10 used **v1**, commit `7cd893a` (`data-v1`), md5 `1bd0667c…`; hash cross-check passed; exact reproduce command given |
| 2 | Model selection | [02_model_selection.md](../evidence/02_model_selection.md) | mlflow_compare_runs, mlflow_get_model_registry | Ranked 5 runs. Flagged that v1 and v2 runs use different test sets (80 vs 114 rows), so F1 is not comparable. Recommended **train-9** (best gate-passed run on latest data) over train-10 despite train-10's higher raw F1, and warned that a metric alone does not make a model production-ready |
| 3 | Pipeline execution | [03_pipeline_execution_trigger.md](../evidence/03_pipeline_execution_trigger.md), [03b_pipeline_execution_status.md](../evidence/03b_pipeline_execution_status.md) | airflow_trigger_dag (pending, then human approval), airflow_get_dag_run_status | Trigger only created a pending action; after approval run `R3TnlSJs` executed; status shows **success** for all 6 tasks |
| 4 | Deployment diagnosis | [04_deployment_diagnosis_failure.md](../evidence/04_deployment_diagnosis_failure.md), [04b_deployment_after_fix.md](../evidence/04b_deployment_after_fix.md) | k8s_diagnose_deployment | Found the not-Ready pod, `Readiness probe failed: statuscode 503` events and the log `model file not found … (check MODEL_PATH in ConfigMap)`; gave the root cause and fix. After the fix: 2/2 ready, Service has endpoints |
| 5 | Multi-tool (mandatory) | [05_multi_tool_integration.md](../evidence/05_multi_tool_integration.md) | MLflow → MLflow provenance → DVC → Airflow → Kubernetes (6 calls) | Facts / uncertainties / actions separated; provenance verified; deployment healthy; retraining **recommended but not started**. The evidence is that the latest pipeline run failed. Note: that run failed because of a deliberate test, which a human should take into account |

## 5. Failure investigations

### Failure 1: Missing `skops` dependency (unexpected, real)
[evidence/failure_1_missing_dependency.md](../evidence/failure_1_missing_dependency.md)

- **Symptom:** the first real DAG run failed. `evaluate_and_log_mlflow` failed 3 times (retries), and the downstream
  tasks were upstream_failed. MLflow runs train-6..8 have status FAILED.
- **Agent diagnosis:** `airflow_get_failed_tasks` gave root cause `evaluate_and_log_mlflow`. `airflow_get_task_logs`
  then returned `ModuleNotFoundError: No module named 'skops'` at `mlflow/sklearn/__init__.py:701 in _save_model`.
- **Root cause:** MLflow 3.18 saves scikit-learn models with `skops` by default. The Airflow container used the slim
  `mlflow-skinny` package, which doesn't include `skops`. The host had full MLflow, so local runs worked.
- **Resolution:** added `skops==0.16.0` to the Airflow image requirements (`docker-compose.yaml`). The next run
  succeeded and was promoted.
- **Agent improvement found:** Airflow 3 returns *structured* logs, and the exception sits in `error_detail`. The
  log parser initially missed it, so error lines came back empty. It was fixed in `airflow_tools._flatten_log`.

The same run also showed `train_model` try 1 failing **before it started**, with no log written
(`run_start_date=None`). This was an executor-level failure under memory pressure: 7.6 GB shared with another
project's containers. The retry recovered it automatically. The log tool now explains such missing logs instead of
returning a bare 404.

### Failure 2: Quality gate blocks promotion (injected)
[evidence/failure_2_quality_gate.md](../evidence/failure_2_quality_gate.md)

- **Injection:** triggered the DAG with conf `{"inject_failure": "quality_gate"}`, which raises the F1 threshold to
  0.999.
- **Agent diagnosis:** the root-cause task is `quality_gate`, and `promote_model` is upstream_failed. The log shows
  `QualityGateError: quality gate failed for run 8b0d99f3…: f1 0.9861 < 0.999`.
- **Root cause and resolution:** the model's F1 is below the threshold. This is the intended safety behavior: the
  model was **not registered**, and `@champion` stayed at v2. The fix is to restore the 0.90 threshold.

### Failure 3: Wrong commit in recorded provenance (unexpected, real)
[evidence/failure_3_provenance_mismatch.md](../evidence/failure_3_provenance_mismatch.md)

- **Symptom:** asked which data trained train-5, the agent reported `PROVENANCE MISMATCH: the run recorded data
  hash 1bd0667c…, but commit d329b2d contains bfa7a203…`.
- **Root cause:** v1 had been restored with `git checkout data-v1 -- dataset.csv.dvc`, which doesn't move HEAD.
  The provenance code recorded HEAD, which points to v2 data, so the recorded commit would not reproduce the run.
- **Resolution:** `training/provenance.py` now records the commit whose `.dvc` file contains the exact hash.
  Train-10 records `7cd893a` (`data-v1`) with
  `data.provenance_check = verified: dataset restored from commit 7cd893a`.

### Failure 4: Prediction API not ready (injected)
[evidence/04_deployment_diagnosis_failure.md](../evidence/04_deployment_diagnosis_failure.md)

- **Injection:** `scripts/inject_k8s_failure.sh bad-config` sets `MODEL_PATH` to a missing file.
- **Diagnosis:** the new pod is not Ready, with readiness probe 503 events. The pod log says
  `model file not found at /app/model/missing-model.joblib`. Because `maxUnavailable: 0`, the old pods kept
  serving, so there was no outage.
- **Resolution:** restore the ConfigMap and restart, then verify 2/2 ready
  ([04b](../evidence/04b_deployment_after_fix.md)).

## 6. Testing, reliability and safety
37 pytest tests with all services mocked. CI runs lint, the tests and a training smoke test on every push and pull
request.

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
- **Pending confirmations are in memory.** Move them to Redis for restarts and multiple replicas.
- **No per-user authentication.** There is only an optional shared API key. Add OIDC.
- **The retraining rule doesn't separate deliberate test failures from real ones.** Tag injected runs and exclude
  them.
- **Run numbering is derived from the run count.** Failed attempts consume numbers. Use DAG run ids.
- **The grounding check is heuristic.** Add per-claim citations of tool results.
- **No drift monitoring.** Add Evidently or Prometheus metrics as a tool.
- **The DVC remote is a local folder.** Use cloud storage with scoped credentials.
- **The Airflow container installs packages at start-up.** Bake a custom image.
- **Recording machine notes:** host ports moved to 5001/8081 because of another local project, and the conversations
  were recorded in offline LLM mode.
