# 04_deployment_diagnosis_failure

- Recorded: 2026-11-15T17:37:21+00:00
- LLM backend: `OfflineLLM`
- Request id: `633358bb8009`

## Question

> Why is my prediction API unavailable?

## Tool calls

| # | Tool | Status | Duration (ms) |
|---|---|---|---|
| 1 | `k8s_diagnose_deployment` | success | 2082.55 |

## Agent answer

### Known facts (from tool evidence)
- Deployment `prediction-api` runs ['prediction-api:1.0.0'] (version 1.0.0), 2/2 replicas ready.
- K8s finding: pod prediction-api-54c48b5588-kllsc: not Ready (readiness probe failing?)
- Event Unhealthy on Pod/prediction-api-54c48b5588-kllsc: Readiness probe failed: HTTP probe failed with statuscode: 503
- Event FailedToUpdateEndpointSlices on Service/prediction-api: Error updating Endpoint Slices for Service ml-serving/prediction-api: failed to update prediction-api-g47zm EndpointSlice for Service ml-serving/prediction-api: Unauthorized
- Event FailedToUpdateEndpoint on Endpoints/prediction-api: Failed to update endpoint ml-serving/prediction-api: Unauthorized
- Pod log errors: ['{"ts":"2026-11-15 17:37:11,819","level":"ERROR","logger":"prediction-api","message":"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)"}', '{"ts":"2026-11-15 17:37:16,820","level":"ERROR","logger":"prediction-api","message":"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)"}']

### Uncertainties
- None identified.

### Recommended actions
- Root cause: the ConfigMap sets a MODEL_PATH that does not exist in the image, so the model never loads and /health/ready returns 503. Fix: restore MODEL_PATH=/app/model/model.joblib (kubectl apply -f kubernetes/configmap.yaml), then restart the deployment via k8s_restart_deployment (requires your confirmation).

## Raw tool evidence

<details><summary>1. k8s_diagnose_deployment (success)</summary>

