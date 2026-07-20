#!/usr/bin/env python3
"""Operate a bounded BJ-Pal human trial without weakening evidence boundaries."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from outcomes import (  # noqa: E402
    PlanFeedbackRepository,
    TRIAL_BACKUP_DISPOSITIONS,
    TrialClosed,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialPurgeNotReady,
    TrialRetentionNotDue,
    sha256_json,
)


CLI_VERSION = "trial_operator_cli_v2"
BUNDLE_VERSION = "trial_enrollment_bundle_v1"


class OperatorSafetyError(ValueError):
    """The requested operator action needs a stronger explicit confirmation."""


def _json_dump(payload: dict[str, Any], *, stream=sys.stdout) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=stream,
    )


def _repository(path: Path | None) -> PlanFeedbackRepository:
    return PlanFeedbackRepository(path) if path is not None else PlanFeedbackRepository()


def _validate_participant_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("participant_url must be an absolute http(s) URL")
    return value.rstrip("/")


def _reserve_secret_output(path: Path) -> int:
    if not path.parent.exists() or not path.parent.is_dir():
        raise ValueError("secret bundle parent directory does not exist")
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def _write_secret_bundle(fd: int, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _trial_metadata(trial) -> dict[str, Any]:
    return {
        "trial_id": trial.trial_id,
        "tenant_id": trial.tenant_id,
        "status": trial.status,
        "purpose": trial.purpose,
        "minimum_participants": trial.minimum_participants,
        "starts_at": trial.starts_at,
        "ends_at": trial.ends_at,
        "retention_until": trial.retention_until,
        "consent_notice_sha256": trial.consent_notice_sha256,
        "cohort_sha256": trial.cohort_sha256,
    }


def _create(args: argparse.Namespace, repository: PlanFeedbackRepository) -> dict[str, Any]:
    trial = repository.create_trial(
        created_by=args.operator_id,
        tenant_id=args.tenant_id,
        duration_days=args.duration_days,
        retention_days=args.retention_days,
        minimum_participants=args.minimum_participants,
    )
    return {
        "version": CLI_VERSION,
        "command": "create",
        "classification": "operator_metadata_not_human_evidence",
        "trial": _trial_metadata(trial),
        "notice": trial.consent_notice,
        "next_action": "review the exact notice, then issue one code per intended participant",
    }


def _issue(args: argparse.Namespace, repository: PlanFeedbackRepository) -> dict[str, Any]:
    if not args.confirm_secret_output:
        raise OperatorSafetyError(
            "issue requires --confirm-secret-output because the bundle contains raw one-time capabilities"
        )
    participant_url = _validate_participant_url(args.participant_url)
    output = args.output.expanduser()
    fd = _reserve_secret_output(output)
    persisted = False
    try:
        trial = repository.get_trial(args.trial_id, tenant_id=args.tenant_id)
        invitations = repository.issue_trial_enrollments(
            trial_id=args.trial_id,
            tenant_id=args.tenant_id,
            issued_by=args.operator_id,
            count=args.count,
            ttl_seconds=args.ttl_hours * 60 * 60,
        )
        persisted = True
        bundle_body = {
            "version": BUNDLE_VERSION,
            "classification": "sensitive_operator_handoff_not_evidence",
            "contains_raw_capabilities": True,
            "warning": (
                "Distribute exactly one code to each intended participant. "
                "Do not commit, upload, or attach this file to evaluation artifacts; "
                "delete the operator copy after distribution or expiry under your trial policy."
            ),
            "trial": _trial_metadata(trial),
            "participant_url": participant_url,
            "participant_instructions": [
                "Open the participant UI and read the exact consent notice.",
                "Enter only your assigned one-time enrollment capability.",
                "Keep the participant capability in the current session; do not send it to the operator.",
            ],
            "invitations": [
                {
                    "sequence": index,
                    "enrollment_invitation_id": invitation.enrollment_invitation_id,
                    "enrollment_capability": invitation.capability,
                    "expires_at": invitation.expires_at,
                    "invitation_sha256": invitation.invitation_sha256,
                }
                for index, invitation in enumerate(invitations, start=1)
            ],
        }
        bundle = {
            **bundle_body,
            "bundle_sha256": sha256_json(bundle_body),
        }
        _write_secret_bundle(fd, bundle)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if not persisted:
            output.unlink(missing_ok=True)
        raise
    return {
        "version": CLI_VERSION,
        "command": "issue",
        "classification": "operator_secret_delivery_metadata",
        "trial_id": args.trial_id,
        "issued_count": len(invitations),
        "bundle_sha256": bundle["bundle_sha256"],
        "secret_output": str(output),
        "secret_output_mode": f"{stat.S_IMODE(output.stat().st_mode):04o}",
        "raw_capabilities_printed": False,
    }


def _status(args: argparse.Namespace, repository: PlanFeedbackRepository) -> dict[str, Any]:
    try:
        trial = repository.get_trial(args.trial_id, tenant_id=args.tenant_id)
    except TrialNotFound:
        receipt = repository.get_trial_purge_receipt(
            args.trial_id,
            tenant_id=args.tenant_id,
        )
        return {
            "version": CLI_VERSION,
            "command": "status",
            "classification": "operator_attested_unverified",
            "status": "purged",
            "purge_receipt": receipt.to_dict(),
        }
    return {
        "version": CLI_VERSION,
        "command": "status",
        "classification": "self_reported_unverified",
        "trial": _trial_metadata(trial),
        "summary": repository.trial_summary(
            trial_id=args.trial_id,
            tenant_id=args.tenant_id,
        ),
    }


def _close(args: argparse.Namespace, repository: PlanFeedbackRepository) -> dict[str, Any]:
    if args.confirm_trial_id != args.trial_id:
        raise OperatorSafetyError(
            "close requires --confirm-trial-id to exactly match --trial-id"
        )
    summary = repository.trial_summary(
        trial_id=args.trial_id,
        tenant_id=args.tenant_id,
    )
    minimum = int(summary["minimum_participants"])
    phase_counts = summary["phase_participant_counts"]
    insufficient = summary["status"] != "closed" and any(
        int(phase_counts[phase]) < minimum for phase in ("decision", "outcome")
    )
    if insufficient and not args.allow_insufficient_evidence:
        raise OperatorSafetyError(
            "trial evidence is below the per-phase participant threshold; "
            "pass --allow-insufficient-evidence only when early closure is intentional"
        )
    snapshot = repository.close_trial(
        trial_id=args.trial_id,
        tenant_id=args.tenant_id,
        closed_by=args.operator_id,
    ).to_dict()
    return {
        "version": CLI_VERSION,
        "command": "close",
        "classification": "self_reported_unverified",
        "snapshot": snapshot,
    }


def _purge(args: argparse.Namespace, repository: PlanFeedbackRepository) -> dict[str, Any]:
    if args.confirm_trial_id != args.trial_id:
        raise OperatorSafetyError(
            "purge requires --confirm-trial-id to exactly match --trial-id"
        )
    if not args.confirm_secret_bundle_disposed:
        raise OperatorSafetyError(
            "purge requires --confirm-secret-bundle-disposed after operator copies are removed or expired"
        )
    receipt = repository.purge_trial(
        trial_id=args.trial_id,
        tenant_id=args.tenant_id,
        purged_by=args.operator_id,
        secret_bundle_disposed=True,
        backup_disposition=args.backup_disposition,
    ).to_dict()
    return {
        "version": CLI_VERSION,
        "command": "purge",
        "classification": "operator_attested_unverified",
        "status": "purged",
        "receipt": receipt,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create, operate, inspect, freeze, and explicitly purge a BJ-Pal trial cohort."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Explicit evidence SQLite path; otherwise BJ_PAL_FEEDBACK_DB/default runtime path is used.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create an append-only trial cohort.")
    create.add_argument("--tenant-id", required=True)
    create.add_argument("--operator-id", required=True)
    create.add_argument("--duration-days", type=int, default=14)
    create.add_argument("--retention-days", type=int, default=90)
    create.add_argument("--minimum-participants", type=int, default=5)

    issue = subparsers.add_parser(
        "issue", help="Issue a bounded batch into a new mode-0600 secret bundle."
    )
    issue.add_argument("--trial-id", required=True)
    issue.add_argument("--tenant-id", required=True)
    issue.add_argument("--operator-id", required=True)
    issue.add_argument("--count", type=int, default=5)
    issue.add_argument("--ttl-hours", type=int, default=168)
    issue.add_argument("--participant-url")
    issue.add_argument("--output", type=Path, required=True)
    issue.add_argument("--confirm-secret-output", action="store_true")

    status = subparsers.add_parser("status", help="Read the current gated aggregate.")
    status.add_argument("--trial-id", required=True)
    status.add_argument("--tenant-id", required=True)

    close = subparsers.add_parser(
        "close", help="Irreversibly freeze a cutoff-bound evidence snapshot."
    )
    close.add_argument("--trial-id", required=True)
    close.add_argument("--tenant-id", required=True)
    close.add_argument("--operator-id", required=True)
    close.add_argument("--confirm-trial-id", required=True)
    close.add_argument("--allow-insufficient-evidence", action="store_true")

    purge = subparsers.add_parser(
        "purge",
        help="Atomically delete one frozen retention-due cohort and retain a receipt.",
    )
    purge.add_argument("--trial-id", required=True)
    purge.add_argument("--tenant-id", required=True)
    purge.add_argument("--operator-id", required=True)
    purge.add_argument("--confirm-trial-id", required=True)
    purge.add_argument("--confirm-secret-bundle-disposed", action="store_true")
    purge.add_argument(
        "--backup-disposition",
        required=True,
        choices=sorted(TRIAL_BACKUP_DISPOSITIONS),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = _repository(args.db)
    handlers = {
        "create": _create,
        "issue": _issue,
        "status": _status,
        "close": _close,
        "purge": _purge,
    }
    try:
        payload = handlers[args.command](args, repository)
    except (
        FileExistsError,
        OperatorSafetyError,
        TrialClosed,
        TrialIntegrityError,
        TrialNotActive,
        TrialNotFound,
        TrialPurgeNotReady,
        TrialRetentionNotDue,
        OSError,
        ValueError,
    ) as exc:
        _json_dump(
            {
                "version": CLI_VERSION,
                "command": args.command,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
            },
            stream=sys.stderr,
        )
        return 2
    _json_dump(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
