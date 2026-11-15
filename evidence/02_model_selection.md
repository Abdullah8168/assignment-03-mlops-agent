# 02_model_selection

- Recorded: 2026-11-15T17:36:41+00:00
- LLM backend: `OfflineLLM`
- Request id: `f527a8a11aec`

## Question

> Compare the last five training runs and recommend the best candidate based on F1 score.

## Tool calls

| # | Tool | Status | Duration (ms) |
|---|---|---|---|
| 1 | `mlflow_compare_runs` | success | 18032.83 |
| 2 | `mlflow_get_model_registry` | success | 170.44 |

## Agent answer

### Known facts (from tool evidence)
- Compared 5 runs on `f1`:
-   - train-10: f1=0.9901, recall=0.9804, data=v1, test_rows=80.0, gate=passed, params={'C': '1.0', 'max_iter': '500', 'f1_threshold': '0.9'}
-   - train-5: f1=0.9901, recall=0.9804, data=v1, test_rows=80.0, gate=passed, params={'C': '1.0', 'max_iter': '500', 'f1_threshold': '0.9'}
-   - train-11: f1=0.9861, recall=0.9861, data=v2, test_rows=114.0, gate=failed, params={'C': '1.0', 'max_iter': '500', 'f1_threshold': '0.9'}
-   - train-9: f1=0.9861, recall=0.9861, data=v2, test_rows=114.0, gate=passed, params={'C': '1.0', 'max_iter': '500', 'f1_threshold': '0.9'}
-   - train-4: f1=0.9722, recall=0.9722, data=v2, test_rows=114.0, gate=passed, params={'C': '10.0', 'max_iter': '1000', 'f1_threshold': '0.9'}
- Registry: `breast-cancer-classifier` has 2 version(s); @champion = v2 (source run `4e824d4872c04ea4b0670e6507c3fc1b`).

### Uncertainties
- Runs were evaluated on different dataset versions ['v1', 'v2'] (different test sets), so their f1 values are NOT directly comparable.

### Recommended actions
- Recommended candidate: **train-9** (f1=0.9861) - highest f1 among runs that passed the quality gate on the latest data (v2); train-10 has a higher raw f1 (0.9901) but on v1 data / 80.0 test rows. Before promoting it, validate it on a held-out set and check latency/drift: a high offline metric alone does not make a model production-ready.

## Raw tool evidence

<details><summary>1. mlflow_compare_runs (success)</summary>

