# MLOps AI Agent

A conversational agent that operates a real ML system. It uses **DVC** for dataset provenance, **MLflow** for
experiments and the model registry, **Apache Airflow 3** for the training pipeline, and **Kubernetes** for the
prediction service. The agent picks tools with Claude tool calling, runs them under a safety policy, and answers
**only from tool evidence**.

```
User ─► FastAPI /chat ─► MLOpsAgent (Claude + tool loop + permissions + tracing)
                              ├─ DVC tools        (7)  dataset versions, diff, status, reproduction
                              ├─ MLflow tools     (8)  runs, best run, compare, provenance, registry
                              ├─ Airflow tools    (6)  DAGs, runs, failed tasks, logs, trigger*
                              └─ Kubernetes tools (11) pods, logs, services, events, diagnosis, restart*/scale*
                                                       * state-changing: requires human confirmation
```

See [architecture.md](architecture.md) for the design and [docs/REPORT.md](docs/REPORT.md) for the technical report.

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| Git | any recent | |
| Docker Desktop | with WSL2 backend on Windows | runs MLflow + Airflow |
| kind (or minikube / Docker Desktop Kubernetes) | | local cluster |
| kubectl | 1.29+ | |
| Anthropic API key | | optional: `--offline` mode works without one |

## 2. Installation

```bash
git clone <your-repo-url> assignment-03-mlops-agent && cd assignment-03-mlops-agent
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -e ".[mlops,dev]"
cp .env.example .env                   # then edit: ANTHROPIC_API_KEY, AIRFLOW_PASSWORD, AIRFLOW_JWT_SECRET
```

## 3. Environment configuration

All configuration is through environment variables, loaded from `.env`. `.env` is gitignored.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (agent LLM) |
| `AGENT_LLM_BACKEND` | `claude` (default) or `offline` (rule-based planner, no key) |
| `MLFLOW_TRACKING_URI` | default `http://localhost:5000` |
| `AIRFLOW_URL`, `AIRFLOW_USERNAME`, `AIRFLOW_PASSWORD` | Airflow 3 API (JWT auth) |
| `AIRFLOW_APPROVED_DAGS` | DAGs the agent may trigger |
| `K8S_APPROVED_NAMESPACES`, `K8S_MUTABLE_DEPLOYMENTS` | Kubernetes permission policy |
| `MODEL_FRESHNESS_DAYS` | retraining freshness policy |

## 4. Launching each service

### 4.1 DVC (dataset versioning)
The repository already contains two tagged dataset versions:

| Tag | Rows | `params.yaml` data.version |
|---|---|---|
| `data-v1` | 400 | v1 |
| `data-v2` | 569 | v2 |

The DVC remote is a local folder (`D:/dvc-remote-mlops-agent`). On a fresh clone:
```bash
dvc remote modify localremote url /path/to/your/remote   # only if the path differs
dvc pull                                                  # or: python scripts/make_dataset.py --version v2 && dvc add data/raw/dataset.csv
dvc status
git checkout data-v1 -- data/raw/dataset.csv.dvc params.yaml && dvc checkout   # reproduce v1
git checkout main -- data/raw/dataset.csv.dvc params.yaml && dvc checkout      # back to v2
dvc repro                                                 # offline training stage
```

### 4.2 MLflow + Airflow (Docker)
```bash
docker compose up -d          # first start takes a few minutes (Airflow installs mlflow-skinny + sklearn)
docker compose ps             # wait until airflow is "healthy"
```
- MLflow UI: http://localhost:5000
- Airflow UI: http://localhost:8080 (user `admin`, password = `AIRFLOW_PASSWORD`)

Populate MLflow with several runs (needed for model-selection demos):
```bash
# Windows PowerShell: $env:MLFLOW_TRACKING_URI="http://localhost:5000"
export MLFLOW_TRACKING_URI=http://localhost:5000
python -m training.train --C 1.0 --promote
python -m training.train --C 0.1
python -m training.train --C 0.01
python -m training.train --C 10 --max-iter 1000
git checkout data-v1 -- data/raw/dataset.csv.dvc params.yaml && dvc checkout
python -m training.train --C 1.0
git checkout main -- data/raw/dataset.csv.dvc params.yaml && dvc checkout
```
Then trigger the Airflow DAG `ml_training_pipeline`, either in the UI or through the agent. Each run logs one more
MLflow run with Airflow lineage tags.

