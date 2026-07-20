"""Independently decode protobuf and verify privacy, topology, and health."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)


EXPECTED_CASES = {
    "loopback-collector-acceptance",
    "injected-exporter-failure",
}
EXPECTED_SPANS = {
    "planning.execute",
    "llm.dpsk.complete",
    "tool.weather.lookup",
}
ALLOWED_SPAN_ATTRIBUTES = {
    "bj_pal.execution.trace_id",
    "bj_pal.execution.span_id",
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "error.type",
}
FORBIDDEN_BYTES = (
    b"private-",
    b"session_id",
    b"user_id",
    b"prompt",
    b"decision",
    b"error_message",
    b"tool.arguments",
)


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    unsigned = deepcopy(payload)
    unsigned.pop("artifact_sha256", None)
    return _sha(unsigned)


def _attributes(items) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        field = item.value.WhichOneof("value")
        if not field or item.key in result:
            raise ValueError("invalid or duplicate OTLP attribute")
        result[item.key] = getattr(item.value, field)
    return result


def _status(status: dict[str, Any], *, state: str, spans: int) -> None:
    if status.get("version") != "trace_export_status_v1":
        raise ValueError("trace export status version mismatch")
    if (
        status.get("backend") != "otlp"
        or status.get("processor") != "batch"
        or status.get("state") != state
        or status.get("content_capture_enabled") is not False
        or status.get("privacy_policy") != "trace_export_minimal_v1"
        or status.get("semconv_profile") != "gen_ai_minimal_v1"
    ):
        raise ValueError("trace export status policy mismatch")
    if not re.fullmatch(r"[a-f0-9]{64}", str(status.get("endpoint_origin_sha256"))):
        raise ValueError("trace export endpoint digest missing")
    if status.get("export_attempt_count") != 1:
        raise ValueError("trace export attempt count mismatch")
    if state == "healthy":
        if status.get("exported_span_count") != spans or status.get("failed_span_count") != 0:
            raise ValueError("healthy trace export counters mismatch")
    elif status.get("failed_span_count") != spans or status.get("exported_span_count") != 0:
        raise ValueError("degraded trace export counters mismatch")


def _verify_accepted(case: dict[str, Any]) -> None:
    if (
        case.get("input_classification") != "fixed_synthetic_spans"
        or case.get("collector_classification")
        != "local_loopback_otlp_http_receiver"
        or case.get("business_result") != "succeeded"
        or case.get("force_flush_succeeded") is not True
    ):
        raise ValueError("OTLP acceptance labels mismatch")
    encoded = case.get("otlp_requests_base64") or []
    content_types = case.get("content_types") or []
    if not encoded or len(encoded) != len(content_types):
        raise ValueError("OTLP request evidence missing")
    if any(item != "application/x-protobuf" for item in content_types):
        raise ValueError("OTLP content type mismatch")

    spans = []
    resources = []
    raw_bodies = []
    for value in encoded:
        try:
            body = base64.b64decode(value, validate=True)
            request = ExportTraceServiceRequest.FromString(body)
        except Exception as exc:
            raise ValueError("invalid OTLP protobuf evidence") from exc
        raw_bodies.append(body)
        for resource_spans in request.resource_spans:
            resources.append(_attributes(resource_spans.resource.attributes))
            for scope_spans in resource_spans.scope_spans:
                spans.extend(scope_spans.spans)
    raw = b"".join(raw_bodies)
    if any(marker in raw for marker in FORBIDDEN_BYTES):
        raise ValueError("forbidden private marker in OTLP payload")
    if not resources or any(
        item.get("service.name") != "bj-pal"
        or item.get("service.version") != "6.21.0"
        for item in resources
    ):
        raise ValueError("OTLP resource identity mismatch")
    if len(spans) != 3 or {span.name for span in spans} != EXPECTED_SPANS:
        raise ValueError("OTLP span registry mismatch")
    if len({span.span_id for span in spans}) != 3 or len({span.trace_id for span in spans}) != 1:
        raise ValueError("OTLP trace identity mismatch")
    by_name = {span.name: span for span in spans}
    root = by_name["planning.execute"]
    if root.parent_span_id or any(
        by_name[name].parent_span_id != root.span_id
        for name in ("llm.dpsk.complete", "tool.weather.lookup")
    ):
        raise ValueError("OTLP parent topology mismatch")
    attributes = {name: _attributes(span.attributes) for name, span in by_name.items()}
    if any(set(items) - ALLOWED_SPAN_ATTRIBUTES for items in attributes.values()):
        raise ValueError("unexpected OTLP span attribute")
    llm = attributes["llm.dpsk.complete"]
    if (
        llm.get("gen_ai.operation.name") != "chat"
        or llm.get("gen_ai.provider.name") != "deepseek"
        or llm.get("gen_ai.usage.input_tokens") != 13
        or llm.get("gen_ai.usage.output_tokens") != 8
    ):
        raise ValueError("GenAI semantic attributes mismatch")
    if attributes["tool.weather.lookup"].get("error.type") != "RuntimeError":
        raise ValueError("stable error type missing")
    _status(case.get("export_status") or {}, state="healthy", spans=3)


def _verify_failure(case: dict[str, Any]) -> None:
    if (
        case.get("input_classification") != "deterministic_failure_injection"
        or case.get("business_result") != "succeeded"
        or case.get("force_flush_succeeded") is not True
        or "otlp_requests_base64" in case
    ):
        raise ValueError("export failure isolation labels mismatch")
    status = case.get("export_status") or {}
    _status(status, state="degraded", spans=1)
    if status.get("last_error_code") != "export_failed":
        raise ValueError("export failure error code mismatch")


def verify_otlp_export_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1 or artifact.get("evaluation") != "otlp-export-boundary":
        raise ValueError("unsupported OTLP export artifact")
    if artifact.get("classification") != "synthetic_protocol_acceptance":
        raise ValueError("OTLP export classification mismatch")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("OTLP export artifact SHA-256 mismatch")
    result = artifact.get("result") or {}
    cases = result.get("cases") or []
    if result.get("case_count") != 2 or len(cases) != 2:
        raise ValueError("OTLP export case count mismatch")
    by_id = {case.get("case_id"): case for case in cases}
    if set(by_id) != EXPECTED_CASES:
        raise ValueError("OTLP export case registry mismatch")
    _verify_accepted(by_id["loopback-collector-acceptance"])
    _verify_failure(by_id["injected-exporter-failure"])
    expected_metrics = {
        "protocol_acceptance_rate": 1,
        "privacy_marker_absence_rate": 1,
        "export_health_visibility_rate": 1,
        "business_failure_isolation_rate": 1,
    }
    if result.get("metrics") != expected_metrics:
        raise ValueError("OTLP export metrics mismatch")
    return artifact
