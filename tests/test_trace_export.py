"""Real OTLP/HTTP protobuf acceptance and non-fatal failure tests."""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace.export import SpanExportResult


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class _CollectorHandler(BaseHTTPRequestHandler):
    bodies: list[bytes] = []
    content_types: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).bodies.append(self.rfile.read(length))
        type(self).content_types.append(self.headers.get("Content-Type", ""))
        body = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        del format, args


@pytest.fixture
def collector():
    _CollectorHandler.bodies = []
    _CollectorHandler.content_types = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _CollectorHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _attributes(items) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        value = item.value
        field = value.WhichOneof("value")
        result[item.key] = getattr(value, field) if field else None
    return result


def _decode_spans(bodies: list[bytes]):
    resources: list[dict[str, object]] = []
    spans = []
    for body in bodies:
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for resource_spans in request.resource_spans:
            resources.append(_attributes(resource_spans.resource.attributes))
            for scope_spans in resource_spans.scope_spans:
                spans.extend(scope_spans.spans)
    return resources, spans


def test_otlp_http_exports_real_protobuf_with_privacy_projection(
    collector, monkeypatch
):
    server, handler = collector
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
    monkeypatch.setenv("BJ_PAL_TRACE", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "2")

    from agents import tracing

    tracing.reset_backend_for_tests()
    tracing.set_session("private-session-marker")
    with tracing.trace_span(
        "planning.execute", attrs={"user_id": "private-user-marker"}
    ):
        with tracing.trace_span(
            "llm.dpsk.complete",
            attrs={
                "input_tokens": 13,
                "output_tokens": 8,
                "prompt": "private-prompt-marker",
                "decision": "private-decision-marker",
            },
        ):
            pass
        try:
            with tracing.trace_span("tool.weather.lookup"):
                raise RuntimeError("private-error-marker")
        except RuntimeError:
            pass

    assert tracing.force_flush_trace_export(5_000) is True
    resources, spans = _decode_spans(handler.bodies)
    assert handler.bodies
    assert all(value == "application/x-protobuf" for value in handler.content_types)
    assert any(resource.get("service.name") == "bj-pal" for resource in resources)
    assert any(resource.get("service.version") == "6.21.0" for resource in resources)
    assert {span.name for span in spans} == {
        "planning.execute",
        "llm.dpsk.complete",
        "tool.weather.lookup",
    }

    by_name = {span.name: span for span in spans}
    root = by_name["planning.execute"]
    assert root.parent_span_id == b""
    assert by_name["llm.dpsk.complete"].parent_span_id == root.span_id
    assert by_name["tool.weather.lookup"].parent_span_id == root.span_id
    llm_attrs = _attributes(by_name["llm.dpsk.complete"].attributes)
    assert llm_attrs["gen_ai.operation.name"] == "chat"
    assert llm_attrs["gen_ai.provider.name"] == "deepseek"
    assert llm_attrs["gen_ai.usage.input_tokens"] == 13
    assert llm_attrs["gen_ai.usage.output_tokens"] == 8
    error_attrs = _attributes(by_name["tool.weather.lookup"].attributes)
    assert error_attrs["error.type"] == "RuntimeError"

    raw = b"".join(handler.bodies)
    for marker in (
        b"private-session-marker",
        b"private-user-marker",
        b"private-prompt-marker",
        b"private-decision-marker",
        b"private-error-marker",
        b"user_id",
        b"prompt",
        b"decision",
    ):
        assert marker not in raw
    export_status = tracing.trace_export_status().to_dict()
    assert export_status["state"] == "healthy"
    assert export_status["exported_span_count"] == 3
    assert export_status["failed_span_count"] == 0
    assert export_status["endpoint_origin_sha256"]
    assert endpoint not in str(export_status)
    tracing.reset_backend_for_tests()


def test_otlp_export_failure_is_nonfatal_and_visible(monkeypatch):
    class _FailingExporter:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def export(self, spans):
            assert spans
            return SpanExportResult.FAILURE

        def force_flush(self, timeout_millis=30_000):
            del timeout_millis
            return True

        def shutdown(self):
            return None

    monkeypatch.setenv("BJ_PAL_TRACE", "otlp")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporter_module

    monkeypatch.setattr(exporter_module, "OTLPSpanExporter", _FailingExporter)
    from agents import tracing

    tracing.reset_backend_for_tests()
    with tracing.trace_span("planning.execute"):
        business_result = "still-succeeded"
    assert business_result == "still-succeeded"
    assert tracing.force_flush_trace_export(5_000) is True
    export_status = tracing.trace_export_status().to_dict()
    assert export_status["state"] == "degraded"
    assert export_status["exported_span_count"] == 0
    assert export_status["failed_span_count"] == 1
    assert export_status["last_error_code"] == "export_failed"
    tracing.reset_backend_for_tests()
