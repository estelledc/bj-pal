"""Build and independently verify BJ-Pal public evaluation artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_profile import load_data_profile  # noqa: E402
from data_profile import DataProfile  # noqa: E402


SCHEMA_ID = "bj-pal.public-eval-artifact"
SCHEMA_VERSION = 1
VOLATILE_KEYS = {
    "booking_id",
    "created_at",
    "duration_s",
    "generated_at",
    "git_sha",
    "latency_ms",
    "plan_id",
    "recorded_at",
    "started_at",
}


class ArtifactVerificationError(ValueError):
    """Artifact bytes or computed summaries do not agree with their claims."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())


def _rate(n_pass: int, n_cases: int) -> float:
    return round(n_pass / n_cases, 3) if n_cases else 0.0


def _expect(report: Mapping[str, Any], key: str, expected: Any, context: str) -> None:
    if report.get(key) != expected:
        raise ArtifactVerificationError(
            f"{context}.{key}: claimed {report.get(key)!r}, recomputed {expected!r}"
        )


def _verify_l1(report: Mapping[str, Any]) -> dict:
    results = report.get("results")
    if not isinstance(results, list):
        raise ArtifactVerificationError("L1.results must contain raw cases")
    n_cases = len(results)
    n_pass = sum(item.get("pass") is True for item in results)
    _expect(report, "n_cases", n_cases, "L1")
    _expect(report, "n_pass", n_pass, "L1")
    _expect(report, "pass_rate", _rate(n_pass, n_cases), "L1")
    return {
        "n_cases": n_cases,
        "n_pass": n_pass,
        "pass_rate": _rate(n_pass, n_cases),
        "gate_pass": n_cases > 0 and n_pass == n_cases,
    }


def _verify_l2(report: Mapping[str, Any]) -> dict:
    modules = report.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ArtifactVerificationError("L2.modules must contain raw module cases")
    total_cases = 0
    total_pass = 0
    for module in modules:
        name = str(module.get("module") or "unknown")
        results = module.get("results")
        if not isinstance(results, list):
            raise ArtifactVerificationError(f"L2.{name}.results must contain raw cases")
        n_cases = len(results)
        n_pass = sum(item.get("pass") is True for item in results)
        _expect(module, "n_cases", n_cases, f"L2.{name}")
        _expect(module, "n_pass", n_pass, f"L2.{name}")
        _expect(module, "pass_rate", _rate(n_pass, n_cases), f"L2.{name}")
        total_cases += n_cases
        total_pass += n_pass
    _expect(report, "n_cases", total_cases, "L2")
    _expect(report, "n_pass", total_pass, "L2")
    _expect(report, "pass_rate", _rate(total_pass, total_cases), "L2")
    return {
        "n_cases": total_cases,
        "n_pass": total_pass,
        "pass_rate": _rate(total_pass, total_cases),
        "gate_pass": total_cases > 0 and total_pass == total_cases,
    }


def _verify_l3(report: Mapping[str, Any]) -> dict:
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ArtifactVerificationError("L3.cases must contain raw cases")

    signal_counts: dict[str, dict[str, int]] = {}
    segment_counts: dict[tuple[str, str], dict[str, int]] = {}
    n_all_pass = 0
    failed_cases = []
    for case in cases:
        signals = case.get("signals")
        if not isinstance(signals, dict) or not signals:
            raise ArtifactVerificationError(f"L3 case {case.get('case_id')} has no raw signals")
        all_pass = all(signal.get("pass") is True for signal in signals.values())
        if case.get("all_pass") is not all_pass:
            raise ArtifactVerificationError(
                f"L3 case {case.get('case_id')}.all_pass disagrees with raw signals"
            )
        n_all_pass += int(all_pass)
        for signal_name, signal in signals.items():
            counts = signal_counts.setdefault(signal_name, {"pass": 0, "total": 0})
            counts["total"] += 1
            counts["pass"] += int(signal.get("pass") is True)
        segment = (str(case.get("persona")), str(case.get("scenario")))
        counts = segment_counts.setdefault(segment, {"pass": 0, "total": 0})
        counts["total"] += 1
        counts["pass"] += int(all_pass)
        if not all_pass and len(failed_cases) < 20:
            failed_cases.append({
                "case_id": case.get("case_id"),
                "query": str(case.get("query") or "")[:60],
                "failed_signals": [
                    name for name, signal in signals.items() if signal.get("pass") is not True
                ],
            })

    signal_summary = [
        {
            "signal": name,
            "pass": counts["pass"],
            "total": counts["total"],
            "rate": _rate(counts["pass"], counts["total"]),
        }
        for name, counts in sorted(signal_counts.items())
    ]
    segment_summary = [
        {
            "persona": persona,
            "scenario": scenario,
            "pass": counts["pass"],
            "total": counts["total"],
            "rate": _rate(counts["pass"], counts["total"]),
        }
        for (persona, scenario), counts in sorted(segment_counts.items())
    ]
    n_cases = len(cases)
    _expect(report, "n_cases", n_cases, "L3")
    _expect(report, "n_all_pass", n_all_pass, "L3")
    _expect(report, "all_pass_rate", _rate(n_all_pass, n_cases), "L3")
    _expect(report, "signal_summary", signal_summary, "L3")
    _expect(report, "segment_summary", segment_summary, "L3")
    _expect(report, "failed_cases", failed_cases, "L3")
    return {
        "n_cases": n_cases,
        "n_all_pass": n_all_pass,
        "all_pass_rate": _rate(n_all_pass, n_cases),
        "minimum_signal_rate": min(item["rate"] for item in signal_summary),
        "gate_pass": all(item["rate"] >= 0.9 for item in signal_summary),
    }


