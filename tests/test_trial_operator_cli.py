from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run(
    db: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/manage_trial.py", "--db", str(db), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _create(db: Path) -> dict:
    completed = _run(
        db,
        "create",
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--duration-days",
        "7",
        "--retention-days",
        "30",
    )
    return json.loads(completed.stdout)


def test_operator_cli_keeps_secret_bundle_out_of_stdout_and_database(tmp_path: Path) -> None:
    db = tmp_path / "feedback.db"
    created = _create(db)
    trial = created["trial"]
    trial_id = trial["trial_id"]
    assert created["classification"] == "operator_metadata_not_human_evidence"
    assert created["notice"]["trial_id"] == trial_id

    output = tmp_path / "pilot.trial-invites.json"
    issued = _run(
        db,
        "issue",
        "--trial-id",
        trial_id,
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--count",
        "5",
        "--participant-url",
        "https://pilot.example.test",
        "--output",
        str(output),
        "--confirm-secret-output",
    )
    delivery = json.loads(issued.stdout)
    bundle = json.loads(output.read_text(encoding="utf-8"))
    capabilities = [
        item["enrollment_capability"] for item in bundle["invitations"]
    ]

    assert delivery["issued_count"] == 5
    assert delivery["raw_capabilities_printed"] is False
    assert delivery["bundle_sha256"] == bundle["bundle_sha256"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert len(set(capabilities)) == 5
    assert all(item.startswith("trienroll-") for item in capabilities)
    assert not any(item in issued.stdout or item in issued.stderr for item in capabilities)
    assert not any(item.encode("utf-8") in db.read_bytes() for item in capabilities)

    canonical = dict(bundle)
    observed_sha = canonical.pop("bundle_sha256")
    sys.path.insert(0, str(ROOT / "src"))
    from outcomes import sha256_json

    assert observed_sha == sha256_json(canonical)

    status_payload = json.loads(
        _run(
            db,
            "status",
            "--trial-id",
            trial_id,
            "--tenant-id",
            "pilot-tenant",
        ).stdout
    )
    assert status_payload["summary"]["issued_enrollment_count"] == 5
    assert status_payload["summary"]["enrolled_participant_count"] == 0
    assert status_payload["summary"]["decision_acceptance_rate"] is None


def test_operator_cli_requires_explicit_secret_and_close_confirmations(
    tmp_path: Path,
) -> None:
    db = tmp_path / "feedback.db"
    trial_id = _create(db)["trial"]["trial_id"]
    secret_output = tmp_path / "codes.trial-invites.json"

    missing_secret_confirmation = _run(
        db,
        "issue",
        "--trial-id",
        trial_id,
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--output",
        str(secret_output),
        check=False,
    )
    assert missing_secret_confirmation.returncode == 2
    assert not secret_output.exists()

    wrong_close_confirmation = _run(
        db,
        "close",
        "--trial-id",
        trial_id,
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--confirm-trial-id",
        "trial-wrong",
        check=False,
    )
    assert wrong_close_confirmation.returncode == 2

    insufficient_close = _run(
        db,
        "close",
        "--trial-id",
        trial_id,
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--confirm-trial-id",
        trial_id,
        check=False,
    )
    assert insufficient_close.returncode == 2
    assert "below the per-phase participant threshold" in insufficient_close.stderr

    closed = json.loads(
        _run(
            db,
            "close",
            "--trial-id",
            trial_id,
            "--tenant-id",
            "pilot-tenant",
            "--operator-id",
            "pilot-operator",
            "--confirm-trial-id",
            trial_id,
            "--allow-insufficient-evidence",
        ).stdout
    )
    assert closed["snapshot"]["status"] == "closed"
    assert closed["snapshot"]["phase_participant_counts"] == {
        "decision": 0,
        "outcome": 0,
    }
    repeated_close = json.loads(
        _run(
            db,
            "close",
            "--trial-id",
            trial_id,
            "--tenant-id",
            "pilot-tenant",
            "--operator-id",
            "pilot-operator",
            "--confirm-trial-id",
            trial_id,
        ).stdout
    )
    assert repeated_close["snapshot"]["snapshot_sha256"] == (
        closed["snapshot"]["snapshot_sha256"]
    )

    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_evidence_snapshots"
        ).fetchone()[0] == 1


def test_operator_cli_never_overwrites_a_secret_bundle(tmp_path: Path) -> None:
    db = tmp_path / "feedback.db"
    trial_id = _create(db)["trial"]["trial_id"]
    output = tmp_path / "existing.trial-invites.json"
    output.write_text("operator-owned\n", encoding="utf-8")

    refused = _run(
        db,
        "issue",
        "--trial-id",
        trial_id,
        "--tenant-id",
        "pilot-tenant",
        "--operator-id",
        "pilot-operator",
        "--output",
        str(output),
        "--confirm-secret-output",
        check=False,
    )
    assert refused.returncode == 2
    assert output.read_text(encoding="utf-8") == "operator-owned\n"
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_enrollment_invitations"
        ).fetchone()[0] == 0


