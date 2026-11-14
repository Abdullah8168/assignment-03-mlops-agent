"""Read the DVC dataset provenance (version label, content hash, git commit) for logging to MLflow."""

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


def get_data_provenance(data_path: str | Path, root: Path = PROJECT_ROOT) -> dict[str, str]:
    data_path = Path(data_path)
    rel = data_path.relative_to(root) if data_path.is_absolute() and root in data_path.parents else data_path
    dvc_file = root / f"{rel.as_posix()}.dvc"
    md5 = size = None
    if dvc_file.exists():
        out = ((yaml.safe_load(dvc_file.read_text(encoding="utf-8")) or {}).get("outs") or [{}])[0]
        md5, size = out.get("md5"), out.get("size")

    version = None
    params_file = root / "params.yaml"
    if params_file.exists():
        version = ((yaml.safe_load(params_file.read_text(encoding="utf-8")) or {}).get("data") or {}).get("version")

    commit = _git("rev-parse", "HEAD", cwd=root) or os.getenv("GIT_COMMIT", "unknown")
    dirty = _git("status", "--porcelain", "--", f"{rel.as_posix()}.dvc", "params.yaml", cwd=root)
    return {
        "data.path": rel.as_posix(),
        "data.version": str(version or "unversioned"),
        "data.dvc_md5": str(md5 or "untracked"),
        "data.size_bytes": str(size or ""),
        "data.git_commit": commit,
        "data.git_dirty": "true" if dirty else "false",
    }
