from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import PlanningJobRepository  # noqa: E402


def _failed_job(database: Path) -> str:
    repository = PlanningJobRepository(database)
    submitted = repository.submit(
        request_id="req-cli-diagnosis",
        request_payload={"user_input": "private-cli-input"},
        tenant_id="tenant-cli",
        submitted_by="cli-test",
        max_attempts=1,
    )
    repository.claim_next(worker_id="worker-private-cli")
    repository.retry_or_dead_letter(
        job_id=submitted.job_id,
        worker_id="worker-private-cli",
        error_code="planning_execution_failed",
        error_message="private-cli-provider-error",
        backoff_seconds=0,
    )
    return submitted.job_id


def test_cli_writes_new_private_mode_artifact_without_raw_job_payload(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    job_id = _failed_job(database)
    output = tmp_path / "diagnosis.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/diagnose_job.py",
            "--job-id",
            job_id,
            "--tenant-id",
            "tenant-cli",
            "--database",
            str(database),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(completed.stdout)

    assert artifact["classification"] == "runtime_or_dependency_unknown"
    assert summary["artifact_sha256"] == artifact["artifact_sha256"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "private" not in output.read_text(encoding="utf-8") + completed.stdout


def test_cli_refuses_cross_tenant_lookup_and_existing_output(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    job_id = _failed_job(database)
    output = tmp_path / "existing.json"
    output.write_text("owner-content", encoding="utf-8")
    base = [
        sys.executable,
        "scripts/diagnose_job.py",
        "--job-id",
        job_id,
        "--database",
        str(database),
        "--output",
        str(output),
    ]

    wrong_tenant = subprocess.run(
        [*base, "--tenant-id", "tenant-other"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    existing = subprocess.run(
        [*base, "--tenant-id", "tenant-cli"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert wrong_tenant.returncode != 0
    assert "not found" in wrong_tenant.stderr
    assert existing.returncode != 0
    assert output.read_text(encoding="utf-8") == "owner-content"
