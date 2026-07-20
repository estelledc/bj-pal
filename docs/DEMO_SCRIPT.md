# BJ-Pal · Demo 脚本（黑客松历史版）

> 本文保留历史路演结构，其中数据规模、商业估算和“真实输出”字样不代表当前公开仓库证据。当前演示应使用 `demo` profile，并以 [README](../README.md) 和 [QA_PREP](QA_PREP.md) 的边界说明开场。

当前简历项目的工程排练应先跑：

```bash
make demo PYTHON=.venv/bin/python
make demo-clarification PYTHON=.venv/bin/python
make eval-observability PYTHON=.venv/bin/python
make eval-side-effects PYTHON=.venv/bin/python
```

先用 `make demo` 展示 Stage 3：CLI 创建 quote-bound 沙箱请求，打印精确金额、有效期和 approval SHA；不同演示 principal 批准后，worker 才生成 receipt SHA。Stage 4 只渲染消息预览，不执行发送。然后用澄清演示说明：文本说 2 人、结构化字段写 4 人时，Planner 尚未执行；系统返回并持久化 typed options、request/decision SHA 和 TTL。选择文本值后，同一原请求继续生成，Constraint Ledger 显示 `source=user_clarification`；重复同一答案返回缓存结果，改用另一答案则冲突。这些演示证明状态机与审计契约，不代表真实多轮满意度、真实预订或 exactly-once 外部写入。

随后用 15 秒展示 CLI 的 `[execution]` 行和 observability artifact：同步/worker 分别关联 request/job ID，span tree 与调用/token 汇总可由独立 verifier 复算；mock token 显示 `unavailable`。不要把这说成已部署 OTel 或真实成本监控。

## 90 秒 Pitch（评委初筛 / 海选阶段）

### 0:00-0:15 痛点镜头（不开屏，先口播）
> "周六下午，小明把手机递给老婆问'你看这样行不'。30 秒后老婆说：'换一个'。
>
> 这是真实的痛点——不是搜索推荐能解决的。"

### 0:15-0:20 输入（开屏 BJ-Pal Web UI）
> 用户输入框打字（已预填）：
>
> "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。"
>
> 点击「🚀 生成方案」

### 0:20-0:35 v1 方案飘入
> 时间轴上飘入 5 步方案（实测真实输出）：
>
> - 14:00 雍和宫 (citywalk)
> - 15:00 金鼎轩(地坛店) ¥88 (meal)
> - 16:15 地坛公园 (culture)
> - 17:45 雍和炸鸡烧饼 ¥18 (rest)
> - 18:30 返程
>
> 旁白："Planner 用 5,656 北京 POI + UGC 软信号挑出了这套方案。但是——"

### 0:35-0:50 ⭐ Reroute Wow 时刻
> 第 1 步「五道营胡同」时间槽变黄 → 红
>
> 弹窗：
> > ⚠️ 检测到风险：**五道营胡同** 排队/拥堵
> > • UGC[crowd]: 多条评价指出周末 14-18 点为 citywalk 黄金时段，南锣鼓巷、五道营等商业化胡同人流量大、拥挤明显
> > • UGC[transport]: 评价明确提到五道营胡同不太好停车，不建议自驾前往
> > → 已切换到 **国子监**
>
> 时间轴上第 1 步动画替换为「国子监 🔄」，地图 polyline 重画
>
> 旁白："这就是 agent 的异常处理——**1102 条 UGC 软信号**（5 类来源透明区分）告诉它周末爆棚 + 停车不便，自动切换到 7 分钟步行的国子监，同样是片区代表性文化点。**evidence 字段一键展开 raw_text_excerpt 可溯源每条来源**。"

### 0:50-1:10 一键发朋友闭环
> 切到下方话术化卡片：
>
> > 搞定了！下午这样安排你看行不
> >
> > 14:00 → 国子监 🔄
> > 15:00 → 金鼎轩(地坛店)
> > 16:15 → 地坛公园
> > 17:45 → 雍和炸鸡烧饼
> >
> > 五道营-雍和宫片区 下午 14:00 起，family 画像
> > 回我一个 OK 我就预订~
>
> 点击「📱 一键发送」→ 显示"✅ 已发送给 老婆" → 1 秒后"📩 老婆：OK 就这么定吧"

