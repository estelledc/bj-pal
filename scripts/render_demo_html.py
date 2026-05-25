"""把 longcat_demo_results.json 渲染为单页 HTML。"""
from __future__ import annotations
import json, html, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "longcat_demo_results.json"
OUT = ROOT / "data" / "longcat_demo_results.html"

KIND_LABEL = {
    "citywalk": "city walk", "culture": "文化", "snack": "小食",
    "rest": "休息", "meal": "正餐", "shopping": "购物",
    "depart": "返程", "scenic": "景点", "museum": "博物馆",
}


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def render_steps(steps):
    rows = []
    for s in steps:
        rerouted = "rerouted" if s["is_rerouted"] else ""
        kind = esc(KIND_LABEL.get(s["kind"], s["kind"]))
        rationale = esc(s["rationale"] or "")
        rows.append(f"""
        <li class="step {rerouted}">
          <div class="step-head">
            <span class="step-idx">#{s["step_index"]}</span>
            <span class="step-time">{esc(s["start_time"])}</span>
            <span class="step-kind">{kind}</span>
            <span class="step-poi">{esc(s["poi_name"])}</span>
            <span class="step-meta">{s["duration_min"]}min · {esc(s["mode_to_here"] or "—")}</span>
            {"<span class='reroute-flag'>rerouted</span>" if s["is_rerouted"] else ""}
          </div>
          {f'<div class="step-rationale">{rationale}</div>' if rationale else ''}
        </li>""")
    return "<ol class='steps'>" + "".join(rows) + "</ol>"


def render_events(events):
    if not events:
        return '<div class="no-event">probe 未触发 reroute，方案 v1 通过</div>'
    parts = []
    for ev in events:
        evi = "".join(f"<li>{esc(e)}</li>" for e in ev["evidence"])
        parts.append(f"""
        <div class="event">
          <div class="event-head">
            <span class="event-tag">step #{ev["failed_step_idx"]} · {esc(ev["reason"])}</span>
            <span class="event-from">{esc(ev["failed_poi_name"])}</span>
            <span class="event-arrow">→</span>
            <span class="event-to">{esc(ev["replacement_poi_name"])}</span>
          </div>
          <ul class="evidence">{evi}</ul>
        </div>""")
    return "".join(parts)


def render_im_card(card):
    body = esc(card["body"]).replace("\n", "<br>")
    actions = "".join(f"<button class='im-btn'>{esc(a)}</button>" for a in card["actions"])
    return f"""
    <div class="im-card">
      <div class="im-meta">发送给 {esc(card["contact"])} · audience={esc(card["audience"])}</div>
      <div class="im-title">{esc(card["title"])}</div>
      <div class="im-body">{body}</div>
      <div class="im-actions">{actions}</div>
    </div>"""


def render_tool_calls(calls):
    if not calls:
        return '<div class="muted">本场景无 tool 调用记录</div>'
    rows = []
    for c in calls:
        rows.append(f"""
        <tr>
          <td class="mono">{esc(c["timestamp"][11:19])}</td>
          <td class="mono">{esc(c["tool_name"])}</td>
          <td class="num">{c["latency_ms"]:.0f}ms</td>
          <td>{esc(c["status"])}</td>
          <td class="mono small">{esc(c["params_brief"])}</td>
        </tr>""")
    return f"""<table class="tool-table">
        <thead><tr><th>time</th><th>tool</th><th>latency</th><th>status</th><th>params</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>"""


