"""mock 餐厅预订 / 蛋糕鲜花配送。

接口签名向真实 API 对齐：
- 餐厅 → 美团商家开放 / 哗啦啦 / 客如云 SaaS
- 鲜花蛋糕 → 美团跑腿 / 美团秒送

Mock 行为：
- 5% timeout / 10% no_availability / 8% 排队超长（party_size>5）/ 2% 商家拒单
- 成功时返回 booking_id + confirmation_url + 菜单 + 座位号 + 真实延迟（v2 改 3）
"""

from __future__ import annotations

import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .tool_call_log import timed_call  # noqa: E402

MOCK_MENU_PATH = Path(__file__).resolve().parent / "data" / "mock_menu.json"

BookingStatus = Literal["confirmed", "no_availability", "timeout", "rejected_by_merchant"]


@dataclass
class BookingResult:
    booking_id: Optional[str]
    poi_id: str
    poi_name: str
    party_size: int
    target_time: str
    status: BookingStatus
    confirmation_url: Optional[str] = None
    message: str = ""
    estimated_wait_min: int = 0
    # v2 改 3：真实感字段
    seat_no: Optional[str] = None              # "A区 5号桌"
    menu_preview: list[dict] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    waiting_parties: int = 0                   # 当前排队桌数
    latency_ms: float = 0.0                    # 模拟 API 延迟
    # P1.4：mock 标识（信号 - 刘晋川焦虑：怕"一键下单"真扣钱）
    is_mock: bool = True
    simulated_at: str = ""                     # ISO 时间戳
    real_api_path: str = "POST https://api.meituan.com/merchant/v1/reservation/create"


@dataclass
class CakeDeliveryResult:
    delivery_id: Optional[str]
    restaurant_id: str
    cake_spec: str
    delivery_time: str
    greeting_message: str
    status: Literal["scheduled", "out_of_stock", "timeout"]
    eta_min: Optional[int] = None
    # P1.4：mock 标识
    is_mock: bool = True
    simulated_at: str = ""
    real_api_path: str = "POST https://api.meituan.com/cake/v1/instant_delivery"


# ============================================================
# Restaurant booking
# ============================================================

_MENU_CACHE: Optional[dict] = None


def _load_menu_for(poi_name: str) -> list[dict]:
    global _MENU_CACHE
    if _MENU_CACHE is None:
        try:
            _MENU_CACHE = json.loads(MOCK_MENU_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            _MENU_CACHE = {"_default": []}
    return _MENU_CACHE.get(poi_name) or _MENU_CACHE.get("_default") or []


def _gen_seat_no(rng: random.Random) -> str:
    zone = rng.choice(["A", "B", "C", "VIP"])
    return f"{zone} 区 {rng.randint(1, 28)} 号桌"


def book_restaurant(
    poi_id: str,
    poi_name: str,
    target_time: str,
    party_size: int,
    contact_name: str = "用户",
    note: Optional[str] = None,
    photos: Optional[list[str]] = None,
    seed: Optional[int] = None,
    simulate_latency: bool = True,
) -> BookingResult:
    """模拟餐厅预订（v2 改 3 升级）。

    生产环境路径：POST 到美团商家 API
        /api/v1/reservation/create
        body: {merchant_id, party_size, time, contact, note}

    Args:
        photos: POI 照片 URL 列表（从 amap photos 字段传入）
        simulate_latency: 是否模拟 0.3-1.2s 真实 API 延迟
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    with timed_call("mock_book.book_restaurant",
                    params={"poi_id": poi_id, "poi_name": poi_name,
                            "target_time": target_time, "party_size": party_size}) as rec:
        t_start = time.time()
        # v2 改 3：模拟真实 API 延迟（生产环境通常 200-1500ms）
        if simulate_latency:
            time.sleep(0.3 + rng.random() * 0.9)

        sim_at = datetime.now().isoformat(timespec="seconds")
        # 故障注入
        roll = rng.random()
        if roll < 0.02:
            result = BookingResult(
                booking_id=None, poi_id=poi_id, poi_name=poi_name,
                party_size=party_size, target_time=target_time,
                status="rejected_by_merchant",
                message="商家暂不接收 4 人以上预订",
                latency_ms=(time.time() - t_start) * 1000,
                simulated_at=sim_at,
            )
        elif roll < 0.07:
            result = BookingResult(
                booking_id=None, poi_id=poi_id, poi_name=poi_name,
                party_size=party_size, target_time=target_time,
                status="timeout",
                message="商家未在 30 秒内响应，请稍后重试",
                latency_ms=(time.time() - t_start) * 1000,
                simulated_at=sim_at,
            )
        elif roll < 0.17:
            result = BookingResult(
                booking_id=None, poi_id=poi_id, poi_name=poi_name,
                party_size=party_size, target_time=target_time,
                status="no_availability",
                message=f"{target_time} 已无 {party_size} 人座位，建议提前 30 分钟到店等位",
                estimated_wait_min=30 + rng.randint(0, 30),
                waiting_parties=rng.randint(3, 12),
                latency_ms=(time.time() - t_start) * 1000,
                simulated_at=sim_at,
            )
        else:
            # v2 改 3：成功时携带真实菜单 / 座位号 / 照片 / 当前等位数
            ts = datetime.now().strftime("%y%m%d")
            booking_id = f"BK{ts}{uuid.uuid4().hex[:8].upper()}"
            menu = _load_menu_for(poi_name)
            seat_no = _gen_seat_no(rng)
            waiting = rng.choices([0, 0, 0, 1, 2, 3], weights=[5, 4, 3, 2, 1, 1])[0]
            result = BookingResult(
                booking_id=booking_id, poi_id=poi_id, poi_name=poi_name,
                party_size=party_size, target_time=target_time,
                status="confirmed",
                confirmation_url=f"https://meituan-mock.local/reservation/{booking_id}",
                message=f"✅ {poi_name} {target_time} 已为 {contact_name}（{party_size} 人）锁定 {seat_no}",
                seat_no=seat_no,
                menu_preview=menu[:6],
                photos=list(photos or [])[:3],
                waiting_parties=waiting,
                latency_ms=(time.time() - t_start) * 1000,
                simulated_at=sim_at,
            )
        rec["response"] = result
        return result


# ============================================================
# Cake delivery to restaurant
# ============================================================

def book_cake_delivery(
    restaurant_id: str,
    restaurant_name: str,
    cake_spec: str,
    delivery_time: str,
    greeting_message: str,
    seed: Optional[int] = None,
) -> CakeDeliveryResult:
    """模拟蛋糕配送到餐厅。

    生产环境路径：POST 美团秒送 / 跑腿
        /api/v1/instant_delivery/cake
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    with timed_call("mock_book.book_cake_delivery",
                    params={"restaurant": restaurant_name, "spec": cake_spec,
                            "delivery_time": delivery_time}) as rec:
        sim_at = datetime.now().isoformat(timespec="seconds")
        if rng.random() < 0.05:
            result = CakeDeliveryResult(
                delivery_id=None, restaurant_id=restaurant_id,
                cake_spec=cake_spec, delivery_time=delivery_time,
                greeting_message=greeting_message,
                status="out_of_stock",
                simulated_at=sim_at,
            )
        else:
            delivery_id = f"DL{uuid.uuid4().hex[:10].upper()}"
            result = CakeDeliveryResult(
                delivery_id=delivery_id, restaurant_id=restaurant_id,
                cake_spec=cake_spec, delivery_time=delivery_time,
                greeting_message=greeting_message,
                status="scheduled",
                eta_min=20 + rng.randint(0, 30),
                simulated_at=sim_at,
            )
        rec["response"] = result
        return result
