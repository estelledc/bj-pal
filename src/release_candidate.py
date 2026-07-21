"""Deterministic, payload-minimized release-candidate scope manifest."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from storage.verified_copy import canonical_sha256


MANIFEST_VERSION = "release_candidate_manifest_v1"
MAX_FILE_BYTES = 1024 * 1024
IMPLEMENTATION_GROUP = "implementation"
DOCUMENTATION_GROUP = "documentation"

ALLOWED_TOP_LEVEL_FILES = frozenset(
    {
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
)
ALLOWED_TOP_LEVEL_DIRECTORIES = frozenset(
    {".github", "docs", "evals", "fixtures", "scripts", "src", "tests"}
)
DENIED_PREFIXES = (
    ".venv/",
    ".streamlit/",
    "_site/",
    "data/",
    "evals/results/",
    "runtime/",
)
DENIED_SUFFIXES = (
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".local.png",
    ".pyc",
    ".trial-invites.json",
)
_POSIX_ROOT = "/"
PORTABLE_PATH_PATTERNS = (
    re.compile(re.escape(_POSIX_ROOT + "Users/") + r"[^/\s]+/"),
    re.compile(re.escape(_POSIX_ROOT + "home/") + r"[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\s]+\\\\"),
)
SAFE_GIT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def _validate_base_ref(base_ref: str) -> None:
    if not SAFE_GIT_REF.fullmatch(base_ref) or ".." in base_ref:
        raise ValueError("unsafe release base ref")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8", errors="strict").strip()


def candidate_status(repo: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        ],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    records = result.stdout.split(b"\0")
    parsed: list[tuple[str, str]] = []
    for raw in records:
        if not raw:
            continue
        text = raw.decode("utf-8", errors="strict")
        if len(text) < 4 or text[2] != " ":
            raise ValueError("unexpected git porcelain record")
        parsed.append((text[:2], text[3:]))
    return sorted(parsed, key=lambda item: item[1])


def classify_group(path: str) -> str:
    return (
        DOCUMENTATION_GROUP
        if path == "README.md" or path.startswith("docs/")
        else IMPLEMENTATION_GROUP
    )


def _path_policy(path: str) -> list[str]:
    violations: list[str] = []
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        violations.append("unsafe_relative_path")
        return violations
    if path == ".env" or path.startswith(".env."):
        violations.append("environment_file_forbidden")
    if path.startswith(DENIED_PREFIXES):
        violations.append("generated_or_runtime_path_forbidden")
    if path.endswith(DENIED_SUFFIXES):
        violations.append("generated_or_state_file_forbidden")
    top = candidate.parts[0] if candidate.parts else ""
    if path not in ALLOWED_TOP_LEVEL_FILES and top not in ALLOWED_TOP_LEVEL_DIRECTORIES:
        violations.append("outside_release_roots")
    return violations


def _file_entry(repo: Path, status: str, relative_path: str) -> tuple[dict, list[dict]]:
    path = repo / relative_path
    violations = [
        {"path": relative_path, "code": code} for code in _path_policy(relative_path)
    ]
    entry: dict[str, Any] = {
        "path": relative_path,
        "status": status,
        "group": classify_group(relative_path),
    }
    if not path.exists() and not path.is_symlink():
        if "D" in status:
            entry.update({"kind": "deleted", "size_bytes": 0, "sha256": None})
            return entry, violations
        violations.append({"path": relative_path, "code": "candidate_missing"})
        entry.update({"kind": "missing", "size_bytes": 0, "sha256": None})
        return entry, violations
    if path.is_symlink():
        violations.append({"path": relative_path, "code": "symlink_forbidden"})
        entry.update({"kind": "symlink", "size_bytes": 0, "sha256": None})
        return entry, violations
    if not path.is_file():
        violations.append({"path": relative_path, "code": "non_regular_file"})
        entry.update({"kind": "special", "size_bytes": 0, "sha256": None})
        return entry, violations

    payload = path.read_bytes()
    size = len(payload)
    git_mode = "100755" if path.stat().st_mode & 0o111 else "100644"
    entry.update(
        {
            "kind": "file",
            "git_mode": git_mode,
            "size_bytes": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    if size > MAX_FILE_BYTES:
        violations.append({"path": relative_path, "code": "file_too_large"})
    if b"\0" in payload:
        violations.append({"path": relative_path, "code": "binary_file_forbidden"})
        return entry, violations
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        violations.append({"path": relative_path, "code": "non_utf8_file_forbidden"})
        return entry, violations
    if any(pattern.search(text) for pattern in PORTABLE_PATH_PATTERNS):
        violations.append({"path": relative_path, "code": "local_absolute_path"})
    return entry, violations


def generate_release_candidate_manifest(
    repo: Path,
    *,
    base_ref: str = "origin/main",
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    _validate_base_ref(base_ref)
    statuses = candidate_status(repo)
    entries: list[dict] = []
    violations: list[dict] = []
    for status, path in statuses:
        entry, entry_violations = _file_entry(repo, status, path)
        entries.append(entry)
        violations.extend(entry_violations)

    status_counts: dict[str, int] = {}
    group_counts = {IMPLEMENTATION_GROUP: 0, DOCUMENTATION_GROUP: 0}
    for entry in entries:
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1
        group_counts[entry["group"]] += 1
    behind, ahead = (int(value) for value in _git(
        repo, "rev-list", "--left-right", "--count", f"{base_ref}...HEAD"
    ).split())
    body = {
        "version": MANIFEST_VERSION,
        "classification": "local_uncommitted_release_candidate",
        "ready": not violations and bool(entries),
        "git": {
            "branch": _git(repo, "branch", "--show-current"),
            "head": _git(repo, "rev-parse", "HEAD"),
            "base_ref": base_ref,
            "ahead": ahead,
            "behind": behind,
        },
        "policy": {
            "allowed_top_level_files": sorted(ALLOWED_TOP_LEVEL_FILES),
            "allowed_top_level_directories": sorted(ALLOWED_TOP_LEVEL_DIRECTORIES),
            "max_file_bytes": MAX_FILE_BYTES,
            "symlinks": "forbidden",
            "binary_files": "forbidden",
            "local_absolute_paths": "forbidden",
            "credential_literals": "verified_by_separate_secret_scan_gate",
            "commit_order": [IMPLEMENTATION_GROUP, DOCUMENTATION_GROUP],
        },
        "summary": {
            "candidate_count": len(entries),
            "group_counts": group_counts,
            "status_counts": status_counts,
            "total_bytes": sum(entry["size_bytes"] for entry in entries),
        },
        "entries": entries,
        "violations": sorted(violations, key=lambda item: (item["path"], item["code"])),
    }
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
