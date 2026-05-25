"""L3 全量评测入口：100 case × 多信号 = ~280 次信号检查。

跑法：
    python3 evals/behavioral/run_l3.py
    python3 evals/behavioral/run_l3.py --json
    python3 evals/behavioral/run_l3.py --save
    python3 evals/behavioral/run_l3.py --persona family --scenario rainy_day  # 子集

输出：
- per-signal pass rate（S1-S5 各信号通过率）
- per-segment heatmap（persona × scenario）
- 失败 case top 列表

退出码：全过 = 0；任意 signal pass rate < 90% = 1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.behavioral.L3_full.fixtures import build_all_cases  # noqa: E402
from evals.behavioral.L3_full.signal_checks import (  # noqa: E402
    check_all_signals,
    reset_plan_cache,
)


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "nogit"


def run_all(*, verbose: bool = True,
            persona_filter: str | None = None,
            scenario_filter: str | None = None) -> dict:
    cases = build_all_cases()
    if persona_filter:
        cases = [c for c in cases if c.persona == persona_filter]
    if scenario_filter:
        cases = [c for c in cases if c.scenario == scenario_filter]

    if verbose:
        print(f"L3 跑 {len(cases)} case ...")

    started = time.time()
    reset_plan_cache()

    case_results = []
    signal_stats: dict[str, dict] = defaultdict(lambda: {"pass": 0, "total": 0})
    segment_stats: dict[tuple[str, str], dict] = defaultdict(lambda: {"pass": 0, "total": 0})

    for i, case in enumerate(cases, 1):
        if verbose and i % 10 == 0:
            print(f"  [{i}/{len(cases)}]")
        signals = check_all_signals(case)
        all_pass = all(s["pass"] for s in signals.values())

        case_results.append({
            "case_id": case.case_id,
            "persona": case.persona,
            "scenario": case.scenario,
            "query": case.query,
            "all_pass": all_pass,
            "signals": signals,
        })

        for sig, r in signals.items():
            signal_stats[sig]["total"] += 1
            if r["pass"]:
                signal_stats[sig]["pass"] += 1

        seg = (case.persona, case.scenario)
        segment_stats[seg]["total"] += 1
        if all_pass:
            segment_stats[seg]["pass"] += 1

    n = len(case_results)
    n_all_pass = sum(1 for r in case_results if r["all_pass"])

    # 排序信号通过率
    signal_summary = []
    for sig in ("S1", "S2", "S3", "S4", "S5"):
        if sig in signal_stats:
            s = signal_stats[sig]
            signal_summary.append({
                "signal": sig,
                "pass": s["pass"],
                "total": s["total"],
                "rate": round(s["pass"] / s["total"], 3) if s["total"] else 0.0,
            })

    summary = {
        "level": "L3",
        "git_sha": _git_sha(),
        "started_at": round(started, 3),
        "duration_s": round(time.time() - started, 3),
        "n_cases": n,
        "n_all_pass": n_all_pass,
        "all_pass_rate": round(n_all_pass / n, 3) if n else 0.0,
        "signal_summary": signal_summary,
        "segment_summary": [
            {
                "persona": p, "scenario": sc,
                "pass": v["pass"], "total": v["total"],
                "rate": round(v["pass"] / v["total"], 3) if v["total"] else 0.0,
            }
            for (p, sc), v in sorted(segment_stats.items())
        ],
        "failed_cases": [
            {"case_id": r["case_id"], "query": r["query"][:60],
             "failed_signals": [s for s, x in r["signals"].items() if not x["pass"]]}
            for r in case_results if not r["all_pass"]
        ][:20],
    }

    if verbose:
        print(f"\n## per-signal pass rate")
        for s in signal_summary:
            mark = "✓" if s["rate"] >= 0.9 else "✗"
            print(f"  {mark} {s['signal']}: {s['pass']:3d}/{s['total']:3d}  ({s['rate']:.0%})")

        print(f"\n## persona × scenario heatmap")
        # 表格化打印
        scenarios = sorted({sc for _, sc in segment_stats})
        personas = sorted({p for p, _ in segment_stats})
        header = "  " + " " * 16 + "  ".join(f"{sc[:14]:>14}" for sc in scenarios)
        print(header)
        for p in personas:
            row = [f"{p:14}"]
            for sc in scenarios:
                v = segment_stats.get((p, sc), {"pass": 0, "total": 0})
                if v["total"]:
                    pct = int(100 * v["pass"] / v["total"])
                    row.append(f"{v['pass']:3d}/{v['total']:<3d}({pct:3d}%)")
                else:
                    row.append("    -")
            print(f"  {row[0]} " + "  ".join(c.rjust(14) for c in row[1:]))

        print(f"\n## summary")
        print(f"  全 case 全信号通过：{n_all_pass}/{n} ({summary['all_pass_rate']:.0%})")
        print(f"  耗时 {summary['duration_s']}s · sha {summary['git_sha']}")
        if summary["failed_cases"]:
            print(f"\n  前 5 个失败 case：")
            for f in summary["failed_cases"][:5]:
                print(f"    - {f['case_id']}: {f['query']!r} 缺 {f['failed_signals']}")

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--persona", default=None)
    ap.add_argument("--scenario", default=None)
    args = ap.parse_args()

    summary = run_all(
        verbose=not args.json,
        persona_filter=args.persona,
        scenario_filter=args.scenario,
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.save:
        results_dir = ROOT / "evals" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        ts = int(summary["started_at"])
        out = results_dir / f"L3_{summary['git_sha']}_{ts}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\nsaved: {out.relative_to(ROOT)}")

    # 任意信号 < 90% → exit 1
    fail = any(s["rate"] < 0.9 for s in summary["signal_summary"])
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