```json
{
  "tool": "k8s_diagnose_deployment",
  "input": {},
  "result": {
    "tool": "k8s_diagnose_deployment",
    "status": "success",
    "data": {
      "deployment": {
        "deployment": "prediction-api",
        "namespace": "ml-serving",
        "version_label": "1.0.0",
        "images": [
          "prediction-api:1.0.0"
        ],
        "model_version_annotation": "champion",
        "env_versions": {},
        "revision": "3",
        "ready_replicas": 2,
        "desired_replicas": 2
      },
      "pods": [
        {
          "name": "prediction-api-54c48b5588-kllsc",
          "phase": "Running",
          "ready": false,
          "restarts": 0,
          "age": "0m31s",
          "node": "mlops-control-plane",
          "version_label": "1.0.0",
          "containers": [
            {
              "name": "prediction-api",
              "image": "docker.io/library/prediction-api:1.0.0",
              "ready": false,
              "restart_count": 0,
              "state": "running",
              "reason": null,
              "message": null,
              "last_termination_reason": null,
              "last_exit_code": null
            }
          ],
          "unhealthy_reasons": [
            "not Ready (readiness probe failing?)"
          ]
        },
        {
          "name": "prediction-api-756784bc64-fvzd5",
          "phase": "Running",
          "ready": true,
          "restarts": 0,
          "age": "2h57m",
          "node": "mlops-control-plane",
          "version_label": "1.0.0",
          "containers": [
            {
              "name": "prediction-api",
              "image": "docker.io/library/prediction-api:1.0.0",
              "ready": true,
              "restart_count": 0,
              "state": "running",
              "reason": null,
              "message": null,
              "last_termination_reason": null,
              "last_exit_code": null
            }
          ],
          "unhealthy_reasons": []
        },
        {
          "name": "prediction-api-756784bc64-n8hwg",
          "phase": "Running",
          "ready": true,
          "restarts": 0,
          "age": "2h57m",
          "node": "mlops-control-plane",
          "version_label": "1.0.0",
          "containers": [
            {
              "name": "prediction-api",
              "image": "docker.io/library/prediction-api:1.0.0",
              "ready": true,
              "restart_count": 0,
              "state": "running",
              "reason": null,
              "message": null,
              "last_termination_reason": null,
              "last_exit_code": null
            }
          ],
          "unhealthy_reasons": []
        }
      ],
      "services": [
        {
          "name": "prediction-api",
          "type": "ClusterIP",
          "cluster_ip": "10.96.95.192",
          "ports": [
            "80->http/TCP"
          ],
          "selector": {
            "app": "prediction-api"
          },
          "ready_endpoints": 2
        }
      ],
      "warning_events": [
        {
          "type": "Warning",
          "reason": "Unhealthy",
          "object": "Pod/prediction-api-54c48b5588-kllsc",
          "message": "Readiness probe failed: HTTP probe failed with statuscode: 503",
          "count": 5,
          "last_seen": "0m5s"
        },
        {
          "type": "Warning",
          "reason": "FailedToUpdateEndpointSlices",
          "object": "Service/prediction-api",
          "message": "Error updating Endpoint Slices for Service ml-serving/prediction-api: failed to update prediction-api-g47zm EndpointSlice for Service ml-serving/prediction-api: Unauthorized",
          "count": 1,
          "last_seen": "0m30s"
        },
        {
          "type": "Warning",
          "reason": "FailedToUpdateEndpoint",
          "object": "Endpoints/prediction-api",
          "message": "Failed to update endpoint ml-serving/prediction-api: Unauthorized",
          "count": 1,
          "last_seen": "0m30s"
        },
        {
          "type": "Warning",
          "reason": "FailedCreate",
          "object": "ReplicaSet/prediction-api-54c48b5588",
          "message": "Error creating: Unauthorized",
          "count": 1,
          "last_seen": "0m31s"
        },
        {
          "type": "Warning",
          "reason": "ReplicaSetCreateError",
          "object": "Deployment/prediction-api",
          "message": "Failed to create new replica set \"prediction-api-54c48b5588\": Unauthorized",
          "count": 1,
          "last_seen": "0m31s"
        },
        {
          "type": "Warning",
          "reason": "Unhealthy",
          "object": "Pod/prediction-api-756784bc64-n8hwg",
          "message": "Readiness probe failed: Get \"http://10.244.0.8:8000/health/ready\": dial tcp 10.244.0.8:8000: connect: connection refused",
          "count": 1,
          "last_seen": "2h57m"
        },
        {
          "type": "Warning",
          "reason": "Unhealthy",
          "object": "Pod/prediction-api-5666c48c58-8t9l2",
          "message": "Readiness probe failed: Get \"http://10.244.0.6:8000/health/ready\": dial tcp 10.244.0.6:8000: connect: connection refused",
          "count": 1,
          "last_seen": "2h57m"
        },
        {
          "type": "Warning",
          "reason": "Unhealthy",
          "object": "Pod/prediction-api-756784bc64-fvzd5",
          "message": "Readiness probe failed: Get \"http://10.244.0.7:8000/health/ready\": dial tcp 10.244.0.7:8000: connect: connection refused",
          "count": 1,
          "last_seen": "2h57m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-4g459",
          "message": "Error: ImagePullBackOff",
          "count": 12,
          "last_seen": "2h57m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-8t9l2",
          "message": "Error: ImagePullBackOff",
          "count": 12,
          "last_seen": "2h57m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-8t9l2",
          "message": "Failed to pull image \"prediction-api:1.0.0\": failed to pull and unpack image \"docker.io/library/prediction-api:1.0.0\": failed to resolve reference \"docker.io/library/prediction-api:1.0.0\": pull access denied, repository does not exist or may require authorization: server message: insufficient_scope:",
          "count": 5,
          "last_seen": "2h58m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-8t9l2",
          "message": "Error: ErrImagePull",
          "count": 5,
          "last_seen": "2h58m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-4g459",
          "message": "Failed to pull image \"prediction-api:1.0.0\": failed to pull and unpack image \"docker.io/library/prediction-api:1.0.0\": failed to resolve reference \"docker.io/library/prediction-api:1.0.0\": pull access denied, repository does not exist or may require authorization: server message: insufficient_scope:",
          "count": 5,
          "last_seen": "2h58m"
        },
        {
          "type": "Warning",
          "reason": "Failed",
          "object": "Pod/prediction-api-5666c48c58-4g459",
          "message": "Error: ErrImagePull",
          "count": 5,
          "last_seen": "2h58m"
        }
      ],
      "logs_of_first_unhealthy_pod": {
        "pod": "prediction-api-54c48b5588-kllsc",
        "namespace": "ml-serving",
        "previous": false,
        "error_lines": [
          "{\"ts\":\"2026-11-15 17:36:54,067\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"ERROR loading model: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "{\"ts\":\"2026-11-15 17:36:56,849\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "{\"ts\":\"2026-11-15 17:37:01,821\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "{\"ts\":\"2026-11-15 17:37:06,820\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "{\"ts\":\"2026-11-15 17:37:11,819\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "{\"ts\":\"2026-11-15 17:37:16,820\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}"
        ],
        "lines": [
          "INFO:     Started server process [1]",
          "INFO:     Waiting for application startup.",
          "{\"ts\":\"2026-11-15 17:36:53,521\",\"level\":\"INFO\",\"logger\":\"prediction-api\",\"message\":\"starting prediction-api version=1.0.0\"}",
          "{\"ts\":\"2026-11-15 17:36:54,067\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"ERROR loading model: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     Application startup complete.",
          "INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)",
          "{\"ts\":\"2026-11-15 17:36:56,849\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     10.244.0.1:60234 - \"GET /health/ready HTTP/1.1\" 503 Service Unavailable",
          "{\"ts\":\"2026-11-15 17:37:01,821\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     10.244.0.1:34196 - \"GET /health/ready HTTP/1.1\" 503 Service Unavailable",
          "{\"ts\":\"2026-11-15 17:37:06,820\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     10.244.0.1:34208 - \"GET /health/ready HTTP/1.1\" 503 Service Unavailable",
          "INFO:     10.244.0.1:38110 - \"GET /health/live HTTP/1.1\" 200 OK",
          "{\"ts\":\"2026-11-15 17:37:11,819\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     10.244.0.1:38120 - \"GET /health/ready HTTP/1.1\" 503 Service Unavailable",
          "{\"ts\":\"2026-11-15 17:37:16,820\",\"level\":\"ERROR\",\"logger\":\"prediction-api\",\"message\":\"readiness check failed: FileNotFoundError: model file not found at /app/model/missing-model.joblib (check MODEL_PATH in ConfigMap)\"}",
          "INFO:     10.244.0.1:38126 - \"GET /health/ready HTTP/1.1\" 503 Service Unavailable",
          "INFO:     10.244.0.1:41136 - \"GET /health/live HTTP/1.1\" 200 OK"
        ]
      },
      "findings": [
        "pod prediction-api-54c48b5588-kllsc: not Ready (readiness probe failing?)"
      ]
    },
    "error": null,
    "duration_ms": 2082.55,
  
```

</details>
