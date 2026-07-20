from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.trace_export import TraceExportStatus  # noqa: E402
from jobs import DurableWorkloadHealth  # noqa: E402


def _trace() -> dict:
    return TraceExportStatus(
        version="trace_export_status_v1",
        backend="off",
        state="disabled",
        processor="none",
        privacy_policy="trace_export_minimal_v1",
        semconv_profile="gen_ai_minimal_v1",
        content_capture_enabled=False,
        endpoint_origin_sha256=None,
        export_attempt_count=0,
        exported_span_count=0,
        failed_span_count=0,
        dropped_attribute_count=0,
        last_error_code=None,
    ).to_dict()


def test_cli_combines_integrity_checked_sources_into_mode_0600_artifact(
    tmp_path: Path,
) -> None:
    workload_path = tmp_path / "workload.json"
    trace_path = tmp_path / "trace.json"
    output_path = tmp_path / "alerts.json"
    workload = DurableWorkloadHealth.create(
        window_start="2026-07-20T00:00:00Z",
        window_end="2026-07-20T01:00:00Z",
        records=(),
    )
    workload_path.write_text(json.dumps(workload.to_dict()), encoding="utf-8")
    trace_path.write_text(json.dumps(_trace()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_operational_alerts.py"),
            "--workload-snapshot",
            str(workload_path),
            "--trace-status",
            str(trace_path),
            "--observed-at",
            datetime(2026, 7, 20, 2, tzinfo=timezone.utc).isoformat(),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["overall_state"] == "insufficient_data"
    assert payload["disabled_rule_count"] == 1
    assert os.stat(output_path).st_mode & 0o777 == 0o600


def test_cli_rejects_rehashed_source_drift_and_existing_output(tmp_path: Path) -> None:
    workload_path = tmp_path / "workload.json"
    trace_path = tmp_path / "trace.json"
    output_path = tmp_path / "alerts.json"
    workload = DurableWorkloadHealth.create(
        window_start="2026-07-20T00:00:00Z",
        window_end="2026-07-20T01:00:00Z",
        records=(),
    ).to_dict()
    workload["job_count"] = 1
    workload_path.write_text(json.dumps(workload), encoding="utf-8")
    trace_path.write_text(json.dumps(_trace()), encoding="utf-8")
    output_path.write_text("owned", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_operational_alerts.py"),
            "--workload-snapshot",
            str(workload_path),
            "--trace-status",
            str(trace_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "integrity" in completed.stderr
    assert output_path.read_text(encoding="utf-8") == "owned"
