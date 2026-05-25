"""L1 anchor evals 入口。

跑法：
    python3 evals/behavioral/run_l1.py
    python3 evals/behavioral/run_l1.py --json   # 仅打印 JSON
    python3 evals/behavioral/run_l1.py --save   # 写 evals/results/L1_<sha>_<ts>.json

退出码：全过 = 0，任何 fail = 1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.behavioral.anchor_cases import ANCHOR_CASES  # noqa: E402


def _git_sha_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "nogit"


def run_all(verbose: bool = True) -> dict:
    started_at = time.time()
    results = []
    for case in ANCHOR_CASES:
        if verbose:
            print(f"  running {case['name']:<30} [{case['signal']}] ... ", end="", flush=True)
        try:
            r = case["runner"]()
            r["name"] = case["name"]
            r["signal"] = case["signal"]
            r["description"] = case["description"]
            results.append(r)
            if verbose:
                mark = "PASS" if r["pass"] else "FAIL"
                print(f"{mark} ({r['latency_ms']}ms)")
        except Exception as exc:
            results.append({
                "name": case["name"],
                "signal": case["signal"],
                "description": case["description"],
                "pass": False,
                "observed": {"error": f"{type(exc).__name__}: {exc}"},
                "latency_ms": 0,
            })
            if verbose:
                print(f"ERROR ({type(exc).__name__})")

    n = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    pass_rate = n_pass / n if n else 0.0
    summary = {
        "level": "L1",
        "git_sha": _git_sha_short(),
        "started_at": round(started_at, 3),
        "duration_s": round(time.time() - started_at, 3),
        "n_cases": n,
        "n_pass": n_pass,
        "pass_rate": round(pass_rate, 3),
        "results": results,
    }

    if verbose:
        print(f"\nL1 通过率: {n_pass}/{n} ({pass_rate:.0%})")
        print(f"耗时: {summary['duration_s']}s   sha: {summary['git_sha']}")
        if pass_rate < 1.0:
            fails = [r["name"] for r in results if not r["pass"]]
            print(f"未过: {', '.join(fails)}")

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="仅打印 JSON")
    ap.add_argument("--save", action="store_true", help="写 evals/results/L1_<sha>_<ts>.json")
    args = ap.parse_args()

    summary = run_all(verbose=not args.json)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.save:
        results_dir = ROOT / "evals" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        ts = int(summary["started_at"])
        out_path = results_dir / f"L1_{summary['git_sha']}_{ts}.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\nsaved: {out_path.relative_to(ROOT)}")

    sys.exit(0 if summary["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
