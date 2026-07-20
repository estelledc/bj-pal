"""Independent integrity and metric verification for requirement artifacts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .evaluate import VALID_STATUSES, load_golden_set, recompute_metrics


def canonical_artifact_sha256(payload: dict) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_requirement_artifact(artifact_path: Path, golden_path: Path) -> dict:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported requirement artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("requirement artifact SHA-256 mismatch")

    golden = load_golden_set(golden_path)
    recorded_golden = artifact.get("golden_set") or {}
    if recorded_golden.get("sha256") != golden.sha256:
        raise ValueError("requirement golden-set SHA-256 mismatch")
    if recorded_golden.get("case_count") != len(golden.cases):
        raise ValueError("requirement golden-set case count mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if result.get("case_count") != len(golden.cases) or len(raw_cases) != len(golden.cases):
        raise ValueError("requirement result case count mismatch")
    by_id = {item.get("case_id"): item for item in raw_cases}
    if len(by_id) != len(raw_cases):
        raise ValueError("requirement raw case IDs must be unique")
    for case in golden.cases:
        observed = by_id.get(case.case_id)
        if observed is None:
            raise ValueError(f"missing requirement raw case: {case.case_id}")
        if observed.get("expected_status") != case.expected_status:
            raise ValueError(f"requirement expected status mismatch: {case.case_id}")
        expected_clarification = case.expected_status == "clarification_required"
        if observed.get("expected_clarification") is not expected_clarification:
            raise ValueError(f"requirement clarification label mismatch: {case.case_id}")
        observed_status = observed.get("observed_status")
        if observed_status not in VALID_STATUSES:
            raise ValueError(f"invalid observed requirement status: {case.case_id}")
        if observed.get("clarification_triggered") is not (
            observed_status == "clarification_required"
        ):
            raise ValueError(f"requirement trigger mismatch: {case.case_id}")
        if observed.get("correct") is not (observed_status == case.expected_status):
            raise ValueError(f"requirement correctness mismatch: {case.case_id}")
        if case.follow_up is None:
            if observed.get("follow_up_status") is not None:
                raise ValueError(f"unexpected requirement follow-up: {case.case_id}")
        else:
            follow_up_status = observed.get("follow_up_status")
            if follow_up_status not in VALID_STATUSES:
                raise ValueError(f"invalid requirement follow-up status: {case.case_id}")
            expected_executable = follow_up_status != "clarification_required"
            if observed.get("post_clarification_executable") is not expected_executable:
                raise ValueError(f"requirement follow-up executability mismatch: {case.case_id}")

    recomputed = recompute_metrics(raw_cases)
    if result.get("metrics") != recomputed:
        raise ValueError("requirement metrics do not match raw cases")
    return artifact
