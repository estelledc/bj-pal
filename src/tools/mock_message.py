"""mock IM（微信话术化卡片）+ 群发投票（v2 改 2）。

命题 task.md 第 3 节最后一条字面要求：
"对外沟通：把最终计划话术化分享给他人"
"把安排发给朋友们，看他们是否认可"

接口签名向真实 API 对齐：
- 微信小程序卡片 / 服务号模板消息
- 短信兜底
- 企业微信群机器人（群发用）
"""

from __future__ import annotations

import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.types import Plan  # noqa: E402

from .tool_call_log import timed_call  # noqa: E402


# ============================================================
# P0.3 群发预算脱敏（信号 7：预算绝对私密 5/5）
# ============================================================

_PRICE_RE = re.compile(r"¥\s*(\d+)")


def _price_band(amount: float) -> str:
    """把具体金额映射为模糊档位标签。"""
    if amount < 60:
        return "亲民档"
    if amount < 150:
        return "中等档位"
    if amount < 300:
        return "中高档"
    return "高档位"


def _scrub_budget_text(text: str) -> str:
    """把 ¥xxx 替换为模糊档位标签，群发安全。"""
    if not text:
        return text

    def _sub(m: re.Match) -> str:
        try:
            return _price_band(float(m.group(1)))
        except ValueError:
            return m.group(0)

    return _PRICE_RE.sub(_sub, text)


@dataclass
class MessageCard:
    title: str
    body: str
    audience: Literal["spouse", "friend", "group"]
    plan_summary: str
    actions: list[dict] = field(default_factory=list)
    raw_text: str = ""
    style: str = "default"   # 'default' | 'elderly_friendly'（P1.3）


@dataclass
class SendResult:
    delivered: bool
    contact: str
    message_id: Optional[str] = None
    error: Optional[str] = None


# ============================================================
# 卡片生成
# ============================================================

def render_im_card(
    plan: Plan,
    audience: str = "spouse",
    style: str = "default",
) -> MessageCard:
    """把 Plan 转成 IM 友好的话术卡片。

    设计：
    - 第一句要"嗨/搞定了/我跟你说"——口语开场
    - 列出 3-5 个时间槽（不全列）
    - 末尾一个行动按钮：[确认] [换一个]
    - audience='spouse' 用亲密称呼，'friend'/'group' 用朋友口吻并脱敏预算（P0.3）
    - style='elderly_friendly' 大字号 + 简化按钮（P1.3）
    """
    with timed_call("mock_message.render_im_card",
                    params={"audience": audience, "style": style,
                            "n_steps": len(plan.steps)}) as rec:
        is_group = audience in ("friend", "group")
        elderly = style == "elderly_friendly"

        if elderly:
            opener = "下午活动安排好了，您看一下"
            sign_off = "全家同意了，去不去？"
        elif audience == "spouse":
            opener = "搞定了！下午这样安排你看行不"
            sign_off = "回我一个 OK 我就预订~"
        elif audience == "friend":
            opener = "嗨各位，下午活动我已经摸了一套，看下"
            sign_off = "@大家 30 分钟内回复，没问题我就锁定"
        else:
            opener = "下午活动方案"
            sign_off = "确认后请回复"

        # 取关键 step（跳过 depart）
        key_steps = [s for s in plan.steps if s.kind != "depart"][:5]
        body_lines = [opener, ""]
        for s in key_steps:
            marker = "🔄" if s.is_rerouted else ""
            line = f"{s.start_time} → {s.poi_name} {marker}".rstrip()
            if is_group:
                line = _scrub_budget_text(line)
            body_lines.append(line)
        body_lines.append("")
        summary_line = plan.summary or ""
        if is_group:
            summary_line = _scrub_budget_text(summary_line)
        body_lines.append(summary_line)
        body_lines.append(sign_off)
        # [55] 责任盾牌：群发场景显式标注"AI 拍板"，让发起人不用扛选错的责任
        if is_group and not elderly:
            body_lines.append("")
            body_lines.append("（AI 综合大家偏好生成，有问题找 BJ-Pal）")
        body = "\n".join(body_lines)

        if elderly:
            # 老年简化版：去 emoji，按钮压成 是/否
            body = body.replace("🔄", "(已换)")
            actions = [
                {"label": "是", "action": "confirm_plan"},
                {"label": "否", "action": "regenerate_plan"},
            ]
        else:
            actions = [
                {"label": "确认", "action": "confirm_plan"},
                {"label": "换一个", "action": "regenerate_plan"},
            ]
            if any(s.kind == "meal" for s in plan.steps):
                actions.append({"label": "顺便订蛋糕", "action": "trigger_cake_addon"})

        title = f"BJ-Pal · 下午 {key_steps[0].start_time if key_steps else ''} 出发"
        card = MessageCard(
            title=title,
            body=body,
            audience=audience,
            plan_summary=_scrub_budget_text(plan.summary or "") if is_group else (plan.summary or ""),
            actions=actions,
            raw_text=body,
            style=style,
        )
        rec["response"] = card
        return card