### 1:10-1:30 收尾
> 切到「🎯 行动」面板：
>
> 点「✅ 确认下单（mock）」→ 显示 "✅ 已锁定 金鼎轩 15:00 3 人座位 / booking_id=BK..."
>
> 旁白：
> > "**5,656 个北京真实 POI + 1,102 条结构化 UGC（103 片区 5 类来源透明）+ 1,892 条多模态路线** + 主动 reroute Agent + 一键发朋友闭环 = BJ-Pal。
> > 美团生态闭环：方案 → 美团下单 → 高德导航 → 微信卡片。
> > 这是北京试点，下一步上海杭州一键扩展。"

---

## 5 分钟现场 Demo（决赛 / 复试阶段）

### 0:00-0:30 老用户回顾（5min 现场新开场）
> 切到「📋 我的北京下午足迹」面板（P1.1）：
>
> > 上周六下午 14:00-18:30
> > 4 个人在三里屯
> > ❌ 第 1 站京 X 烤肉排队 40 分钟
> > 🔄 改计划又花 20 分钟
> > 实际 19:30 才吃上
>
> 旁白："这是孙倩访谈里的真话——'集体决策没人愿承担选错的风险'。"
>
> 切回输入框：
>
> > "今天周六下午，4 个人，2 男 2 女，三里屯，2 小时定方案。"
>
> 点「🚀 生成方案」
>
> "现在你只需 30 秒——而且 BJ-Pal 替你扛下选错的责任。"

### 0:30-1:30 重复 90s pitch

### 1:30-2:30 偏好镜子（agent-native 创新点）
> 切回输入框，新输入：
>
> > "下午带 5 岁娃出去玩。老婆减脂。"
>
> 点击「Clarify」按钮（Preference Mirror）→ 弹出对话框：
>
> > 老婆减脂是低糖优先（少甜品/含糖饮料）还是低油优先（少炸物/红烧）？
> > [低糖优先] [低油优先] [都要严格]
>
> 旁白："这是 form 表单做不到的——agent 用一句反问把模糊偏好变成可执行约束。"
>
> 选「低糖优先」→ ranking 实时变化：原本第 2 名的某甜品店被替换；新方案出来了

### 2:30-3:30 画像切换（同片区天差地别）
> 顶部 sidebar：从「家庭」切到「朋友」
>
> 重新生成 → 完全不同方案：
>
> - 14:30 五道营胡同
> - 15:30 国子监
> - 16:30 雍和宫
> - 17:30 京兆尹(雍和宫店) ¥220 / 悦真雅院 ¥220（朋友画像 ¥250 预算下解锁）
> - 19:00 Cafe Zarah(鼓楼东大街店)
>
> 旁白："同一片区，家庭画像 ¥120 vs 朋友画像 ¥250，方案完全不一样。京兆尹（人均 ¥966）家庭场景被预算 hard filter，朋友场景下虽然超预算也被砍掉，悦真雅院 ¥220 反而成为顶部。"

### 3:30-4:30 Trace 侧栏（评委 Q&A 用）
> 展开「🔍 Tool Call Trace」：
>
> 显示 ~30 行 tool_calls 表格：
>
> | time | tool | latency | params |
> |---|---|---|---|
> | 14:32:01 | amap.search_pois | 14ms | area=五道营... category=food |
> | 14:32:01 | ugc_signals.summarize_area | 8ms | ... |
> | 14:32:01 | rank_fuse.fuse_and_rank | 21ms | candidates=12 ... |
> | 14:32:02 | availability_probe | 6ms | poi=雍和宫 ... |
> | 14:32:02 | replanner.replan_step | 18ms | failed_idx=0 ... |
> | 14:32:02 | mock_book.book_restaurant | 0ms | poi_id=B000A7KVRU ... |
> | ... |
>
> 旁白："评委可以问任何'为什么选 X 不选 Y'——每个决策都有 tool call 留痕，每个 POI 都有 reasons 引用 UGC 原文。"
>
> 点开任意一个 RankedPOI 的 reasons：展开 3 条
>
> - amap_rating: +0.336 ｜ 高德评分 4.8/5.0
> - ugc_soft_score: -0.065 ｜ [queue] 周末高峰排队 85 分钟（UGC conf=0.86）
> - budget_fit: +0.150 ｜ 人均 ¥88 远低于预算 ¥120

