"""v3.1 D7 校准时序 seed 工具。

跑法：python3 -m etl.seed_calibration_data [--n 30]

为什么需要 seed：
- v2.4 D1 加了 plan_tracer 后，每次 plan() 都自动落 trace，但 outcome
  需要业务回填（用户实际去过没去过）。demo 阶段没有真实回填链路。
- 本工具按 plan 的每步 confidence 概率反推 outcome（含噪声），让
  calibration_history 时序图有足够样本。

数据生成模型（保证 ECE > 0 但合理）：
  实际 success 概率 = clip(confidence + noise, 0, 1)，noise ~ U(-0.15, +0.10)
  → 模型整体略乐观（noise 中位数偏负），ECE 会显示"过度自信"
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.plan_tracer import iter_steps, record_outcome  # noqa: E402
from agents.planner import plan as make_plan  # noqa: E402
from agents.types import UserPreferences  # noqa: E402


SEED_QUERIES = [
    ("family",  "今天下午带 5 岁娃出去玩，老婆减脂"),
    ("family",  "周六全家出门，娃喜欢动物"),
    ("family",  "下午带娃去博物馆"),
    ("family",  "周日全家溜达，4 小时左右"),
    ("family",  "带孩子下午找咖啡店"),
    ("family",  "周末带娃和老婆吃饭"),
    ("friends", "4 个朋友周六下午出去玩"),
    ("friends", "周末和朋友找下午茶"),
    ("friends", "几个朋友想 hang out"),
    ("friends", "4 人雍和宫片区下午"),
    ("friends", "周日 4 个人吃饭"),
    ("friends", "朋友们下午想喝咖啡"),
    ("solo",    "一个人下午想出去走走"),
    ("solo",    "自己周末去南锣"),
    ("solo",    "一个人想找咖啡店看书"),
    ("solo",    "下午独自溜达"),
    ("solo",    "想自己安静度过"),
    ("with_parents", "周末带父母出门"),
    ("with_parents", "下午带爹妈逛逛"),
    ("with_parents", "和父母想找不太累的地方"),
    ("with_parents", "带二老下午散步"),
    ("with_parents", "周日带老人喝茶"),
    ("friends", "周五下班 4 人吃饭"),
    ("family",  "雨天带娃想去博物馆"),
    ("solo",    "雨天一个人想去书店"),
    ("friends", "下雨天朋友 4 人室内"),
    ("family",  "周一中午带娃临时约饭"),
    ("friends", "工作日下午想溜达"),
    ("family",  "周五晚老婆生日吃饭"),
    ("friends", "周五晚去簋街吃宵夜"),
]


def seed(n: int = 30, *, rng_seed: int = 42, verbose: bool = True) -> dict:
    """跑 n 个 plan + 给每步反推 outcome。"""
    rng = random.Random(rng_seed)
    queries = SEED_QUERIES * (n // len(SEED_QUERIES) + 1)
    queries = queries[:n]

    n_plans = 0
    n_outcomes = 0
    for persona, q in queries:
        prefs = UserPreferences(persona=persona, raw_input=q,
                                 target_start="14:00", duration_hours=4.0)
        try:
            p = make_plan(user_input=q, persona=persona, prefs=prefs)
            n_plans += 1
        except Exception as exc:
            if verbose:
                print(f"  [skip] {persona} '{q[:30]}'  → {type(exc).__name__}")
            continue

        # 给每步反推 outcome
        traces = iter_steps(p.plan_id)
        for t in traces:
            # 实际 success 概率 = clip(conf + noise, 0, 1)
            noise = rng.uniform(-0.15, 0.10)
            actual_p = max(0.0, min(1.0, t.confidence + noise))
            success = rng.random() < actual_p
            record_outcome(
                p.plan_id,
                t.step_index,
                success,
                evidence_classification="synthetic_test",
            )
            n_outcomes += 1

        if verbose and n_plans % 5 == 0:
            print(f"  seeded {n_plans}/{n} plans, {n_outcomes} outcomes ...")

    if verbose:
        print(f"\n✓ 完成：{n_plans} plans, {n_outcomes} outcomes")
    return {"n_plans": n_plans, "n_outcomes": n_outcomes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    print(f"开始 seed {args.n} 个 plan ...")
    seed(n=args.n)

    # 报告 calibration_history 现状
    from agents.calibration_history import get_plan_count_summary
    summary = get_plan_count_summary()
    print(f"\n当前数据状态：")
    print(f"  plans={summary['n_plans']}")
    print(f"  traces={summary['n_traces']}")
    print(f"  outcomes={summary['n_outcomes']}")
    print(f"  paired={summary['n_paired']}")
    print(f"  global_ece={summary['global_ece']}")


if __name__ == "__main__":
    main()
