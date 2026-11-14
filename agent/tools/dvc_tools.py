"""DVC data-management tools.

Commands go through `run_approved`, which only allows a fixed set of read-only git/dvc
sub-commands (plus `checkout`, which is a confirmation-gated mutating tool). Arguments are passed
as a list (never through a shell) and git revisions are validated against a strict pattern.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from agent.config import get_settings
from agent.schemas import NoInput, StrictInput, validate_git_ref
from agent.tools.base import PermissionDeniedError, ServiceUnavailableError, ToolError, ToolSpec

APPROVED_COMMANDS: dict[str, set[str]] = {
    "git": {"rev-parse", "log", "show", "tag", "ls-tree", "status", "checkout"},
    "dvc": {"status", "diff", "checkout", "data"},
}
COMMAND_TIMEOUT_S = 60


def run_approved(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    if len(cmd) < 2 or cmd[0] not in APPROVED_COMMANDS or cmd[1] not in APPROVED_COMMANDS[cmd[0]]:
        raise PermissionDeniedError(f"command not in approved list: {' '.join(cmd[:2])}")
    if cmd[0] == "git" and cmd[1] == "checkout" and "--" not in cmd:
        raise PermissionDeniedError("git checkout is only allowed for specific paths (git checkout <rev> -- <paths>)")
    if shutil.which(cmd[0]) is None:
        raise ServiceUnavailableError(f"'{cmd[0]}' executable not found on PATH")
    cwd = cwd or get_settings().repo_path
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, cwd=cwd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out: {' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        raise ToolError(f"`{' '.join(cmd)}` failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


# ------------------------------------------------------------------ helpers
def _git_show(rev: str, path: str) -> str | None:
    try:
        return run_approved(["git", "show", f"{rev}:{path}"])
    except ToolError:
        return None


def _tracked_dvc_files(rev: str = "HEAD") -> list[str]:
    out = run_approved(["git", "ls-tree", "-r", "--name-only", rev])
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".dvc")]


def _parse_dvc_file(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    doc = yaml.safe_load(text) or {}
    outs = doc.get("outs") or [{}]
    out = outs[0]
    md5 = out.get("md5")
    # YAML parses an all-digit hash as int - hashes are always strings
    return {"path": out.get("path"), "md5": str(md5) if md5 is not None else None, "size": out.get("size"),
            "nfiles": out.get("nfiles")}


def _resolve_commit(rev: str) -> str:
    validate_git_ref(rev)
    return run_approved(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"]).strip()


def _data_version_label(rev: str) -> str | None:
    params = _git_show(rev, "params.yaml")
    if not params:
        return None
    return ((yaml.safe_load(params) or {}).get("data") or {}).get("version")


def _tags_at(commit: str) -> list[str]:
    out = run_approved(["git", "tag", "--points-at", commit])
    return [t for t in out.split() if t]


# ------------------------------------------------------------------ inputs
class RevInput(StrictInput):
    rev: str = Field(default="HEAD", description="Git revision: commit SHA, tag (e.g. data-v1) or branch.")

    @field_validator("rev")
    @classmethod
    def _v(cls, v: str) -> str:
        return validate_git_ref(v)


class CompareInput(StrictInput):
    rev_a: str = Field(description="First git revision or data tag (e.g. data-v1).")
    rev_b: str = Field(default="HEAD", description="Second git revision or data tag.")

    @field_validator("rev_a", "rev_b")
    @classmethod
    def _v(cls, v: str) -> str:
        return validate_git_ref(v)


class CheckoutInput(RevInput):
    rev: str = Field(description="Git revision / data tag whose dataset should be restored into the workspace.")


# ------------------------------------------------------------------ tools
def list_tracked_datasets(_: NoInput) -> dict[str, Any]:
    datasets = []
    for dvc_file in _tracked_dvc_files():
        meta = _parse_dvc_file((get_settings().repo_path / dvc_file).read_text(encoding="utf-8"))
        meta["dvc_file"] = dvc_file
        datasets.append(meta)
    pipeline_outs: list[str] = []
    dvc_yaml = get_settings().repo_path / "dvc.yaml"
    if dvc_yaml.exists():
        stages = (yaml.safe_load(dvc_yaml.read_text(encoding="utf-8")) or {}).get("stages", {})
        for name, stage in stages.items():
            for out in stage.get("outs", []) or []:
                pipeline_outs.append(f"{name}: {out if isinstance(out, str) else next(iter(out))}")
    return {"tracked_datasets": datasets, "pipeline_outputs": pipeline_outs}


def get_dataset_version(params: RevInput) -> dict[str, Any]:
    commit = _resolve_commit(params.rev)
    log = run_approved(["git", "log", "-1", "--format=%H|%cI|%s", commit]).strip().split("|", 2)
    datasets = []
    for dvc_file in _tracked_dvc_files(commit):
        meta = _parse_dvc_file(_git_show(commit, dvc_file))
        meta["dvc_file"] = dvc_file
        datasets.append(meta)
    return {
        "requested_rev": params.rev,
        "git_commit": commit,
        "git_commit_short": commit[:7],
        "commit_date": log[1] if len(log) > 1 else None,
        "commit_message": log[2] if len(log) > 2 else None,
        "tags": _tags_at(commit),
        "data_version": _data_version_label(commit),
        "datasets": datasets,
    }


def list_dataset_versions(_: NoInput) -> dict[str, Any]:
    tags = [t for t in run_approved(["git", "tag", "--list", "data-v*", "--sort=creatordate"]).split() if t]
    versions = []
    for tag in tags:
        commit = _resolve_commit(tag)
        files = _tracked_dvc_files(commit)
        md5s = {f: _parse_dvc_file(_git_show(commit, f)).get("md5") for f in files}
        versions.append(
            {"tag": tag, "git_commit": commit[:7], "data_version": _data_version_label(commit), "md5": md5s}
        )
    return {"versions": versions, "count": len(versions)}


def compare_dataset_versions(params: CompareInput) -> dict[str, Any]:
    commit_a, commit_b = _resolve_commit(params.rev_a), _resolve_commit(params.rev_b)
    files = sorted(set(_tracked_dvc_files(commit_a)) | set(_tracked_dvc_files(commit_b)))
    comparison = []
    for f in files:
        a, b = _parse_dvc_file(_git_show(commit_a, f)), _parse_dvc_file(_git_show(commit_b, f))
        size_delta = (b.get("size") or 0) - (a.get("size") or 0) if a and b else None
        comparison.append(
            {
                "dvc_file": f,
                "md5_a": a.get("md5"),
                "md5_b": b.get("md5"),
                "size_a": a.get("size"),
                "size_b": b.get("size"),
                "size_delta_bytes": size_delta,
                "changed": a.get("md5") != b.get("md5"),
            }
        )
    dvc_diff: Any = None
    try:  # needs both versions in the DVC cache; best effort
        dvc_diff = json.loads(run_approved(["dvc", "diff", "--json", commit_a, commit_b]) or "{}")
    except (ToolError, ServiceUnavailableError, json.JSONDecodeError) as exc:
        dvc_diff = f"dvc diff unavailable: {exc}"
    return {
        "rev_a": {"rev": params.rev_a, "commit": commit_a[:7], "data_version": _data_version_label(commit_a)},
        "rev_b": {"rev": params.rev_b, "commit": commit_b[:7], "data_version": _data_version_label(commit_b)},
        "files": comparison,
        "any_changed": any(c["changed"] for c in comparison),
        "dvc_diff": dvc_diff,
    }


def check_data_changes(_: NoInput) -> dict[str, Any]:
    status_raw = run_approved(["dvc", "status", "--json"])
    status = json.loads(status_raw or "{}")
    git_changes = run_approved(["git", "status", "--porcelain", "--", "*.dvc", "params.yaml", "dvc.lock"]).splitlines()
    return {
        "workspace_matches_dvc_files": status == {},
        "dvc_status": status,
        "uncommitted_dvc_metadata": [line.strip() for line in git_changes if line.strip()],
    }


def get_reproduction_steps(params: RevInput) -> dict[str, Any]:
    commit = _resolve_commit(params.rev)
    files = _tracked_dvc_files(commit)
    data_paths = [p for p in (_parse_dvc_file(_git_show(commit, f)).get("path") for f in files) if p]
    targets = " ".join(files)
    return {
        "rev": params.rev,
        "git_commit": commit,
        "data_version": _data_version_label(commit),
        "steps": [
            "git fetch --all --tags",
            f"git checkout {commit[:7]} -- {targets} params.yaml",
            f"dvc pull {targets}   # fetch the exact bytes from the DVC remote",
            f"dvc checkout {targets}",
            "dvc status            # should report 'Data and pipelines are up to date.'",
        ],
        "data_paths": data_paths,
        "note": "The .dvc file at that commit pins the content hash (md5), so the restored data is byte-identical.",
    }


def checkout_dataset_version(params: CheckoutInput) -> dict[str, Any]:
    commit = _resolve_commit(params.rev)
    files = _tracked_dvc_files(commit)
    run_approved(["git", "checkout", commit, "--", *files, "params.yaml"])
    run_approved(["dvc", "checkout", *files])
    after = check_data_changes(NoInput())
    return {"checked_out_rev": params.rev, "git_commit": commit[:7], "files": files, "status_after": after}


def _checkout_precheck(params: CheckoutInput) -> None:
    _resolve_commit(params.rev)  # fails early if revision does not exist


checkout_dataset_version.precheck = _checkout_precheck  # type: ignore[attr-defined]


TOOLS = [
    ToolSpec("dvc_list_tracked_datasets", "List datasets tracked by DVC (.dvc files) with their content hash "
             "and size, plus outputs of the dvc.yaml pipeline.", "dvc", NoInput, list_tracked_datasets),
    ToolSpec("dvc_get_dataset_version", "Get the dataset version label, DVC md5 hashes and Git commit for a "
             "revision (default HEAD = current).", "dvc", RevInput, get_dataset_version),
    ToolSpec("dvc_list_dataset_versions", "List all tagged dataset versions (git tags data-v*) with commits and "
             "content hashes.", "dvc", NoInput, list_dataset_versions),
    ToolSpec("dvc_compare_dataset_versions", "Compare tracked data between two git revisions / data tags "
             "(hash, size, changed flag).", "dvc", CompareInput, compare_dataset_versions),
    ToolSpec("dvc_check_data_changes", "Detect whether tracked data in the workspace differs from what the "
             ".dvc files record (dvc status).", "dvc", NoInput, check_data_changes),
    ToolSpec("dvc_get_reproduction_steps", "Explain the exact commands to reproduce the dataset of a previous "
             "revision / data tag.", "dvc", RevInput, get_reproduction_steps),
    ToolSpec("dvc_checkout_dataset_version", "Restore the dataset of a previous revision into the workspace "
             "(git checkout of .dvc files + dvc checkout).", "dvc", CheckoutInput, checkout_dataset_version,
             mutating=True),
]