### 4:00-4:30 数据厚度展示（v2.2 新增 / **v3.0 后扩到 8,666 条**）
> 切到 sidebar "UGC 数据厚度" 标签 / 或展开 trace 表的 dataset_version 字段：
>
> 旁白：
> > "数据扩展是这次最大的工程投入——从最初 37 条扩到 **8,666 条 / 6300+ POI 派生信号网 / 5 类透明来源**：
> > - manual_v1 截图抽取 37 条
> > - Class A 公开评论汇总 479 条 + R6-R100 跨片区主题 +2366 条（10+ 北京片区 LongCat 抽 aspect schema）
> > - Class B amap 属性推理 333 条（仅基于客观字段，禁止编造）
> > - Class C 场景主题 137 条（亲子 / 雨天 / 避暑 / 老人友好等 12 个主题）
> > - 跨片区主题 116 条（咖啡 / 烤鸭 / 胡同 / 红叶等 11 类）
> >
> > 加 **weekend_afternoon_intensity 列 100% 覆盖**——'周六下午'画像有真证据，不是 prompt 写死。
> > 加 **routes 1,892 条**（52 amap cache + 1,840 估算）覆盖 150 核心 POI 任意 1-hop 替代点。
> > 加 **动态 trap 评分**——全聚德等老字号自动识别，不再硬编码 4 个 demo POI。
> > 加 **v3.0 9 个北京特色派生信号**：facilities / audience_segment / seasonal / heritage_brand / reservation / weather_shelter / crowd_forecast / poi_graph / parking。"

### 4:30-5:00 商业价值收尾
> 旁白：
> > "美团生态接通：方案确认 → 商家预订（美团商家 / 哗啦啦）→ 蛋糕配送（美团秒送）→ 微信通知。
> >
> > 商业价值需要真实用户漏斗、供应商能力和订单证据验证；当前不展示未经验证的 GMV 推算。
> >
> > 数据扩展与实时接入必须先建立 provider provenance、缓存/时效和 acceptance sample，路线见 `docs/ROADMAP.md`。
> >
> > 隐私 / 合规：UGC `privacy_status` 三种（identity_removed / public_review_aggregation_no_pii / amap_objective_no_pii）；用户对话不持久化；mock 接口标注了所有真实 API 对接路径。
> >
> > BJ-Pal 北京下午管家——不是搜索推荐，是把事做完。"

---

## 评委 Q&A 准备（见 docs/QA_PREP.md 的 20 题）

## 录像清单（W2 D6 用户操作）

1. **场景 A 完整流（90s）**：用 OBS / Quicktime 录屏 → 实际跑 `streamlit run` + 输入 family 场景 → 60 帧/秒，1080p
2. **场景 B 偏好镜子（30s）**：单独录这一段
3. **场景 C 画像切换（30s）**：单独录
4. **场景 D Trace 侧栏（30s）**：单独录

合成方案：用 Final Cut / DaVinci Resolve 合成 5 分钟主版本，并裁出 90s 版本。

录屏工具推荐：macOS 自带 `cmd+shift+5`（最简单）或 OBS（多镜头切换）。

---

## v3.x 演化升级段（决赛版加塞用）

如果时间富裕（5min 现场超时还能续 1-2min），加这两段提评测严谨度 + 算法跃迁。

### A. 三分支 Planner 选择（30s · 算法亮点）

切回输入框，输 **复杂约束 query**：

> "今天周六下午，4 个朋友 + 1 老人 + 2 娃，15:00 起 4 小时，雨天，预算人均 ¥150-300，老人腿脚不便，娃要文化场所。"

Planner 自动选 **OPTW 分支**（候选 ≥ 30 + 强时间窗 + 多硬约束）：

> 旁白："看顶部 trace badge——`planner.plan_optw` → OR-Tools CP-SAT 5s 出 FEASIBLE 解。
> 7 步访问序列全局最优，避免局部贪心。"

切到 demo 模式 toggle，强制选 **ToT 分支**（branch_hint="balanced"）：

> 同 query，K=3 候选并发 + 自评分（commonsense + hard_constraint + utility + diversity + rationale_quality） → 选最优分支。
>
> "三个分支并行思考，AI 自己挑了均衡组，不是单条 prompt 拍出来的。"

### B. 履约证据面板（30s · 可解释性亮点）⭐

切到方案下方的 **履约证据面板**（`ui/trust_panel.py`）：

显示：

- 每一步的 `evidence_support_v1` 和来源
- POI grounding / rating / UGC 厚度 / 路线 / 风险 / 预订等组成因子
- synthetic/mixed 数据 profile 与 0.79 上限

旁白：

> "这里显示的是证据支持度，不是成功概率。分数来自可核对的 POI、UGC、路线和风险因子；公开 demo 用合成数据，所以最高只到 79%。
>
> ToT 的方案效用也不会冒充单步成功率。只有未来拿到来源清楚、和 trace 配对的履约 outcome，我们才重新报告 ECE。"

