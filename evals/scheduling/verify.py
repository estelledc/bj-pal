"""Independently recompute durable scheduling order and claim evidence."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


POLICY_VERSION = "tenant_fair_priority_aging_v2"
PRIORITY_POLICY_VERSION = "priority_aging_v1"
TENANT_FAIRNESS_POLICY_VERSION = "least_recently_served_tenant_v1"
MIN_PRIORITY = 0
MAX_PRIORITY = 9
AGING_SECONDS = 60


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("scheduling timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _wait_ms(eligible_at: str, claimed_at: str) -> int:
    wait = _parse_timestamp(claimed_at) - _parse_timestamp(eligible_at)
    return max(
        0,
        wait.days * 86_400_000 + wait.seconds * 1000 + wait.microseconds // 1000,
    )


def _effective_priority(priority: int, wait_ms: int) -> int:
    return min(MAX_PRIORITY, priority + wait_ms // (AGING_SECONDS * 1000))


def verify_scheduling_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported scheduling artifact schema")
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _canonical_sha256(canonical):
        raise ValueError("scheduling artifact SHA-256 mismatch")
    if artifact.get("policy") != {
        "version": POLICY_VERSION,
        "priority_policy": PRIORITY_POLICY_VERSION,
        "tenant_fairness_policy": TENANT_FAIRNESS_POLICY_VERSION,
        "minimum_priority": MIN_PRIORITY,
        "maximum_priority": MAX_PRIORITY,
        "aging_seconds": AGING_SECONDS,
        "tie_breakers": [
            "effective_priority_desc",
            "tenant_last_claimed_event_id_asc",
            "eligible_at_asc",
            "created_at_asc",
            "job_id_asc",
        ],
    }:
        raise ValueError("scheduling policy metadata mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if not raw_cases or len({case.get("case_id") for case in raw_cases}) != len(raw_cases):
        raise ValueError("scheduling cases must be non-empty and uniquely identified")
    observed_contract = {
        case.get("case_id"): case.get("assertion") for case in raw_cases
    }
    if observed_contract != {
        "priority_preemption": "priority_ordering",
        "starvation_prevention": "starvation_prevention",
        "backoff_exclusion": "backoff_exclusion",
        "tenant_fairness": "tenant_fairness",
    }:
        raise ValueError("scheduling case contract mismatch")
    outcomes = [_verify_case(case) for case in raw_cases]
    metrics = {
        "case_count": len(outcomes),
        "ordering_accuracy_rate": _rate(outcomes, "ordering_valid"),
        "effective_priority_evidence_rate": _rate(outcomes, "effective_priority_valid"),
        "queue_wait_evidence_rate": _rate(outcomes, "queue_wait_valid"),
        "starvation_case_pass_rate": _applicable_rate(outcomes, "starvation_valid"),
        "backoff_exclusion_pass_rate": _applicable_rate(outcomes, "backoff_valid"),
        "tenant_fairness_pass_rate": _applicable_rate(outcomes, "tenant_fairness_valid"),
    }
    if result.get("metrics") != metrics:
        raise ValueError("scheduling metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("scheduling contract gate did not pass")
    return artifact


def _verify_case(case: dict[str, Any]) -> dict[str, bool | None]:
    claimed_at = str(case.get("claimed_at") or "")
    _parse_timestamp(claimed_at)
    candidates = case.get("candidates") or []
    candidate_ids = [candidate.get("job_id") for candidate in candidates]
    if not candidates or None in candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("scheduling candidates must be non-empty and uniquely identified")

    computed: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("status") != "queued":
            raise ValueError("scheduling candidate must be queued before claim")
        priority = candidate.get("base_priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("scheduling priority must be an integer")
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise ValueError("scheduling priority is out of range")
        tenant_id = candidate.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("scheduling candidate tenant is invalid")
        tenant_last_claimed = candidate.get("tenant_last_claimed_event_id_before")
        if (
            isinstance(tenant_last_claimed, bool)
            or not isinstance(tenant_last_claimed, int)
            or tenant_last_claimed < 0
        ):
            raise ValueError("scheduling tenant claim cursor is invalid")
        eligible_at = str(candidate.get("eligible_at") or "")
        created_at = str(candidate.get("created_at") or "")
        wait_ms = _wait_ms(eligible_at, claimed_at)
        eligible = _parse_timestamp(eligible_at) <= _parse_timestamp(claimed_at)
        deadline_at = candidate.get("deadline_at")
        before_deadline = deadline_at is None or _parse_timestamp(deadline_at) > _parse_timestamp(
            claimed_at
        )
        computed.append(
            {
                **candidate,
                "eligible": eligible and before_deadline,
                "wait_ms": wait_ms,
                "effective_priority": _effective_priority(priority, wait_ms),
                "eligible_at_value": _parse_timestamp(eligible_at),
                "created_at_value": _parse_timestamp(created_at),
            }
        )
    eligible_candidates = [candidate for candidate in computed if candidate["eligible"]]
    if not eligible_candidates:
        raise ValueError("scheduling case has no eligible candidate")
    ranked = sorted(
        eligible_candidates,
        key=lambda candidate: (
            -candidate["effective_priority"],
            candidate["tenant_last_claimed_event_id_before"],
            candidate["eligible_at_value"],
            candidate["created_at_value"],
            candidate["job_id"],
        ),
    )
    selected = ranked[0]
    if case.get("expected_job_id") != selected["job_id"]:
        raise ValueError("scheduling expected selection does not match candidate evidence")

    event = case.get("claim_event") or {}
    payload = event.get("payload") or {}
    observed_job_id = case.get("observed_job_id")
    ordering_valid = (
        observed_job_id == selected["job_id"]
        and event.get("job_id") == selected["job_id"]
        and event.get("event_type") in {"claimed", "lease_reclaimed"}
        and event.get("created_at") == claimed_at
    )
    effective_priority_valid = (
        payload.get("scheduling_policy") == POLICY_VERSION
        and payload.get("priority_policy") == PRIORITY_POLICY_VERSION
        and payload.get("tenant_fairness_policy") == TENANT_FAIRNESS_POLICY_VERSION
        and payload.get("tenant_id") == selected["tenant_id"]
        and payload.get("tenant_last_claimed_event_id_before")
        == selected["tenant_last_claimed_event_id_before"]
        and payload.get("base_priority") == selected["base_priority"]
        and payload.get("effective_priority") == selected["effective_priority"]
        and payload.get("priority_aging_seconds") == AGING_SECONDS
    )
    queue_wait_valid = (
        payload.get("eligible_at") == selected["eligible_at"]
        and payload.get("queue_wait_ms") == selected["wait_ms"]
    )

    assertion = case.get("assertion")
    starvation_valid: bool | None = None
    if assertion == "starvation_prevention":
        competitors = [candidate for candidate in eligible_candidates if candidate is not selected]
        starvation_valid = (
            selected["base_priority"] == MIN_PRIORITY
            and selected["effective_priority"] == MAX_PRIORITY
            and any(
                competitor["base_priority"] == MAX_PRIORITY
                and competitor["effective_priority"] == MAX_PRIORITY
                and competitor["eligible_at_value"] > selected["eligible_at_value"]
                for competitor in competitors
            )
            and ordering_valid
        )

    backoff_valid: bool | None = None
    if assertion == "backoff_exclusion":
        delayed = [
            candidate
            for candidate in computed
            if not candidate["eligible"]
            and candidate.get("attempt", 0) >= 1
            and (candidate.get("last_event") or {}).get("event_type") == "retry_scheduled"
        ]
        backoff_valid = bool(delayed) and all(
            candidate["base_priority"] > selected["base_priority"]
            and (candidate.get("last_event") or {}).get("payload", {}).get("available_at")
            == candidate["eligible_at"]
            and (candidate.get("last_event") or {}).get("payload", {}).get(
                "backoff_seconds", 0
            )
            > 0
            for candidate in delayed
        ) and ordering_valid

    tenant_fairness_valid: bool | None = None
    if assertion == "tenant_fairness":
        competitors = [
            candidate
            for candidate in eligible_candidates
            if candidate is not selected
            and candidate["effective_priority"] == selected["effective_priority"]
        ]
        tenant_fairness_valid = (
            bool(competitors)
            and all(
                selected["tenant_last_claimed_event_id_before"]
                < competitor["tenant_last_claimed_event_id_before"]
                for competitor in competitors
            )
            and any(
                selected["eligible_at_value"] > competitor["eligible_at_value"]
                for competitor in competitors
            )
            and ordering_valid
        )

    return {
        "ordering_valid": ordering_valid,
        "effective_priority_valid": effective_priority_valid,
        "queue_wait_valid": queue_wait_valid,
        "starvation_valid": starvation_valid,
        "backoff_valid": backoff_valid,
        "tenant_fairness_valid": tenant_fairness_valid,
    }


def _rate(outcomes: list[dict[str, bool | None]], key: str) -> float:
    return round(sum(bool(outcome[key]) for outcome in outcomes) / len(outcomes), 3)


def _applicable_rate(outcomes: list[dict[str, bool | None]], key: str) -> float:
    applicable = [outcome[key] for outcome in outcomes if outcome[key] is not None]
    if not applicable:
        raise ValueError(f"scheduling metric has no applicable cases: {key}")
    return round(sum(bool(value) for value in applicable) / len(applicable), 3)