def render_scenario(r):
    if not r.get("ok"):
        return f"""
        <section class="scenario error">
          <h2>{esc(r["scenario"])} · {esc(r["title"])}</h2>
          <div class="err">{esc(r.get("error",""))}</div>
        </section>"""
    inp = r["input"]
    prefs = inp["prefs"]
    pref_chips = "".join(
        f"<span class='chip'>{esc(k)}: {esc(v)}</span>"
        for k, v in prefs.items() if v not in (None, [], "")
    )
    timing = r["timing"]
    return f"""
    <section class="scenario">
      <header class="sc-head">
        <div class="sc-id">{esc(r["scenario"])}</div>
        <div class="sc-title">{esc(r["title"])}</div>
        <div class="sc-timing">plan {timing["plan_s"]}s · probe {timing["probe_s"]}s · 总 {timing["total_s"]}s</div>
      </header>

      <div class="block">
        <h3>输入</h3>
        <div class="user-input">{esc(inp["user_input"])}</div>
        <div class="meta-row">
          <span class="chip strong">persona: {esc(inp["persona"])}</span>
          <span class="chip strong">area: {esc(inp["area_anchor"])}</span>
          {pref_chips}
        </div>
      </div>

      <div class="grid-2">
        <div class="block">
          <h3>v1 方案（Planner 输出）</h3>
          {render_steps(r["v1"]["steps"])}
          {f'<div class="summary">{esc(r["v1"]["summary"])}</div>' if r["v1"]["summary"] else ''}
        </div>
        <div class="block">
          <h3>v2 方案（Probe + Reroute 后）</h3>
          {render_steps(r["v2"]["steps"])}
          {f'<div class="summary">{esc(r["v2"]["summary"])}</div>' if r["v2"]["summary"] else ''}
        </div>
      </div>

      <div class="block">
        <h3>Probe 触发的 reroute</h3>
        {render_events(r["events"])}
      </div>

      <div class="block">
        <h3>话术化 IM 卡片</h3>
        {render_im_card(r["im_card"])}
      </div>

      <details class="block">
        <summary><h3 style="display:inline">Tool Call Log（{len(r["tool_calls"])} 条）</h3></summary>
        {render_tool_calls(r["tool_calls"])}
      </details>
    </section>"""


CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       max-width: 1200px; margin: 0 auto; padding: 24px; color: #1d1d1f; line-height: 1.5;
       background: #fafafa; }
h1 { font-size: 22px; margin: 0 0 8px; }
h2 { font-size: 18px; margin: 0 0 12px; }
h3 { font-size: 14px; margin: 0 0 10px; color: #555; font-weight: 600;
     text-transform: uppercase; letter-spacing: 0.04em; }
