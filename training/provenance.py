"""Read the DVC dataset provenance (version label, content hash, git commit) for logging to MLflow.

The recorded `data.git_commit` is the commit whose .dvc file contains the *same content hash* as the data used,
not simply HEAD. HEAD is wrong whenever an older dataset was restored with
`git checkout <rev> -- data.csv.dvc` (HEAD does not move, but the data does). `code.git_commit` records HEAD
separately, and `data.provenance_check` says how the commit was resolved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str, cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _md5_of(dvc_text: str | None) -> str | None:
    if not dvc_text:
        return None
    md5 = ((yaml.safe_load(dvc_text) or {}).get("outs") or [{}])[0].get("md5")
    return str(md5) if md5 is not None else None


def find_data_commit(dvc_rel: str, md5: str, root: Path) -> str | None:
    """Most recent commit whose version of `dvc_rel` records `md5`."""
    history = _git("log", "--format=%H", "--", dvc_rel, cwd=root) or ""
    for commit in history.split():
        if _md5_of(_git("show", f"{commit}:{dvc_rel}", cwd=root)) == md5:
            return commit
    return None


def get_data_provenance(data_path: str | Path, root: Path = PROJECT_ROOT) -> dict[str, str]:
    data_path = Path(data_path)
    rel = data_path.relative_to(root) if data_path.is_absolute() and root in data_path.parents else data_path
    dvc_rel = f"{rel.as_posix()}.dvc"
    dvc_file = root / dvc_rel
    md5 = size = None
    if dvc_file.exists():
        out = ((yaml.safe_load(dvc_file.read_text(encoding="utf-8")) or {}).get("outs") or [{}])[0]
        md5, size = (str(out["md5"]) if out.get("md5") is not None else None), out.get("size")

    version = None
    params_file = root / "params.yaml"
    if params_file.exists():
        params = yaml.safe_load(params_file.read_text(encoding="utf-8")) or {}
        version = (params.get("data") or {}).get("version")

    head = _git("rev-parse", "HEAD", cwd=root) or os.getenv("GIT_COMMIT", "unknown")
    data_commit, check = head, "head (no dvc hash to verify)"
    if md5:
        # the commit that introduced this exact dataset version (not HEAD, which may hold a different one)
        found = find_data_commit(dvc_rel, md5, root)
        data_commit = found or "uncommitted"
        if not found:
            check = "WARNING: this data hash is not committed in git - not reproducible"
        elif _md5_of(_git("show", f"HEAD:{dvc_rel}", cwd=root)) == md5:
            check = f"verified: dataset introduced in commit {found[:7]}, unchanged at HEAD"
        else:
            check = f"verified: dataset restored from commit {found[:7]} (HEAD has a different dataset)"
    tags = _git("tag", "--points-at", data_commit, cwd=root) if data_commit not in ("uncommitted", "unknown") else None
    return {
        "data.path": rel.as_posix(),
        "data.version": str(version or "unversioned"),
        "data.dvc_md5": str(md5 or "untracked"),
        "data.size_bytes": str(size or ""),
        "data.git_commit": data_commit,
        "data.git_tag": (tags or "").replace("\n", ",") or "none",
        "data.provenance_check": check,
        "code.git_commit": head,
    }
