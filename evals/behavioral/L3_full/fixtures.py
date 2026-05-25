"""L3 fixture 工厂 — 参数化生成 100+ case 矩阵。

每 case dict：
    case_id, persona, scenario, query, expected_signals (list[S1-S5])
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))


@dataclass
class L3Case:
    case_id: str
    persona: str
    scenario: str
    query: str
    expected_signals: list[str] = field(default_factory=list)
    notes: str = ""


# ============================================================
# Persona × Scenario 模板矩阵
# ============================================================

PERSONAS = ["family", "friends", "solo", "with_parents"]

# 每 scenario 对应一组 5 个 query 变体（覆盖不同语气 / 长度 / 噪声）
# 注：S5 仅在 scenario=important_dinner 触发
# 注：S4 仅在 scenario=weekday_lunch 触发
SCENARIO_TEMPLATES: dict[str, dict] = {
    "normal_weekend": {
        "expected_signals_base": ["S1", "S2", "S3"],   # 默认场景三信号
        "queries_by_persona": {
            "family": [
                "今天下午带 5 岁娃出去玩，别离家太远，4 小时左右",
                "带娃下午溜达，老婆减脂",
                "周六下午全家出门",
                "周日带 6 岁娃和老婆出门",
                "下午想找个地方陪孩子",
            ],
            "friends": [
                "4 个朋友周六下午出去玩，2 男 2 女，能聊天",
                "4 人下午雍和宫附近溜达吃饭",
                "周末和朋友找个地方下午茶",
                "几个朋友想约下午",
                "4 个人想找个能 hang out 的地方",
            ],
            "solo": [
                "一个人下午想出去走走",
                "周末自己去南锣转转",
                "一个人想找咖啡店看书",
                "下午独自溜达",
                "想自己安静度过周末下午",
            ],
            "with_parents": [
                "周末带父母出门走走",
                "下午带爹妈逛逛",
                "和父母想找个不太累的地方",
                "周日带两位老人出门",
                "想带老人下午散步喝茶",
            ],
        },
    },
    "important_dinner": {
        "expected_signals_base": ["S1", "S2", "S5"],
        "queries_by_persona": {
            "family": [
                "老婆生日带娃带双方父母 6 人吃饭",
                "妈妈生日全家 7 人聚餐",
                "孩子生日 8 个人吃饭找地方",
                "双方父母第一次见面家宴",
                "结婚纪念日 6 人吃饭",
            ],
            "friends": [
                "朋友结婚纪念日饭",
                "好朋友生日 6 个人聚餐",
                "10 年同学聚会 8 人吃饭",
                "朋友升职聚 7 人",
                "朋友结婚前夜 6 人聚餐",
            ],
            "solo": [
                "庆祝自己生日想一个人吃顿好的",   # 改：加明确"庆祝+生日"
                "庆祝转正自己吃饭",
                "重要节日想一个人去好餐厅",
                "考过证想犒劳自己",
                "想一个人去米其林",
            ],
            "with_parents": [
                "家宴老人首次见",
                "父亲生日带二老吃饭",
                "妈妈生日 6 人聚餐",
                "父母结婚纪念日聚",
                "带父母 6 人正式聚餐",
            ],
        },
    },
    "rainy_day": {
        "expected_signals_base": ["S1", "S2", "S3"],
        "queries_by_persona": {
            "family": [
                "下大雨想找室内地方带娃",
                "雨天带 5 岁娃想去博物馆",
                "下雨天和老婆娃找个室内的地方",
                "雷雨天带娃想去书店",
                "周六下雨找室内活动",
            ],
            "friends": [
                "下雨天 4 个人找室内地方",
                "雨天朋友们想去咖啡店",
                "下大雨找个能聊天的室内地方",
                "雷阵雨想约室内活动",
                "雨天 4 人找博物馆",
            ],
            "solo": [
                "下雨天一个人想去书店",
                "雨天想自己安静待会儿",
                "下大雨找个室内的咖啡馆",
                "雷雨想自己看展",
                "雨天独自找个屋",
            ],
            "with_parents": [
                "下雨天带父母去博物馆",
                "雨天带二老找个室内的地方",
                "下大雨想带父母看个展",
                "雷雨带爹妈去美术馆",
                "雨天和父母找室内活动",
            ],
        },
    },
    "weekday_lunch": {
        "expected_signals_base": ["S1", "S4"],
        "queries_by_persona": {
            "family": [
                "周一中午临时请假带娃出门",
                "礼拜三中午想和老婆约饭",
                "工作日中午带娃溜达",
                "周二午休去吃饭",
                "礼拜四午休带孩子出门",
            ],
            "friends": [
                "周一中午一起吃个饭",
                "周三下班后聚一下",
                "工作日下午想溜达",
                "礼拜二中午约朋友",
                "周五下班前聚",
            ],
            "solo": [
                "周一中午自己出去吃个饭",
                "工作日午休一个人去吃",
                "周三午休出去走走",
                "礼拜四中午自己吃饭",
                "周二中午请假",
            ],
            "with_parents": [
                "周一中午陪父母吃饭",
                "工作日中午带二老吃",
                "礼拜二中午带爹妈出门",
                "周三午休陪老人",
                "周四中午带父母逛",
            ],
        },
    },
    "friday_night": {
        "expected_signals_base": ["S1", "S2"],
        "queries_by_persona": {
            "family": [
                "周五晚带老婆和大点的娃吃饭",
                "周五下班全家吃饭",
                "周五晚带 8 岁娃外出",
                "周五晚老婆生日吃饭",
                "周五下班带娃吃饭",
            ],
            "friends": [
                "周五下班后跟同事喝点",
                "周五晚去簋街吃宵夜",
                "周五晚 4 人聚",
                "周五下班朋友吃烤串",
                "周五晚约朋友吃饭",
            ],
            "solo": [
                "周五下班自己去吃个饭",
                "周五晚一个人去酒馆",
                "周五自己想吃宵夜",
                "周五下班自己放松",
                "周五晚一个人去烤肉店",
            ],
            "with_parents": [
                "周五晚带父母吃饭",
                "周五下班带爹妈聚",
                "周五晚带二老外出",
                "周五带父母吃饭",
                "周五晚和老人聚餐",
            ],
        },
    },
}


def build_all_cases() -> list[L3Case]:
    cases: list[L3Case] = []
    for scenario, tmpl in SCENARIO_TEMPLATES.items():
        for persona in PERSONAS:
            queries = tmpl["queries_by_persona"].get(persona, [])
            base_signals = tmpl["expected_signals_base"]
            for i, q in enumerate(queries, 1):
                cases.append(L3Case(
                    case_id=f"{scenario}_{persona}_{i:02d}",
                    persona=persona,
                    scenario=scenario,
                    query=q,
                    expected_signals=list(base_signals),
                ))
    return cases


if __name__ == "__main__":
    all_cases = build_all_cases()
    print(f"L3 fixture：共 {len(all_cases)} case")
    by_scenario: dict[str, int] = {}
    by_persona: dict[str, int] = {}
    for c in all_cases:
        by_scenario[c.scenario] = by_scenario.get(c.scenario, 0) + 1
        by_persona[c.persona] = by_persona.get(c.persona, 0) + 1
    print(f"  by scenario: {by_scenario}")
    print(f"  by persona:  {by_persona}")
    print(f"\n前 3 case：")
    for c in all_cases[:3]:
        print(f"  {c.case_id}: {c.query!r}  signals={c.expected_signals}")
