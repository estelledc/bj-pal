"""Deterministic alert decisions over workload and trace-export evidence.

This module intentionally evaluates one point-in-time snapshot. It does not claim
to deliver alerts, establish a production SLO, or replace a time-series backend.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from agents.trace_export import (
    TRACE_EXPORT_PRIVACY_POLICY,
    TRACE_EXPORT_STATUS_VERSION,
)
from jobs.workload_health import (
    WORKLOAD_HEALTH_VERSION,
    DurableWorkloadHealth,
    canonical_timestamp,
    parse_utc_timestamp,
)


OPERATIONAL_ALERT_POLICY_VERSION = "portfolio_operational_alert_policy_v1"
OPERATIONAL_ALERT_SNAPSHOT_VERSION = "operational_alert_snapshot_v1"

RuleState = Literal["firing", "healthy", "insufficient_data", "disabled"]


def _canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class OperationalAlertPolicy:
    version: str = OPERATIONAL_ALERT_POLICY_VERSION
    minimum_terminal_jobs: int = 20
    terminal_failure_rate_threshold: float = 0.2
    minimum_queue_wait_samples: int = 20
    queue_wait_p95_ms_threshold: float = 30_000.0
    minimum_jobs: int = 20
    retry_job_rate_threshold: float = 0.3
    trace_backend: str = "otlp"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def verify(self) -> None:
        if self.version != OPERATIONAL_ALERT_POLICY_VERSION:
            raise ValueError("operational alert policy version is unsupported")
        if min(
            self.minimum_terminal_jobs,
            self.minimum_queue_wait_samples,
            self.minimum_jobs,
        ) < 1:
            raise ValueError("operational alert policy sample floors must be positive")
        if not 0 <= self.terminal_failure_rate_threshold <= 1:
            raise ValueError("terminal failure threshold must be a rate")
        if not 0 <= self.retry_job_rate_threshold <= 1:
            raise ValueError("retry threshold must be a rate")
        if self.queue_wait_p95_ms_threshold < 0:
            raise ValueError("queue wait threshold cannot be negative")
        if self.trace_backend != "otlp":
            raise ValueError("operational alert policy only supports OTLP trace health")


@dataclass(frozen=True)
class AlertRuleEvaluation:
    rule_id: str
    signal: str
    state: RuleState
    severity: Literal["warning", "critical"]
    observed_value: float | str | None
    threshold_value: float | str | None
    comparison: Literal["gte", "state"]
    sample_count: int
    required_sample_count: int
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationalAlertSnapshot:
    version: str
    observed_at: str
    window_start: str
    window_end: str
    policy: OperationalAlertPolicy
    overall_state: RuleState
    rules: tuple[AlertRuleEvaluation, ...]
    firing_rule_count: int
    evaluated_rule_count: int
    insufficient_data_rule_count: int
    disabled_rule_count: int
    workload_artifact_sha256: str
    trace_status_sha256: str
    policy_sha256: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        workload: DurableWorkloadHealth,
        trace_status: Mapping[str, Any],
        observed_at: datetime | None = None,
        policy: OperationalAlertPolicy | None = None,
    ) -> "OperationalAlertSnapshot":
        resolved_policy = policy or OperationalAlertPolicy()
        resolved_policy.verify()
        if workload.version != WORKLOAD_HEALTH_VERSION or not workload.verify_integrity():
            raise ValueError("workload health source failed integrity verification")
        normalized_trace = _validate_trace_status(trace_status)
        observed = observed_at or datetime.now(timezone.utc)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("operational alert observation time must include a timezone")
        observed_utc = observed.astimezone(timezone.utc)
        if parse_utc_timestamp(workload.window_end) > observed_utc:
            raise ValueError("operational alert workload window must be closed")

        rules = (
            _terminal_failure_rule(workload, resolved_policy),
            _queue_wait_rule(workload, resolved_policy),
            _retry_rule(workload, resolved_policy),
            _trace_export_rule(normalized_trace, resolved_policy),
        )
        counts = _rule_counts(rules)
        overall_state = _overall_state(rules)
        payload = {
            "version": OPERATIONAL_ALERT_SNAPSHOT_VERSION,
            "observed_at": canonical_timestamp(observed_utc),
            "window_start": workload.window_start,
            "window_end": workload.window_end,
            "policy": resolved_policy.to_dict(),
            "overall_state": overall_state,
            "rules": [rule.to_dict() for rule in rules],
            **counts,
            "workload_artifact_sha256": workload.artifact_sha256,
            "trace_status_sha256": _canonical_sha256(normalized_trace),
            "policy_sha256": _canonical_sha256(resolved_policy.to_dict()),
        }
        return cls._from_payload(payload)

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any]) -> "OperationalAlertSnapshot":
        unsigned = dict(payload)
        unsigned.pop("artifact_sha256", None)
        policy = OperationalAlertPolicy(**dict(unsigned["policy"]))
        policy.verify()
        rules = tuple(
            AlertRuleEvaluation(**dict(item)) for item in unsigned["rules"]
        )
        return cls(
            version=str(unsigned["version"]),
            observed_at=str(unsigned["observed_at"]),
            window_start=str(unsigned["window_start"]),
            window_end=str(unsigned["window_end"]),
            policy=policy,
            overall_state=unsigned["overall_state"],
            rules=rules,
            firing_rule_count=int(unsigned["firing_rule_count"]),
            evaluated_rule_count=int(unsigned["evaluated_rule_count"]),
            insufficient_data_rule_count=int(
                unsigned["insufficient_data_rule_count"]
            ),
            disabled_rule_count=int(unsigned["disabled_rule_count"]),
            workload_artifact_sha256=str(unsigned["workload_artifact_sha256"]),
            trace_status_sha256=str(unsigned["trace_status_sha256"]),
            policy_sha256=str(unsigned["policy_sha256"]),
            artifact_sha256=_canonical_sha256(unsigned),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperationalAlertSnapshot":
        snapshot = cls._from_payload(payload)
        if snapshot.version != OPERATIONAL_ALERT_SNAPSHOT_VERSION:
            raise ValueError("operational alert snapshot version is unsupported")
        if str(payload.get("artifact_sha256")) != snapshot.artifact_sha256:
            raise ValueError("operational alert snapshot failed integrity verification")
        if snapshot.policy_sha256 != _canonical_sha256(snapshot.policy.to_dict()):
            raise ValueError("operational alert policy digest mismatch")
        if _rule_counts(snapshot.rules) != {
            "firing_rule_count": snapshot.firing_rule_count,
            "evaluated_rule_count": snapshot.evaluated_rule_count,
            "insufficient_data_rule_count": snapshot.insufficient_data_rule_count,
            "disabled_rule_count": snapshot.disabled_rule_count,
        }:
            raise ValueError("operational alert rule counts are inconsistent")
        if snapshot.overall_state != _overall_state(snapshot.rules):
            raise ValueError("operational alert overall state is inconsistent")
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "observed_at": self.observed_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "policy": self.policy.to_dict(),
            "overall_state": self.overall_state,
            "rules": [rule.to_dict() for rule in self.rules],
            "firing_rule_count": self.firing_rule_count,
            "evaluated_rule_count": self.evaluated_rule_count,
            "insufficient_data_rule_count": self.insufficient_data_rule_count,
            "disabled_rule_count": self.disabled_rule_count,
            "workload_artifact_sha256": self.workload_artifact_sha256,
            "trace_status_sha256": self.trace_status_sha256,
            "policy_sha256": self.policy_sha256,
            "artifact_sha256": self.artifact_sha256,
        }

    def verify_integrity(self) -> bool:
        try:
            self.from_dict(self.to_dict())
        except (KeyError, TypeError, ValueError):
            return False
        return True


def _validate_trace_status(payload: Mapping[str, Any]) -> dict[str, Any]:
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
        raise ValueError("trace export status has an unexpected schema")
    normalized = dict(payload)
    if normalized["version"] != TRACE_EXPORT_STATUS_VERSION:
        raise ValueError("trace export status version is unsupported")
    if normalized["privacy_policy"] != TRACE_EXPORT_PRIVACY_POLICY:
        raise ValueError("trace export privacy policy is unsupported")
    if normalized["content_capture_enabled"] is not False:
        raise ValueError("content-capturing trace status cannot feed alerting")
    if normalized["backend"] not in {"off", "jsonl", "otlp", "invalid"}:
        raise ValueError("trace export backend is invalid")
    if normalized["state"] not in {
        "disabled",
        "configured_unproven",
        "healthy",
        "degraded",
    }:
        raise ValueError("trace export state is invalid")
    if normalized["backend"] == "invalid" and normalized["state"] != "degraded":
        raise ValueError("invalid trace export configuration must be degraded")
    if normalized["backend"] == "off" and normalized["state"] != "disabled":
        raise ValueError("disabled trace export backend has an inconsistent state")
    for key in (
        "export_attempt_count",
        "exported_span_count",
        "failed_span_count",
        "dropped_attribute_count",
    ):
        value = normalized[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("trace export counters must be non-negative integers")
    endpoint_digest = normalized["endpoint_origin_sha256"]
    if endpoint_digest is not None and (
        not isinstance(endpoint_digest, str)
        or len(endpoint_digest) != 64
        or any(character not in "0123456789abcdef" for character in endpoint_digest)
    ):
        raise ValueError("trace export endpoint digest is invalid")
    if normalized["backend"] == "otlp" and endpoint_digest is None:
        raise ValueError("configured OTLP trace export requires an endpoint digest")
    return normalized


def _rate_rule(
    *,
    rule_id: str,
    signal: str,
    severity: Literal["warning", "critical"],
    value: float | None,
    threshold: float,
    sample_count: int,
    required_sample_count: int,
) -> AlertRuleEvaluation:
    if sample_count < required_sample_count or value is None:
        return AlertRuleEvaluation(
            rule_id=rule_id,
            signal=signal,
            state="insufficient_data",
            severity=severity,
            observed_value=value,
            threshold_value=threshold,
            comparison="gte",
            sample_count=sample_count,
            required_sample_count=required_sample_count,
            reason_code="minimum_sample_not_met",
        )
    firing = value >= threshold
    return AlertRuleEvaluation(
        rule_id=rule_id,
        signal=signal,
        state="firing" if firing else "healthy",
        severity=severity,
        observed_value=value,
        threshold_value=threshold,
        comparison="gte",
        sample_count=sample_count,
        required_sample_count=required_sample_count,
        reason_code="threshold_breached" if firing else "within_threshold",
    )


def _terminal_failure_rule(
    workload: DurableWorkloadHealth,
    policy: OperationalAlertPolicy,
) -> AlertRuleEvaluation:
    return _rate_rule(
        rule_id="terminal_failure_rate",
        signal="durable_job_terminal_failure_rate",
        severity="critical",
        value=workload.terminal_failure_rate,
        threshold=policy.terminal_failure_rate_threshold,
        sample_count=workload.terminal_job_count,
        required_sample_count=policy.minimum_terminal_jobs,
    )


def _queue_wait_rule(
    workload: DurableWorkloadHealth,
    policy: OperationalAlertPolicy,
) -> AlertRuleEvaluation:
    return _rate_rule(
        rule_id="queue_wait_p95_ms",
        signal="durable_job_queue_wait_p95_ms",
        severity="warning",
        value=workload.queue_wait_ms.p95_ms,
        threshold=policy.queue_wait_p95_ms_threshold,
        sample_count=workload.queue_wait_ms.sample_count,
        required_sample_count=policy.minimum_queue_wait_samples,
    )


def _retry_rule(
    workload: DurableWorkloadHealth,
    policy: OperationalAlertPolicy,
) -> AlertRuleEvaluation:
    return _rate_rule(
        rule_id="retry_job_rate",
        signal="durable_job_retry_rate",
        severity="warning",
        value=workload.retry_job_rate,
        threshold=policy.retry_job_rate_threshold,
        sample_count=workload.job_count,
        required_sample_count=policy.minimum_jobs,
    )


def _trace_export_rule(
    trace_status: Mapping[str, Any],
    policy: OperationalAlertPolicy,
) -> AlertRuleEvaluation:
    backend = str(trace_status["backend"])
    state = str(trace_status["state"])
    attempt_count = int(trace_status["export_attempt_count"])
    if backend == "invalid":
        return AlertRuleEvaluation(
            rule_id="trace_export_health",
            signal="otlp_trace_export_state",
            state="firing",
            severity="critical",
            observed_value=backend,
            threshold_value="healthy",
            comparison="state",
            sample_count=attempt_count,
            required_sample_count=1,
            reason_code="trace_export_invalid_configuration",
        )
    if backend != policy.trace_backend:
        return AlertRuleEvaluation(
            rule_id="trace_export_health",
            signal="otlp_trace_export_state",
            state="disabled",
            severity="critical",
            observed_value=backend,
            threshold_value=policy.trace_backend,
            comparison="state",
            sample_count=attempt_count,
            required_sample_count=1,
            reason_code="otlp_export_not_configured",
        )
    if state == "configured_unproven" or attempt_count == 0:
        return AlertRuleEvaluation(
            rule_id="trace_export_health",
            signal="otlp_trace_export_state",
            state="insufficient_data",
            severity="critical",
            observed_value=state,
            threshold_value="healthy",
            comparison="state",
            sample_count=attempt_count,
            required_sample_count=1,
            reason_code="export_attempt_not_observed",
        )
    firing = state == "degraded"
    if state not in {"healthy", "degraded"}:
        raise ValueError("configured OTLP trace status is inconsistent")
    return AlertRuleEvaluation(
        rule_id="trace_export_health",
        signal="otlp_trace_export_state",
        state="firing" if firing else "healthy",
        severity="critical",
        observed_value=state,
        threshold_value="healthy",
        comparison="state",
        sample_count=attempt_count,
        required_sample_count=1,
        reason_code="trace_export_degraded" if firing else "trace_export_healthy",
    )


def _rule_counts(rules: tuple[AlertRuleEvaluation, ...]) -> dict[str, int]:
    return {
        "firing_rule_count": sum(rule.state == "firing" for rule in rules),
        "evaluated_rule_count": sum(
            rule.state in {"firing", "healthy"} for rule in rules
        ),
        "insufficient_data_rule_count": sum(
            rule.state == "insufficient_data" for rule in rules
        ),
        "disabled_rule_count": sum(rule.state == "disabled" for rule in rules),
    }


def _overall_state(rules: tuple[AlertRuleEvaluation, ...]) -> RuleState:
    states = {rule.state for rule in rules}
    if "firing" in states:
        return "firing"
    if "insufficient_data" in states:
        return "insufficient_data"
    if "healthy" in states:
        return "healthy"
    return "disabled"