def recompute_summary(evaluations: Mapping[str, Any]) -> dict:
    """Recompute every public claim from raw results and reject stale summaries."""
    expected_levels = {"L1", "L2", "L3"}
    if set(evaluations) != expected_levels:
        raise ArtifactVerificationError(
            f"evaluations must be exactly {sorted(expected_levels)}, got {sorted(evaluations)}"
        )
    levels = {
        "L1": _verify_l1(evaluations["L1"]),
        "L2": _verify_l2(evaluations["L2"]),
        "L3": _verify_l3(evaluations["L3"]),
    }
    return {
        "levels": levels,
        "overall_pass": all(level["gate_pass"] for level in levels.values()),
    }


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def semantic_projection(artifact: Mapping[str, Any]) -> dict:
    """Stable evidence-only view used to compare runs across machines/timings."""
    return {
        "schema_id": artifact.get("schema_id"),
        "schema_version": artifact.get("schema_version"),
        "evaluations": _strip_volatile(artifact.get("evaluations")),
        "summary": artifact.get("summary"),
    }


def seal_artifact(artifact: dict) -> dict:
    """Replace integrity fields after the artifact body is final."""
    artifact.pop("integrity", None)
    artifact["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": _sha256(artifact),
        "semantic_sha256": _sha256(semantic_projection(artifact)),
    }
    return artifact


def build_artifact(
    reports: Mapping[str, Any],
    *,
    command: str = "python evals/run_public.py",
    profile: DataProfile | None = None,
) -> dict:
    evaluations = copy.deepcopy(dict(reports))
    summary = recompute_summary(evaluations)
    active_profile = profile or load_data_profile()
    if not active_profile.public_reproducible:
        raise ArtifactVerificationError(
            "public evaluation requires `make bootstrap-demo` and a reproducible profile"
        )
    artifact = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "run": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "git": {
                "sha": _git_output("rev-parse", "HEAD"),
                "dirty": _git_dirty(),
            },
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "backend": "mock",
            "deterministic": True,
            "command": command,
            "data_profile": asdict(active_profile),
        },
        "evaluations": evaluations,
        "summary": summary,
    }
    return seal_artifact(artifact)


def verify_artifact(artifact: Mapping[str, Any]) -> dict:
    if artifact.get("schema_id") != SCHEMA_ID:
        raise ArtifactVerificationError(f"unsupported schema_id: {artifact.get('schema_id')!r}")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactVerificationError(
            f"unsupported schema_version: {artifact.get('schema_version')!r}"
        )
    run = artifact.get("run")
    if not isinstance(run, dict):
        raise ArtifactVerificationError("run metadata is missing")
    if run.get("backend") != "mock" or run.get("deterministic") is not True:
        raise ArtifactVerificationError("public artifact must use the deterministic mock backend")
    profile = run.get("data_profile")
    if not isinstance(profile, dict) or profile.get("public_reproducible") is not True:
        raise ArtifactVerificationError("public artifact requires a reproducible data profile")
    integrity = artifact.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
        raise ArtifactVerificationError("integrity block is missing or unsupported")
    body = dict(artifact)
    body.pop("integrity", None)
    expected_payload = _sha256(body)
    if integrity.get("payload_sha256") != expected_payload:
        raise ArtifactVerificationError("payload_sha256 mismatch")
    expected_semantic = _sha256(semantic_projection(artifact))
    if integrity.get("semantic_sha256") != expected_semantic:
        raise ArtifactVerificationError("semantic_sha256 mismatch")
    summary = recompute_summary(artifact.get("evaluations") or {})
    if artifact.get("summary") != summary:
        raise ArtifactVerificationError("top-level summary disagrees with raw evaluations")
    return summary


def write_artifact(artifact: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_artifact(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
