# Prediction API image (the ML application deployed to Kubernetes).
# Build after exporting a model:  python scripts/export_model.py && docker build -t prediction-api:1.0.0 .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# scikit-learn must match the version that pickled the model (see pyproject / docker-compose)
ARG SKLEARN_VERSION=1.9.1
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "scikit-learn==${SKLEARN_VERSION}" \
        "pandas>=2.1" "joblib>=1.3" \
    && useradd --create-home --uid 10001 appuser

COPY serving/ /app/serving/
COPY serving/model/ /app/model/

USER appuser
EXPOSE 8000
CMD ["uvicorn", "serving.app:app", "--host", "0.0.0.0", "--port", "8000"]
