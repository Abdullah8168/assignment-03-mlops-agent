"""DVC tools against a real temporary git repository (no DVC remote needed)."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from agent.config import get_settings
from agent.schemas import ToolStatus
from agent.tools import build_registry, dvc_tools
from agent.tools.base import PermissionDeniedError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

DVC_FILE = "outs:\n- md5: {md5}\n  size: {size}\n  hash: md5\n  path: dataset.csv\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    for version, md5, size in (("v1", "1" * 32, 1000), ("v2", "2" * 32, 1500)):
        (tmp_path / "data" / "raw" / "dataset.csv.dvc").write_text(DVC_FILE.format(md5=md5, size=size))
        (tmp_path / "params.yaml").write_text(f"data:\n  version: {version}\n")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", f"data {version}")
        _git(tmp_path, "tag", f"data-{version}")
    settings = dataclasses.replace(get_settings(), repo_path=tmp_path)
    monkeypatch.setattr(dvc_tools, "get_settings", lambda: settings)
    return tmp_path


def test_current_dataset_version_and_commit(repo):
    result = build_registry().execute("dvc_get_dataset_version", {})
    assert result.ok, result.error
    assert result.data["data_version"] == "v2"
    assert result.data["tags"] == ["data-v2"]
    assert result.data["datasets"][0]["md5"] == "2" * 32


def test_compare_dataset_versions(repo):
    result = build_registry().execute("dvc_compare_dataset_versions", {"rev_a": "data-v1", "rev_b": "data-v2"})
    assert result.ok, result.error
    assert result.data["any_changed"] is True
    assert result.data["files"][0]["size_delta_bytes"] == 500


def test_reproduction_steps_for_old_version(repo):
    result = build_registry().execute("dvc_get_reproduction_steps", {"rev": "data-v1"})
    assert result.ok
    assert result.data["data_version"] == "v1"
    assert any(step.startswith("dvc checkout") for step in result.data["steps"])


def test_unsafe_revisions_and_commands_are_blocked(repo):
    registry = build_registry()
    injected = registry.execute("dvc_get_dataset_version", {"rev": "--upload-pack=touch /tmp/pwned"})
    traversal = registry.execute("dvc_get_dataset_version", {"rev": "HEAD..main"})
    assert injected.status == ToolStatus.INVALID_INPUT
    assert traversal.status == ToolStatus.INVALID_INPUT
    for cmd in (["dvc", "push"], ["git", "push"], ["rm", "-rf", "/"], ["git", "checkout", "main"]):
        with pytest.raises(PermissionDeniedError):
            dvc_tools.run_approved(cmd)


def test_dataset_checkout_requires_confirmation(repo):
    result = build_registry().execute("dvc_checkout_dataset_version", {"rev": "data-v1"})
    assert result.status == ToolStatus.CONFIRMATION_REQUIRED
    assert "version: v2" in (repo / "params.yaml").read_text()  # workspace untouched
