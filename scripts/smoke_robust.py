"""Smoke test：只跑 4 个之前失败的场景，验证 [73][75] 修复有效。

之前失败：
- S04 family/什刹海 — JSON 截断
- S11 friends/三里屯V3 — JSON 截断
- S15 friends/望京V3 — JSON 截断
- S16 friends/珠市口V3 — RPM 限流（连续 S16-S23 触发）

跑完看 4/4 是否能全过。
"""
from __future__ import annotations
import sys, time, json, traceback, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

# 复用主 demo 的场景定义
import importlib.util
spec = importlib.util.spec_from_file_location("run_longcat_demo", ROOT / "scripts" / "run_longcat_demo.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

target_ids = {"S04", "S11", "S15", "S16"}
SCENARIOS = [s for s in m.SCENARIOS if s["id"] in target_ids]
print(f"smoke: {len(SCENARIOS)} 场景：{[s['id'] for s in SCENARIOS]}")

results = []
t0 = time.time()
for i, sc in enumerate(SCENARIOS, 1):
    print(f"\n=== [{i}/{len(SCENARIOS)}] {sc['id']} {sc['title']} ===", flush=True)
    results.append(m.run_one(sc))

out_path = ROOT / "data" / "smoke_robust_results.json"
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
n_ok = sum(1 for r in results if r.get("ok"))
print(f"\n=== smoke 结果：{n_ok}/{len(results)} 通过，总耗时 {time.time()-t0:.0f}s ===")
for r in results:
    flag = "✓" if r.get("ok") else "✗"
    err = "" if r.get("ok") else f" — {r.get('error','')[:80]}"
    repaired = ""
    if r.get("ok") and r.get("v1", {}).get("steps"):
        # 看 plan 字典里有没有 _repaired 痕迹（注意 Plan dataclass 不会保留这个字段，
        # 但日志里能看 RateLimiter / retry 输出）
        pass
    print(f"  {flag} {r['scenario']} {r['title']}{err}")
print(f"\n详情：{out_path}")
