"""L3 全量评测：100+ fixture × 5 强信号 × persona × scenario 矩阵。

每 release 跑，30min 内（mock LLM）。
基于 docs/USER_RESEARCH_FINDINGS.md 100 AI 用户访谈，5 强信号通过率给基线。

矩阵设计：
- 4 personas（family / friends / solo / with_parents）
- 5 scenarios（normal_weekend / important_dinner / rainy_day / weekday_lunch / friday_night）
- 5 query 变体
- 总 4 × 5 × 5 = 100 case

每 case 标注 expected_signals（[S1-S5]），跑完 plan 后验证：
- S1 责任承担 → plan_tracer 覆盖 + 含 fallback_action
- S2 看到吐槽   → plan reasons 出现负面 aspect
- S3 选错容忍   → 多轮 reroute 后 apology
- S4 工作日不属于 → 工作日 query 应转澄清
- S5 重要场合   → 触发 screening_mode

输出：per-signal pass rate + persona × scenario heatmap + 失败 case 列表
"""