### C. v6.3 结果反馈闭环（30s · 简历复试版）

切到方案下方的“结果反馈”tab：

1. 先选择“接受这版方案”，提交后页面显示“用户自报、未经核验”；
2. 再选择“只完成了一部分”，原因选“天气影响”；
3. 指出页面底部仍显示“样本不足，暂不展示”，不要预先 seed 或手改数据库凑比例；
4. 运行 `make eval-outcomes PYTHON=.venv/bin/python`，展示 4-case artifact 由独立 verifier 重算绑定、幂等、过期、append-only、隐私和样本门。

旁白：

> “这一步补的是质量闭环的入口，不是伪造效果数字。反馈凭证和精确方案版本绑定，原文不落库，只收枚举原因；当前真实样本仍为 0，所以我只展示机制和 badcase 分类能力，不说用户成功率。”

### D. v6.4 知情试用分母（45s · 深挖版）

运行：

```bash
make demo-trial PYTHON=.venv/bin/python
make eval-trials PYTHON=.venv/bin/python
```

展示：

1. cohort 有固定用途、窗口、retention deadline 和精确 notice SHA；
2. operator enrollment code 只使用一次，参与者 capability 不落 artifact；
3. 同一参与凭证每 phase 一条，退出者从开放汇总排除，关闭后冻结 cutoff snapshot；
4. 6-case/13-metric verifier 从 raw evidence 重算全部 hash、分母契约和 retention purge transaction；
5. 明确输出的 0.8 来自 5 个 synthetic participant，其中一个是受控负例，不是用户采纳率。

旁白：

> “v6.3 解决一份反馈是否绑定一版方案，v6.4 继续解决试用分母是否知情、退出后是否误计和关闭后能否篡改。它仍不做真人身份核验，所以我只说不同匿名参与凭证，不说 5 位真人；当前真实 participant 和 report 都是 0。”

### E. v6.5 Operator 安全门（30s · 工程追问版）

运行 `make trial-operator-help PYTHON=.venv/bin/python`，再展示 `tests/test_trial_operator_cli.py`：

1. issue 缺 `--confirm-secret-output` 时不创建码表；
2. 既有输出文件绝不覆盖，成功 bundle 权限固定为 0600；
3. stdout/SQLite 都找不到原始 `trienroll-*`；
4. close 必须精确复述 trial ID，低样本默认拒绝，重复关闭返回同一 snapshot。

旁白：

> “API 契约正确还不够，真实试用最容易在操作层泄露 secret 或误冻结。我把这些风险变成命令级安全门；但它只是本地 privileged operator 工具，仍不能替代真实招募、远程 IAM 和 secret 分发治理。”

### F. v6.6 Retention 清除事务（30s · 数据生命周期追问版）

展示 `manage_trial.py purge --help` 与 `tests/test_trial_evidence.py` 的 retention 用例，不要对默认运行库执行 purge：

1. 未冻结、未到期、trial ID 不匹配或缺 secret/backup disposition 时零删除；
2. WAL 模式 fail closed；成功路径要求 `secure_delete=ON`；
3. 只删除目标 cohort，保留其他 cohort 和 legacy feedback；
4. trigger 恢复、foreign key 为零，注入坏 trigger SQL 后整事务回滚；
5. hash-only receipt 可幂等读取，篡改时 fail closed。

旁白：

> “notice 写 retention deadline 只是承诺，必须有可执行生命周期。我把到期删除做成一个有前置条件、原子回滚和非敏感收据的事务；但这个收据只证明当前 SQLite live-table 契约，不是取证级擦除或外部备份删除证明。”

### G. v6.7 请求级执行预算（30s · Agent 可靠性追问版）

运行：

```bash
make eval-execution-budget PYTHON=.venv/bin/python
```

展示 `evals/results/execution-budget-core.json` 的 6 个 raw case：

1. 正常请求输出 `completed` budget snapshot，并与 execution observation 的 LLM/data/tool/token 计数对账；
2. 第二个 LLM/data-provider 或第一个超额 tool 在进入 body 前被拒绝，`post_limit_work_executed=false`；
3. provider-reported token 在 call 返回后超过上限，后续阶段停止，但不声称能追回已经消耗的 token；
4. wall-clock 在下一个安全检查点终止，不声称 Python 能强杀已经阻塞的网络调用；
5. 同步 API 使用 429，durable job 使用 terminal failed 且不进入普通 execution retry；
6. verifier 独立重算 limit 语义、snapshot/artifact SHA 和敏感 marker 排除。

旁白：

