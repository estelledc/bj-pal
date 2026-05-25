"""L2 集成评测入口：5 模块 × 5 case = 25 case，每周跑，5min 内。

跑法：
    python3 evals/behavioral/run_l2.py
    python3 evals/behavioral/run_l2.py --json
    python3 evals/behavioral/run_l2.py --save
    python3 evals/behavioral/run_l2.py --module weekday   # 仅跑某模块

退出码：全过 = 0；任意 module 通过率 < 100% = 1
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


L2_MODULES = [
    ("weekday", "evals.behavioral.L2_integration.weekday_cases"),
    ("time_bucket", "evals.behavioral.L2_integration.time_bucket_cases"),
    ("text_intake", "evals.behavioral.L2_integration.text_intake_cases"),
    ("convergence", "evals.behavioral.L2_integration.convergence_cases"),
    ("memory", "evals.behavioral.L2_integration.memory_cases"),
]


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "nogit"


def run_module(name: str, mod_path: str, *, verbose: bool = True) -> dict:
    if verbose:
        print(f"\n## {name} ({mod_path.split('.')[-1]})")
    mod = importlib.import_module(mod_path)
    cases = getattr(mod, "CASES", [])
    results = []
    for case in cases:
        if verbose:
            print(f"  {case['name']:<32} ", end="", flush=True)
        try:
            r = case["runner"]()
            r["name"] = case["name"]
            r["capability"] = case["capability"]
            r["description"] = case["description"]
            results.append(r)
            if verbose:
                mark = "PASS" if r["pass"] else "FAIL"
                print(f"{mark} ({r['latency_ms']}ms)")
        except Exception as exc:
            results.append({
                "name": case["name"],
                "capability": case["capability"],
                "description": case["description"],
                "pass": False,
                "observed": {"error": f"{type(exc).__name__}: {exc}"},
                "latency_ms": 0,
            })
            if verbose:
                print(f"ERROR ({type(exc).__name__})")

    n = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    return {
        "module": name,
        "n_cases": n,
        "n_pass": n_pass,
        "pass_rate": round(n_pass / n, 3) if n else 0.0,
        "results": results,
    }


def run_all(*, verbose: bool = True, only_module: str | None = None) -> dict:
    started = time.time()
    module_reports = []
    for name, mod_path in L2_MODULES:
        if only_module and only_module != name:
            continue
        module_reports.append(run_module(name, mod_path, verbose=verbose))

    n = sum(m["n_cases"] for m in module_reports)
    n_pass = sum(m["n_pass"] for m in module_reports)
    summary = {
        "level": "L2",
        "git_sha": _git_sha(),
        "started_at": round(started, 3),
        "duration_s": round(time.time() - started, 3),
        "n_cases": n,
        "n_pass": n_pass,
        "pass_rate": round(n_pass / n, 3) if n else 0.0,
        "modules": module_reports,
    }

    if verbose:
        print(f"\n## summary")
        for m in module_reports:
            mark = "✓" if m["pass_rate"] == 1.0 else "✗"
            print(f"  {mark} {m['module']:<14} {m['n_pass']}/{m['n_cases']} "
                  f"({m['pass_rate']:.0%})")
        print(f"\nL2 总通过率：{n_pass}/{n} ({summary['pass_rate']:.0%})")
        print(f"耗时 {summary['duration_s']}s · sha {summary['git_sha']}")

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="仅打印 JSON")
    ap.add_argument("--save", action="store_true",
                    help="写 evals/results/L2_<sha>_<ts>.json")
    ap.add_argument("--module", default=None,
                    help="仅跑某模块（weekday / time_bucket / text_intake / convergence / memory）")
    args = ap.parse_args()

    summary = run_all(verbose=not args.json, only_module=args.module)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.save:
        results_dir = ROOT / "evals" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        ts = int(summary["started_at"])
        out = results_dir / f"L2_{summary['git_sha']}_{ts}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\nsaved: {out.relative_to(ROOT)}")

    sys.exit(0 if summary["pass_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
