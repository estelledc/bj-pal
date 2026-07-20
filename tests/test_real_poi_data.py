import sys
import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_mock_data import _build_pois  # noqa: E402
from scripts.build_mock_data import REAL_POI_FILE  # noqa: E402


@unittest.skipUnless(
    REAL_POI_FILE.exists(),
    "requires the local real AMap POI cache; public demo profile is synthetic",
)
class RealPOIDataTest(unittest.TestCase):
    def test_generated_poi_dataset_has_expanded_real_candidate_volume(self) -> None:
        pois = _build_pois()

        self.assertGreaterEqual(len(pois), 5000)

    def test_generated_poi_dataset_does_not_expose_fake_pois(self) -> None:
        pois = _build_pois()
        fake = [
            p for p in pois
            if p["provider_poi_id"].startswith(("P_SUP_", "P_MOCK_"))
            or "mock 地址" in (p.get("address") or "")
            or re.search(r"模拟(家常菜|咖啡厅|甜品店|京味小吃|轻食)\d{4}", p["name"])
        ]

        self.assertEqual(fake[:10], [])

    def test_generated_poi_dataset_excludes_inactive_places(self) -> None:
        pois = _build_pois()
        inactive = [
            p for p in pois
            if any(marker in p["name"] for marker in ("暂停营业", "装修中", "已关闭", "歇业"))
        ]

        self.assertEqual(inactive[:10], [])


if __name__ == "__main__":
    unittest.main()