# ============================================================
# 发送
# ============================================================

def send_via_wechat_mock(
    card: MessageCard,
    contact: str,
) -> SendResult:
    """模拟发送到微信。

    生产环境路径：
    - 微信小程序：subscribeMessage.send
    - 微信公众号：customerservice.sendMessage
    - 企业微信：webhook bot
    """
    with timed_call("mock_message.send_via_wechat_mock",
                    params={"contact": contact, "title": card.title}) as rec:
        result = SendResult(
            delivered=True,
            contact=contact,
            message_id=f"MSG{abs(hash(card.body + contact)) % 10**10:010d}",
        )
        rec["response"] = result
        return result


# ============================================================
# v2 改 2：群发投票（命题字面字眼"发给朋友们看是否认可"）
# ============================================================

@dataclass
class GroupMember:
    """4 人朋友群中的一人，含偏好。"""
    name: str
    avatar_emoji: str = "👤"
    diet_aversion: list[str] = field(default_factory=list)  # ["spicy", "expensive"]
    prefers: list[str] = field(default_factory=list)        # ["photo", "coffee"]


@dataclass
class ContactResponse:
    contact: str
    avatar: str
    status: Literal["confirmed", "rejected", "waiting", "no_reply"]
    reply_text: str = ""
    reply_at_ms: int = 0
    rejection_reason: str = ""    # spicy / expensive / too_loud / other


# demo 默认 4 人朋友群（3 男 1 女，差异化偏好）
DEMO_FRIEND_GROUP = [
    GroupMember(name="@小张", avatar_emoji="🧑", diet_aversion=["spicy"], prefers=["coffee"]),
    GroupMember(name="@阿明", avatar_emoji="👨", diet_aversion=["expensive"], prefers=["meat"]),
    GroupMember(name="@小雅", avatar_emoji="👩", diet_aversion=["heavy_oil"], prefers=["photo", "dessert"]),
    GroupMember(name="@老王", avatar_emoji="🧓", diet_aversion=[], prefers=["meat", "drink"]),
]


def broadcast_to_group(
    card: MessageCard,
    members: list[GroupMember],
) -> list[SendResult]:
    """群发 N 张卡片，每人一张。

    生产环境路径：企业微信 / 微信群机器人 webhook。

    P0.3 防御：群发卡片在出网前再次脱敏（即使 render_im_card 已处理一遍）。
    """
    with timed_call("mock_message.broadcast_to_group",
                    params={"audience_count": len(members), "title": card.title}) as rec:
        # 防御性二次脱敏：哪怕 card.audience 是 spouse 也别让金额走出去
        card.body = _scrub_budget_text(card.body)
        card.raw_text = _scrub_budget_text(card.raw_text)
        card.plan_summary = _scrub_budget_text(card.plan_summary)
        assert "¥" not in card.body, "broadcast 卡片不允许出现具体金额"

        results = []
        for m in members:
            results.append(SendResult(
                delivered=True,
                contact=m.name,
                message_id=f"MSG{abs(hash(card.body + m.name)) % 10**10:010d}",
            ))
        rec["response"] = {"sent": len(results)}
        return results


# ============================================================
# P0.5 错误自承认 apology card（信号 3：选错容忍度 = 2 次）
# ============================================================

