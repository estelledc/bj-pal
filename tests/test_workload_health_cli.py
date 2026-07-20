from __future__ import annotations

import json
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import PlanningJobRepository  # noqa: E402


def test_cli_writes_new_mode_0600_snapshot_without_identifiers(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    repository = PlanningJobRepository(database)
    submitted = repository.submit(
        request_id="private-cli-request",
        request_payload={"user_input": "private-cli-input"},
        tenant_id="tenant-cli",
        submitted_by="private-cli-principal",
    )
    now = datetime.now(timezone.utc)
    output = tmp_path / "workload-health.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/snapshot_job_health.py",
            "--tenant-id",
            "tenant-cli",
            "--window-start",
            (now - timedelta(minutes=1)).isoformat(),
            "--window-end",
            datetime.now(timezone.utc).isoformat(),
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
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(completed.stdout)

    assert snapshot["job_count"] == 1
    assert summary["artifact_sha256"] == snapshot["artifact_sha256"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    serialized = output.read_text(encoding="utf-8") + completed.stdout
    assert submitted.job_id not in serialized
    assert "private" not in serialized
    assert "tenant-cli" not in serialized


def test_cli_refuses_invalid_window_and_existing_output(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    PlanningJobRepository(database)
    output = tmp_path / "existing.json"
    output.write_text("owner-content", encoding="utf-8")
    base = [
        sys.executable,
        "scripts/snapshot_job_health.py",
        "--tenant-id",
        "tenant-cli",
        "--database",
        str(database),
        "--output",
        str(output),
    ]
    invalid = subprocess.run(
        [
            *base,
            "--window-start",
            "2026-07-21T00:00:00Z",
            "--window-end",
            "2026-07-20T00:00:00Z",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    existing = subprocess.run(
        [
            *base,
            "--window-start",
            "2026-07-20T00:00:00Z",
            "--window-end",
            "2026-07-21T00:00:00Z",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert invalid.returncode != 0
    assert "after its start" in invalid.stderr
    assert existing.returncode != 0
    assert output.read_text(encoding="utf-8") == "owner-content"
