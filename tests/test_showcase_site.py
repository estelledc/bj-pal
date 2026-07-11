from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROMO = ROOT / "promo"
PAGE = PROMO / "landing-page.html"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class _ShowcaseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.document_language = ""
        self.ids: Set[str] = set()
        self.links: List[str] = []
        self.images: List[Dict[str, str]] = []
        self.meta: Dict[str, str] = {}
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        data = {key: value or "" for key, value in attrs}
        if tag == "html":
            self.document_language = data.get("lang", "")
        if data.get("id"):
            self.ids.add(data["id"])
        if tag == "a" and data.get("href"):
            self.links.append(data["href"])
        if tag == "img":
            self.images.append(data)
        if tag == "meta":
            key = data.get("property") or data.get("name")
            if key:
                self.meta[key] = data.get("content", "")
        if tag == "link" and data.get("rel") == "canonical":
            self.canonical = data.get("href", "")


class ShowcaseSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.page = _ShowcaseParser()
        cls.page.feed(cls.html)

    def test_case_has_complete_public_story_and_share_metadata(self) -> None:
        self.assertEqual(self.page.document_language, "zh-CN")
        self.assertTrue({"main", "problem", "role", "system", "outcomes", "evidence", "limits"} <= self.page.ids)
        self.assertEqual(self.page.canonical, "https://estelledc.github.io/bj-pal/")
        for key in ["description", "og:title", "og:description", "og:image", "twitter:card"]:
            with self.subTest(metadata=key):
                self.assertTrue(self.page.meta.get(key))
        self.assertIn('type="application/ld+json"', self.html)
        self.assertIn('<link rel="icon" type="image/png" href="favicon.png"', self.html)
        self.assertTrue((PROMO / "favicon.png").exists())

    def test_navigation_images_and_local_evidence_are_accessible(self) -> None:
        self.assertIn('class="skip-link"', self.html)
        self.assertIn(":focus-visible", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertNotIn("width=1920", self.html)

        for image in self.page.images:
            with self.subTest(image=image.get("src")):
                self.assertTrue(image.get("alt"))
                self.assertTrue((PROMO / image["src"]).exists())

        for link in self.page.links:
            parsed = urlparse(link)
            if parsed.scheme or link.startswith("//"):
                continue
            if link.startswith("#"):
                self.assertIn(link[1:], self.page.ids)
                continue
            with self.subTest(link=link):
                self.assertTrue((PROMO / parsed.path).exists())

    def test_role_attribution_separates_project_lead_from_team_credit(self) -> None:
        for expected in [
            "Jason / Project lead",
            "项目负责人；主导产品系统设计、Agent 编排、评测与公开展示",
            "KeepL · 共同作者",
            "不代表所有团队产出均由一人完成",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.html)

    def test_navigation_includes_stable_about_and_resume_routes(self) -> None:
        for route in [
            "https://estelledc.github.io/about/",
            "https://estelledc.github.io/resume/",
        ]:
            with self.subTest(route=route):
                self.assertGreaterEqual(self.page.links.count(route), 2)

    def test_pages_actions_are_pinned_to_immutable_commits(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        pinned = re.findall(r"uses: actions/[\w-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+", workflow)
        self.assertEqual(len(pinned), 4)


if __name__ == "__main__":
    unittest.main()
