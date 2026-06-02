import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.build_mock_data import _build_pois  # noqa: E402
from tools.types import haversine_km  # noqa: E402


class MockDataGeographyTest(unittest.TestCase):
    def test_qianmen_named_supplemental_pois_are_near_qianmen(self) -> None:
        pois = _build_pois()
        generated_qianmen = [
            p for p in pois
            if p["provider_poi_id"].startswith("P_SUP_")
            and p["name"].startswith("前门")
        ]
        if not generated_qianmen:
            self.skipTest("real AMap POI cache is active; no generated P_SUP_* POIs")

        by_id = {p["provider_poi_id"]: p for p in pois}
        qianmen = by_id["P_QMDJ"]
        self.assertGreaterEqual(len(generated_qianmen), 10)
        far = [
            (p["name"], haversine_km(
                qianmen["longitude"], qianmen["latitude"],
                p["longitude"], p["latitude"],
            ))
            for p in generated_qianmen
            if haversine_km(
                qianmen["longitude"], qianmen["latitude"],
                p["longitude"], p["latitude"],
            ) > 0.6
        ]

        self.assertEqual(far, [])

    def test_supplemental_pois_do_not_overlap_within_area(self) -> None:
        pois = _build_pois()
        by_area: dict[str, list[dict]] = {}
        for poi in pois:
            if not poi["provider_poi_id"].startswith("P_SUP_"):
                continue
            by_area.setdefault(poi["business_area"], []).append(poi)

        duplicates = []
        for area, area_pois in by_area.items():
            seen = {}
            for poi in area_pois:
                key = (poi["longitude"], poi["latitude"])
                if key in seen:
                    duplicates.append((area, seen[key], poi["name"], key))
                else:
                    seen[key] = poi["name"]

        self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