def test_operator_cli_purge_requires_attestations_and_exposes_receipt_status(
    tmp_path: Path,
) -> None:
    db = tmp_path / "feedback.db"
    sys.path.insert(0, str(ROOT / "src"))
    from outcomes import PlanFeedbackRepository

    repository = PlanFeedbackRepository(db)
    trial = repository.create_trial(
        created_by="pilot-operator",
        tenant_id="pilot-tenant",
        duration_days=1,
        retention_days=1,
        now=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    repository.close_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        closed_by="pilot-operator",
        now=datetime(2020, 1, 1, 1, tzinfo=timezone.utc),
    )

    wrong_confirmation = _run(
        db,
        "purge",
        "--trial-id",
        trial.trial_id,
        "--tenant-id",
        trial.tenant_id,
        "--operator-id",
        "pilot-operator",
        "--confirm-trial-id",
        "trial-wrong",
        "--confirm-secret-bundle-disposed",
        "--backup-disposition",
        "no_managed_backups",
        check=False,
    )
    assert wrong_confirmation.returncode == 2
    assert "exactly match" in wrong_confirmation.stderr

    missing_secret_attestation = _run(
        db,
        "purge",
        "--trial-id",
        trial.trial_id,
        "--tenant-id",
        trial.tenant_id,
        "--operator-id",
        "pilot-operator",
        "--confirm-trial-id",
        trial.trial_id,
        "--backup-disposition",
        "no_managed_backups",
        check=False,
    )
    assert missing_secret_attestation.returncode == 2
    assert "confirm-secret-bundle-disposed" in missing_secret_attestation.stderr

    purged = json.loads(
        _run(
            db,
            "purge",
            "--trial-id",
            trial.trial_id,
            "--tenant-id",
            trial.tenant_id,
            "--operator-id",
            "pilot-operator",
            "--confirm-trial-id",
            trial.trial_id,
            "--confirm-secret-bundle-disposed",
            "--backup-disposition",
            "no_managed_backups",
        ).stdout
    )
    assert purged["status"] == "purged"
    assert purged["receipt"]["classification"] == "operator_attested_unverified"
    assert "tenant_id" not in purged["receipt"]
    assert "purged_by" not in purged["receipt"]
    assert purged["receipt"]["deleted_counts"] == {
        "cohorts": 1,
        "enrollment_invitations": 0,
        "evidence_snapshots": 1,
        "feedback_invitations": 0,
        "feedback_reports": 0,
        "participant_events": 0,
        "participants": 0,
    }

    status_payload = json.loads(
        _run(
            db,
            "status",
            "--trial-id",
            trial.trial_id,
            "--tenant-id",
            trial.tenant_id,
        ).stdout
    )
    assert status_payload["status"] == "purged"
    assert status_payload["purge_receipt"] == purged["receipt"]
    repeated = json.loads(
        _run(
            db,
            "purge",
            "--trial-id",
            trial.trial_id,
            "--tenant-id",
            trial.tenant_id,
            "--operator-id",
            "pilot-operator",
            "--confirm-trial-id",
            trial.trial_id,
            "--confirm-secret-bundle-disposed",
            "--backup-disposition",
            "operator_attested_backups_purged",
        ).stdout
    )
    assert repeated["receipt"] == purged["receipt"]

    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_cohorts WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM trial_purge_receipts WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 1
