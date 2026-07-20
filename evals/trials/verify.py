"""Independently recompute consent-bound trial evidence metrics."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


EXPECTED_CASES = {
    "consent_and_capability_binding",
    "participant_and_cohort_isolation",
    "withdrawal_close_and_append_only",
    "minimum_gate_and_snapshot_integrity",
    "retention_boundary",
    "retention_purge_transaction",
}
EXPECTED_POLICY = {
    "protocol_version": "bj_pal_trial_protocol_v1",
    "classification": "self_reported_unverified",
    "minimum_participants": 5,
    "enrollment": "operator_issued_single_use_capability",
    "participant_identity": "anonymous_capability_not_verified_human",
    "report_uniqueness": "trial_participant_phase",
    "withdrawal": "exclude_unfrozen_aggregate_and_block_new_evidence",
    "retention": "explicit_local_operator_purge_without_hosted_scheduler",
}


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _pop_and_verify(payload: dict, sha_key: str) -> tuple[dict, str | None]:
    canonical = deepcopy(payload)
    observed = canonical.pop(sha_key, None)
    return canonical, observed


def _verify_minimum_raw_evidence(case: dict) -> None:
    if case.get("notice_sha256") != _sha(case.get("notice")):
        raise ValueError("trial consent notice SHA-256 mismatch")
    if case.get("cohort_sha256") != _sha(case.get("cohort")):
        raise ValueError("trial cohort SHA-256 mismatch")
    enrollment_evidence = case.get("enrollment_evidence") or []
    enrollment_hashes = case.get("enrollment_sha256s") or []
    participant_evidence = case.get("participant_evidence") or []
    participant_hashes = case.get("participant_sha256s") or []
    reports = case.get("reports") or []
    if not (
        len(enrollment_evidence)
        == len(enrollment_hashes)
        == len(participant_evidence)
        == len(participant_hashes)
        == 5
    ):
        raise ValueError("trial raw participant evidence count mismatch")
    if [_sha(item) for item in enrollment_evidence] != enrollment_hashes:
        raise ValueError("trial enrollment evidence SHA-256 mismatch")
    if [_sha(item) for item in participant_evidence] != participant_hashes:
        raise ValueError("trial participant evidence SHA-256 mismatch")
    enrollment_ids = {
        item.get("enrollment_invitation_id") for item in enrollment_evidence
    }
    participant_ids = {item.get("participant_id") for item in participant_evidence}
    trial_id = case.get("cohort", {}).get("trial_id")
    notice_sha256 = case.get("notice_sha256")
    if any(
        item.get("enrollment_invitation_id") not in enrollment_ids
        or item.get("trial_id") != trial_id
        or item.get("consent_notice_sha256") != notice_sha256
        for item in participant_evidence
    ):
        raise ValueError("trial participant raw binding mismatch")
    report_hashes: list[str] = []
    participant_phases: set[tuple[str, str]] = set()
    for raw in reports:
        report, observed = _pop_and_verify(raw, "report_sha256")
        if observed != _sha(report):
            raise ValueError("trial report SHA-256 mismatch")
        if (
            report.get("version") != "plan_feedback_report_v2"
            or report.get("trial_id") != trial_id
            or report.get("participant_id") not in participant_ids
            or report.get("consent_notice_sha256") != notice_sha256
        ):
            raise ValueError("trial report raw binding mismatch")
        participant_phase = (report["participant_id"], report.get("phase"))
        if participant_phase in participant_phases:
            raise ValueError("trial participant phase duplicate in raw evidence")
        participant_phases.add(participant_phase)
        report_hashes.append(observed)
    snapshot = case.get("snapshot") or {}
    snapshot_payload, snapshot_sha256 = _pop_and_verify(snapshot, "snapshot_sha256")
    if snapshot_sha256 != _sha(snapshot_payload):
        raise ValueError("trial snapshot SHA-256 mismatch")
    expected_root = {
        "version": "trial_evidence_root_v1",
        "trial_id": trial_id,
        "cohort_sha256": case.get("cohort_sha256"),
        "cutoff_at": snapshot.get("cutoff_at"),
        "enrollment_invitation_sha256s": sorted(enrollment_hashes),
        "eligible_participant_sha256s": sorted(participant_hashes),
        "withdrawal_event_sha256s": [],
        "included_report_sha256s": sorted(report_hashes),
    }
    if case.get("evidence_root_input") != expected_root:
        raise ValueError("trial evidence-root raw input mismatch")
    if snapshot.get("evidence_root_sha256") != _sha(expected_root):
        raise ValueError("trial evidence-root SHA-256 mismatch")


def verify_trial_artifact(path: Path) -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported trial artifact schema")
    canonical = deepcopy(artifact)
    observed_sha256 = canonical.pop("artifact_sha256", None)
    if observed_sha256 != _sha(canonical):
        raise ValueError("trial artifact SHA-256 mismatch")
    if artifact.get("classification") != "synthetic_contract":
        raise ValueError("trial artifact classification mismatch")
    if artifact.get("policy") != EXPECTED_POLICY:
        raise ValueError("trial evidence policy mismatch")
    cases = (artifact.get("result") or {}).get("raw_cases") or []
    indexed = {case.get("case_id"): case for case in cases}
    if set(indexed) != EXPECTED_CASES or len(indexed) != len(cases):
        raise ValueError("trial evidence case contract mismatch")

    consent = indexed["consent_and_capability_binding"]
    if consent.get("notice_sha256") != _sha(consent.get("notice")):
        raise ValueError("consent-case notice SHA-256 mismatch")
    consent_report, consent_report_sha256 = _pop_and_verify(
        consent.get("report") or {}, "report_sha256"
    )
    if consent_report_sha256 != _sha(consent_report):
        raise ValueError("consent-case report SHA-256 mismatch")
    isolation = indexed["participant_and_cohort_isolation"]
    withdrawal = indexed["withdrawal_close_and_append_only"]
    withdrawal_event, withdrawal_event_sha256 = _pop_and_verify(
        withdrawal.get("withdrawal_event") or {}, "event_sha256"
    )
    if withdrawal_event_sha256 != _sha(withdrawal_event):
        raise ValueError("trial withdrawal event SHA-256 mismatch")
    withdrawal_snapshot, withdrawal_snapshot_sha256 = _pop_and_verify(
        withdrawal.get("snapshot") or {}, "snapshot_sha256"
    )
    if withdrawal_snapshot_sha256 != _sha(withdrawal_snapshot):
        raise ValueError("trial withdrawal snapshot SHA-256 mismatch")
    minimum = indexed["minimum_gate_and_snapshot_integrity"]
    _verify_minimum_raw_evidence(minimum)
    retention = indexed["retention_boundary"]
    purge = indexed["retention_purge_transaction"]
    purge_receipt, purge_receipt_sha256 = _pop_and_verify(
        purge.get("receipt") or {}, "receipt_sha256"
    )
    expected_deleted_counts = {
        "feedback_reports": 2,
        "feedback_invitations": 1,
        "participant_events": 0,
        "participants": 1,
        "enrollment_invitations": 1,
        "evidence_snapshots": 1,
        "cohorts": 1,
    }
    purge_receipt_valid = (
        purge_receipt_sha256 == _sha(purge_receipt)
        and purge_receipt.get("version") == "trial_retention_purge_receipt_v1"
        and str(purge_receipt.get("receipt_id", "")).startswith("trpurge-")
        and str(purge_receipt.get("trial_id", "")).startswith("trial-")
        and purge_receipt.get("tenant_sha256")
        == hashlib.sha256(b"purge").hexdigest()
        and purge_receipt.get("purged_by_sha256")
        == hashlib.sha256(b"purge-operator").hexdigest()
        and "tenant_id" not in purge_receipt
        and "purged_by" not in purge_receipt
        and purge_receipt.get("classification") == "operator_attested_unverified"
        and purge_receipt.get("secret_bundle_disposition")
        == "operator_attested_disposed"
        and purge_receipt.get("backup_disposition") == "no_managed_backups"
        and purge_receipt.get("deleted_counts") == expected_deleted_counts
        and purge_receipt.get("sqlite_deletion_controls")
        == {"journal_mode": "delete", "secure_delete": True}
    )
    minimum_snapshot, minimum_snapshot_sha256 = _pop_and_verify(
        minimum.get("snapshot") or {}, "snapshot_sha256"
    )
    metrics = {
        "case_count": len(cases),
        "exact_consent_binding_rate": float(
            consent.get("wrong_consent_error") == "trial_consent_mismatch"
            and consent.get("notice_sha256") == _sha(consent.get("notice"))
            and consent.get("participant_id")
            == consent.get("authorized_participant_id")
        ),
        "single_use_enrollment_rate": float(
            consent.get("replay_error") == "trial_enrollment_conflict"
        ),
        "capability_minimization_rate": float(
            consent.get("raw_capabilities_persisted") is False
            and consent.get("free_text_columns_present") is False
        ),
        "participant_phase_uniqueness_rate": float(
            isolation.get("duplicate_phase_error") == "feedback_phase_conflict"
        ),
        "cohort_tenant_isolation_rate": float(
            isolation.get("cross_tenant_error") == "trial_not_found"
            and (isolation.get("alpha_summary") or {})
            .get("phase_participant_counts", {})
            .get("decision")
            == 1
            and (isolation.get("beta_summary") or {})
            .get("phase_participant_counts", {})
            .get("decision")
            == 1
        ),
        "uncohorted_exclusion_rate": float(
            (isolation.get("uncohorted_summary") or {}).get("phase_counts")
            == {"decision": 0, "outcome": 0}
        ),
        "withdrawal_exclusion_rate": float(
            withdrawal.get("submit_after_withdrawal_error")
            == "trial_participant_withdrawn"
            and (withdrawal.get("summary_after_withdrawal") or {}).get(
                "included_participant_count"
            )
            == 0
            and (withdrawal.get("summary_after_withdrawal") or {}).get(
                "phase_participant_counts"
            )
            == {"decision": 0, "outcome": 0}
        ),
        "closure_fail_closed_rate": float(
            withdrawal.get("authorize_after_close_error") == "trial_closed"
        ),
        "append_only_rate": float(
            withdrawal.get("participant_append_only") is True
            and withdrawal.get("snapshot_append_only") is True
        ),
        "minimum_participant_gate_rate": float(
            (minimum.get("before") or {}).get("decision_acceptance_rate") == 0.6
            and (minimum.get("before") or {}).get("outcome_completion_rate") is None
            and (minimum.get("snapshot") or {}).get("outcome_completion_rate") == 0.8
        ),
        "snapshot_integrity_rate": float(
            minimum_snapshot_sha256 == _sha(minimum_snapshot)
            and (minimum.get("snapshot") or {}).get("evidence_root_sha256")
            == _sha(minimum.get("evidence_root_input"))
        ),
        "retention_boundary_rate": float(
            retention.get("retention_state") == "raw_purge_due"
            and retention.get("issue_after_collection_error") == "trial_not_active"
        ),
        "retention_purge_rate": float(
            purge_receipt_valid
            and purge.get("not_frozen_error") == "trial_purge_not_ready"
            and purge.get("not_due_error") == "trial_retention_not_due"
            and purge.get("before_counts") == expected_deleted_counts
            and set((purge.get("after_counts") or {}))
            == set(expected_deleted_counts)
            and all(
                value == 0 for value in (purge.get("after_counts") or {}).values()
            )
            and purge.get("receipt_count") == 1
            and purge.get("target_lookup_error") == "trial_not_found"
            and purge.get("preserved_cohort_count") == 1
            and purge.get("foreign_key_violation_count") == 0
            and purge.get("delete_triggers_restored") is True
            and purge.get("idempotent_receipt") is True
        ),
    }
    claimed = (artifact.get("result") or {}).get("metrics")
    if claimed != metrics:
        raise ValueError(f"trial evidence metrics mismatch: {claimed!r} != {metrics!r}")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("trial evidence safety gate failed")
    return artifact
