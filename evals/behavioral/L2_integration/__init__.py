"""L2 集成评测：v2.4-v2.7 各能力 5 case，每周跑，目标 5min 内。

每模块负责一个能力：
- weekday_cases.py     时段识别 5 case（v2.4 S4）
- time_bucket_cases.py 时段画像 5 case（v2.6 D4）
- text_intake_cases.py 多模态文本 5 case（v2.5 D2）
- convergence_cases.py 群收敛器 5 case（v2.4 D5）
- memory_cases.py      跨 session 记忆 5 case（v2.7 D6）

每个 case dict：
    name / capability / description / runner -> {pass: bool, observed: ..., latency_ms: int}

L1 是 P0 anchor case（每 commit 跑），L2 是 P1 集成（每周跑）。
"""
