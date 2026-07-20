"""Opt-in live weather smoke test.

This script never runs from ``make check``. It requires the caller to select a
lawful Open-Meteo mode through environment variables, then performs one network
fetch plus one cache read using a public BJ-Pal area anchor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from providers import OpenMeteoWeatherProvider, WeatherRequest, create_weather_provider  # noqa: E402
from tools.amap_search import resolve_area_center  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--area-anchor", default="五道营-雍和宫片区")
    parser.add_argument("--target-local-time", default="14:00")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "results" / "weather-live-smoke.json",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("live weather access requires the explicit --live flag")
    if os.environ.get("BJ_PAL_WEATHER_PROVIDER") != "open_meteo":
        parser.error("set BJ_PAL_WEATHER_PROVIDER=open_meteo and an authorized usage mode")

    provider = create_weather_provider()
    if not isinstance(provider, OpenMeteoWeatherProvider):
        parser.error("live weather provider configuration did not resolve to Open-Meteo")
    center = resolve_area_center(args.area_anchor)
    if center is None:
        parser.error("area anchor has no public center coordinate")
    request = WeatherRequest(
        latitude=center[1],
        longitude=center[0],
        target_local_time=args.target_local_time,
        target_date=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
    )
    first = provider.forecast(request)
    second = provider.forecast(request)
    if first.cache_status != "miss" or second.cache_status != "hit":
        raise RuntimeError("live weather smoke did not preserve miss-then-hit cache behavior")

    artifact = {
        "schema_version": 1,
        "artifact_type": "weather_live_smoke",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "live_network_used": True,
        "usage_mode": os.environ.get("BJ_PAL_OPEN_METEO_USAGE"),
        "area_anchor": args.area_anchor,
        "coordinate_scope": "public_area_anchor",
        "first": first.to_decision_context(),
        "second_cache_status": second.cache_status,
        "limitations": [
            "A successful smoke test proves one bounded response, not forecast accuracy or SLA.",
            "Commercial/promotional deployment still requires the configured commercial or self-hosted authorization.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "weather live smoke: "
        f"provider={first.provider} freshness={first.freshness} cache={second.cache_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
