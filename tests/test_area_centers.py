import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import fetch_amap_real_pois as fetcher  # noqa: E402
from tools.amap_search import resolve_area_center  # noqa: E402


class AreaCenterCoverageTest(unittest.TestCase):
    def test_fetch_script_covers_weekend_leisure_expansion_areas(self) -> None:
        expected = {
            "798艺术区片区",
            "前门-大栅栏片区",
            "西单片区",
            "东直门-簋街片区",
            "五道口片区",
            "望京片区",
            "亮马桥片区",
            "国贸-CBD片区",
            "朝阳公园片区",
            "中国美术馆-五四大街片区",
            "牛街片区",
        }

        self.assertEqual(expected - set(fetcher.AREA_CENTERS), set())

    def test_manual_area_aliases_resolve_to_centers(self) -> None:
        aliases = (
            "798 艺术区",
            "前门",
            "大栅栏",
            "西单",
            "东直门",
            "簋街",
            "五道口",
            "望京",
            "亮马桥",
            "国贸",
            "朝阳公园",
            "中国美术馆",
            "牛街",
        )

        unresolved = [alias for alias in aliases if resolve_area_center(alias) is None]
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
