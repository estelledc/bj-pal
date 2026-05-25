"""[12] eval 100 场景跑 LongCat 验证。

跑全 100 场景的版本（基于 run_longcat_demo.py SCENARIOS）。
特性：
- 增量保存到 data/longcat_eval100_v3.json，每 5 个 flush 一次
- 失败场景标 ok=False 但不终止
- 支持 --skip / --only-new 只跑增量；--limit 跑前 N 个
- 完成后自动用 evals/eval_plans 算指标

用法：
    BJ_PAL_LLM=longcat python -m scripts.run_longcat_eval100 --limit 30
    BJ_PAL_LLM=longcat python -m scripts.run_longcat_eval100 --only-new

预估：100 场景 × ~37s 平均 = ~60 分钟（受 LongCat RPM=10 限制）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.run_longcat_demo import SCENARIOS, run_one  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 个（0=全跑）")
    ap.add_argument("--only-new", action="store_true",
                    help="只跑 S41-S100（v3 新增的 60 个）")
    ap.add_argument("--skip", type=int, default=0,
                    help="跳过前 N 个")
    ap.add_argument("--out", default=str(ROOT / "data" / "longcat_eval100_v3.json"))
    args = ap.parse_args()

    scenarios = SCENARIOS
    if args.only_new:
        scenarios = [s for s in scenarios if int(s["id"][1:]) >= 41]
    if args.skip:
        scenarios = scenarios[args.skip:]
    if args.limit:
        scenarios = scenarios[:args.limit]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    if out_path.exists():
        try:
            results = json.loads(out_path.read_text())
            done_ids = {r["scenario"] for r in results if r.get("ok")}
            scenarios = [s for s in scenarios if s["id"] not in done_ids]
            print(f"已有 {len(done_ids)} 条 ok 结果，本次跑 {len(scenarios)}")
        except Exception:
            results = []

    t0 = time.time()
    for i, sc in enumerate(scenarios, 1):
        print(f"\n=== [{i}/{len(scenarios)}] {sc['id']} ===", flush=True)
        results.append(run_one(sc))
        if i % 5 == 0:
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(f"  flushed {len(results)} 条到 {out_path}", flush=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    n_ok = sum(1 for r in results if r.get("ok"))
    elapsed = time.time() - t0
    print(f"\n完成 {n_ok}/{len(results)}（本次跑 {len(scenarios)}），"
          f"总耗时 {elapsed:.0f}s（avg {elapsed/max(1,len(scenarios)):.1f}s/场景）")
    print(f"写入 {out_path}")
    print("\n下一步算评估指标：")
    print(f"  python -m evals.eval_plans --input {out_path} --plan-key v2")


if __name__ == "__main__":
    main()
