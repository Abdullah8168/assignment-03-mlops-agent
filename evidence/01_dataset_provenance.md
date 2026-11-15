# 01_dataset_provenance

- Recorded: 2026-11-15T17:35:48+00:00
- LLM backend: `OfflineLLM`
- Request id: `95fe7c402704`

## Question

> Which dataset version was used for experiment 10, and how do I reproduce it?

## Tool calls

| # | Tool | Status | Duration (ms) |
|---|---|---|---|
| 1 | `mlflow_get_run_provenance` | success | 14678.36 |
| 2 | `dvc_get_dataset_version` | success | 418.99 |

## Agent answer

### Known facts (from tool evidence)
- Run train-10 was trained on dataset version **v1**, git commit `7cd893ae4be571b8eb348fc25e1217110043e9ac`, DVC md5 `1bd0667cf3f8ec8f1dbb204a4a057e2d`.
- Provenance check recorded at training time: verified: dataset restored from commit 7cd893a (HEAD has a different dataset).
- DVC at `7cd893a` (tags ['data-v1']): data version **v1**, files [('dataset.csv', '1bd0667cf3f8ec8f1dbb204a4a057e2d')].
- Cross-check passed: the run's recorded data hash matches the .dvc file at that commit - the dataset is reproducible from it.

### Uncertainties
- None identified.

### Recommended actions
- Reproduce with: git checkout 7cd893a -- data/raw/dataset.csv.dvc params.yaml && dvc pull && dvc checkout

## Raw tool evidence

<details><summary>1. mlflow_get_run_provenance (success)</summary>

```json
{
  "tool": "mlflow_get_run_provenance",
  "input": {
    "run": "10"
  },
  "result": {
    "tool": "mlflow_get_run_provenance",
    "status": "success",
    "data": {
      "run_id": "6e14f9e03abd406192eb9d57a8a1d4e9",
      "run_name": "train-10",
      "provenance": {
        "data.version": "v1",
        "data.git_commit": "7cd893ae4be571b8eb348fc25e1217110043e9ac",
        "data.git_tag": "data-v1",
        "data.dvc_md5": "1bd0667cf3f8ec8f1dbb204a4a057e2d",
        "data.path": "data/raw/dataset.csv",
        "data.provenance_check": "verified: dataset restored from commit 7cd893a (HEAD has a different dataset)",
        "code.git_commit": "d329b2d0ecf75f1e03ae3833b70fc7648e584c4b",
        "pipeline.dag_id": null,
        "pipeline.dag_run_id": null,
        "quality_gate.status": "passed",
        "quality_gate.reason": "all thresholds met"
      },
      "missing_provenance_fields": [],
      "reproduce_with": "dvc_get_reproduction_steps(rev='7cd893ae4be571b8eb348fc25e1217110043e9ac')"
    },
    "error": null,
    "duration_ms": 14678.36,
    "confirmation_token": null
  }
}
```

</details>

<details><summary>2. dvc_get_dataset_version (success)</summary>

```json
{
  "tool": "dvc_get_dataset_version",
  "input": {
    "rev": "7cd893ae4be571b8eb348fc25e1217110043e9ac"
  },
  "result": {
    "tool": "dvc_get_dataset_version",
    "status": "success",
    "data": {
      "requested_rev": "7cd893ae4be571b8eb348fc25e1217110043e9ac",
      "git_commit": "7cd893ae4be571b8eb348fc25e1217110043e9ac",
      "git_commit_short": "7cd893a",
      "commit_date": "2026-11-15T02:19:35+05:00",
      "commit_message": "Initialize DVC and track dataset v1 (400 rows)",
      "tags": [
        "data-v1"
      ],
      "data_version": "v1",
      "datasets": [
        {
          "path": "dataset.csv",
          "md5": "1bd0667cf3f8ec8f1dbb204a4a057e2d",
          "size": 85940,
          "nfiles": null,
          "dvc_file": "data/raw/dataset.csv.dvc"
        }
      ]
    },
    "error": null,
    "duration_ms": 418.99,
    "confirmation_token": null
  }
}
```

</details>
