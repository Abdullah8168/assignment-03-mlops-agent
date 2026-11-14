"""Create a dataset version from the sklearn breast-cancer dataset.

    python scripts/make_dataset.py --version v1     # 400 rows (initial collection)
    python scripts/make_dataset.py --version v2     # all 569 rows (additional collection)
    python scripts/make_dataset.py --corrupt        # data/raw/corrupted.csv for failure injection

After creating a version:  dvc add data/raw/dataset.csv && git add ... && git tag data-vN
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import yaml
from sklearn.datasets import load_breast_cancer

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


def load_frame():
    ds = load_breast_cancer(as_frame=True)
    df = ds.frame.copy()
    df.columns = [c.replace(" ", "_") for c in df.columns]
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["v1", "v2"], default="v1")
    parser.add_argument("--corrupt", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    df = load_frame()

    if args.corrupt:
        bad = df.drop(columns=["target"]).copy()
        rng = np.random.default_rng(0)
        mask = rng.random(bad.shape) < 0.2
        bad = bad.mask(mask)
        bad.to_csv(RAW / "corrupted.csv", index=False)
        print(f"wrote {RAW / 'corrupted.csv'} (no target column, 20% nulls)")
        return

    if args.version == "v1":
        df = df.sample(n=400, random_state=7).sort_index()
    df.to_csv(RAW / "dataset.csv", index=False)
    params_path = ROOT / "params.yaml"
    params = yaml.safe_load(params_path.read_text())
    params["data"]["version"] = args.version
    params_path.write_text(yaml.safe_dump(params, sort_keys=False))
    print(f"wrote dataset {args.version}: {len(df)} rows -> {RAW / 'dataset.csv'}")


if __name__ == "__main__":
    main()
