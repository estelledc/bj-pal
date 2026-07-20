"""Regression tests for explicit dietary negation in the offline extractor."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.text_intake import extract_from_text  # noqa: E402


def test_no_spicy_is_a_constraint_not_a_positive_taste() -> None:
    result = extract_from_text("我老婆不吃辣，避开海鲜", use_llm=False)

    assert "no_spicy" in result.diet_flags
    assert "spicy" not in result.taste_tags
    assert "no_seafood" in result.risk_tags


def test_light_diet_and_child_scene_survive_rule_extraction() -> None:
    result = extract_from_text(
        "带 5 岁娃出去玩，老婆减脂不吃辣，找咖啡店",
        use_llm=False,
    )

    assert {"light_diet", "no_spicy"}.issubset(result.diet_flags)
    assert "kid_friendly" in result.scene_tags
    assert "coffee" in result.taste_tags
