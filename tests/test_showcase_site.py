from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROMO = ROOT / "promo"
PAGE = PROMO / "landing-page.html"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
HERO = ROOT / "src" / "ui" / "hero.py"


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
            "Jason Xun · 项目负责人",
            "KeepL · 共同作者",
            "AI · 协作工具",
            "归因不代表所有团队产出均由一人完成",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.html)

    def test_first_view_surfaces_status_attribution_and_bounded_evidence(self) -> None:
        first_view = self.html[: self.html.index('<figure class="hero-figure">')]
        for expected in [
            "Public release · v6.23.0",
            "Jason Xun · 项目负责人",
            "KeepL · 共同作者",
            "900 passed",
            "903 collected · 3 real-cache skipped",
            "1 provider run · 0 real users",
            "1464 实报 token · 单个 synthetic 场景",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, first_view)

    def test_identity_uses_the_canonical_portfolio_person(self) -> None:
        payload = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(payload)
        data = json.loads(payload.group(1))
        creator = next(item for item in data["creator"] if item.get("@type") == "Person" and item.get("@id"))
        self.assertEqual(creator["@id"], "https://estelledc.github.io/#person")
        self.assertEqual(creator["name"], "Jason Xun")
        self.assertEqual(self.page.meta["author"], "Jason Xun · KeepL")
        self.assertNotIn('"name": "Jason"', self.html)

    def test_evaluation_heading_describes_observation_not_verified_outcomes(self) -> None:
        self.assertIn("04 / Reproducible engineering evidence", self.html)
        self.assertIn("Evidence ladder · 四层证据不混写", self.html)
        self.assertNotIn("Verified outcomes", self.html)

    def test_decision_trace_replays_a_sourced_observed_case_without_javascript(self) -> None:
        for expected in [
            'class="jx-case-question"',
            'id="decision-trace"',
            'type="radio" name="decision-trace"',
            'for="trace-intent"',
            'for="trace-plan"',
            'for="trace-probe"',
            'for="trace-replan"',
            "SCENARIO · S01",
            "queue 65 min",
            "queue 85 min",
            "五道营胡同 → 国子监",
            "雍和宫 → 北京孔庙",
            "Observed historical run · 不是用户研究",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.html)
        self.assertNotIn("addEventListener", self.html)
        self.assertIn('trace-intent:focus-visible', self.html)

    def test_sources_verification_and_next_work_are_explicit(self) -> None:
        self.assertGreaterEqual(self.html.count('class="source-tag"'), 6)
        self.assertIn("最后验证 2026-07-21", self.html)
        self.assertIn("来源类型：Observed historical run", self.html)
        self.assertIn("Next product system · 03", self.html)
        self.assertIn("https://estelledc.github.io/practicemate/", self.page.links)

    def test_narrow_view_and_motion_preferences_are_contractually_supported(self) -> None:
        self.assertIn("@media (max-width: 360px)", self.html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.html)
        self.assertIn("overflow-wrap: anywhere", self.html)

    def test_interaction_motion_is_input_appropriate_and_repeat_safe(self) -> None:
        hover_start = self.html.index("@media (hover: hover) and (pointer: fine)")
        hover_end = self.html.index("@media (max-width: 980px)", hover_start)
        hover_rules = self.html[hover_start:hover_end]
        before_hover_rules = self.html[:hover_start]

        self.assertIn(".button:hover:not(:active)", hover_rules)
        self.assertIn(".evidence-card:hover:not(:active)", hover_rules)
        self.assertNotIn(".button:hover", before_hover_rules)
        self.assertNotIn(".evidence-card:hover", before_hover_rules)
        self.assertIn(".button:active", self.html)
        self.assertIn(".evidence-card:active", self.html)

        reduced_start = self.html.index("@media (prefers-reduced-motion: reduce)")
        reduced_end = self.html.index("@media print", reduced_start)
        reduced_rules = self.html[reduced_start:reduced_end]
        self.assertIn("opacity 120ms ease-out", reduced_rules)
        self.assertIn("transform: none", reduced_rules)
        self.assertNotIn("transition-duration: 0.01ms", reduced_rules)

        replay_start = self.html.index(".trace-panel")
        replay_end = self.html.index(".trace-observation", replay_start)
        replay_rules = self.html[replay_start:replay_end]
        self.assertNotIn("animation:", replay_rules)
        self.assertNotIn("transition:", replay_rules)

        hero_source = HERO.read_text(encoding="utf-8")
        self.assertNotIn("animation:", hero_source)
        self.assertNotIn("@keyframes", hero_source)
        self.assertNotIn("ease-in", hero_source)

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

    def test_static_publish_fallbacks_are_present_and_safe(self) -> None:
        robots = (PROMO / "robots.txt").read_text(encoding="utf-8")
        sitemap = (PROMO / "sitemap.xml").read_text(encoding="utf-8")
        not_found = (PROMO / "404.html").read_text(encoding="utf-8")
        self.assertIn("Sitemap: https://estelledc.github.io/bj-pal/sitemap.xml", robots)
        self.assertIn("https://estelledc.github.io/bj-pal/", sitemap)
        self.assertIn('name="robots" content="noindex, nofollow"', not_found)
        for outlet in [
            'href="/bj-pal/"',
            'href="https://estelledc.github.io/"',
            'href="https://estelledc.github.io/about/"',
            'href="https://estelledc.github.io/resume/"',
            'href="https://github.com/estelledc"',
        ]:
            with self.subTest(outlet=outlet):
                self.assertIn(outlet, not_found)

    def test_public_sources_do_not_disclose_local_workspace_paths(self) -> None:
        checked = [
            PROMO / "landing-page.html",
            PROMO / "README.md",
            ROOT / "docs" / "DEMO_SCRIPT.md",
            ROOT / "docs" / "EVAL_FRAMEWORK.md",
            ROOT / "docs" / "archive" / "V2.4_ITERATION_PLAN.md",
            ROOT / "src" / "agents" / "llm_client.py",
        ]
        forbidden = [
            "/Users/",
            "/home/",
            "~/intern-journal",
            "intern-journal/",
            "global memory",
            "user_background.md",
        ]
        for file in checked:
            content = file.read_text(encoding="utf-8")
            for needle in forbidden:
                with self.subTest(file=file.relative_to(ROOT), needle=needle):
                    self.assertNotIn(needle, content)


if __name__ == "__main__":
    unittest.main()
