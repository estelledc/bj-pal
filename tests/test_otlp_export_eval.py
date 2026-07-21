from __future__ import annotations

import base64
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.otlp_export import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_otlp_export,
    verify_otlp_export_artifact,
)


def _artifact() -> dict:
    artifact = {
        "schema_version": 1,
        "evaluation": "otlp-export-boundary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "synthetic_protocol_acceptance",
        "scope_warning": "synthetic loopback only",
        "result": evaluate_otlp_export(),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    return artifact


def _write(path: Path, artifact: dict) -> None:
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")


def _accepted(artifact: dict) -> dict:
    return next(
        case
        for case in artifact["result"]["cases"]
        if case["case_id"] == "loopback-collector-acceptance"
    )


def test_otlp_export_artifact_round_trip(tmp_path) -> None:
    path = tmp_path / "otlp.json"
    _write(path, _artifact())
    verified = verify_otlp_export_artifact(path)
    assert verified["result"]["metrics"] == {
        "protocol_acceptance_rate": 1,
        "privacy_marker_absence_rate": 1,
        "export_health_visibility_rate": 1,
        "business_failure_isolation_rate": 1,
    }


def test_otlp_export_verifier_rejects_resigned_private_attribute(tmp_path) -> None:
    artifact = deepcopy(_artifact())
    accepted = _accepted(artifact)
    request = ExportTraceServiceRequest.FromString(
        base64.b64decode(accepted["otlp_requests_base64"][0])
    )
    span = request.resource_spans[0].scope_spans[0].spans[0]
    attribute = span.attributes.add()
    attribute.key = "prompt"
    attribute.value.string_value = "private-prompt-marker"
    accepted["otlp_requests_base64"][0] = base64.b64encode(
        request.SerializeToString()
    ).decode("ascii")
    path = tmp_path / "private.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="forbidden private marker"):
        verify_otlp_export_artifact(path)


def test_otlp_export_verifier_rejects_resigned_broken_parent(tmp_path) -> None:
    artifact = deepcopy(_artifact())
    accepted = _accepted(artifact)
    request = ExportTraceServiceRequest.FromString(
        base64.b64decode(accepted["otlp_requests_base64"][0])
    )
    spans = request.resource_spans[0].scope_spans[0].spans
    child = next(span for span in spans if span.name == "llm.dpsk.complete")
    child.parent_span_id = b"\x01" * 8
    accepted["otlp_requests_base64"][0] = base64.b64encode(
        request.SerializeToString()
    ).decode("ascii")
    path = tmp_path / "parent.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="parent topology"):
        verify_otlp_export_artifact(path)
