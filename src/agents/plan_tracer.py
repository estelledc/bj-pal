"""D1 履约 trace 内核 — 在 plan 每步记录 (decision, support score, fallback)。

参考：docs/V2.4_ITERATION_PLAN.md Round 3 + Round 7

不做 SLA 外壳（mock 阶段说不清"赔付"）。本模块只做内核：
- 每步落 SQLite plan_trace 表 + 同步走 trace_span（OTel 兼容）
- 保留历史列名 confidence；新 trace 必须在 evidence 里声明来源与语义
- 仅在有配对 outcome 时提供 ECE，不能把无 outcome 的支持分当成功概率
- UI 侧栏读 SQLite，展示分数来源和证据组成

度量目标：plan_trace 完整覆盖率 100%；有真实配对样本后再讨论 ECE。

API：
    record_step(plan_id, step_index, decision, confidence, fallback_action, evidence)
    replace_steps(plan_id, steps) -> int
    clear_steps(plan_id) -> int
    iter_steps(plan_id) -> list[StepTrace]
    compute_ece(predictions, outcomes, n_bins=10) -> float
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.tracing import trace_span  # noqa: E402
from storage.state_layout import (  # noqa: E402
    LEGACY_SHARED_DB,
    PLAN_EVIDENCE_SCHEMA,
    ensure_plan_evidence_metadata,
    resolve_plan_evidence_path,
)

ROOT = Path(__file__).resolve().parent.parent.parent
# Explicit test/operator override.  Normal runtime resolution uses a verified
# dedicated store, while existing installations remain on the legacy store
# until the non-destructive migration receipt is present.
_DB_PATH: Path | None = None
_DB_LOCK = threading.Lock()


# ============================================================
# Schema
# ============================================================

_SCHEMA = PLAN_EVIDENCE_SCHEMA


def database_path() -> Path:
    return resolve_plan_evidence_path(_DB_PATH)


def _conn() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_schema():
    with _DB_LOCK, closing(_conn()) as conn:
        conn.executescript(_SCHEMA)
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(plan_outcome)").fetchall()
        }
        if "evidence_classification" not in columns:
            conn.execute(
                "ALTER TABLE plan_outcome ADD COLUMN evidence_classification "
                "TEXT NOT NULL DEFAULT 'legacy_unclassified'"
            )
        conn.commit()
    path = database_path()
    if path.resolve() != LEGACY_SHARED_DB.resolve():
        ensure_plan_evidence_metadata(path)


_ensure_schema()


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StepTrace:
    plan_id: str
    step_index: int
    step_kind: Optional[str]
    poi_id: Optional[str]
    decision: str
    confidence: float
    fallback_action: Optional[dict]
    evidence: dict
    created_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StepTrace":
        return cls(
            plan_id=row["plan_id"],
            step_index=row["step_index"],
            step_kind=row["step_kind"],
            poi_id=row["poi_id"],
            decision=row["decision"],
            confidence=row["confidence"],
            fallback_action=json.loads(row["fallback_action"]) if row["fallback_action"] else None,
            evidence=json.loads(row["evidence"]) if row["evidence"] else {},
            created_at=row["created_at"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StepTraceInput:
    """One pending trace row used by the atomic plan replacement path."""

    step_index: int
    decision: str
    confidence: float
    step_kind: Optional[str] = None
    poi_id: Optional[str] = None
    fallback_action: Optional[dict] = None
    evidence: dict = field(default_factory=dict)


# ============================================================
# Public API
# ============================================================

def record_step(
    plan_id: str,
    step_index: int,
    decision: str,
    confidence: float,
    *,
    step_kind: Optional[str] = None,
    poi_id: Optional[str] = None,
    fallback_action: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> int:
    """记录一步 plan trace。

    同时开一个 trace_span（OTel 兼容），方便 trace 树里看到。
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0,1], got {confidence}")

    evidence = evidence or {}
    fallback_json = json.dumps(fallback_action, ensure_ascii=False) if fallback_action else None
    evidence_json = json.dumps(evidence, ensure_ascii=False)

    with trace_span(
        f"plan_step.{step_index}",
        attrs={
            "plan_id": plan_id,
            "step_kind": step_kind or "",
            "decision": decision[:120],
            "confidence": confidence,
            "poi_id": poi_id or "",
        },
    ):
        with _DB_LOCK, closing(_conn()) as conn:
            cur = conn.execute(
                "INSERT INTO plan_trace(plan_id, step_index, step_kind, poi_id, "
                "decision, confidence, fallback_action, evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (plan_id, step_index, step_kind, poi_id,
                 decision, confidence, fallback_json, evidence_json, time.time()),
            )
            conn.commit()
            return cur.lastrowid


def clear_steps(plan_id: str) -> int:
    """删除同一 plan 的旧 trace，供 reroute 后用最终方案重写。"""
    with _DB_LOCK, closing(_conn()) as conn:
        cur = conn.execute("DELETE FROM plan_trace WHERE plan_id=?", (plan_id,))
        conn.commit()
        return cur.rowcount