def render_reroute_notice(
    event,
    organizer_contact: str = "@组织者",
    group_members: Optional[list] = None,
) -> dict:
    """P0.4：根据 RerouteEvent.notify_strategy 决定走群里直发还是先单独通知组织者。

    Returns:
        {
            "strategy": "group_direct" | "private_first",
            "private_card": MessageCard | None,
            "group_card": MessageCard | None,
            "send_order": ["private", "group"] | ["group"],
            "private_timeout_sec": int (default 60),
        }
    """
    summary = getattr(event, "change_summary_zh", "") or "方案有调整"
    magnitude = getattr(event, "change_magnitude", "small")
    strategy = getattr(event, "notify_strategy", "group_direct")
    unchanged = getattr(event, "unchanged_steps", [])
    unchanged_text = (
        f"其他 {len(unchanged)} 站保持不变" if unchanged else "其他站保持不变"
    )

    if strategy == "warn_only":
        # 找不到替补，纯提示
        warn_card = MessageCard(
            title="BJ-Pal · 风险提示",
            body=f"⚠ {summary}\n{unchanged_text}",
            audience="spouse",
            plan_summary="",
            actions=[{"label": "我知道了", "action": "ack"}],
            raw_text=summary,
        )
        return {"strategy": "warn_only", "private_card": warn_card,
                "group_card": None, "send_order": ["private"],
                "private_timeout_sec": 0}

    if strategy == "private_first":
        # 中/大幅：先单独发起人确认 60s
        private = MessageCard(
            title=f"BJ-Pal · {magnitude} 调整待确认",
            body=(
                f"我准备改方案：\n  {summary}\n\n"
                f"{unchanged_text}。\n"
                f"60 秒内回复 [确认改] 我就同步给群；超时我直接群里说。"
            ),
            audience="spouse",
            plan_summary=summary,
            actions=[
                {"label": "确认改", "action": "confirm_reroute"},
                {"label": "维持原计划", "action": "reject_reroute"},
            ],
            raw_text=summary,
        )
        group = MessageCard(
            title="BJ-Pal · 方案微调（已确认）",
            body=f"📣 {summary}\n{unchanged_text}",
            audience="group",
            plan_summary=summary,
            actions=[{"label": "好", "action": "ack_group"}],
            raw_text=summary,
        )
        # 群卡防御性脱敏
        group.body = _scrub_budget_text(group.body)
        return {"strategy": "private_first", "private_card": private,
                "group_card": group,
                "send_order": ["private", "group"], "private_timeout_sec": 60}

    # 小幅：直接群里说
    group = MessageCard(
        title="BJ-Pal · 方案微调",
        body=f"📣 {summary}\n{unchanged_text}",
        audience="group",
        plan_summary=summary,
        actions=[{"label": "好", "action": "ack_group"}],
        raw_text=summary,
    )
    group.body = _scrub_budget_text(group.body)
    return {"strategy": "group_direct", "private_card": None,
            "group_card": group, "send_order": ["group"],
            "private_timeout_sec": 0}


def apology_card(
    contact: str,
    poi_name: str,
    last_predicted: str,
    actual_observed: str,
    new_confidence: float,
    suggestion: str = "",
) -> MessageCard:
    """主动认错卡片：上次预测偏差，把可信度调低让用户先看。

    用法：当 self-evaluation 检测到上次预测偏差 > 阈值，下次给该 POI
    出方案前先发这张卡。生产路径走微信服务通知。
    """
    title = f"BJ-Pal · 关于上次{poi_name}的更正"
    body = (
        f"上次给您的{poi_name}排队信息不准——\n"
        f"  我说：{last_predicted}\n"
        f"  实际：{actual_observed}\n"
        f"\n"
        f"这次我把可信度从 0.8 调到 {new_confidence:.1f}，您先看一下再用。"
    )
    if suggestion:
        body += f"\n\n建议：{suggestion}"

    actions = [
        {"label": "我知道了", "action": "ack_apology"},
        {"label": "换一家", "action": "regenerate_plan"},
    ]
    card = MessageCard(
        title=title,
        body=body,
        audience="spouse",
        plan_summary="",
        actions=actions,
        raw_text=body,
    )
    return card