.muted { color: #888; font-size: 13px; }
.summary-bar { display: flex; gap: 24px; padding: 12px 16px; background: #fff;
               border: 1px solid #e5e5e5; border-radius: 8px; margin-bottom: 24px;
               font-size: 13px; }
.summary-bar .num { font-weight: 600; color: #1d1d1f; font-size: 15px; }

.scenario { background: #fff; border: 1px solid #e5e5e5; border-radius: 12px;
            padding: 20px; margin-bottom: 20px; }
.scenario.error { border-color: #d33; }
.sc-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px;
           padding-bottom: 12px; border-bottom: 1px solid #f0f0f0; }
.sc-id { font-weight: 700; font-size: 16px; color: #06c; }
.sc-title { font-size: 16px; flex: 1; }
.sc-timing { font-size: 12px; color: #888; font-family: ui-monospace, monospace; }

.block { margin: 16px 0; }
.user-input { font-size: 14px; padding: 10px 14px; background: #f5f5f7;
              border-left: 3px solid #06c; border-radius: 4px; margin-bottom: 10px; }
.meta-row { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { display: inline-block; padding: 2px 8px; background: #f0f0f0; border-radius: 4px;
        font-size: 12px; font-family: ui-monospace, monospace; color: #555; }
.chip.strong { background: #e8f0fe; color: #06c; }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) { .grid-2 { grid-template-columns: 1fr; } }

.steps { list-style: none; padding: 0; margin: 0; }
.step { padding: 8px 10px; border-left: 2px solid #ddd; margin: 4px 0;
        background: #fbfbfb; border-radius: 0 4px 4px 0; }
.step.rerouted { border-left-color: #f60; background: #fff5ee; }
.step-head { display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; font-size: 13px; }
.step-idx { color: #888; font-family: ui-monospace, monospace; min-width: 22px; }
.step-time { font-family: ui-monospace, monospace; font-size: 12px; color: #555; }
.step-kind { background: #e8f0fe; color: #06c; padding: 1px 6px; border-radius: 3px;
             font-size: 11px; }
.step-poi { font-weight: 600; }
.step-meta { color: #888; font-size: 12px; font-family: ui-monospace, monospace; margin-left: auto; }
.reroute-flag { background: #f60; color: #fff; padding: 1px 6px; border-radius: 3px;
                font-size: 11px; font-weight: 600; }
.step-rationale { color: #666; font-size: 12px; margin-top: 4px; padding-left: 30px; }

.summary { font-size: 13px; padding: 8px 12px; background: #f5f5f7; border-radius: 4px;
           color: #555; margin-top: 10px; }

.event { padding: 10px 12px; background: #fff5ee; border: 1px solid #ffd9b8;
         border-radius: 6px; margin: 6px 0; }
.event-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.event-tag { background: #f60; color: #fff; padding: 2px 8px; border-radius: 3px;
             font-size: 11px; font-weight: 600; }
.event-from { color: #888; text-decoration: line-through; }
.event-arrow { color: #f60; font-weight: 600; }
.event-to { color: #16a34a; font-weight: 600; }
.evidence { margin: 6px 0 0 20px; padding: 0; font-size: 12px; color: #555; }
.evidence li { margin: 2px 0; }
.no-event { padding: 10px 12px; background: #f0f9ff; border: 1px solid #bae6fd;
            border-radius: 6px; color: #0369a1; font-size: 13px; }

.im-card { max-width: 360px; padding: 14px 16px; background: linear-gradient(180deg,#fff,#f7f7f8);
           border: 1px solid #d0d0d0; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.im-meta { font-size: 11px; color: #888; margin-bottom: 6px; font-family: ui-monospace, monospace; }
.im-title { font-weight: 600; font-size: 14px; margin-bottom: 8px; color: #1d1d1f; }
.im-body { font-size: 13px; line-height: 1.6; color: #333; white-space: pre-wrap;
           padding: 8px 0; border-top: 1px solid #eee; border-bottom: 1px solid #eee; }
.im-actions { display: flex; gap: 6px; margin-top: 10px; }
.im-btn { padding: 4px 12px; font-size: 12px; background: #f0f0f0; border: 1px solid #d0d0d0;
          border-radius: 4px; cursor: pointer; }
.im-btn:first-child { background: #06c; color: #fff; border-color: #06c; }

.tool-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 6px; }
.tool-table th { text-align: left; padding: 6px 8px; background: #f5f5f7;
                 font-weight: 600; color: #555; border-bottom: 1px solid #e0e0e0; }
.tool-table td { padding: 4px 8px; border-bottom: 1px solid #f0f0f0; }
.tool-table .mono { font-family: ui-monospace, monospace; }
.tool-table .num { text-align: right; font-family: ui-monospace, monospace; }
.tool-table .small { font-size: 11px; color: #888; max-width: 400px; overflow: hidden;
                     text-overflow: ellipsis; white-space: nowrap; }

details > summary { cursor: pointer; }
details[open] > summary { margin-bottom: 8px; }
"""


def main():
    results = json.loads(SRC.read_text())
    n_ok = sum(1 for r in results if r.get("ok"))
    n_reroute_total = sum(len(r.get("events", [])) for r in results if r.get("ok"))
    total_s = sum(r.get("timing", {}).get("total_s", 0) for r in results if r.get("ok"))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    summary_html = f"""
    <div class="summary-bar">
      <div><span class="muted">生成时间</span> <span class="num">{now}</span></div>
      <div><span class="muted">场景</span> <span class="num">{n_ok}/{len(results)}</span></div>
      <div><span class="muted">reroute 触发</span> <span class="num">{n_reroute_total}</span></div>
      <div><span class="muted">总耗时</span> <span class="num">{total_s:.1f}s</span></div>
      <div><span class="muted">LLM</span> <span class="num">LongCat (Anthropic 兼容协议)</span></div>
    </div>"""

    body = "".join(render_scenario(r) for r in results)
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>BJ-Pal · LongCat 端到端测试报告</title>
<style>{CSS}</style>
</head>
<body>
  <h1>BJ-Pal · LongCat 端到端测试报告</h1>
  <p class="muted">4 个场景 × 真实 LongCat API。每个场景展示用户输入、Planner 产出 v1 方案、Probe 检测的风险与 reroute、v2 方案、话术化 IM 卡片，以及 tool 调用日志。</p>
  {summary_html}
  {body}
</body>
</html>"""
    OUT.write_text(html_doc)
    print(f"写入 {OUT}")
    print(f"打开：open {OUT}")


if __name__ == "__main__":
    main()
