from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from evals.release_candidate.verify import verify_release_candidate_manifest
from release_candidate import generate_release_candidate_manifest
from storage.verified_copy import canonical_sha256


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "baseline")
    return repo


def _resign(artifact: dict) -> None:
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = canonical_sha256(artifact)


def test_release_manifest_groups_and_verifies_current_candidate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("updated\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_feature.py").write_text(
        "def test_value(): assert 1 == 1\n", encoding="utf-8"
    )

    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    summary = verify_release_candidate_manifest(artifact, repo)

    assert artifact["ready"] is True
    assert summary["candidate_count"] == 3
    assert summary["group_counts"] == {"implementation": 2, "documentation": 1}


def test_release_manifest_allows_public_compose_contract(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "compose.public.yaml").write_text(
        "services:\n  api:\n    image: example.invalid/bj-pal:v1.0.0\n",
        encoding="utf-8",
    )

    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    summary = verify_release_candidate_manifest(artifact, repo)

    assert artifact["ready"] is True
    assert summary["candidate_count"] == 1
    assert artifact["entries"][0]["path"] == "compose.public.yaml"


def test_release_manifest_rejects_secret_runtime_and_absolute_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".env.local").write_text("TOKEN=placeholder\n", encoding="utf-8")
    (repo / "runtime").mkdir()
    (repo / "runtime" / "state.json").write_text("{}\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "local.py").write_text(
        'PATH = "' + "/" + 'Users/example/work/repo"\n', encoding="utf-8"
    )

    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    codes = {item["code"] for item in artifact["violations"]}

    assert artifact["ready"] is False
    assert {
        "environment_file_forbidden",
        "generated_or_runtime_path_forbidden",
        "outside_release_roots",
        "local_absolute_path",
    } <= codes


def test_release_manifest_rejects_large_and_binary_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "fixtures").mkdir()
    (repo / "fixtures" / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    (repo / "fixtures" / "binary.bin").write_bytes(b"ok\0not-text")

    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    codes = {item["code"] for item in artifact["violations"]}

    assert artifact["ready"] is False
    assert {"file_too_large", "binary_file_forbidden"} <= codes


def test_release_manifest_verifier_rejects_resigned_file_hash_tamper(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    tampered = copy.deepcopy(artifact)
    tampered["entries"][0]["sha256"] = "0" * 64
    _resign(tampered)

    with pytest.raises(ValueError, match="file sha256 mismatch"):
        verify_release_candidate_manifest(tampered, repo)


def test_release_manifest_verifier_rejects_worktree_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    path = repo / "src" / "feature.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    path.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="file sha256 mismatch"):
        verify_release_candidate_manifest(artifact, repo)


def test_release_manifest_verifier_rejects_executable_mode_change(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "scripts").mkdir()
    path = repo / "scripts" / "run.sh"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    path.chmod(0o755)

    with pytest.raises(ValueError, match="git mode mismatch"):
        verify_release_candidate_manifest(artifact, repo)


def test_release_manifest_verifier_rejects_resigned_policy_tamper(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    artifact = generate_release_candidate_manifest(repo, base_ref="HEAD")
    artifact["policy"]["binary_files"] = "allowed"
    _resign(artifact)

    with pytest.raises(ValueError, match="policy mismatch"):
        verify_release_candidate_manifest(artifact, repo)


def test_release_manifest_rejects_unsafe_base_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe release base ref"):
        generate_release_candidate_manifest(repo, base_ref="--all")
