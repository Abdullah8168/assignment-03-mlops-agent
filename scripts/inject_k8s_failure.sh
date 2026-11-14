#!/usr/bin/env bash
# Deliberate failure injection for the demo / report.
#   ./scripts/inject_k8s_failure.sh bad-image    -> ImagePullBackOff on a new rollout
#   ./scripts/inject_k8s_failure.sh bad-config   -> wrong MODEL_PATH: model not loaded, readiness 503
#   ./scripts/inject_k8s_failure.sh restore      -> back to the healthy manifests
set -euo pipefail
NS=ml-serving
cd "$(dirname "$0")/.."

case "${1:-}" in
  bad-image)
    kubectl -n "$NS" set image deployment/prediction-api prediction-api=prediction-api:9.9.9-does-not-exist
    ;;
  bad-config)
    kubectl -n "$NS" patch configmap prediction-api-config --type merge \
      -p '{"data":{"MODEL_PATH":"/app/model/missing-model.joblib"}}'
    kubectl -n "$NS" rollout restart deployment/prediction-api
    ;;
  restore)
    kubectl apply -f kubernetes/configmap.yaml -f kubernetes/deployment.yaml -f kubernetes/service.yaml
    kubectl -n "$NS" rollout restart deployment/prediction-api
    kubectl -n "$NS" rollout status deployment/prediction-api --timeout=120s
    ;;
  *)
    echo "usage: $0 bad-image|bad-config|restore" >&2
    exit 1
    ;;
esac
kubectl -n "$NS" get pods -l app=prediction-api
