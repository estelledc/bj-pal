from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.weather.acceptance import build_weather_acceptance  # noqa: E402
from evals.weather.verify import verify_weather_acceptance  # noqa: E402


FIXTURE = ROOT / "fixtures" / "weather" / "beijing_synthetic.json"


def test_weather_acceptance_is_explicitly_offline_and_independently_verifiable(tmp_path) -> None:
    artifact = build_weather_acceptance(
        fixture_path=FIXTURE,
        generated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    path = tmp_path / "weather.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")

    verified = verify_weather_acceptance(path, FIXTURE)

    assert verified["acceptance_level"] == "offline_contract_only"
    assert verified["live_network_used"] is False
    assert verified["live_provider_accepted"] is False
    assert verified["decision_checks"]["outdoor_action"] == "reroute"
    assert verified["decision_checks"]["indoor_action"] == "proceed"


def test_weather_verifier_rejects_a_forged_live_claim(tmp_path) -> None:
    artifact = build_weather_acceptance(fixture_path=FIXTURE)
    artifact["live_provider_accepted"] = True
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_weather_acceptance(path, FIXTURE)
