"""v2.5 D2 多模态首屏 — 文本 intake。

镜像 vision_extractor.py 的能力，但接文本输入：
- 公众号文章片段
- 朋友圈 / 微信对话截图的 OCR 后文本
- 用户手贴的"我朋友说 xxx 那家不错"

输出与 vision 一致的 schema：{area_anchor, poi_name, aspects, taste_tags, scene_tags}
让下游 ranking / preference_mirror 可以用同一套消费链路。

设计：
- 优先走 LLM 抽取（LongCat / mock）
- 失败时走规则 fallback（关键词匹配）
- 抽完入 SQLite ugc_aspects 表（source_platform="user_text"）
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .llm_client import LLMClient, LLMResponse, get_llm_client  # noqa: E402
from .tracing import trace_span  # noqa: E402


TEXT_INTAKE_SYSTEM = """你是 BJ-Pal Text Intake。给一段用户提供的文本（可能是公众号文章、朋友圈、聊天截图 OCR、口头转述等），抽取与北京活动规划相关的结构化信号。

只识别北京区域 / 北京 POI。如果文本明显不在北京，返回空 aspects 列表。

schema：
```
{
  "area_anchor": "<五道营-雍和宫片区 / 三里屯片区 / 什刹海-鼓楼片区 / 王府井-东单片区 / 798 / 南锣鼓巷 / ... / 空字符串>",
  "poi_name": "<文本中提到的具体店名 / 空字符串>",
  "taste_tags": ["coffee", "dessert", "meat", "spicy", "yogurt", "fruit", "drink"],
  "scene_tags": ["citywalk", "photo", "indoor", "outdoor", "quiet", "loud", "kid_friendly", "elderly_friendly"],
  "risk_tags": ["queue_long", "expensive", "loud", "crowded", "closed_often"],
  "aspects": [
    {
      "aspect_type": "environment | comfort | food | budget | crowd | transport | scenario_fit",
      "sentiment": "positive | negative | mixed",
      "confidence": 0.0-1.0,
      "evidence_summary": "<30-60 字 用户原话或概括>"
    }
  ]
}
```

抽取要点：
- 一段 100-500 字的文本通常给 2-5 条 aspect
- 没明确证据的字段不要编造，taste_tags / scene_tags / risk_tags 都可以为空数组
- 只输出 JSON，不要任何其他文字、不要 markdown 包裹

