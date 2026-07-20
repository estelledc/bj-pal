"""Exercise the product exporter against a real local OTLP/HTTP receiver."""

from __future__ import annotations

import base64
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace.export import SpanExportResult


PRIVATE_MARKERS = (
    "private-session-marker",
    "private-user-marker",
    "private-prompt-marker",
    "private-decision-marker",
    "private-error-marker",
)


class _CollectorHandler(BaseHTTPRequestHandler):
    bodies: list[bytes] = []
    content_types: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).bodies.append(self.rfile.read(length))
        type(self).content_types.append(self.headers.get("Content-Type", ""))
        response = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args) -> None:
        del format, args


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _accepted_case() -> dict[str, Any]:
    from agents import tracing

    _CollectorHandler.bodies = []
    _CollectorHandler.content_types = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
    try:
        with _environment(
            {
                "BJ_PAL_TRACE": "otlp",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
                "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
                "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "2",
            }
        ):
            tracing.reset_backend_for_tests()
            tracing.set_session(PRIVATE_MARKERS[0])
            with tracing.trace_span(
                "planning.execute", attrs={"user_id": PRIVATE_MARKERS[1]}
            ):
                with tracing.trace_span(
                    "llm.dpsk.complete",
                    attrs={
                        "input_tokens": 13,
                        "output_tokens": 8,
                        "prompt": PRIVATE_MARKERS[2],
                        "decision": PRIVATE_MARKERS[3],
                    },
                ):
                    pass
                try:
                    with tracing.trace_span("tool.weather.lookup"):
                        raise RuntimeError(PRIVATE_MARKERS[4])
                except RuntimeError:
                    pass
            business_result = "succeeded"
            flushed = tracing.force_flush_trace_export(5_000)
            export_status = tracing.trace_export_status().to_dict()
            tracing.reset_backend_for_tests()
        return {
            "case_id": "loopback-collector-acceptance",
            "input_classification": "fixed_synthetic_spans",
            "collector_classification": "local_loopback_otlp_http_receiver",
            "business_result": business_result,
            "force_flush_succeeded": flushed,
            "content_types": list(_CollectorHandler.content_types),
            "otlp_requests_base64": [
                base64.b64encode(body).decode("ascii")
                for body in _CollectorHandler.bodies
            ],
            "export_status": export_status,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _failure_case() -> dict[str, Any]:
    from agents import tracing
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporter_module

    class _FailingExporter:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def export(self, spans):
            return SpanExportResult.FAILURE if spans else SpanExportResult.SUCCESS

        def force_flush(self, timeout_millis=30_000):
            del timeout_millis
            return True

        def shutdown(self):
            return None

    original = exporter_module.OTLPSpanExporter
    exporter_module.OTLPSpanExporter = _FailingExporter
    try:
        with _environment(
            {
                "BJ_PAL_TRACE": "otlp",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": (
                    "http://127.0.0.1:4318/v1/traces"
                ),
                "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            }
        ):
            tracing.reset_backend_for_tests()
            with tracing.trace_span("planning.execute"):
                business_result = "succeeded"
            flushed = tracing.force_flush_trace_export(5_000)
            export_status = tracing.trace_export_status().to_dict()
            tracing.reset_backend_for_tests()
    finally:
        exporter_module.OTLPSpanExporter = original
    return {
        "case_id": "injected-exporter-failure",
        "input_classification": "deterministic_failure_injection",
        "business_result": business_result,
        "force_flush_succeeded": flushed,
        "export_status": export_status,
    }


def evaluate_otlp_export() -> dict[str, Any]:
    cases = [_accepted_case(), _failure_case()]
    accepted, failure = cases
    raw = b"".join(
        base64.b64decode(item)
        for item in accepted["otlp_requests_base64"]
    )
    return {
        "case_count": len(cases),
        "cases": cases,
        "metrics": {
            "protocol_acceptance_rate": int(
                bool(accepted["otlp_requests_base64"])
                and accepted["force_flush_succeeded"]
            ),
            "privacy_marker_absence_rate": int(
                all(marker.encode("utf-8") not in raw for marker in PRIVATE_MARKERS)
            ),
            "export_health_visibility_rate": int(
                accepted["export_status"]["state"] == "healthy"
                and failure["export_status"]["state"] == "degraded"
            ),
            "business_failure_isolation_rate": int(
                failure["business_result"] == "succeeded"
            ),
        },
    }
