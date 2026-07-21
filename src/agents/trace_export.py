"""Privacy-minimized trace projections and monitored OTLP/HTTP export."""

from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit


TRACE_EXPORT_STATUS_VERSION = "trace_export_status_v1"
TRACE_EXPORT_PRIVACY_POLICY = "trace_export_minimal_v1"
TRACE_EXPORT_SEMCONV_PROFILE = "gen_ai_minimal_v1"

_SAFE_SPAN_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,95}$")
_PROVIDER_BY_PREFIX = {
    "llm.anthropic.": "anthropic",
    "llm.dpsk.": "deepseek",
    "llm.longcat.": "longcat",
}
_SAFE_NUMERIC_ATTRIBUTES = {
    "attempt": "bj_pal.attempt",
    "branch_count": "bj_pal.branch.count",
    "max_workers": "bj_pal.worker.limit",
    "response_chars": "bj_pal.response.characters",
    "score": "bj_pal.score",
    "step_count": "bj_pal.step.count",
}


class ExportableSpan(Protocol):
    name: str
    span_id: str
    parent_id: str | None
    trace_id: str
    session_id: str
    start_ts: float
    end_ts: float
    attrs: dict[str, Any]
    status: str
    error: str
    backend_handle: Any


@dataclass(frozen=True)
class TraceExportStatus:
    version: str
    backend: str
    state: str
    processor: str
    privacy_policy: str
    semconv_profile: str
    content_capture_enabled: bool
    endpoint_origin_sha256: str | None
    export_attempt_count: int
    exported_span_count: int
    failed_span_count: int
    dropped_attribute_count: int
    last_error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceExportMonitor:
    """Thread-safe, payload-free health for the optional trace sink."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._backend = "off"
            self._state = "disabled"
            self._processor = "none"
            self._endpoint_origin_sha256: str | None = None
            self._export_attempt_count = 0
            self._exported_span_count = 0
            self._failed_span_count = 0
            self._dropped_attribute_count = 0
            self._last_error_code: str | None = None

    def configure(
        self,
        *,
        backend: str,
        state: str,
        processor: str,
        endpoint_origin_sha256: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._lock:
            self._backend = backend
            self._state = state
            self._processor = processor
            self._endpoint_origin_sha256 = endpoint_origin_sha256
            self._last_error_code = error_code

    def record_projection(self, dropped_attribute_count: int) -> None:
        with self._lock:
            self._dropped_attribute_count += max(0, dropped_attribute_count)

    def record_local_export(self, span_count: int = 1) -> None:
        with self._lock:
            self._export_attempt_count += 1
            self._exported_span_count += max(0, span_count)
            self._state = "healthy"
            self._last_error_code = None

    def record_batch_result(
        self,
        *,
        span_count: int,
        succeeded: bool,
        error_code: str | None = None,
    ) -> None:
        with self._lock:
            self._export_attempt_count += 1
            if succeeded:
                self._exported_span_count += max(0, span_count)
                self._state = "healthy"
                self._last_error_code = None
            else:
                self._failed_span_count += max(0, span_count)
                self._state = "degraded"
                self._last_error_code = error_code or "export_failed"

    def record_runtime_failure(self, error_code: str) -> None:
        with self._lock:
            self._state = "degraded"
            self._last_error_code = error_code

    def snapshot(self) -> TraceExportStatus:
        with self._lock:
            return TraceExportStatus(
                version=TRACE_EXPORT_STATUS_VERSION,
                backend=self._backend,
                state=self._state,
                processor=self._processor,
                privacy_policy=TRACE_EXPORT_PRIVACY_POLICY,
                semconv_profile=TRACE_EXPORT_SEMCONV_PROFILE,
                content_capture_enabled=False,
                endpoint_origin_sha256=self._endpoint_origin_sha256,
                export_attempt_count=self._export_attempt_count,
                exported_span_count=self._exported_span_count,
                failed_span_count=self._failed_span_count,
                dropped_attribute_count=self._dropped_attribute_count,
                last_error_code=self._last_error_code,
            )


def validate_otlp_http_configuration(environment: dict[str, str]) -> str:
    """Validate the explicit HTTP/protobuf target and return an origin hash."""
    protocol = (
        environment.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
        or environment.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    ).lower()
    if protocol != "http/protobuf":
        raise ValueError("unsupported_protocol")
    endpoint = environment.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or environment.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if not endpoint:
        raise ValueError("missing_endpoint")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_endpoint")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid_endpoint") from exc
    default_port = 443 if parsed.scheme == "https" else 80
    origin = f"{parsed.scheme}://{parsed.hostname.lower()}:{port or default_port}"
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()


def safe_span_name(value: str) -> str:
    return value if _SAFE_SPAN_NAME.fullmatch(value) else "bj_pal.unknown_operation"


def stable_error_type(value: str) -> str | None:
    if ":" not in value:
        return None
    candidate = value.split(":", 1)[0].strip()
    return candidate if _SAFE_ERROR_TYPE.fullmatch(candidate) else None


def sanitized_attributes(span: ExportableSpan) -> tuple[dict[str, Any], int]:
    """Keep bounded operational fields; never export business/content identifiers."""
    attributes: dict[str, Any] = {
        "bj_pal.execution.trace_id": span.trace_id,
        "bj_pal.execution.span_id": span.span_id,
    }
    operation = _gen_ai_operation(span.name)
    if operation is not None:
        attributes["gen_ai.operation.name"] = operation
    provider = _provider_name(span.name)
    if provider is not None:
        attributes["gen_ai.provider.name"] = provider

    for source, target in _SAFE_NUMERIC_ATTRIBUTES.items():
        value = span.attrs.get(source)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            continue
        attributes[target] = value
    input_tokens = _non_negative_int(span.attrs.get("input_tokens"))
    output_tokens = _non_negative_int(span.attrs.get("output_tokens"))
    if input_tokens is not None:
        attributes["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attributes["gen_ai.usage.output_tokens"] = output_tokens
    error_type = stable_error_type(span.error)
    if error_type is not None:
        attributes["error.type"] = error_type

    retained_sources = set(_SAFE_NUMERIC_ATTRIBUTES)
    retained_sources.update({"input_tokens", "output_tokens"})
    dropped = sum(key not in retained_sources for key in span.attrs)
    return attributes, dropped


def sanitized_jsonl_payload(span: ExportableSpan) -> tuple[dict[str, Any], int]:
    attributes, dropped = sanitized_attributes(span)
    session_hash = (
        hashlib.sha256(span.session_id.encode("utf-8")).hexdigest()
        if span.session_id
        else None
    )
    return (
        {
            "version": "trace_export_span_v1",
            "name": safe_span_name(span.name),
            "span_id": span.span_id,
            "parent_span_id": span.parent_id,
            "trace_id": span.trace_id,
            "session_id_sha256": session_hash,
            "started_at_epoch": round(span.start_ts, 6),
            "duration_ms": round(max(0.0, span.end_ts - span.start_ts) * 1000, 3),
            "attributes": attributes,
            "status": "error" if span.status == "error" else "ok",
        },
        dropped,
    )


def hashed_trace_filename(session_id: str) -> str:
    material = session_id or "default"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"trace-{digest}.jsonl"


class MonitoredSpanExporter:
    """SpanExporter-compatible wrapper that records only bounded health counts."""

    def __init__(self, delegate: Any, monitor: TraceExportMonitor) -> None:
        self._delegate = delegate
        self._monitor = monitor

    def export(self, spans: Sequence[Any]):
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            result = self._delegate.export(spans)
        except Exception:
            self._monitor.record_batch_result(
                span_count=len(spans),
                succeeded=False,
                error_code="export_exception",
            )
            return SpanExportResult.FAILURE
        succeeded = result == SpanExportResult.SUCCESS
        self._monitor.record_batch_result(
            span_count=len(spans),
            succeeded=succeeded,
            error_code=None if succeeded else "export_failed",
        )
        return result

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def apply_span_projection(otel_span: Any, span: ExportableSpan) -> int:
    attributes, dropped = sanitized_attributes(span)
    for key, value in attributes.items():
        otel_span.set_attribute(key, value)
    return dropped


def _gen_ai_operation(span_name: str) -> str | None:
    if span_name.startswith("llm."):
        return "chat"
    if span_name.startswith("tool."):
        return "execute_tool"
    if span_name in {"planning.execute", "planner.plan", "planner.plan_tot"}:
        return "invoke_agent"
    if span_name.startswith(("planning.", "planner.", "tot.")):
        return "invoke_workflow"
    return None


def _provider_name(span_name: str) -> str | None:
    for prefix, provider in _PROVIDER_BY_PREFIX.items():
        if span_name.startswith(prefix):
            return provider
    return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