只输出 JSON。
"""


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TextIntakeResult:
    area_anchor: str = ""
    poi_name: str = ""
    taste_tags: list[str] = field(default_factory=list)
    scene_tags: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)
    aspects: list[dict] = field(default_factory=list)
    source: str = "llm"   # "llm" | "rules" | "empty"

    def is_empty(self) -> bool:
        return not (self.poi_name or self.area_anchor or self.taste_tags
                    or self.scene_tags or self.aspects)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 规则 fallback 关键词字典
# ============================================================

AREA_KEYWORDS = {
    "五道营-雍和宫片区": ["五道营", "雍和宫", "国子监", "地坛", "簋街"],
    "三里屯片区": ["三里屯", "工体", "蓝色港湾"],
    "什刹海-鼓楼片区": ["什刹海", "鼓楼", "南锣", "南锣鼓巷", "钟楼"],
    "王府井-东单片区": ["王府井", "东单", "国贸", "东方新天地"],
    "798": ["798", "酒厂"],
    "故宫片区": ["故宫", "紫禁城", "天安门"],
    "颐和园-清华片区": ["颐和园", "清华", "圆明园"],
}

TASTE_KEYWORDS = {
    "coffee": ["咖啡", "cafe", "café", "拿铁", "espresso", "美式"],
    "dessert": ["甜品", "蛋糕", "马卡龙", "甜筒", "可丽饼", "提拉米苏"],
    "meat": ["烤鸭", "牛排", "烤肉", "羊蝎子", "肉饼", "炖肉"],
    "spicy": ["辣", "麻辣", "川菜", "重庆", "火锅", "酸辣"],
    "yogurt": ["酸奶"],
    "fruit": ["水果", "鲜果", "果切"],
    "drink": ["酒吧", "鸡尾酒", "餐酒", "红酒", "精酿"],
}

SCENE_KEYWORDS = {
    "citywalk": ["遛弯", "散步", "溜达", "citywalk", "走走"],
    "photo": ["拍照", "出片", "网红", "打卡", "美照", "好看"],
    "indoor": ["室内", "博物馆", "书店", "商场", "美术馆"],
    "outdoor": ["户外", "公园", "胡同", "街区"],
    "quiet": ["安静", "清净", "舒服", "放松"],
    "loud": ["热闹", "氛围感", "派对", "嗨"],
    "kid_friendly": ["带娃", "亲子", "小朋友", "孩子"],
    "elderly_friendly": ["老人", "父母", "长辈"],
}

RISK_KEYWORDS = {
    "queue_long": ["排队", "等位", "排号", "排很久", "等了 1 小时"],
    "expensive": ["贵", "肉痛", "人均 500", "人均一千", "踩雷价"],
    "loud": ["吵", "嘈杂", "闹"],
    "crowded": ["挤", "人满", "爆满"],
    "closed_often": ["不开门", "经常关", "不规律营业"],
}


def _rules_extract(text: str) -> TextIntakeResult:
    """规则 fallback：纯关键词扫描，离线可跑。"""
    text_lower = text.lower()
    result = TextIntakeResult(source="rules")

    # area
    for area, kws in AREA_KEYWORDS.items():
        if any(kw in text or kw.lower() in text_lower for kw in kws):
            result.area_anchor = area
            break

    # taste
    for tag, kws in TASTE_KEYWORDS.items():
        if any(kw in text or kw.lower() in text_lower for kw in kws):
            result.taste_tags.append(tag)

    # scene
    for tag, kws in SCENE_KEYWORDS.items():
        if any(kw in text or kw.lower() in text_lower for kw in kws):
            result.scene_tags.append(tag)

    # risk
    for tag, kws in RISK_KEYWORDS.items():
        if any(kw in text or kw.lower() in text_lower for kw in kws):
            result.risk_tags.append(tag)

    # poi_name 启发式：找形如 "xxx店"/"xxx馆"/书名号包围
    m = re.search(r"《([^》]{2,15})》", text)
    if m:
        result.poi_name = m.group(1)
    else:
        # 一些常见 POI 字面
        for kw in ["金鼎轩", "胡大饭馆", "京兆尹", "雍和炸鸡", "南锣鼓巷"]:
            if kw in text:
                result.poi_name = kw
                break

    if result.is_empty():
        result.source = "empty"
    return result


# ============================================================
# 主接口
# ============================================================

def extract_from_text(
    raw: str,
    *,
    client: Optional[LLMClient] = None,
    use_llm: bool = True,
) -> TextIntakeResult:
    """从文本抽取结构化信号。

    Args:
        raw: 用户原文（公众号片段 / 朋友圈 / 聊天）
        client: LLM client（None 走默认）
        use_llm: False 时直接走规则 fallback，跳过 LLM

    Returns:
        TextIntakeResult；失败/空文本返回 source="empty"
    """
    if not raw or not raw.strip():
        return TextIntakeResult(source="empty")

    # 太长截断（避免 LLM token 超额）
    text = raw.strip()[:2000]

    if not use_llm:
        return _rules_extract(text)

    with trace_span("text_intake.extract", attrs={"len": len(text)}):
        client = client or get_llm_client()
        try:
            resp = client.complete(
                system=TEXT_INTAKE_SYSTEM,
                user=f"请按 schema 抽取下面这段文本的结构化信号。只输出 JSON。\n\n---\n{text}\n---",
                json_schema={"intake": True},
                temperature=0.2,
            )
            parsed = resp.parsed or _safe_parse_json(resp.text)
            if parsed and isinstance(parsed, dict) and "aspects" in parsed:
                return _result_from_parsed(parsed)
        except Exception:
            pass
        # LLM 失败 → 规则 fallback
        return _rules_extract(text)


def _result_from_parsed(d: dict) -> TextIntakeResult:
    """LLM 返回 dict → TextIntakeResult。"""
    return TextIntakeResult(
        area_anchor=d.get("area_anchor", "") or "",
        poi_name=d.get("poi_name", "") or "",
        taste_tags=list(d.get("taste_tags", []) or []),
        scene_tags=list(d.get("scene_tags", []) or []),
        risk_tags=list(d.get("risk_tags", []) or []),
        aspects=list(d.get("aspects", []) or []),
        source="llm",
    )


def _safe_parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s[s.find("\n") + 1:]
        if s.endswith("```"):
            s = s[:-3]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# ============================================================
# 偏好注入：合并 TextIntakeResult 到 user query
# ============================================================

def merge_into_user_input(
    base_input: str,
    intake: TextIntakeResult,
    *,
    intent_hint: str = "",
) -> str:
    """把 intake 信号附加到用户原 query 后面，给 planner 看。

    生成的字符串结构：
        <base_input>

        [来自外部输入的偏好提示]
        - 关注片区: 五道营-雍和宫片区
        - 心仪 POI: 金鼎轩
        - 口味偏好: coffee, dessert
        - 场景标签: citywalk, photo
        - 需要规避: queue_long
    """
    if intake.is_empty():
        return base_input

    lines = ["", "[来自外部输入的偏好提示]"]
    if intake.area_anchor:
        lines.append(f"- 关注片区: {intake.area_anchor}")
    if intake.poi_name:
        lines.append(f"- 心仪 POI: {intake.poi_name}")
    if intake.taste_tags:
        lines.append(f"- 口味偏好: {', '.join(intake.taste_tags)}")
    if intake.scene_tags:
        lines.append(f"- 场景标签: {', '.join(intake.scene_tags)}")
    if intake.risk_tags:
        lines.append(f"- 需要规避: {', '.join(intake.risk_tags)}")
    if intent_hint:
        lines.append(f"- 用户意图: {intent_hint}")

    return base_input + "\n".join(lines) if base_input else "\n".join(lines[1:])


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # Case A: 公众号文章片段（含明确片区 + 多个 taste/scene）
    article = """
    周末去了五道营胡同，发现一家叫静水的小馆很赞。
    咖啡和甜品都有惊喜，环境安静适合带娃看绘本。
    人均 80 左右，不算贵。建议中午去，下午容易排队。
    """
    r = extract_from_text(article, use_llm=False)
    print(f"\n[A] rules: area={r.area_anchor!r} poi={r.poi_name!r}")
    print(f"    taste={r.taste_tags}  scene={r.scene_tags}  risk={r.risk_tags}")
    assert r.area_anchor == "五道营-雍和宫片区"
    assert "coffee" in r.taste_tags or "dessert" in r.taste_tags
    assert "kid_friendly" in r.scene_tags

    # Case B: 朋友圈短句
    weibo = "今天三里屯逛了一下午，太挤了，火锅店还排队 1 小时"
    r = extract_from_text(weibo, use_llm=False)
    print(f"\n[B] rules: area={r.area_anchor!r}  taste={r.taste_tags}  risk={r.risk_tags}")
    assert r.area_anchor == "三里屯片区"
    assert "spicy" in r.taste_tags
    assert "queue_long" in r.risk_tags

    # Case C: 空文本
    r = extract_from_text("", use_llm=False)
    print(f"\n[C] empty: source={r.source}")
    assert r.source == "empty"

    # Case D: 不在北京
    r = extract_from_text("上海徐汇区有家咖啡店不错", use_llm=False)
    print(f"\n[D] non-bj rules: area={r.area_anchor!r}")
    # 规则版可能误命中 "咖啡" → taste，但没有北京 area
    # area_anchor 应为空（没北京 area 关键词）
    assert r.area_anchor == ""

    # Case E: merge_into_user_input
    intake = TextIntakeResult(
        area_anchor="五道营-雍和宫片区",
        poi_name="静水",
        taste_tags=["coffee", "dessert"],
        scene_tags=["quiet", "kid_friendly"],
    )
    merged = merge_into_user_input("4 人下午找地方", intake, intent_hint="想避开嘈杂")
    print(f"\n[E] merged:\n{merged}")
    assert "五道营" in merged
    assert "静水" in merged

    print("\n所有 text_intake 自测通过！")