def simulate_group_responses(
    plan: Plan,
    members: list[GroupMember],
    force_one_dissent: bool = True,
    seed: Optional[int] = None,
) -> list[ContactResponse]:
    """模拟 4 人对 plan 的反馈。

    Args:
        force_one_dissent: demo 模式必触发——保证至少 1 人否决，让 reroute 能演出来
        seed: 控制随机
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    with timed_call("mock_message.simulate_group_responses",
                    params={"members": len(members), "force_dissent": force_one_dissent}) as rec:
        responses: list[ContactResponse] = []
        # 找 plan 中的"风险特征"——让最对应那个 aversion 的人否决
        plan_blob = " ".join(s.poi_name + (s.rationale or "") for s in plan.steps)
        plan_max_price = _detect_max_price(plan)

        forced_dissenter_idx = -1
        if force_one_dissent and members:
            # 优先让 expensive 反感的人否决（如果 plan 含贵店）
            if plan_max_price >= 200:
                forced_dissenter_idx = next(
                    (i for i, m in enumerate(members) if "expensive" in m.diet_aversion), 0
                )
            elif "辣" in plan_blob or "麻辣" in plan_blob or "火锅" in plan_blob:
                forced_dissenter_idx = next(
                    (i for i, m in enumerate(members) if "spicy" in m.diet_aversion), 0
                )
            else:
                # 默认：spicy/heavy_oil 之一随机否决
                non_zero_aversion = [i for i, m in enumerate(members) if m.diet_aversion]
                if non_zero_aversion:
                    forced_dissenter_idx = rng.choice(non_zero_aversion)
                else:
                    forced_dissenter_idx = rng.randint(0, len(members) - 1)

        for i, m in enumerate(members):
            base_delay = 200 + rng.randint(0, 800)  # 0.2-1.0s
            if i == forced_dissenter_idx:
                reason, text = _pick_rejection_text(m, plan_blob, plan_max_price, rng)
                responses.append(ContactResponse(
                    contact=m.name, avatar=m.avatar_emoji,
                    status="rejected", reply_text=text,
                    reply_at_ms=base_delay,
                    rejection_reason=reason,
                ))
            else:
                # 80% 通过
                if rng.random() < 0.8:
                    responses.append(ContactResponse(
                        contact=m.name, avatar=m.avatar_emoji,
                        status="confirmed", reply_text="OK 没问题",
                        reply_at_ms=base_delay,
                    ))
                else:
                    responses.append(ContactResponse(
                        contact=m.name, avatar=m.avatar_emoji,
                        status="waiting", reply_text="",
                        reply_at_ms=0,
                    ))
        rec["response"] = {"rejected": sum(1 for r in responses if r.status == "rejected")}
        return responses


def _detect_max_price(plan: Plan) -> float:
    """plan 步骤中最贵的那家的 avg_price——通过 booking 字段或 rationale 文本。"""
    max_p = 0.0
    for s in plan.steps:
        if s.booking and isinstance(s.booking, dict):
            p = s.booking.get("avg_price") or 0
            if p:
                max_p = max(max_p, float(p))
        # 从 rationale 文本里抽 ¥xxx
        import re
        m = re.search(r"¥\s*(\d+)", s.rationale or "")
        if m:
            try:
                max_p = max(max_p, float(m.group(1)))
            except ValueError:
                pass
    return max_p


def _pick_rejection_text(member: GroupMember, plan_blob: str,
                          plan_max_price: float, rng: random.Random) -> tuple[str, str]:
    """根据 member 的 aversion + plan 内容，选最自然的否决文案。"""
    if "expensive" in member.diet_aversion and plan_max_price >= 200:
        return "expensive", f"{member.name[1:]}：那家人均{int(plan_max_price)}有点贵，换个性价比高的呗"
    if "spicy" in member.diet_aversion and ("辣" in plan_blob or "麻辣" in plan_blob):
        return "spicy", f"{member.name[1:]}：那家辣的我吃不了，换"
    if "heavy_oil" in member.diet_aversion:
        return "heavy_oil", f"{member.name[1:]}：我最近减脂，能不能换个清淡点的"
    aversions = member.diet_aversion or ["other"]
    av = rng.choice(aversions)
    return av, f"{member.name[1:]}：换一个吧，这个我不太行"

