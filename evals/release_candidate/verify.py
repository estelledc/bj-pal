from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from storage.verified_copy import canonical_sha256


TOP_FILES = {
    ".dockerignore",
    ".env_example",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "README.md",
    "compose.public.yaml",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
}
TOP_DIRECTORIES = {".github", "docs", "evals", "fixtures", "scripts", "src", "tests"}
DENIED_PREFIXES = (".venv/", ".streamlit/", "_site/", "data/", "evals/results/", "runtime/")
DENIED_SUFFIXES = (
    ".db", ".db-journal", ".db-shm", ".db-wal", ".local.png", ".pyc",
    ".trial-invites.json",
)
_POSIX_ROOT = "/"
ABSOLUTE_PATTERNS = (
    re.compile(re.escape(_POSIX_ROOT + "Users/") + r"[^/\s]+/"),
    re.compile(re.escape(_POSIX_ROOT + "home/") + r"[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\s]+\\\\"),
)
SAFE_GIT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
EXPECTED_POLICY = {
    "allowed_top_level_files": sorted(TOP_FILES),
    "allowed_top_level_directories": sorted(TOP_DIRECTORIES),
    "max_file_bytes": 1024 * 1024,
    "symlinks": "forbidden",
    "binary_files": "forbidden",
    "local_absolute_paths": "forbidden",
    "credential_literals": "verified_by_separate_secret_scan_gate",
    "commit_order": ["implementation", "documentation"],
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("utf-8").strip()


def _status(repo: Path) -> list[tuple[str, str]]:
    payload = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    rows: list[tuple[str, str]] = []
    for raw in payload.split(b"\0"):
        if raw:
            text = raw.decode("utf-8")
            rows.append((text[:2], text[3:]))
    return sorted(rows, key=lambda row: row[1])


def _forbidden(path: str, payload: bytes) -> bool:
    candidate = Path(path)
    top = candidate.parts[0] if candidate.parts else ""
    if candidate.is_absolute() or ".." in candidate.parts:
        return True
    if path == ".env" or path.startswith(".env."):
        return True
    if path.startswith(DENIED_PREFIXES) or path.endswith(DENIED_SUFFIXES):
        return True
    if path not in TOP_FILES and top not in TOP_DIRECTORIES:
        return True
    if len(payload) > 1024 * 1024 or b"\0" in payload:
        return True
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(pattern.search(text) for pattern in ABSOLUTE_PATTERNS)


def verify_release_candidate_manifest(
    artifact: dict[str, Any],
    repo: Path,
) -> dict[str, Any]:
    signed = dict(artifact)
    claimed_sha = signed.pop("artifact_sha256", None)
    if claimed_sha != canonical_sha256(signed):
        raise ValueError("release manifest sha256 mismatch")
    if artifact.get("version") != "release_candidate_manifest_v1":
        raise ValueError("unsupported release manifest version")
    if artifact.get("classification") != "local_uncommitted_release_candidate":
        raise ValueError("release manifest classification mismatch")
    if artifact.get("policy") != EXPECTED_POLICY:
        raise ValueError("release manifest policy mismatch")
    if artifact.get("ready") is not True or artifact.get("violations") != []:
        raise ValueError("release manifest is not ready")

    repo = Path(repo).resolve()
    current = _status(repo)
    entries = artifact.get("entries")
    if not isinstance(entries, list) or len(entries) != len(current):
        raise ValueError("release manifest candidate count mismatch")
    expected_pairs = [(entry.get("status"), entry.get("path")) for entry in entries]
    if expected_pairs != current:
        raise ValueError("release manifest paths or statuses changed")

    groups = {"implementation": 0, "documentation": 0}
    statuses: dict[str, int] = {}
    total_bytes = 0
    for entry in entries:
        path = entry["path"]
        candidate = repo / path
        expected_group = (
            "documentation" if path == "README.md" or path.startswith("docs/")
            else "implementation"
        )
        if entry.get("group") != expected_group:
            raise ValueError("release manifest group mismatch")
        groups[expected_group] += 1
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
        if entry.get("kind") == "deleted":
            if candidate.exists() or "D" not in entry["status"]:
                raise ValueError("release manifest deletion mismatch")
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("release manifest candidate is not a regular file")
        payload = candidate.read_bytes()
        if _forbidden(path, payload):
            raise ValueError("release manifest contains a forbidden candidate")
        if entry.get("size_bytes") != len(payload):
            raise ValueError("release manifest size mismatch")
        expected_mode = "100755" if candidate.stat().st_mode & 0o111 else "100644"
        if entry.get("git_mode") != expected_mode:
            raise ValueError("release manifest git mode mismatch")
        if entry.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise ValueError("release manifest file sha256 mismatch")
        total_bytes += len(payload)

    summary = artifact.get("summary")
    expected_summary = {
        "candidate_count": len(entries),
        "group_counts": groups,
        "status_counts": statuses,
        "total_bytes": total_bytes,
    }
    if summary != expected_summary:
        raise ValueError("release manifest summary mismatch")
    git = artifact.get("git", {})
    base_ref = git.get("base_ref")
    if (
        not isinstance(base_ref, str)
        or not SAFE_GIT_REF.fullmatch(base_ref)
        or ".." in base_ref
    ):
        raise ValueError("release manifest base ref is unsafe")
    if git.get("head") != _git(repo, "rev-parse", "HEAD"):
        raise ValueError("release manifest HEAD changed")
    if git.get("branch") != _git(repo, "branch", "--show-current"):
        raise ValueError("release manifest branch changed")
    behind, ahead = (
        int(value)
        for value in _git(
            repo,
            "rev-list",
            "--left-right",
            "--count",
            f"{base_ref}...HEAD",
        ).split()
    )
    if git.get("ahead") != ahead or git.get("behind") != behind:
        raise ValueError("release manifest base divergence changed")
    return expected_summary
