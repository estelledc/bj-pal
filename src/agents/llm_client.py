"""LLM client 抽象层。

环境变量 BJ_PAL_LLM 控制后端：
    mock     — 默认；规则 + 模板生成结构化输出，不联网，离线可跑
    longcat  — 美团自研（占位，TODO 接入）
    anthropic — Claude API（你 .env 里有 ANTHROPIC_API_KEY 就能用）

接口：
    client = get_llm_client()
    response = client.complete(system, user, json_schema=None)
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# ============================================================
# .env 自动加载（复用 activity-planner 配的 LONGCAT_*）
# ============================================================

def _autoload_env():
    """优先级：进程已设置 > BJ-Pal 本地 .env > activity-planner/.env"""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",  # bj-pal/.env
        Path(__file__).resolve().parents[5] / "activity-planner" / ".env",
        Path.home() / "intern-journal" / "activity-planner" / ".env",
    ]
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        # 没装 dotenv 就手工解析
        load_dotenv = None
    for p in candidates:
        if p.exists():
            if load_dotenv is not None:
                load_dotenv(p, override=False)
            else:
                _manual_dotenv(p)
            break


def _manual_dotenv(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        os.environ.setdefault(k, v)


_autoload_env()


@dataclass
class LLMResponse:
    text: str
    parsed: Optional[dict] = None  # 当 json_schema 给定时，pre-parsed dict
    raw: Optional[Any] = None      # 原 response 对象，调试用


class LLMClient(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        json_schema: Optional[dict] = None,
        temperature: float = 0.3,
    ) -> LLMResponse: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    def vision_complete(
        self,
        system: str,
        user: str,
        image_bytes: bytes,
        image_mime: str = "image/jpeg",
        json_schema: Optional[dict] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """vision 调用。子类可 override；默认抛 NotImplementedError。"""
        raise NotImplementedError(f"{self.name} 后端未实现 vision_complete")


# ============================================================
# Mock：规则 + 模板，离线可跑
# ============================================================

class MockLLMClient(LLMClient):
    """不联网、不调 API。Plan / Replan / 偏好澄清都走规则。

    Planner 模块在 prompt 里把 area summary + 候选 POI 喂进来，
    这里识别关键词后按预定义模板拼出 Plan JSON。
    """

    @property
    def name(self) -> str:
        return "mock"

    def complete(self, system, user, json_schema=None, temperature=0.3):
        from .tracing import trace_span
        with trace_span("llm.mock.complete", attrs={"temperature": temperature}):
            # 路由：根据 system prompt 关键词判断在哪个 agent 上下文
            if "Plan-and-Execute Planner" in system:
                return self._mock_plan(user)
            if "Replanner" in system:
                return self._mock_replan(user)
            if "Preference Mirror" in system:
                return self._mock_preference_clarify(user)
            if "Text Intake" in system:
                return self._mock_text_intake(user)
            # fallback
            text = '{"answer": "mock-fallback"}'
            return LLMResponse(text=text, parsed=json.loads(text))

    def _mock_text_intake(self, user_text: str) -> "LLMResponse":
        """v2.5 D2：mock 文本抽取——委托给规则 fallback。"""
        from .text_intake import _rules_extract  # local import 避免循环
        # user_text 形如 "请按 schema...\n---\n{原文}\n---"，提取 --- 之间的原文
        raw = user_text
        if "---" in user_text:
            parts = user_text.split("---")
            if len(parts) >= 3:
                raw = parts[1].strip()
        r = _rules_extract(raw)
        out = {
            "area_anchor": r.area_anchor,
            "poi_name": r.poi_name,
            "taste_tags": r.taste_tags,
            "scene_tags": r.scene_tags,
            "risk_tags": r.risk_tags,
            "aspects": r.aspects or [
                {"aspect_type": "scenario_fit", "sentiment": "positive",
                 "confidence": 0.6, "evidence_summary": "规则版 mock 抽取"},
            ],
        }
        text = json.dumps(out, ensure_ascii=False)
        return LLMResponse(text=text, parsed=out)

    def vision_complete(self, system, user, image_bytes, image_mime="image/jpeg",
                        json_schema=None, temperature=0.2):
        """mock 视觉抽取——不真看图，按 image_bytes 哈希返回一组确定 aspects。

        demo 时也能跑（不需要真 vision API），评委拖任何图都有合理 aspects。
        """
        import hashlib
        h = int(hashlib.md5(image_bytes).hexdigest()[:8], 16)
        # 用 hash 选一个固定的 mock aspects 集合（5 套循环）
        aspects_pool = [
            {
                "area_anchor": "三里屯片区",
                "poi_name": "三联韬奋书店(三里屯店)",
                "aspects": [
                    {"aspect_type": "environment", "sentiment": "positive", "confidence": 0.84,
                     "evidence_summary": "书店环境安静、装修文艺，适合带孩子和朋友放松阅读",
                     "normalized_value": {"scene_tags": ["citywalk", "photo", "indoor", "quiet"]}},
                    {"aspect_type": "comfort", "sentiment": "positive", "confidence": 0.78,
                     "evidence_summary": "空间充足，有座位区，娃可以随便看绘本",
                     "normalized_value": {"comfort_tags": ["walkable", "rest_friendly"]}},
                ],
            },
            {
                "area_anchor": "什刹海-鼓楼片区",
                "poi_name": "胡大饭馆(簋街店)",
                "aspects": [
                    {"aspect_type": "queue", "sentiment": "negative", "confidence": 0.91,
                     "evidence_summary": "周末晚上排队 1.5-2 小时是常态，建议提前 5 点到",
                     "normalized_value": {"risk_tags": ["weekend_long_queue", "no_reservation"], "queue_wait_min": 90}},
                    {"aspect_type": "food", "sentiment": "positive", "confidence": 0.86,
                     "evidence_summary": "麻小招牌，川菜辣度足，适合朋友聚会但不适合带 5 岁娃",
                     "normalized_value": {"taste_tags": ["spicy", "seafood"], "scene_tags": ["friends_gathering"]}},
                ],
            },
            {
                "area_anchor": "五道营-雍和宫片区",
                "poi_name": "蜜思酸奶(雍和宫店)",
                "aspects": [
                    {"aspect_type": "food", "sentiment": "positive", "confidence": 0.82,
                     "evidence_summary": "现做酸奶 + 鲜果，5 岁娃零食加餐首选，¥18-25",
                     "normalized_value": {"taste_tags": ["yogurt", "fruit", "child_friendly"]}},
                    {"aspect_type": "scenario_fit", "sentiment": "positive", "confidence": 0.75,
                     "evidence_summary": "适合下午加餐 / 餐后甜品 / 减脂期低糖选择",
                     "normalized_value": {"scene_tags": ["snack_break", "low_sugar_option"]}},
                ],
            },
        ]
        choice = aspects_pool[h % len(aspects_pool)]
        text = json.dumps(choice, ensure_ascii=False, indent=2)
        return LLMResponse(text=text, parsed=choice)

    def _mock_plan(self, user_prompt: str) -> LLMResponse:
        """从 prompt 里抓 candidates_food / candidates_scenic / persona 关键字，
        按规则拼一个 5 步 plan。
        """
        # 解析 prompt 中嵌入的 JSON 上下文
        ctx = _extract_json_block(user_prompt, key="<context>")
        persona = ctx.get("persona", "family")
        area = ctx.get("area_anchor", "五道营-雍和宫片区")
        target_start = ctx.get("target_start", "14:00")
        candidates = ctx.get("candidates", {})
        scenic = candidates.get("scenic", [])
        food = candidates.get("food", [])
        landmark = candidates.get("landmark", [])

        # 选第一个非空类目作为 Step 1（citywalk 起点）
        first_scenic = (scenic + landmark)[0] if (scenic or landmark) else None
        # 选中等价位的食物
        mid_food = _pick_mid_priced(food, persona=persona)
        # 选另一个 scenic 作为饭后点
        second_scenic = (scenic + landmark)[1] if len(scenic + landmark) > 1 else None
        # 收尾点：景点附近的轻食/咖啡
        coffee = _pick_by_keyword(food, ["咖啡", "茶饼", "甜品", "炸鸡"]) or (food[1] if len(food) > 1 else None)

        steps = []
        t = _hh(target_start)
        if first_scenic:
            steps.append({
                "step_index": 1,
                "kind": "citywalk",
                "poi_id": first_scenic["id"],
                "poi_name": first_scenic["name"],
                "start_time": _fmt(t),
                "duration_min": 60,
                "mode_to_here": "walking",
                "rationale": f"从 {area} 起步，{first_scenic['name']} 是这片区代表性地点，慢逛 1 小时建立家庭/朋友的下午节奏",
            })
            t += 60
        if mid_food:
            steps.append({
                "step_index": 2,
                "kind": "meal",
                "poi_id": mid_food["id"],
                "poi_name": mid_food["name"],
                "start_time": _fmt(t),
                "duration_min": 75,
                "mode_to_here": "walking",
                "rationale": f"早午餐/正餐：{mid_food['name']}（rating {mid_food.get('rating', '?')} ¥{mid_food.get('avg_price', '?')}），符合 {persona} 画像预算与口味",
            })
            t += 75
        if second_scenic and second_scenic.get("id") != (first_scenic or {}).get("id"):
            steps.append({
                "step_index": 3,
                "kind": "culture",
                "poi_id": second_scenic["id"],
                "poi_name": second_scenic["name"],
                "start_time": _fmt(t),
                "duration_min": 90,
                "mode_to_here": "walking",
                "rationale": f"饭后文化体验：{second_scenic['name']}，距离上一站步行可达",
            })
            t += 90
        if coffee:
            steps.append({
                "step_index": 4,
                "kind": "rest",
                "poi_id": coffee["id"],
                "poi_name": coffee["name"],
                "start_time": _fmt(t),
                "duration_min": 45,
                "mode_to_here": "walking",
                "rationale": f"加餐/休息：{coffee['name']}，5 岁娃需要中场补给" if persona == "family"
                else f"饭后社交场：{coffee['name']}，朋友聚会延续",
            })
            t += 45
        # 收尾
        steps.append({
            "step_index": len(steps) + 1,
            "kind": "depart",
            "poi_id": None,
            "poi_name": "返程",
            "start_time": _fmt(t),
            "duration_min": 0,
            "mode_to_here": "transit",
            "rationale": "总时长约 4-5 小时，符合命题下午时长要求",
        })

        plan = {
            "persona": persona,
            "area_anchor": area,
            "steps": steps,
            "fallback_strategies": {
                "queue_overflow": "若餐厅排队 >30min，切换到本片区同类 4.0+ 评分备选",
                "weather_bad": "户外景点改为室内博物馆类",
                "child_tired": "缩短为 3 步，跳过 culture 或 rest",
            },
            "summary": f"{area} 下午 {target_start} 起，{persona} 画像，{len(steps)-1} 个有效 stop 加返程",
        }
        text = json.dumps(plan, ensure_ascii=False, indent=2)
        return LLMResponse(text=text, parsed=plan)

    def _mock_replan(self, user_prompt: str) -> LLMResponse:
        """触发 reroute 时——把 failed_step 替换为它的 fallback POI。"""
        ctx = _extract_json_block(user_prompt, key="<context>")
        original = ctx.get("original_plan", {})
        failed_idx = ctx.get("failed_step_idx", 0)
        replacement = ctx.get("replacement_candidate", {})
        steps = list(original.get("steps", []))
        if 0 <= failed_idx < len(steps) and replacement:
            old = steps[failed_idx]
            steps[failed_idx] = {
                **old,
                "poi_id": replacement.get("id"),
                "poi_name": replacement.get("name"),
                "rationale": (
                    f"⚠️ reroute：原 POI {old.get('poi_name')} 触发拥堵 "
                    f"({ctx.get('reroute_reason', '排队超时')}); "
                    f"切换到 {replacement.get('name')} (rating {replacement.get('rating', '?')})，"
                    f"本片区同类候选"
                ),
                "is_rerouted": True,
            }
        new_plan = {**original, "steps": steps, "rerouted_at_step": failed_idx}
        text = json.dumps(new_plan, ensure_ascii=False, indent=2)
        return LLMResponse(text=text, parsed=new_plan)

    def _mock_preference_clarify(self, user_prompt: str) -> LLMResponse:
        """偏好镜子：用户说"老婆减脂" → 反问"低糖还是低油"。"""
        ctx = _extract_json_block(user_prompt, key="<context>")
        raw_pref = (ctx.get("raw_preference") or "").lower()
        # 简单关键词路由
        if "减脂" in raw_pref or "低脂" in raw_pref:
            q = {
                "needs_clarification": True,
                "clarify_question": "老婆减脂是低糖优先（少甜品/含糖饮料）还是低油优先（少炸物/红烧）？",
                "options": ["低糖优先", "低油优先", "都要严格"],
                "default_assumption": "低油优先",
            }
        elif "孩子" in raw_pref or "娃" in raw_pref:
            q = {
                "needs_clarification": True,
                "clarify_question": "孩子年龄段是？（影响场地选择和步行容忍）",
                "options": ["3-5 岁", "6-9 岁", "10+ 岁"],
                "default_assumption": "5 岁",
            }
        elif "辣" in raw_pref or "麻" in raw_pref:
            q = {
                "needs_clarification": False,
                "extracted_constraint": {"diet_flags": ["no_spicy"]},
                "rationale": "明确不吃辣，已记入约束",
            }
        else:
            q = {
                "needs_clarification": False,
                "extracted_constraint": {},
                "rationale": "偏好已明确，无需追问",
            }
        text = json.dumps(q, ensure_ascii=False)
        return LLMResponse(text=text, parsed=q)


# ============================================================
# LongCat：占位（TODO）
# ============================================================

class LongCatClient(LLMClient):
    """美团 LongCat（Anthropic 兼容协议）。

    复用 activity-planner 的接入方式：
        - SDK：anthropic
        - 环境变量：LONGCAT_API_KEY + LONGCAT_BASE_URL
        - 模型：LongCat-2.0-Preview（可用 BJ_PAL_LONGCAT_MODEL 覆盖）

    本机 .env 由 activity-planner 维护；本模块在 import 时
    会自动尝试加载 activity-planner/.env 作为 fallback，省得用户重新配。
    """

    @property
    def name(self) -> str:
        return "longcat"

    def complete(self, system, user, json_schema=None, temperature=0.3):
        from .tracing import trace_span
        with trace_span("llm.longcat.complete", attrs={
            "temperature": temperature, "user_chars": len(user),
        }) as _sp:
            return self._complete_inner(system, user, json_schema, temperature, _sp)

    def _complete_inner(self, system, user, json_schema, temperature, _sp):
        try:
            import anthropic  # type: ignore
        except ImportError:
            raise RuntimeError("pip install anthropic（已在 requirements 里）")
        api_key = os.environ.get("LONGCAT_API_KEY")
        base_url = os.environ.get("LONGCAT_BASE_URL", "https://api.longcat.chat/anthropic")
        if not api_key:
            raise RuntimeError(
                "LONGCAT_API_KEY 未设置。可 (1) export LONGCAT_API_KEY=...，"
                "或 (2) 让 BJ-Pal 自动从 activity-planner/.env 加载（已在启动时尝试），"
                "或 (3) 用 BJ_PAL_LLM=mock 跑离线模式"
            )
        # LongCat 用 Bearer 认证（不是 anthropic 默认的 x-api-key）；
        # trust_env 让 httpx 走 dev box 代理，避开 APIConnectionError
        try:
            import httpx  # type: ignore
        except ImportError:
            raise RuntimeError("pip install httpx（已在 anthropic 依赖里）")
        client = anthropic.Anthropic(
            api_key="placeholder",
            base_url=base_url,
            default_headers={"Authorization": f"Bearer {api_key}"},
            http_client=httpx.Client(trust_env=True, timeout=60.0),
        )
        model = os.environ.get(
            "BJ_PAL_LONGCAT_MODEL",
            os.environ.get("ACTIVITY_PLANNER_MODEL", "LongCat-2.0-Preview"),
        )
        # max_tokens 在 v1 4096 偶发把长 plan 截断，提到 8192；可由 env 覆盖
        max_tokens = int(os.environ.get("BJ_PAL_LONGCAT_MAX_TOKENS", "8192"))

        # 鲁棒性层：RPM 限速 + 限流退避（[73][75] 改进）
        from .llm_robust import get_global_limiter, repair_json, retry_with_backoff

        def _call():
            with get_global_limiter():
                return client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )

        msg = retry_with_backoff(_call, max_attempts=4, base_delay=2.0, max_delay=60.0)
        text = "".join(getattr(b, "text", "") for b in msg.content)
        # repair_json 比 _extract_first_json 强：能恢复截断 / 抠 steps
        parsed = repair_json(text) if json_schema is not None else None
        if _sp is not None:
            _sp.set_attribute("response_chars", len(text))
            usage = getattr(msg, "usage", None)
            if usage is not None:
                in_tok = getattr(usage, "input_tokens", None)
                out_tok = getattr(usage, "output_tokens", None)
                if in_tok is not None:
                    _sp.set_attribute("input_tokens", in_tok)
                if out_tok is not None:
                    _sp.set_attribute("output_tokens", out_tok)
        return LLMResponse(text=text, parsed=parsed, raw=msg)

    def vision_complete(self, system, user, image_bytes, image_mime="image/jpeg",
                        json_schema=None, temperature=0.2):
        """LongCat vision（Anthropic 兼容 base64 image 协议）。"""
        import base64
        try:
            import anthropic  # type: ignore
            import httpx  # type: ignore
        except ImportError:
            raise RuntimeError("pip install anthropic httpx")
        api_key = os.environ.get("LONGCAT_API_KEY")
        base_url = os.environ.get("LONGCAT_BASE_URL", "https://api.longcat.chat/anthropic")
        if not api_key:
            raise RuntimeError("LONGCAT_API_KEY 未设置")
        client = anthropic.Anthropic(
            api_key="placeholder",
            base_url=base_url,
            default_headers={"Authorization": f"Bearer {api_key}"},
            http_client=httpx.Client(trust_env=True, timeout=60.0),
        )
        model = os.environ.get(
            "BJ_PAL_LONGCAT_VISION_MODEL",
            os.environ.get("BJ_PAL_LONGCAT_MODEL", "LongCat-2.0-Preview"),
        )
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        from .llm_robust import get_global_limiter, repair_json, retry_with_backoff
        max_tokens = int(os.environ.get("BJ_PAL_LONGCAT_MAX_TOKENS", "8192"))

        def _call():
            with get_global_limiter():
                return client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": image_mime, "data": b64,
                            }},
                            {"type": "text", "text": user},
                        ],
                    }],
                )

        msg = retry_with_backoff(_call, max_attempts=4, base_delay=2.0, max_delay=60.0)
        text = "".join(getattr(b, "text", "") for b in msg.content)
        parsed = repair_json(text) if json_schema is not None else None
        return LLMResponse(text=text, parsed=parsed, raw=msg)


# ============================================================
# Anthropic：可选 fallback（开发期实测用）
# ============================================================

class AnthropicClient(LLMClient):
    """通过 anthropic SDK 调 Claude。set ANTHROPIC_API_KEY。"""

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, system, user, json_schema=None, temperature=0.3):
        try:
            import anthropic  # type: ignore
        except ImportError:
            raise RuntimeError(
                "anthropic 未安装。pip install anthropic，或用 BJ_PAL_LLM=mock"
            )
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7"),
            max_tokens=4096,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        parsed = None
        if json_schema is not None:
            try:
                # 简单提取第一个 ```json ... ``` 块或整段
                parsed = _extract_first_json(text)
            except Exception:
                parsed = None
        return LLMResponse(text=text, parsed=parsed, raw=msg)


# ============================================================
# Factory
# ============================================================

def get_llm_client(override: Optional[str] = None) -> LLMClient:
    backend = (override or os.environ.get("BJ_PAL_LLM") or "mock").lower()
    if backend == "mock":
        return MockLLMClient()
    if backend == "longcat":
        return LongCatClient()
    if backend == "anthropic":
        return AnthropicClient()
    raise ValueError(f"未知 LLM 后端：{backend}（支持：mock / longcat / anthropic）")


# ============================================================
# helpers
# ============================================================

def _extract_json_block(text: str, key: str = "<context>") -> dict:
    """在 prompt 文本里找 <context>{...}</context> 块。"""
    start_tag = key
    end_tag = key.replace("<", "</")
    start = text.find(start_tag)
    if start == -1:
        return {}
    start += len(start_tag)
    end = text.find(end_tag, start)
    body = text[start:end if end != -1 else len(text)].strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def _extract_first_json(text: str) -> Optional[dict]:
    """LLM 返回里抓第一个 JSON 对象——容忍 ```json ... ``` 包裹。"""
    s = text.strip()
    if s.startswith("```"):
        # 去掉首行 ``` 标记
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    # 找最外层 {...}
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


def _hh(time_str: str) -> int:
    """'14:00' → 14*60=840（分钟）。"""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)


def _fmt(minutes: int) -> str:
    h, m = minutes // 60, minutes % 60
    return f"{h:02d}:{m:02d}"


def _pick_mid_priced(food: list[dict], persona: str = "family") -> Optional[dict]:
    """按 persona 选合适价位：family 偏 ¥30-150；friends ¥80-300。"""
    if not food:
        return None
    if persona == "family":
        lo, hi = 30, 150
    else:
        lo, hi = 80, 300
    in_range = [f for f in food if f.get("avg_price") and lo <= f["avg_price"] <= hi]
    if in_range:
        return max(in_range, key=lambda f: f.get("rating") or 0)
    return food[0]


def _pick_by_keyword(food: list[dict], keywords: list[str]) -> Optional[dict]:
    for f in food:
        name = (f.get("name") or "") + (f.get("category_lv3") or "")
        if any(kw in name for kw in keywords):
            return f
    return None