### 4.3 Kubernetes (prediction API)
```bash
kind create cluster --name mlops
python scripts/export_model.py              # pulls models:/breast-cancer-classifier@champion (or --local)
docker build -t prediction-api:1.0.0 .
kind load docker-image prediction-api:1.0.0 --name mlops
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/configmap.yaml -f kubernetes/deployment.yaml -f kubernetes/service.yaml
kubectl apply -f kubernetes/agent-rbac.yaml
kubectl -n ml-serving rollout status deployment/prediction-api
kubectl -n ml-serving port-forward svc/prediction-api 8000:80   # optional: curl localhost:8000/version
```

## 5. Running the tests

```bash
pytest -v          # 37 tests; every external service is mocked
ruff check .
```
CI (`.github/workflows/ci.yml`) runs lint and tests on every pull request and push to `main`, plus a training
smoke test.

## 6. Talking to the agent

**CLI**
```bash
python -m agent.main                     # interactive; /confirm <token> to approve actions
python -m agent.main "Which dataset version was used for experiment 25?"
python -m agent.main --offline "Why is my prediction API unavailable?"
```

**HTTP API**
```bash
uvicorn api.main:app --port 8001
curl -X POST localhost:8001/chat -H "Content-Type: application/json" \
     -d '{"message": "Find the best recent model, identify the dataset version used to train it, check the deployment status, and explain whether retraining is necessary."}'
curl -X POST localhost:8001/actions/confirm -H "Content-Type: application/json" -d '{"token": "<token>", "approve": true}'
```
Interactive API docs: http://localhost:8001/docs

### Demo scenarios
| # | Scenario | Prompt |
|---|---|---|
| 1 | Dataset provenance | "Which dataset version was used for experiment 3, and how do I reproduce it?" |
| 2 | Model selection | "Compare the last five training runs and recommend the best candidate based on F1 score." |
| 3 | Pipeline execution | "Trigger the model training pipeline and report whether execution succeeded." (then `/confirm`, then "What is the status of the latest pipeline run?") |
| 4 | Deployment diagnosis | "Why is my prediction API unavailable?" |
| 5 | Multi-tool reasoning | the mandatory integration prompt above |

### Failure injection
```bash
# Airflow: corrupted data -> validate_dataset fails, nothing downstream runs
python scripts/make_dataset.py --corrupt
#   trigger with conf {"inject_failure": "validation"}
# Airflow: quality gate failure -> model is NOT promoted
#   trigger with conf {"inject_failure": "quality_gate"}
# Kubernetes
bash scripts/inject_k8s_failure.sh bad-config   # wrong MODEL_PATH -> readiness 503
bash scripts/inject_k8s_failure.sh bad-image    # ImagePullBackOff
bash scripts/inject_k8s_failure.sh restore
```
Then ask the agent: "Why did the last training pipeline fail?" or "Why is my prediction API unavailable?"

## 7. Repository layout
```
agent/            orchestrator.py (tool loop), llm.py (Claude + offline), schemas.py, config.py, logging_utils.py
agent/tools/      base.py (registry, timeouts, confirmation), dvc_ / mlflow_ / airflow_ / kubernetes_tools.py
api/main.py       FastAPI interface for the agent
dags/             training_pipeline.py (Airflow 3 DAG)
training/         pipeline stages, provenance capture, CLI
serving/app.py    FastAPI inference service (containerized, deployed to k8s)
kubernetes/       namespace, configmap, deployment, service, agent RBAC
scripts/          dataset creation, model export, failure injection
tests/            pytest suite (mocked services)
docs/REPORT.md    technical report
```

## Security notes
- No secrets in the repo. Keys come from environment variables, and logs redact tokens, passwords and API keys.
- The agent can never execute a state change by itself. Mutating tools return a one-time token that only a human
  can redeem, and only for approved DAGs, namespaces and deployments.
- DVC/Git commands run without a shell, from a fixed allowlist, and git revisions are validated.
