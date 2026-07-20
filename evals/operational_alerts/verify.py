"""Independent verifier for operational alert contract artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


POLICY = {
    "version": "portfolio_operational_alert_policy_v1",
    "minimum_terminal_jobs": 20,
    "terminal_failure_rate_threshold": 0.2,
    "minimum_queue_wait_samples": 20,
    "queue_wait_p95_ms_threshold": 30_000.0,
    "minimum_jobs": 20,
    "retry_job_rate_threshold": 0.3,
    "trace_backend": "otlp",
}
RULE_ORDER = (
    "terminal_failure_rate",
    "queue_wait_p95_ms",
    "retry_job_rate",
    "trace_export_health",
)
FIXED_CASES = {
    "healthy_enough_samples": {
        "overall_state": "healthy",
        "rule_states": {
            "terminal_failure_rate": "healthy",
            "queue_wait_p95_ms": "healthy",
            "retry_job_rate": "healthy",
            "trace_export_health": "healthy",
        },
    },
    "all_thresholds_firing": {
        "overall_state": "firing",
        "rule_states": {
            "terminal_failure_rate": "firing",
            "queue_wait_p95_ms": "firing",
            "retry_job_rate": "firing",
            "trace_export_health": "firing",
        },
    },
    "minimum_samples_not_met": {
        "overall_state": "insufficient_data",
        "rule_states": {
            "terminal_failure_rate": "insufficient_data",
            "queue_wait_p95_ms": "insufficient_data",
            "retry_job_rate": "insufficient_data",
            "trace_export_health": "insufficient_data",
        },
    },
    "otlp_not_configured": {
        "overall_state": "healthy",
        "rule_states": {
            "terminal_failure_rate": "healthy",
            "queue_wait_p95_ms": "healthy",
            "retry_job_rate": "healthy",
            "trace_export_health": "disabled",
        },
    },
}
FORBIDDEN_KEYS = {
    "tenant_id",
    "principal_id",
    "request_id",
    "job_id",
    "worker_id",
    "endpoint_url",
    "headers",
    "prompt",
    "content",
    "tool_arguments",
    "user_id",
    "location",
    "error_message",
}
FORBIDDEN_MARKERS = (
    "private-marker",
    "authorization:",
    "bearer ",
    "sk-",
    "api_key",
    "http://",
    "https://",
)


def _canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_artifact_sha256(artifact: Mapping[str, Any]) -> str:
    unsigned = dict(artifact)
    unsigned.pop("artifact_sha256", None)
    return _canonical_sha256(unsigned)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _require_digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _scan_privacy(value: Any, path: str = "artifact") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden key at {path}.{key}")
            _scan_privacy(nested, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_privacy(nested, f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_MARKERS):
            raise ValueError(f"private marker at {path}")


def _verify_workload(payload: Mapping[str, Any]) -> None:
    if payload.get("version") != "durable_workload_health_v1":
        raise ValueError("workload version mismatch")
    unsigned = dict(payload)
    observed_sha = _require_digest(
        unsigned.pop("artifact_sha256", None), "workload artifact_sha256"
    )
    if observed_sha != _canonical_sha256(unsigned):
        raise ValueError("workload artifact_sha256 mismatch")
    _require_digest(payload.get("evidence_sha256"), "workload evidence_sha256")
    status_counts = payload.get("status_counts")
    expected_statuses = {
        "queued",
        "running",
        "succeeded",
        "failed",
        "dead_lettered",
        "cancelled",
        "timed_out",
    }
    if not isinstance(status_counts, Mapping) or set(status_counts) != expected_statuses:
        raise ValueError("workload status counts schema mismatch")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in status_counts.values()
    ):
        raise ValueError("workload status counts must be non-negative integers")
    job_count = payload.get("job_count")
    terminal_count = payload.get("terminal_job_count")
    active_count = payload.get("active_job_count")
    retry_count = payload.get("retry_job_count")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (job_count, terminal_count, active_count, retry_count)
    ):
        raise ValueError("workload counts must be non-negative integers")
    derived_terminal = sum(
        status_counts[key]
        for key in ("succeeded", "failed", "dead_lettered", "cancelled", "timed_out")
    )
    derived_failure = sum(
        status_counts[key] for key in ("failed", "dead_lettered", "timed_out")
    )
    if job_count != sum(status_counts.values()):
        raise ValueError("workload job_count mismatch")
    if terminal_count != derived_terminal:
        raise ValueError("workload terminal_job_count mismatch")
    if active_count != status_counts["queued"] + status_counts["running"]:
        raise ValueError("workload active_job_count mismatch")
    if retry_count > job_count:
        raise ValueError("workload retry_job_count exceeds jobs")
    expected_rates = {
        "terminal_success_rate": _rate(status_counts["succeeded"], terminal_count),
        "terminal_failure_rate": _rate(derived_failure, terminal_count),
        "dead_letter_rate": _rate(status_counts["dead_lettered"], terminal_count),
        "timeout_rate": _rate(status_counts["timed_out"], terminal_count),
        "cancellation_rate": _rate(status_counts["cancelled"], terminal_count),
        "retry_job_rate": _rate(retry_count, job_count),
        "lease_recovery_job_rate": _rate(
            int(payload.get("lease_recovery_job_count", -1)), job_count
        ),
    }
    for key, expected in expected_rates.items():
        if payload.get(key) != expected:
            raise ValueError(f"workload {key} mismatch")
    queue = payload.get("queue_wait_ms")
    if not isinstance(queue, Mapping):
        raise ValueError("workload queue_wait_ms is missing")
    if queue.get("quantile_method") != "nearest_rank":
        raise ValueError("workload queue quantile method mismatch")
    sample_count = queue.get("sample_count")
    if not isinstance(sample_count, int) or sample_count < 0 or sample_count > job_count:
        raise ValueError("workload queue sample_count mismatch")
    p95 = queue.get("p95_ms")
    if sample_count == 0 and p95 is not None:
        raise ValueError("workload empty queue p95 must be null")
    if sample_count > 0 and (
        not isinstance(p95, (int, float))
        or isinstance(p95, bool)
        or not math.isfinite(float(p95))
        or p95 < 0
    ):
        raise ValueError("workload queue p95 is invalid")


def _verify_trace(payload: Mapping[str, Any]) -> None:
    expected_keys = {
        "version",
        "backend",
        "state",
        "processor",
        "privacy_policy",
        "semconv_profile",
        "content_capture_enabled",
        "endpoint_origin_sha256",
        "export_attempt_count",
        "exported_span_count",
        "failed_span_count",
        "dropped_attribute_count",
        "last_error_code",
    }
    if set(payload) != expected_keys:
        raise ValueError("trace status schema mismatch")
    if payload.get("version") != "trace_export_status_v1":
        raise ValueError("trace status version mismatch")
    if payload.get("privacy_policy") != "trace_export_minimal_v1":
        raise ValueError("trace privacy policy mismatch")
    if payload.get("content_capture_enabled") is not False:
        raise ValueError("trace content capture must remain disabled")
    if payload.get("backend") not in {"off", "jsonl", "otlp", "invalid"}:
        raise ValueError("trace backend mismatch")
    if payload.get("state") not in {
        "disabled",
        "configured_unproven",
        "healthy",
        "degraded",
    }:
        raise ValueError("trace state mismatch")
    if payload.get("backend") == "invalid" and payload.get("state") != "degraded":
        raise ValueError("invalid trace configuration state mismatch")
    if payload.get("backend") == "off" and payload.get("state") != "disabled":
        raise ValueError("disabled trace backend state mismatch")
    for key in (
        "export_attempt_count",
        "exported_span_count",
        "failed_span_count",
        "dropped_attribute_count",
    ):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"trace {key} mismatch")
    endpoint_digest = payload.get("endpoint_origin_sha256")
    if endpoint_digest is not None:
        _require_digest(endpoint_digest, "trace endpoint digest")
    if payload.get("backend") == "otlp" and endpoint_digest is None:
        raise ValueError("configured OTLP endpoint digest missing")


def _numeric_rule(
    *,
    rule_id: str,
    signal: str,
    severity: str,
    value: float | None,
    threshold: float,
    sample_count: int,
    required_sample_count: int,
) -> dict[str, Any]:
    if sample_count < required_sample_count or value is None:
        state = "insufficient_data"
        reason = "minimum_sample_not_met"
    elif value >= threshold:
        state = "firing"
        reason = "threshold_breached"
    else:
        state = "healthy"
        reason = "within_threshold"
    return {
        "rule_id": rule_id,
        "signal": signal,
        "state": state,
        "severity": severity,
        "observed_value": value,
        "threshold_value": threshold,
        "comparison": "gte",
        "sample_count": sample_count,
        "required_sample_count": required_sample_count,
        "reason_code": reason,
    }


def _trace_rule(trace: Mapping[str, Any]) -> dict[str, Any]:
    backend = trace["backend"]
    state = trace["state"]
    attempts = trace["export_attempt_count"]
    if backend == "invalid":
        rule_state = "firing"
        reason = "trace_export_invalid_configuration"
    elif backend != "otlp":
        rule_state = "disabled"
        reason = "otlp_export_not_configured"
    elif state == "configured_unproven" or attempts == 0:
        rule_state = "insufficient_data"
        reason = "export_attempt_not_observed"
    elif state == "degraded":
        rule_state = "firing"
        reason = "trace_export_degraded"
    elif state == "healthy":
        rule_state = "healthy"
        reason = "trace_export_healthy"
    else:
        raise ValueError("configured OTLP trace state is inconsistent")
    return {
        "rule_id": "trace_export_health",
        "signal": "otlp_trace_export_state",
        "state": rule_state,
        "severity": "critical",
        "observed_value": state if backend == "otlp" else backend,
        "threshold_value": "healthy" if backend in {"otlp", "invalid"} else "otlp",
        "comparison": "state",
        "sample_count": attempts,
        "required_sample_count": 1,
        "reason_code": reason,
    }


def _expected_rules(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    workload = source["workload"]
    trace = source["trace_status"]
    queue = workload["queue_wait_ms"]
    return [
        _numeric_rule(
            rule_id="terminal_failure_rate",
            signal="durable_job_terminal_failure_rate",
            severity="critical",
            value=workload["terminal_failure_rate"],
            threshold=POLICY["terminal_failure_rate_threshold"],
            sample_count=workload["terminal_job_count"],
            required_sample_count=POLICY["minimum_terminal_jobs"],
        ),
        _numeric_rule(
            rule_id="queue_wait_p95_ms",
            signal="durable_job_queue_wait_p95_ms",
            severity="warning",
            value=queue["p95_ms"],
            threshold=POLICY["queue_wait_p95_ms_threshold"],
            sample_count=queue["sample_count"],
            required_sample_count=POLICY["minimum_queue_wait_samples"],
        ),
        _numeric_rule(
            rule_id="retry_job_rate",
            signal="durable_job_retry_rate",
            severity="warning",
            value=workload["retry_job_rate"],
            threshold=POLICY["retry_job_rate_threshold"],
            sample_count=workload["job_count"],
            required_sample_count=POLICY["minimum_jobs"],
        ),
        _trace_rule(trace),
    ]


def _overall_state(rules: list[Mapping[str, Any]]) -> str:
    states = {rule["state"] for rule in rules}
    if "firing" in states:
        return "firing"
    if "insufficient_data" in states:
        return "insufficient_data"
    if "healthy" in states:
        return "healthy"
    return "disabled"


def _verify_case(case: Mapping[str, Any]) -> dict[str, bool]:
    case_id = case.get("case_id")
    if case_id not in FIXED_CASES:
        raise ValueError(f"unexpected operational alert case: {case_id}")
    if case.get("classification") != "authored_synthetic_operational_fixture":
        raise ValueError(f"{case_id}: classification mismatch")
    if case.get("expected") != FIXED_CASES[case_id]:
        raise ValueError(f"{case_id}: authored expectation drift")
    source = case.get("source")
    observed = case.get("observed")
    if not isinstance(source, Mapping) or not isinstance(observed, Mapping):
        raise ValueError(f"{case_id}: source or observed snapshot missing")
    workload = source.get("workload")
    trace = source.get("trace_status")
    if not isinstance(workload, Mapping) or not isinstance(trace, Mapping):
        raise ValueError(f"{case_id}: source schemas missing")
    _verify_workload(workload)
    _verify_trace(trace)
    expected_rules = _expected_rules(source)
    if observed.get("version") != "operational_alert_snapshot_v1":
        raise ValueError(f"{case_id}: snapshot version mismatch")
    if observed.get("policy") != POLICY:
        raise ValueError(f"{case_id}: policy drift")
    if observed.get("rules") != expected_rules:
        raise ValueError(f"{case_id}: rule decision mismatch")
    if [rule["rule_id"] for rule in expected_rules] != list(RULE_ORDER):
        raise ValueError(f"{case_id}: rule ordering mismatch")
    overall_state = _overall_state(expected_rules)
    if observed.get("overall_state") != overall_state:
        raise ValueError(f"{case_id}: overall state mismatch")
    expected_counts = {
        "firing_rule_count": sum(rule["state"] == "firing" for rule in expected_rules),
        "evaluated_rule_count": sum(
            rule["state"] in {"firing", "healthy"} for rule in expected_rules
        ),
        "insufficient_data_rule_count": sum(
            rule["state"] == "insufficient_data" for rule in expected_rules
        ),
        "disabled_rule_count": sum(
            rule["state"] == "disabled" for rule in expected_rules
        ),
    }
    for key, expected in expected_counts.items():
        if observed.get(key) != expected:
            raise ValueError(f"{case_id}: {key} mismatch")
    if observed.get("window_start") != workload.get("window_start") or observed.get(
        "window_end"
    ) != workload.get("window_end"):
        raise ValueError(f"{case_id}: workload window binding mismatch")
    if observed.get("observed_at") != source.get("observed_at"):
        raise ValueError(f"{case_id}: observation time binding mismatch")
    if observed.get("workload_artifact_sha256") != workload.get("artifact_sha256"):
        raise ValueError(f"{case_id}: workload source binding mismatch")
    if observed.get("trace_status_sha256") != _canonical_sha256(trace):
        raise ValueError(f"{case_id}: trace source binding mismatch")
    if observed.get("policy_sha256") != _canonical_sha256(POLICY):
        raise ValueError(f"{case_id}: policy binding mismatch")
    unsigned = dict(observed)
    observed_digest = _require_digest(
        unsigned.pop("artifact_sha256", None), f"{case_id} snapshot digest"
    )
    if observed_digest != _canonical_sha256(unsigned):
        raise ValueError(f"{case_id}: snapshot artifact_sha256 mismatch")
    observed_states = {rule["rule_id"]: rule["state"] for rule in expected_rules}
    expected_fixture = FIXED_CASES[case_id]
    checks = {
        "decision_matches": (
            overall_state == expected_fixture["overall_state"]
            and observed_states == expected_fixture["rule_states"]
        ),
        "sample_gate_matches": all(
            rule["state"] != "healthy"
            or rule["sample_count"] >= rule["required_sample_count"]
            for rule in expected_rules
        ),
        "integrity_valid": True,
        "source_binding_valid": True,
    }
    if case.get("checks") != checks:
        raise ValueError(f"{case_id}: self-reported checks mismatch")
    return checks


def verify_operational_alert_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("operational alert artifact must be a JSON object")
    observed_digest = _require_digest(
        artifact.get("artifact_sha256"), "artifact_sha256"
    )
    if observed_digest != canonical_artifact_sha256(artifact):
        raise ValueError("operational alert artifact_sha256 mismatch")
    if artifact.get("schema_version") != 1:
        raise ValueError("operational alert schema_version mismatch")
    if artifact.get("evaluation") != "operational-alert-contract":
        raise ValueError("operational alert evaluation mismatch")
    if artifact.get("classification") != "deterministic_synthetic_contract":
        raise ValueError("operational alert classification mismatch")
    result = artifact.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("operational alert result is missing")
    raw_cases = result.get("raw_cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(FIXED_CASES):
        raise ValueError("operational alert raw case count mismatch")
    if result.get("case_count") != len(raw_cases):
        raise ValueError("operational alert case_count mismatch")
    if {case.get("case_id") for case in raw_cases} != set(FIXED_CASES):
        raise ValueError("operational alert fixed case set mismatch")
    checks = [_verify_case(case) for case in raw_cases]
    metrics = {
        "decision_accuracy_rate": sum(item["decision_matches"] for item in checks)
        / len(checks),
        "sample_gate_accuracy_rate": sum(
            item["sample_gate_matches"] for item in checks
        )
        / len(checks),
        "integrity_rate": sum(item["integrity_valid"] for item in checks)
        / len(checks),
        "source_binding_rate": sum(
            item["source_binding_valid"] for item in checks
        )
        / len(checks),
        "privacy_minimization_rate": 1.0,
    }
    if result.get("metrics") != metrics:
        raise ValueError("operational alert metrics mismatch")
    _scan_privacy(artifact)
    return artifact
