"""v3.1 D7 校准时序 — plan_trace × plan_outcome 的滑窗 ECE。

回答：随着用户用得多了，AI 的置信度校准在变好还是变差？

数据源：plan_tracer 写的 plan_trace 表 + plan_outcome 表（user_memory.db 同库）
输出：
  - get_calibration_timeline(window_size, n_bins) → [{window_start_ts, ece, n_samples, mean_conf, mean_acc}]
  - get_confidence_distribution() → confidence 分布（直方图）
  - get_plan_count_by_day() → 每日 plan 增长

复用 plan_tracer.compute_ece，不重复实现。
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .plan_tracer import compute_ece  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = ROOT / "tool_calls.db"
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class TimelineWindow:
    window_index: int
    n_samples: int
    ts_min: float
    ts_max: float
    ece: float
    mean_confidence: float
    mean_actual_success: float

    def to_dict(self) -> dict:
        return {
            "window_index": self.window_index,
            "n_samples": self.n_samples,
            "ts_min": round(self.ts_min, 1),
            "ts_max": round(self.ts_max, 1),
            "ece": self.ece,
            "mean_confidence": self.mean_confidence,
            "mean_actual_success": self.mean_actual_success,
        }


def _fetch_paired() -> list[dict]:
    """读所有 plan_trace × plan_outcome 已配对的样本，按 outcome.recorded_at 升序。"""
    with _LOCK, closing(_conn()) as conn:
        rows = conn.execute(
            """
            SELECT t.confidence  AS conf,
                   o.actual_success AS success,
                   o.recorded_at AS recorded_at,
                   t.plan_id AS plan_id,
                   t.step_index AS step_index
              FROM plan_trace t
              JOIN plan_outcome o
                ON t.plan_id = o.plan_id AND t.step_index = o.step_index
             ORDER BY o.recorded_at ASC, t.step_index ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_calibration_timeline(
    window_size: int = 20,
    n_bins: int = 5,
) -> list[TimelineWindow]:
    """滑窗 ECE 时序。

    每 window_size 个样本一个窗口；窗口间不重叠。
    需要至少 window_size 个 (trace, outcome) 配对样本。
    """
    paired = _fetch_paired()
    if len(paired) < window_size:
        return []

    out: list[TimelineWindow] = []
    for i in range(0, len(paired) // window_size):
        start = i * window_size
        end = start + window_size
        chunk = paired[start:end]
        confs = [r["conf"] for r in chunk]
        succs = [r["success"] for r in chunk]
        ece_result = compute_ece(confs, succs, n_bins=n_bins)
        out.append(TimelineWindow(
            window_index=i + 1,
            n_samples=len(chunk),
            ts_min=chunk[0]["recorded_at"],
            ts_max=chunk[-1]["recorded_at"],
            ece=ece_result["ece"],
            mean_confidence=round(sum(confs) / len(confs), 3),
            mean_actual_success=round(sum(succs) / len(succs), 3),
        ))
    return out


def get_confidence_distribution(n_bins: int = 10) -> list[dict]:
    """全部 plan_trace 的 confidence 分布直方图。"""
    with _LOCK, closing(_conn()) as conn:
        rows = conn.execute("SELECT confidence FROM plan_trace").fetchall()
    confs = [r["confidence"] for r in rows]
    if not confs:
        return []

    bins = [0] * n_bins
    for c in confs:
        idx = min(int(c * n_bins), n_bins - 1)
        bins[idx] += 1
    out = []
    for i, n in enumerate(bins):
        out.append({
            "range_lo": round(i / n_bins, 2),
            "range_hi": round((i + 1) / n_bins, 2),
            "n": n,
            "pct": round(n / len(confs), 3),
        })
    return out


def get_plan_count_summary() -> dict:
    """快速汇总：plan 数 / trace step 数 / outcome 配对数 / 全局 ECE。"""
    with _LOCK, closing(_conn()) as conn:
        n_plans = conn.execute(
            "SELECT COUNT(DISTINCT plan_id) AS n FROM plan_trace"
        ).fetchone()["n"]
        n_traces = conn.execute(
            "SELECT COUNT(*) AS n FROM plan_trace"
        ).fetchone()["n"]
        n_outcomes = conn.execute(
            "SELECT COUNT(*) AS n FROM plan_outcome"
        ).fetchone()["n"]

    paired = _fetch_paired()
    if paired:
        confs = [r["conf"] for r in paired]
        succs = [r["success"] for r in paired]
        ece = compute_ece(confs, succs, n_bins=10)["ece"]
    else:
        ece = None

    return {
        "n_plans": n_plans,
        "n_traces": n_traces,
        "n_outcomes": n_outcomes,
        "n_paired": len(paired),
        "global_ece": ece,
    }


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    summary = get_plan_count_summary()
    print(f"现状：plans={summary['n_plans']}  traces={summary['n_traces']}  "
          f"outcomes={summary['n_outcomes']}  paired={summary['n_paired']}  "
          f"global_ece={summary['global_ece']}")

    timeline = get_calibration_timeline(window_size=10, n_bins=5)
    if timeline:
        print(f"\n时序 ({len(timeline)} 窗口):")
        for w in timeline:
            print(f"  W{w.window_index}: ece={w.ece:.3f} conf={w.mean_confidence:.3f} "
                  f"acc={w.mean_actual_success:.3f} (n={w.n_samples})")
    else:
        print("\n样本不足 10，无法画时序")

    dist = get_confidence_distribution(n_bins=10)
    if dist:
        print(f"\nconfidence 分布:")
        for b in dist:
            bar = "█" * int(b["pct"] * 50)
            print(f"  [{b['range_lo']:.1f}-{b['range_hi']:.1f}] n={b['n']:4d} "
                  f"({b['pct']:.1%}) {bar}")
