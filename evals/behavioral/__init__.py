"""D3 行为评测：5 强信号通过率分层评测（L1 anchor / L2 集成 / L3 全量）。

参考：docs/V2.4_ITERATION_PLAN.md Round 4 + video-eval-agent gstack 三阶段防火墙。

5 强信号（来自 docs/USER_RESEARCH_FINDINGS.md 100 AI 用户访谈）：
- S1 选错的责任 (4/5) — apology 在 2 次失败后必须出现
- S2 必须看到吐槽，不只是分数 (5/5) — red_flags 面板可见
- S3 选错容忍度 = 2 次 (5/5) — 第 3 次 reroute 触发自承认
- S4 工作日不属于这个 App (4/5) — 周一至周五会被偏好镜子询问
- S5 重要场合 = 工具不是代理 (5/5) — 重要场合（约会/求婚）切到筛选模式

L1: 5 anchor case，每 commit 跑，30s
L2: 50 场景，每周跑，5min
L3: 500 场景，每 release 跑，30min（基于 100 AI 用户研究 × 5 强信号）

L1 是当前阶段。L2/L3 在 v2.4 后续轮做。
"""