```json
{
  "tool": "mlflow_compare_runs",
  "input": {
    "last_n": 5,
    "metric": "f1"
  },
  "result": {
    "tool": "mlflow_compare_runs",
    "status": "success",
    "data": {
      "metric": "f1",
      "runs": [
        {
          "run_id": "8b0d99f3a8f44dacaa5e6bb86c05370f",
          "run_name": "train-11",
          "status": "FINISHED",
          "start_time": "2026-11-15T17:35:52+00:00",
          "metrics": {
            "accuracy": 0.9825,
            "precision": 0.9861,
            "recall": 0.9861,
            "f1": 0.9861,
            "roc_auc": 0.9954,
            "test_rows": 114.0
          },
          "data_version": "v2",
          "data_git_commit": "15f54658e37e4755c81bddec2f15ab99a63563e8",
          "quality_gate": "failed",
          "params": {
            "C": "1.0",
            "max_iter": "500",
            "f1_threshold": "0.9"
          }
        },
        {
          "run_id": "6e14f9e03abd406192eb9d57a8a1d4e9",
          "run_name": "train-10",
          "status": "FINISHED",
          "start_time": "2026-11-15T14:42:26+00:00",
          "metrics": {
            "accuracy": 0.9875,
            "precision": 1.0,
            "recall": 0.9804,
            "f1": 0.9901,
            "roc_auc": 0.9993,
            "test_rows": 80.0
          },
          "data_version": "v1",
          "data_git_commit": "7cd893ae4be571b8eb348fc25e1217110043e9ac",
          "quality_gate": "passed",
          "params": {
            "C": "1.0",
            "max_iter": "500",
            "f1_threshold": "0.9"
          }
        },
        {
          "run_id": "4e824d4872c04ea4b0670e6507c3fc1b",
          "run_name": "train-9",
          "status": "FINISHED",
          "start_time": "2026-11-15T14:33:42+00:00",
          "metrics": {
            "accuracy": 0.9825,
            "precision": 0.9861,
            "recall": 0.9861,
            "f1": 0.9861,
            "roc_auc": 0.9954,
            "test_rows": 114.0
          },
          "data_version": "v2",
          "data_git_commit": "d329b2d0ecf75f1e03ae3833b70fc7648e584c4b",
          "quality_gate": "passed",
          "params": {
            "C": "1.0",
            "max_iter": "500",
            "f1_threshold": "0.9"
          }
        },
        {
          "run_id": "20f9ffe54a0a4429853528c17d1dddc9",
          "run_name": "train-5",
          "status": "FINISHED",
          "start_time": "2026-11-15T14:02:29+00:00",
          "metrics": {
            "accuracy": 0.9875,
            "precision": 1.0,
            "recall": 0.9804,
            "f1": 0.9901,
            "roc_auc": 0.9993,
            "test_rows": 80.0
          },
          "data_version": "v1",
          "data_git_commit": "d329b2d0ecf75f1e03ae3833b70fc7648e584c4b",
          "quality_gate": "passed",
          "params": {
            "C": "1.0",
            "max_iter": "500",
            "f1_threshold": "0.9"
          }
        },
        {
          "run_id": "0d19d4f766d34e6e8ed0b54919b4a6a2",
          "run_name": "train-4",
          "status": "FINISHED",
          "start_time": "2026-11-15T14:02:06+00:00",
          "metrics": {
            "accuracy": 0.9649,
            "precision": 0.9722,
            "recall": 0.9722,
            "f1": 0.9722,
            "roc_auc": 0.9934,
            "test_rows": 114.0
          },
          "data_version": "v2",
          "data_git_commit": "d329b2d0ecf75f1e03ae3833b70fc7648e584c4b",
          "quality_gate": "passed",
          "params": {
            "C": "10.0",
            "max_iter": "1000",
            "f1_threshold": "0.9"
          }
        }
      ],
      "ranking_by_metric": [
        {
          "run_name": "train-10",
          "f1": 0.9901
        },
        {
          "run_name": "train-5",
          "f1": 0.9901
        },
        {
          "run_name": "train-11",
          "f1": 0.9861
        },
        {
          "run_name": "train-9",
          "f1": 0.9861
        },
        {
          "run_name": "train-4",
          "f1": 0.9722
        }
      ],
      "recommended_candidate": "train-10"
    },
    "error": null,
    "duration_ms": 18032.83,
    "confirmation_token": null
  }
}
```

</details>

<details><summary>2. mlflow_get_model_registry (success)</summary>

```json
{
  "tool": "mlflow_get_model_registry",
  "input": {},
  "result": {
    "tool": "mlflow_get_model_registry",
    "status": "success",
    "data": {
      "model_name": "breast-cancer-classifier",
      "aliases": {
        "champion": "2"
      },
      "versions": [
        {
          "version": "2",
          "run_id": "4e824d4872c04ea4b0670e6507c3fc1b",
          "status": "READY",
          "created": "2026-11-15T14:33:59+00:00",
          "tags": {}
        },
        {
          "version": "1",
          "run_id": "fee6029444504a679bb92bbb95cd7b72",
          "status": "READY",
          "created": "2026-11-15T14:01:26+00:00",
          "tags": {}
        }
      ]
    },
    "error": null,
    "duration_ms": 170.44,
    "confirmation_token": null
  }
}
```

</details>