> “面试里常问 Agent 会不会无限调用、API 重试会不会放大成本。我没有给当前静态流程硬套多 Agent 框架，而是在统一 Application Service 外围加服务端 request-local budget。逻辑调用 N+1 在开始前停止，LongCat/DPSK 关闭 SDK retry 避免双层重试相乘；token 只认 provider 实报。边界是它没有金额价格表，也不能主动中断已经阻塞的 socket，所以仍不叫生产 billing 或强制 deadline。”

### H. v6.8 编排选型对照（30s · 多 Agent 追问版）

运行：

```bash
make eval-orchestration PYTHON=.venv/bin/python
```

展示 `evals/results/orchestration-comparison.json`：

1. 3 个 case 使用相同 deterministic mock、demo SQLite 和规则 scorer，single/multi 都保留 raw plan projection 与 budget snapshot；
2. 当前多分支质量提升率和语义输出变化率都是 0，LLM/data 调用都是 3 倍；本机 elapsed 只记录、不设性能门；
3. 注入 `culture_first` post-generation 故障后，另外 2 个分支仍能返回，失败标签显式保留；
4. 默认服务端 data-batch budget 会在第二个分支 body 前终止，不允许线程 fan-out 绕过 request-local tracker；
5. verifier 独立重算 plan/budget/artifact SHA、调用倍率和 decision，并拒绝自重哈希伪造调用数。

旁白：

> “旧代码里的 ToT 不是三个 Agent，而是同一个 Planner 的三个提示词分支。我先修了线程池不继承 ContextVar 导致预算旁路的问题，再做同口径对照。当前 mock 不响应 branch hint，所以没有质量增益却有三倍调用，主链保留单分支。这个结论只约束当前证据；如果真实 badcase 证明多分支稳定增益，再用同一个 artifact 重新决策。”

### I. v6.9 模型输出失败关闭（30s · 幻觉/结构化输出追问版）

运行：

```bash
make eval-model-output PYTHON=.venv/bin/python
```

展示 `evals/results/model-output-contract.json`：

1. 12 个 adversarial payload 覆盖 unknown field、类型漂移、候选 ID 幻觉、名称错配、重复地点、depart/index/time/binding 错误和本地补残标记；
2. exact valid 首次通过只执行 1 次模型正文，首次越界后修复成功严格执行 2 次；
3. 第二次仍越界返回 `rejected`，同步/澄清 continuation 为脱敏 502，durable job terminal failed 且不自动重试；
4. request budget 只允许 1 次 LLM 时，第二次 attempt 会计数为 limit+1，但 provider body 不进入；
5. verifier 不调用生产 validator，而是重算 schema/grounding/sequence、模型/预算快照 SHA、调用数和敏感 marker，并拒绝自重哈希篡改。

旁白：

> “给模型传 JSON schema 只是生成提示，不是运行时保证。旧链路会宽松构造 Plan，甚至本地补残，所以我把模型输出当不可信输入，在查路线和写 trace 前精确绑定本次请求与候选池。第一次失败只修复一次，且共享请求预算；第二次失败直接停。这里的 1.000 是 12+4 条 synthetic/scripted 契约成绩，不是线上幻觉率或真实修复率。”

---

## 路演物料速查（promo/）

详见 `promo/README.md`。决赛当天用法：

| 时刻 | 物料 | 怎么用 |
|---|---|---|
| 入场前 30 min | `promo/pitch-deck.pdf` | U 盘备份，应付现场设备故障 |
| 评委桌摆 | `promo/one-pager.pdf` | 打印 A4 一份，每位评委一张 |
| 主屏幕 | `promo/pitch-deck.html` | Chrome 全屏 F11，方向键翻页（10 张横屏 1920×1080） |
| 答辩补料 | `promo/architecture.md` | 手机打开 GitHub 渲染 mermaid 图 |
| 现场 demo 切换 | Streamlit + pitch-deck 双窗口 | 答评委问题时随时回跳 deck 第 7-8 页（算法 + Demo） |

### 路演前 1-2 周引流

- **小红书图文**：用 `promo/xhs-png/` 9 张卡片；发布文案在公开内容计划中单独维护
- **GitHub Pages**：`promo/landing-page.html` 部署到 docs/index.html → `https://estelledc.github.io/bj-pal/`
- **GitHub README banner**：`promo/hero-png/01.png` commit 到 `assets/hero.png`

### 决赛后

- 简历附 `promo/pitch-deck.pdf` + landing page 链接
- 回顾文章附 `promo/one-pager.pdf`