def replace_steps(plan_id: str, steps: Iterable[StepTraceInput]) -> int:
    """Atomically replace every trace row for one plan.

    Materialization and validation happen before the transaction. Readers see
    either the previous complete trace or the next complete trace, never the
    historical delete-plus-partial-insert state.
    """
    if not plan_id:
        raise ValueError("plan_id must not be empty")
    pending = tuple(steps)
    step_indices = [step.step_index for step in pending]
    if len(step_indices) != len(set(step_indices)):
        raise ValueError("step_index values must be unique within a plan trace")
    for step in pending:
        if not 0.0 <= step.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {step.confidence}")

    rows = [
        (
            plan_id,
            step.step_index,
            step.step_kind,
            step.poi_id,
            step.decision,
            step.confidence,
            json.dumps(step.fallback_action, ensure_ascii=False)
            if step.fallback_action
            else None,
            json.dumps(step.evidence, ensure_ascii=False),
            time.time(),
        )
        for step in pending
    ]
    with trace_span(
        "plan_trace.replace",
        attrs={"plan_id": plan_id, "step_count": len(rows)},
    ):
        with _DB_LOCK, closing(_conn()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM plan_trace WHERE plan_id=?", (plan_id,))
            conn.executemany(
                "INSERT INTO plan_trace(plan_id, step_index, step_kind, poi_id, "
                "decision, confidence, fallback_action, evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
    return len(rows)


def record_outcome(
    plan_id: str,
    step_index: int,
    actual_success: bool,
    notes: str = "",
    *,
    evidence_classification: str = "synthetic_test",
) -> int:
    """回填某步成败；默认标为 synthetic，不能冒充真人结果。"""
    allowed_classifications = {
        "synthetic_test",
        "legacy_unclassified",
        "human_verified_step",
    }
    if evidence_classification not in allowed_classifications:
        raise ValueError("unsupported outcome evidence classification")
    if evidence_classification == "human_verified_step" and notes.strip():
        raise ValueError("human step outcomes must not store free-text notes")
    with _DB_LOCK, closing(_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO plan_outcome(plan_id, step_index, actual_success, notes, "
            "evidence_classification, recorded_at) VALUES (?,?,?,?,?,?)",
            (
                plan_id,
                step_index,
                1 if actual_success else 0,
                notes,
                evidence_classification,
                time.time(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def iter_steps(plan_id: str) -> list[StepTrace]:
    """读某个 plan 的所有 step trace（按 step_index 升序）。"""
    with _DB_LOCK, closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM plan_trace WHERE plan_id=? ORDER BY step_index ASC",
            (plan_id,),
        ).fetchall()
    return [StepTrace.from_row(r) for r in rows]


def coverage_rate(plan_id: str, expected_steps: int) -> float:
    """plan_trace 覆盖率：实际记录 step 数 / 期望步数。"""
    if expected_steps <= 0:
        return 0.0
    actual = len(iter_steps(plan_id))
    return min(actual / expected_steps, 1.0)


# ============================================================
# ECE — Expected Calibration Error
# ============================================================

def compute_ece(
    predictions: Iterable[float],
    outcomes: Iterable[int],
    n_bins: int = 10,
) -> dict:
    """计算 Expected Calibration Error。

    ECE = Σ (|bin| / N) × |bin_acc - bin_conf|

    参考 Guo et al. 2017, "On Calibration of Modern Neural Networks"。

    Args:
        predictions: 模型的 confidence 序列（每个 ∈ [0,1]）
        outcomes: 实际成败序列（0 / 1）
        n_bins: 等距分桶数

    Returns:
        {
            "ece": float,
            "n_samples": int,
            "bin_stats": [
                {"range": [lo, hi], "n": ..., "conf": ..., "acc": ..., "gap": ...},
                ...
            ],
        }
    """
    preds = list(predictions)
    outs = list(outcomes)
    if len(preds) != len(outs):
        raise ValueError(f"length mismatch: preds={len(preds)} outs={len(outs)}")
    if not preds:
        return {"ece": 0.0, "n_samples": 0, "bin_stats": []}

    n = len(preds)
    bin_stats = []
    ece = 0.0
    for b in range(n_bins):
        lo = b / n_bins
        hi = (b + 1) / n_bins
        if b == n_bins - 1:
            mask = [(p >= lo and p <= hi) for p in preds]
        else:
            mask = [(p >= lo and p < hi) for p in preds]
        n_b = sum(mask)
        if n_b == 0:
            bin_stats.append({"range": [round(lo, 2), round(hi, 2)], "n": 0,
                              "conf": None, "acc": None, "gap": None})
            continue
        bin_preds = [p for p, m in zip(preds, mask) if m]
        bin_outs = [o for o, m in zip(outs, mask) if m]
        bin_conf = sum(bin_preds) / n_b
        bin_acc = sum(bin_outs) / n_b
        gap = abs(bin_acc - bin_conf)
        ece += (n_b / n) * gap
        bin_stats.append({
            "range": [round(lo, 2), round(hi, 2)],
            "n": n_b,
            "conf": round(bin_conf, 3),
            "acc": round(bin_acc, 3),
            "gap": round(gap, 3),
        })

    return {
        "ece": round(ece, 4),
        "n_samples": n,
        "bin_stats": bin_stats,
    }


def calibration_for_plan(
    plan_id: str,
    n_bins: int = 10,
    *,
    evidence_classification: str | None = None,
) -> Optional[dict]:
    """对一个 plan 跑 ECE：从 plan_trace + plan_outcome 联表。

    Returns None 如果没有 outcome 数据。
    """
    with _DB_LOCK, closing(_conn()) as conn:
        query = (
            "SELECT t.confidence, o.actual_success FROM plan_trace t "
            "JOIN plan_outcome o ON t.plan_id=o.plan_id AND t.step_index=o.step_index "
            "WHERE t.plan_id=?"
        )
        params: tuple = (plan_id,)
        if evidence_classification is not None:
            query += " AND o.evidence_classification=?"
            params += (evidence_classification,)
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return None
    return compute_ece([r["confidence"] for r in rows],
                       [r["actual_success"] for r in rows],
                       n_bins=n_bins)


def calibration_global(
    n_bins: int = 10,
    *,
    evidence_classification: str | None = None,
) -> Optional[dict]:
    """跨 plan 的全局 ECE。"""
    with _DB_LOCK, closing(_conn()) as conn:
        query = (
            "SELECT t.confidence, o.actual_success FROM plan_trace t "
            "JOIN plan_outcome o ON t.plan_id=o.plan_id AND t.step_index=o.step_index"
        )
        params: tuple = ()
        if evidence_classification is not None:
            query += " WHERE o.evidence_classification=?"
            params = (evidence_classification,)
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return None
    return compute_ece([r["confidence"] for r in rows],
                       [r["actual_success"] for r in rows],
                       n_bins=n_bins)


# ============================================================
# 上下文管理器：自动 trace 一步
# ============================================================

@contextmanager
def step_context(
    plan_id: str,
    step_index: int,
    decision: str,
    confidence: float,
    **kwargs,
):
    """简化版：with step_context(...) as step: step.set_outcome(True)
    上下文退出时把 outcome 写入。
    """
    class _StepHandle:
        def __init__(self):
            self._outcome: Optional[bool] = None
            self._notes: str = ""

        def set_outcome(self, success: bool, notes: str = ""):
            self._outcome = success
            self._notes = notes

    handle = _StepHandle()
    record_step(plan_id, step_index, decision, confidence, **kwargs)
    try:
        yield handle
    finally:
        if handle._outcome is not None:
            record_outcome(plan_id, step_index, handle._outcome, handle._notes)


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import uuid

    plan_id = f"test-{uuid.uuid4().hex[:8]}"

    # 记 5 步，confidence 从 0.6 → 0.95
    confidences = [0.6, 0.7, 0.8, 0.9, 0.95]
    outcomes = [True, True, True, False, True]   # confidence 高的一次错了，校准不完美

    for i, (c, ok) in enumerate(zip(confidences, outcomes)):
        record_step(
            plan_id, i, decision=f"step {i} 选 POI X",
            confidence=c, step_kind="visit",
            evidence={"ugc_count": 5 + i, "rank": i + 1},
            fallback_action={"if_full": "去同片区第二", "if_closed": "下一步前移"},
        )
        record_outcome(plan_id, i, ok)

    steps = iter_steps(plan_id)
    assert len(steps) == 5
    print(f"✓ 记录 5 步 + 5 outcome")

    cov = coverage_rate(plan_id, expected_steps=5)
    assert cov == 1.0
    print(f"✓ coverage_rate = {cov}")

    cal = calibration_for_plan(plan_id, n_bins=5)
    print(f"✓ ECE = {cal['ece']:.3f}  n_bins_used = {sum(1 for b in cal['bin_stats'] if b['n']>0)}")

    # 跨 plan 全局
    g = calibration_global(n_bins=5)
    print(f"✓ global ECE 含 {g['n_samples']} 样本 = {g['ece']:.3f}")

    # 验证 ECE 边界：完美校准（conf=1 全对，conf=0 全错）→ ECE = 0
    perfect = compute_ece([1.0, 1.0, 0.0, 0.0], [1, 1, 0, 0], n_bins=10)
    assert perfect["ece"] == 0.0, perfect
    print(f"✓ 完美校准 ECE = {perfect['ece']}")

    # 反例：模型说 0.9 确定但全错 → ECE 应大
    overconf = compute_ece([0.9, 0.9, 0.9, 0.9], [0, 0, 0, 0], n_bins=10)
    assert overconf["ece"] > 0.85, overconf
    print(f"✓ 过度自信 ECE = {overconf['ece']}")

    print("\n所有自测通过！")
