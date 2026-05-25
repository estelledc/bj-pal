"""[12] 收尾对比工具 — v1 / v2 / v3 三档指标对照。

用法：
    .venv/bin/python -m scripts.eval_compare

读取：
    data/longcat_demo_results.json     v1 baseline（40 场景，无 [73][75][15]）
    data/longcat_demo_v2_results.json  v2 实测（40 场景，含 [73][75]）
    data/longcat_eval100_v3.json       v3 实测（100 场景，含 [11][12][88]）
    data/longcat_eval100_mock.json     mock baseline（100 场景，离线）

输出对比表，写到 docs/eval-100-results.md。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.eval_plans import evaluate_run  # noqa: E402

CONFIGS = [
    {"label": "v1 (40, baseline)", "path": "data/longcat_demo_results.json", "key": "v1"},
    {"label": "v2 (40, [73][75])", "path": "data/longcat_demo_results.json", "key": "v2"},
    {"label": "v2_run2 (40)", "path": "data/longcat_demo_v2_results.json", "key": "v2"},
    {"label": "v3 (100, [11]+[12]+[88])", "path": "data/longcat_eval100_v3.json", "key": "v2"},
    {"label": "mock_v3 (100)", "path": "data/longcat_eval100_mock.json", "key": "v2"},
]


def main():
    rows = []
    for cfg in CONFIGS:
        path = ROOT / cfg["path"]
        if not path.exists():
            rows.append({**cfg, "missing": True})
            continue
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            rows.append({**cfg, "error": str(exc)})
            continue
        report = evaluate_run(data, plan_key=cfg["key"])
        rows.append({**cfg, "metrics": report["metrics"], "counts": report["counts"]})

    print("\n=== Eval 对比 ===\n")
    headers = ["label", "delivery", "commonsense", "hard", "final"]
    print(" | ".join(f"{h:<25}" for h in headers))
    print("-" * 130)
    for r in rows:
        if r.get("missing"):
            print(f"{r['label']:<25} | (file missing: {r['path']})")
            continue
        if r.get("error"):
            print(f"{r['label']:<25} | ERROR {r['error']}")
            continue
        m = r["metrics"]
        c = r["counts"]
        print(
            f"{r['label']:<25} | "
            f"{m['delivery_rate']:.3f} ({c['delivery']:<6}) | "
            f"{m['commonsense_pass']:.3f} ({c['commonsense']:<6}) | "
            f"{m['hard_constraint_pass']:.3f} ({c['hard']:<6}) | "
            f"{m['final_pass']:.3f} ({c['final']:<6})"
        )

    # 写 docs/eval-100-results.md
    out = ROOT / "docs" / "eval-100-results.md"
    lines = ["# Eval 对比 — v1 / v2 / v3", ""]
    lines.append("> 自动生成自 `scripts/eval_compare.py`")
    lines.append("")
    lines.append("| 配置 | delivery | commonsense | hard_constraint | **final_pass** |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        if r.get("missing") or r.get("error"):
            lines.append(f"| {r['label']} | (skipped: {r.get('error') or 'no file'}) | - | - | - |")
            continue
        m, c = r["metrics"], r["counts"]
        lines.append(
            f"| {r['label']} | "
            f"{m['delivery_rate']:.3f} ({c['delivery']}) | "
            f"{m['commonsense_pass']:.3f} ({c['commonsense']}) | "
            f"{m['hard_constraint_pass']:.3f} ({c['hard']}) | "
            f"**{m['final_pass']:.3f} ({c['final']})** |"
        )
    out.write_text("\n".join(lines) + "\n")
    print(f"\n→ 写入 {out}")


if __name__ == "__main__":
    main()
