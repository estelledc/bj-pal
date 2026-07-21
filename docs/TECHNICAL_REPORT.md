# BJ-Pal 技术报告

> 本文面向代码评审者，解释项目从黑客松 Demo 到 v6.23 offline-first、需求门控、自然语言约束账本、可续跑澄清、可验证执行观测、隐私最小化 OTLP 导出与可复算运行告警、请求级执行预算、显式 live-provider 凭证交接和 usage/质量验收、模型输出失败关闭、编排选型对照、tenant-aware durable 调度、持久证据驱动的故障诊断与 workload health、原子准入、身份感知控制面、审批式沙箱副作用状态机、用户结果证据链、知情试用分母、安全 operator 工作流、证据型计划质量代理、localhost socket acceptance、隐私最小化工具调用账本、诊断隔离、非破坏业务状态迁移与可复核发布边界的演进。系统细节见 [DESIGN.md](DESIGN.md)，逐项证据见 [ARCHITECTURE_EVIDENCE.md](ARCHITECTURE_EVIDENCE.md)。

> 发布状态（2026-07-21）：v6.18-v6.23 已按 PR #5-#10 的依赖顺序合并到公开 `main`；最终集成提交 `e136b04` 的 [Core workflow](https://github.com/estelledc/bj-pal/actions/runs/29796656281) 与 [Pages 部署](https://github.com/estelledc/bj-pal/actions/runs/29796656267) 均已通过。本文仍区分公开实现、合成证据、单操作者真实 provider observation 与尚未发生的真实用户结果。

## 1. 问题定义

短时活动规划不只是生成推荐文案。一个可执行方案至少要回答：

- 用户和同行人有哪些硬约束；
- 去哪些 grounded 候选点，顺序、时间和路线是什么；
- 数据从哪里来、何时获取、是否代表实时可用；
- 某站排队、闭店或被否决时，如何只改必要部分；
- 长模型调用断线后，任务和结果如何恢复；
- 测试通过能证明什么，不能证明什么。
- 用户是否采纳、是否实际完成，如何在不伪造和不收自由文本 PII 的前提下留下证据。

因此核心不是“多 Agent 数量”，而是数据、决策、状态和证据四条链能否闭合。

## 2. 演进

### v1-v3：产品与算法原型

历史版本完成了 SQLite loader、POI/UGC 检索、Plan-and-Execute、风险 Probe、局部 Replan、Streamlit、群偏好和 ToT/OPTW 等探索，也暴露了原型问题：公开 clone 缺原始数据；UI/CLI 各自拼业务链；confidence 混入启发式和历史 ECE 叙事；脚本式验收未被 pytest 收集；历史汇总缺 raw artifact；只有 UI/CLI 而没有服务与恢复契约。

### v4.0：公开可复现

- 固定 Python 3.11；
- 新增 `demo` / `real-cache` profile；
- 生成 deterministic fixtures、manifest 和 SQLite metadata；
- 历史验收纳入 pytest；
- CI 从新 checkout bootstrap 数据并运行离线主链。

### v4.1：统一应用层

```text
PlanRequest -> PlanningService -> PlanResult
```

Delivery adapter 不再拥有 Planner/Probe 顺序。Planner、Prober、ProfileLoader 和 Recorder 可注入，使契约测试不依赖 Streamlit、网络或真实数据。

### v4.2：支持度与评测 artifact

- confidence 语义收紧为 `evidence_support_v1`；
- source/factors/limitations 与 step 一起返回；
- synthetic/mixed 支持度封顶；
- ToT utility 不作为成功概率；
- L1/L2/L3 统一产出 raw cases、环境、profile 和双 SHA；
- verifier 从 raw cases 重算摘要。

### v4.3：服务、数据面和 durable execution

- FastAPI：严格 Pydantic schema、结构化错误、health/readiness；
- typed provider：独立查询、单点 merge、partial issue 和 provenance；
- durable job：幂等提交、事务 claim、worker lease、过期恢复、失败状态、artifact hash；
- Docker：build 时生成 demo 数据，非 root 运行，health check；
- CI：API/job smoke 与容器 build gate。

### v4.4：就绪审计、事件回放与检索证据

- readiness：校验 manifest、SQLite quick check、必需表、metadata 和精确行数，异常 fail closed；
- event log：submitted/claimed/lease_reclaimed/succeeded/failed 与 job 状态同事务追加，trigger 拒绝修改和删除；
- HTTP replay：按 `after_event_id` 与 limit 增量读取事件；
- query UGC retrieval：确定性领域扩展、aspect 加分、confidence tie-break 和 POI 去重，证据进入 Planner 上下文；
- retrieval eval：19 条 synthetic golden case 同时跑 legacy/candidate，保存逐例结果、golden hash、Recall/MRR、多样性与延迟。

### v4.5：用户记忆生命周期

- 旧 v2.7 SQLite 表通过 additive migration 增加 source、confirmed_at、expires_at 和 revision；
- 同值写入只强化 mention/confidence，显式异值写入创建新 revision，未确认异值冲突拒绝覆盖；
- Planner 只注入已确认、未过期、未 forgotten 的条目，普通规划不自动写记忆；
- hash-only Memory event 保存 action/source/revision/reason，禁止 UPDATE；
- soft forget 可逆，hard delete 删除状态与审计 hash；
- L2 Memory 五例覆盖跨 session、否定语义、冲突、生命周期 gate 和用户隔离。

### v4.6：有界并发与性能证据

- 将数据 provider 的 per-request thread pool 改为进程级固定 8-worker executor，避免 HTTP 并发将数据线程按请求数倍增；SQLite 连接和请求结果仍不共享；
- `record_plan_to_tracer` 从 delete + 每步独立 commit 改为整份 plan 单事务替换，验证失败前不删除旧 trace，写失败自动 rollback；
- tool call log 的进程级 session 全局变量改为 `ContextVar`，并发请求不再互相覆盖 session ID；SQLite 日志连接设置 busy timeout；
- 增加真实 ASGI 主链 benchmark，保存逐请求 status、request ID 回显和 latency；
- 独立 verifier 从 raw requests 复算 error rate、throughput、nearest-rank p50/p95/p99，并校验 artifact SHA-256；
- gate 只约束零请求错误、零 ID mismatch 和 artifact 可验真，不设绝对 latency SLA。

### v4.7：可恢复 worker 与 SSE 投影

- worker 在 lease 有效期内周期 heartbeat；过期任务可被其他 worker reclaim，旧 owner 不能再完成或安排重试；
- retryable 异常按有上限的指数退避回到 `queued + available_at`，耗尽 `max_attempts` 后进入 `dead_lettered`；持久请求非法直接进入 non-retryable `failed`；
- heartbeat、retry、reclaim、dead-letter 事件与 job 状态同事务提交，事件写入失败时状态更新一并回滚；
- JSON cursor 与 bounded SSE 共用同一 append-only 事件表，SSE `id` 直接使用持久 `event_id`，断线后以 `Last-Event-ID` 续读；
- 自动迁移旧 v4.3 job/event schema，保留既有任务和事件，并补 `max_attempts`、`available_at` 与新的 CHECK constraint。

### v4.8：任务控制面

- 增加轻量 job 列表与状态筛选，使用 SQLite 插入顺序定位 `after_job_id`，列表不加载或内联 request/result JSON blob；
- queued cancel 在同一事务直接进入 `cancelled`；running cancel 先持久化 `cancel_requested`，在 Application Service 安全边界、worker 返回或 lease 过期时收敛；
- Planner 前后和 Probe 后检查 durable cancel flag；单次正在进行的模型/provider 调用仍需 adapter 自身支持主动中止；
- dead-letter/failed replay 不清零原 job，而是原子创建 `replayed_from_job_id` 新任务，并在原任务写 `replay_requested`；独立 `Idempotency-Key` 防止重复人工操作；
- cancel/replay 状态与 lineage event 同事务提交，旧 v4.7 schema 保留式迁移到新增状态、字段和事件。

### v4.9：控制面认证与持久 deadline

- 所有 `/v1/planning-jobs*` route 统一使用 Bearer gate；服务端 token 缺失或短于 32 字符时 fail closed 为 503，缺失/错误凭证返回 401；同步规划与健康端点仍公开；
- job 提交独立接受 1-86400 秒 `deadline_seconds`，默认 900，并把绝对 `deadline_at` 持久化；同步 `/v1/plans` 不接受该 durable policy 字段；
- queued 到期不会被 claim；running 到期在 heartbeat、finish、retry、扫描或 Application Service 安全边界原子结算为 `timed_out`；
- cancel/deadline 竞态由 `cancel_requested_at` 与 `deadline_at` 的先后确定，完成结果和 retry 不能覆盖先到的控制信号；
- `timed_out` 是 append-only 终态，可筛选、SSE 续读并以新 job 重放；新 job 继承秒数策略但获得新的绝对 deadline；
- v4.3/v4.7/v4.8 schema 保留历史 row/event；迁移来的旧 job 以 `deadline_at=NULL` grandfather，不凭空改变历史生命周期。

### v5.0：offline-first 真实天气 Adapter

- 将 canonical path 的硬编码小雨窗口替换为 `WeatherProvider` typed contract：Open-Meteo adapter 校验坐标、明确日期、16 天 horizon、timezone、字段长度、单位、WMO code 和目标小时；
- 免费、商业、自托管三种 usage mode 在配置阶段 fail closed；默认 synthetic fixture 保证公开 CI 零网络，live smoke 必须显式 opt-in；
- timeout、429/5xx/4xx/schema failure 有稳定错误码；进程共享 TTL cache 与 stale-if-error 不掩盖过期状态；
- Planner、LLM context、Probe 和 Reroute 共享同一小时快照及 deterministic shelter class，避免“规划看一种天气、执行又查另一种”；
- weather acceptance artifact 保存 fixture SHA、请求范围、attribution、决策结果和 artifact SHA，由独立 verifier 复算，并明确 `live_provider_accepted=false`。

### v5.1：约束保持型 Replan

- 从完整 demo 复现“正餐排队后替换成咖啡馆”的语义失败，新增 `ReplacementPolicy`，把 hard eligibility 与 ranking 分离；
- meal 必须保持正餐，snack/rest 排除正餐；weather 从 museum/shopping 跨类召回且排除 open shelter；
- 替换事件新增策略版本、来源类目、硬约束和四阶段候选计数，经 canonical `PlanResult` 同时进入 HTTP 与 durable artifact；
- 无合法候选时 fail closed 为 `warn_only`，不把流程完成包装成合规替换。

### v5.2：Replan 路线一致性

- 从三站计划复现：替换中间 POI 后，新站没有入站路线，而下一站仍保留旧 POI 到下一站的 distance/duration/options；
- 抽出可注入的 Route Enricher，Planner 和 Replanner 共用缓存优先、确定性估算兜底的 route lookup；
- 刷新前统一清空旧路线字段，只计算真正相邻、坐标完整的 step；缺坐标或查询异常不会跨站连接，也不会恢复旧值；
- 成功 reroute 后重算完整短路线，并在 Plan 与 RerouteEvent 同时保存 `route_refresh_v1`，包含 incoming/outgoing 受影响位置、leg 来源和 warning；
- HTTP 与 durable artifact 共享该字段，旧 artifact 缺字段时由 schema 默认空对象兼容。

### v5.3：路线感知的可执行时间轴

- 从默认 demo 复现三处重叠：上一站结束时间与下一站 `start_time` 相同，但 incoming travel 分别为 5/3/2 分钟；
- 抽出纯确定性 Schedule Reconciler，把 start 定义为到站时间，并在初始规划和每次 reroute route refresh 后运行；
- `minimum_dwell_v1` 只压缩有明确下限的柔性停留，不自动删除地点；安全压缩仍放不下时返回 `overrun_minutes`；
- 缺失 route evidence 时不把 0 分钟当已验证成本，schedule 标为 `partial`；
- `schedule_reconcile_v1` 记录窗口、travel/dwell、预计结束和逐 step 调整，经 canonical result 进入 HTTP 与 durable artifact。

### v5.4：低误澄清的 Requirement Gate

- 从真实离线主链复现“还是上次那个地方”被静默套用默认五道营片区并生成 5 步计划；
- 在 `PlanningService` fan-out 前加入纯确定性 Requirement Normalizer，把结果固定为 `proceed / proceed_with_assumptions / clarification_required`；
- 用 `PlanRequest.provided_fields` 区分显式 `area_anchor` 与 HTTP 默认值；支持常见北京片区规范化，并把低风险默认值写成可覆盖 assumption；
- 历史/序号指代无上下文、相对位置缺参考、文本与结构化片区冲突时返回一个问题和 2-3 个选项，Planner/tool 调用次数保持为 0；
- 同步 API 与 durable submit 共用结构化 409；异常持久请求 non-retryable failed，不进入指数重试；成功 decision 进入 `PlanResult` 与 job artifact；
- 20-case synthetic golden artifact 保存逐例原始状态和补充后状态，verifier 复算 trigger rate 0.350、false clarification rate 0、required recall 1.000、post-clarification gate executability 1.000；最后一项只表示通过门控。

### v5.5：typed Constraint Ledger

- 复现真实 HTTP 静默漂移：文本明确写 2 人、15:00、3 小时、人均 100 和不吃辣，但 Planner 收到的仍是 3 人、14:00、4.5 小时、空预算和空忌口；
- 把确定性约束抽取放进唯一 `PlanningPreflight`，同步、durable submit、worker、UI 和 CLI 共用，Planner 不再各自猜参数；
- `ConstraintEntry` 保存 effective/text value、source、evidence、hardness 和 outcome；原始文本与 `provided_fields` 不被覆盖，durable round-trip 保持幂等；
- 显式结构化字段与文本冲突时 fail closed 为同一 409，Planner/tool 调用数与 job 入队数均为 0；忌口限制采用安全并集；
- 30-case synthetic artifact 覆盖正例、否定短语、显式冲突、rewrite 和重放；独立 verifier 从 raw cases 复算 extraction F1、false extraction、hard-constraint preservation、conflict recall、rewrite coverage 和 idempotency，当前各成功率指标为 1.000、false rate 为 0。

### v5.6：durable Clarification Continuation

- 复现 409 后无法继续：响应虽有问题和自然语言选项，但客户端必须重建原请求；“使用结构化值”若不携带决议证据还会进入同一冲突循环；
- 增加 `ClarificationResolution` 与 SQLite session，持久化原请求、request/decision/resolution/resolved-request/result SHA 链、typed options、delivery/deadline/priority policy、TTL、lease 和结果引用；
- 同步与 job continuation 共享 resolution/claim/fencing，重复相同答案返回缓存计划或同一 job，不同答案冲突；多项冲突时父 session 固定指向同一个 child；
- Streamlit 可在原界面回答后续跑，CLI 增加可排练冲突路径；job 续跑保留原 deadline/priority policy 并继续受 Bearer 保护；
- 16-case synthetic artifact 覆盖七类 typed 字段、片区、相对位置和历史/序号指代；独立 verifier 复算一步续跑、最终值、同冲突复发、双指纹、恢复、options、round-trip 和不同答案 fencing，成功率指标均 1.000、同冲突复发率 0。

### v5.7：可验证 Execution Observation

- 审计发现本地 `plan_trace`、tool-call SQLite 和 JSONL/OTel span 各自存在，但 canonical 结果无法回答一次请求跨 preflight/Planner/Probe 的耗时、调用数与业务降级；原 OTel adapter 还在 span 结束后重新创建 span，parent context 没有真正传入 exporter；
- 在 `PlanningService` 建立单根 request-local capture，固定包裹 preflight、generate、probe/replan、trace persistence 和 profile load；同步入口绑定 `X-Request-ID`，worker 绑定 `job_id`；
- 将隐私最小化 `execution_observation_v1` 放入 `PlanResult`、HTTP、澄清缓存与 job artifact，只公开 span 名称/父子 ID/相对耗时/状态、调用与业务计数，不公开 attrs、prompt、用户输入或 user ID；
- token 只累计 provider 实际返回的 usage，并标记 complete/partial/unavailable/not_applicable；mock 不回报 token 时保持 unavailable，不使用字符数估算，也不生成虚构成本；
- 对 observation 计算 SHA-256；3-case synthetic contract artifact 由独立 verifier 重算树、操作数、token 语义、敏感标记排除与 artifact SHA；
- optional OTel adapter 改为 span enter 时显式传入 parent context。本轮没有 collector、OTLP export 验收、多实例聚合或真实成本数据，因此只主张可验证的本地观测契约。

### v5.8：公平 Durable Scheduling

- 为 job 增加 0-9 基础优先级，并把它纳入提交幂等语义、HTTP/列表响应、clarification job policy 与 replay 继承；
- `priority_aging_v1` 从真正 eligible 的时刻起每 60 秒提升一级、最高 9；同级按 eligible time FIFO，避免持续高优任务让普通任务无限排后；
- retry backoff 未到 `available_at` 前不参与竞争，lease reclaim 从旧 lease 到期时开始等待；claim event 固化 base/effective priority、eligible time 和 queue wait；
- v4.3-v4.8 schema 保留式重建，v4.9-v5.7 以 additive column 默认 priority 0，迁移不重写历史 event；
- 3-case synthetic contract artifact 保存候选任务和 claim event，独立 verifier 重算排序、aging、queue wait 与 backoff exclusion；
- 该实现仍是单机 SQLite 选择策略，不是多实例优先队列或启动 SLA；当时共享控制 token 无 priority admission，也没有在线 reprioritize。

### v5.9：身份感知的 Durable Control Plane

- 将单一共享 token 扩展为严格的静态哈希 credential registry：每项映射 `principal_id/tenant_id/scopes/max_priority`，Bearer candidate 先 SHA-256 再 constant-time 比较，原 token 不进入 registry 或业务持久化；
- 按 route 强制 `jobs:submit/read/control/replay` scope，并在 submit 与 job continuation 消费前执行 principal priority cap；
- job、列表、event/SSE、cancel、replay、continuation 与 idempotency key 全部 tenant-scoped；跨 tenant 返回 404，失败的外租户或越 cap continuation 不改变 pending session；
- v5.8 schema 保留式迁移补齐 `tenant_id/submitted_by`，旧行映射 `default/legacy-migration`，同时把全局 idempotency 唯一约束改成 tenant-local partial unique，保留 event ID/history；
- 新增真实 FastAPI + SQLite 的 4-case access-control artifact；独立 verifier 从 raw HTTP outcome 和 principal policy 复算 scope/cap/tenant/continuation 结果、校验 SHA，并排除凭证泄露；
- 这不是 OAuth/OIDC、动态 RBAC、数据库 RLS 或企业 IAM；v6.27 虽补 PostgreSQL shared job/admission/scheduler store，仍无服务端 credential 过期/轮换/撤销、请求加密、在线迁移、容量或故障恢复证据。

### v6.0：Tenant Admission 与同优先级公平调度

- 借鉴本仓 travel-planner 生态研究中“schema、限流、幂等、持久化和审计属于平台层”的结论，将 active job cap 和 60 秒 accepted-submission cap 放进 credential 派生的 tenant policy，而不是交给 LLM 或调用方自报；
- submit、manual replay 与 job clarification continuation 都在同一 SQLite `BEGIN IMMEDIATE` 中结算 deadline、检查 quota、创建 job 并追加 admission decision；匹配幂等重试复用原 job，不误消耗新 quota；
- admitted/rejected/idempotent-reuse 进入 tenant-scoped append-only audit；active cap 429 与 rate cap 429 分别暴露稳定 code，后者给出 `Retry-After`；
- `tenant_fair_priority_aging_v2` 先保留 effective-priority 排序，再以 tenant 最近 claim event cursor 轮转，最后执行 eligible-time FIFO；claim event 固化 priority/fairness policy 与选择前 cursor；
- v5.9 schema additive 增加 admission/scheduler state 表，不重写历史；并发提交、重放、澄清恢复、迁移、trigger 和跨 tenant HTTP 测试覆盖关键竞态；
- scheduling artifact 扩为 4 case/6 metrics，access-control/admission artifact 扩为 6 case/10 metrics；独立 verifier 从 raw candidate、HTTP outcome 与 audit event 复算；
- 这仍不是 Redis/分布式限流或严格公平：rate 只计 accepted new job，不保护 raw attempt；audit 无 retention；SQLite policy 不跨实例，新 tenant 有一次初始轮转优势。

### v6.1：Approval-gated Sandbox Side Effects

- 从旅行规划研究中落实“推荐是可重试读操作，预订是高风险写操作”：新建独立 `SideEffectOperationRepository`，不把副作用塞进可自动 reclaim 的 planning job；
- 只接受 `restaurant_booking + bj-pal-sandbox + sandbox=true`，将 action、quote reference/validity/currency/amount/terms hash 与 policy 形成 request SHA，再把 operation/tenant/request/expiry 绑定到 approval SHA；
- 将 scope 拆成 `operations:request/read/approve`，同 tenant requester 与 approver 必须是不同 principal；tenant-local idempotency 只复用完全相同 fingerprint，跨 tenant 统一 404；
- worker 只 claim 已批准且未过期的 operation，成功或 provider 明确拒绝保存 `side_effect_receipt_v1`；调用前失败无 receipt，调用后不明或 lease 到期进入终态 `uncertain` 且不自动重试；
- operation event trigger 禁止 UPDATE/DELETE；4-case artifact 和独立 verifier 从 raw operation/event/receipt 复算 9 项安全指标，均为 1.000；
- 该版本完全不触发真实餐厅、支付或消息状态；没有订单查询、补偿、客服 handoff、第三方签名回执、PII/secret 生产治理或多实例执行。

### v6.2：Provider-bound Read-only Reconciliation

- 为 provider 调用后不明的 operation 增加 `side_effect_status_lookup_v1`：只有持久化 provider operation ID 的 uncertain 操作可查询；查询证据同时绑定 operation、request、provider、provider reference、sandbox flag 和 raw payload SHA；
- `operations:reconcile` 与 request/read/approve 分离；HTTP 跨 tenant 返回 404，缺 reference 或状态不适用返回 409；append-only reconciliation endpoint 可按 cursor 回放 evidence/evidence SHA/receipt SHA；
- `confirmed/rejected` 用 lookup response SHA 生成 receipt 并收敛为 succeeded/failed，`still_unknown/not_found` 继续 uncertain；所有路径都不重放原写操作，lease 过期且无 provider reference 时保留人工处置；
- CLI 与 Streamlit 不再直接调用 legacy `mock_book` 作为默认预订路径：请求、独立审批、worker 和回执均为可见步骤；UI 对 uncertain 只提供只读状态核对；
- side-effect artifact 扩为 5 case/12 metrics，独立 verifier 复算 raw lookup payload、binding、receipt、event 与 reconciliation append-only 关系，全部为 1.000；
- 补偿明确保持独立：未来取消必须是重新 quote、幂等、审批和留 receipt 的 `restaurant_cancellation` operation，并绑定 `compensates_operation_id`，不能藏进 reconciliation 或 retry；当前没有实现真实写补偿。

### v6.3：Capability-bound Human Outcome Evidence

- 为 final plan canonical JSON 计算 artifact SHA，并由 delivery 层发放 14 天 capability；原文只返回客户端，SQLite 只存 SHA，canonical `PlanResult` 与 clarification cached artifact 不含 secret；
- decision/outcome 两阶段分别使用固定 value 和受控 reason code，不接收自由文本；report 以 plan artifact + phase 唯一，精确幂等重试返回同一记录，覆盖冲突 409；
- invitation/report 均有 canonical SHA 且由 trigger 禁止 UPDATE/DELETE；公开 summary 固定标为 `self_reported_unverified`，每 phase 少于 5 份时对应比例为 null；
- Streamlit 增加独立结果反馈 tab；同步 HTTP 返回 capability 并提供提交/读取 route，另有只暴露聚合数据的 summary route；
- 旧 step outcome 增加 `synthetic_test/legacy_unclassified/human_verified_step` 分类，seed 不再进入真人校准 UI，plan-level report 不冒充 step-level label；
- 4-case/8-metric synthetic contract artifact 独立重算 hash、绑定、幂等、schema、expiry、append-only、privacy 与 sample gate，全部为 1.000；当前真实 report 数仍为 0，不能报告用户采纳率、完成率或满意度。

### v6.4：Consent-bound Trial Cohort and Frozen Evidence

- 新增 tenant-scoped `TrialCohort`：operator 创建有截止时间的试用批次，并逐人发一次性 enrollment code；数据库只存 code SHA，重复使用冲突；
- 参与者必须对服务端 canonical consent notice 的精确 SHA 明示同意，才得到 session-only participant capability；participant、trial-bound invitation/report 都绑定同一 notice SHA；
- 同一 participant capability 每个 phase 最多一条，普通反馈与试用批次分开汇总；最小样本门改为每 phase 至少 5 个有效参与凭证，门前隐藏 rate、value 和 reason distribution；
- 退出作为 append-only event，阻止后续规划/反馈并从开放汇总排除；关闭批次冻结 cutoff-bound snapshot 与 evidence root，后续写入 fail closed；
- 5-case/12-metric verifier 从 raw notice、cohort、enrollment、participant、report、withdrawal 和 snapshot 复算同意、隔离、分母和冻结契约；CLI 排练使用临时 SQLite，不输出原始 capability；
- 边界：参与凭证不是身份认证，不能证明不同真人；当前真实 participant/report 均为 0；`raw_purge_due` 只是 retention 到期信号，不是物理或备份删除证明。

### v6.5：Safe Local Trial Operator Workflow

- 将真实试用的本地管理入口收敛为 create/issue/status/close 四个命令，避免直接修改 append-only SQLite 或手工拼接多次 HTTP 请求；
- 新增原子批量 enrollment：1-100 个一次性 capability 在同一写事务签发，数据库仅保存 SHA；
- secret bundle 只能在 `--confirm-secret-output` 后写入不存在的新文件，使用 `O_EXCL + 0600`，被 `.gitignore` 排除，stdout 不输出原始码；bundle 自带 canonical SHA 和逐人分发/删除警告；
- close 必须精确确认 trial ID，低于每 phase 门槛时默认拒绝；显式接受低样本冻结后，相同重试幂等返回原 snapshot；
- 定向测试验证 secret 最小化、bundle 完整性/权限/唯一性、防覆盖、低样本门与重复关闭；测试、smoke、benchmark 使用临时反馈库，完整门禁前后默认运行库邀请数不增加；
- 边界：这是拥有本地数据库权限的 privileged operator CLI，不是远程 IAM、secret escrow、自动分发或招募系统；当前仍无真实 participant/report。

### v6.6：Retention-due Atomic Trial Purge

- 在 operator CLI 增加 purge：只允许已冻结且到 retention deadline 的精确 trial/tenant，要求复述 trial ID、确认 secret bundle disposition，并显式声明备份状态；重复请求返回原收据；
- repository 以 `BEGIN EXCLUSIVE` 验证 cohort/snapshot/evidence root，要求 `DELETE/TRUNCATE` journal mode 并开启 `secure_delete=ON`，按 foreign-key 顺序仅删除目标 cohort 数据；
- 删除期间相关 append-only DELETE trigger 只在同一事务内暂时移除，完成后逐个恢复并执行 row-count 与 `foreign_key_check`；任一步失败时删除、DDL 和收据一起 rollback；
- 新增 append-only `trial_retention_purge_receipt_v1`，仅保留随机标识、证据 hash、时间、删除计数和 operator disposition，不保留 capability 或反馈枚举；6-case/13-metric trial verifier 增加清除事务复算；
- 边界：收据证明的是当前 SQLite live-table 删除契约，不是取证级擦除、secret 文件删除、备份删除或合规认证；WAL 模式 fail closed，仍没有托管调度。

### v6.7：Request-level Execution Budget

- 新增服务端 `execution_budget_v1`，以 ContextVar 将 policy/tracker 限定在单次 `PlanningService.execute`，HTTP/job/client payload 不能逐请求抬高上限；非法环境配置启动时 fail closed；
- 在 `llm.*.complete`、`planner.collect_data` 与 `tool.*` body 进入前计数并检查 N+1；默认上限为 2/1/8，并将每 LLM call transport attempt、provider-reported token 与安全检查点 wall-clock 一并纳入策略；
- LongCat/DPSK 关闭 Anthropic SDK retry，只保留应用层 bounded retry，避免两层重试乘法；provider usage 只在 call 返回后累计，超过上限会停止后续阶段，但不伪装为能追回已消耗 token；
- 成功预算快照与 trace operation/token 汇总交叉对账后进入 `execution_observation_v2`；预算终止返回 hash snapshot，同步 HTTP 为 429，durable job 为 terminal failed 且不进入普通 execution retry；
- 6-case synthetic artifact 覆盖 completed、LLM/data/tool N+1、reported-token overrun 与 wall-clock checkpoint，独立 verifier 复算 limit、SHA、终止后代码未执行与敏感标记排除；线程隔离、HTTP、job 和自重哈希篡改单独回归；
- 边界：wall-clock 是安全检查点，不强杀已阻塞 socket/SDK；缺 provider usage 时无法执行 token gate；当前不是金额成本、跨实例全局 quota、billing reconciliation 或生产负载证据。

### v6.8：Orchestration Decision Evidence

- 将历史 ToT 定义收紧为“同一 Planner 的实验多提示词分支”，不再把文件数量或并行调用描述成多个自治 Agent；HTTP/job canonical 主链仍保持单分支；
- 修复线程池没有自动继承 `ContextVar` 的缺口：每个分支用独立 `copy_context()` 传播同一 request budget、trace parent 与 capture，预算异常向上抛出而非被分支失败降级吞掉；
- branch/worker fan-out 上限固定为 3，并验证 label 唯一、temperature 有界；普通分支失败仍在 branch record 中可见，全部失败才返回运行错误；
- 新增 3-case synthetic single/multi 对照 artifact，保存脱敏 plan projection、规则质量分解、branch accounting 和 budget snapshot；独立 verifier 重算质量 delta、输出 SHA、LLM/data 倍率、故障注入、默认预算拒绝与最终 decision；
- 当前 deterministic mock 忽略 branch hint，3 个场景没有质量提升或输出变化，但逻辑 LLM/data 调用均为 3 倍，因此选择 `single_branch_default`；该结论只约束当前主链，不外推真实模型、多 Agent 架构或生产延迟。

### v6.9：Model-output Fail-closed Contract

- 审计发现历史 `Plan.from_dict` 会丢弃未知字段并允许转换，`repair_json` 还能把截断内容补成部分对象，因此“传入 JSON schema”并不等于真实运行时 schema 验证；候选 POI ID/名称也未和本次请求精确绑定；
- 新增 `model_output_contract_v1`，在 `Plan` 构造和路线/trace 副作用前严格拒绝 extra/missing/coercion/literal drift，并核对请求 persona/area、候选 ID/名称、重复地点、连续 index、唯一末尾 depart 与无重叠时间序列；
- 首次失败最多调用同一 provider 修复一次，且与首次调用共享 request-local budget；正常流式状态与最终 plan 复用一次 event stream，不再额外消耗 preflight LLM call；
- 第二次仍失败时同步/澄清 continuation 返回脱敏 502 `invalid_model_output`，durable job terminal failed 且不走普通 execution retry；契约快照只保留稳定问题码、attempt/candidate count 与 SHA；
- 12-case hand-authored adversarial payload 与 4-case deterministic lifecycle artifact 由不调用生产 validator 的 verifier 独立重算，并含自重哈希问题码/body-count 篡改测试；指标只证明契约，不代表真实模型幻觉分布、修复成功率或用户结果。
- 在用户授权下增加经配置 DeepSeek client 的 live contract observations：修正前 Flash 坏例驱动 prompt 消除竖线伪 enum 并补齐 depart 规则；修正后同场景 Flash 两次后仍拒绝、Pro 首次通过，pair verifier 只给出单样本选型信号。随后把 runner 收敛为 3 个固定 synthetic scenario registry，新增五道营家庭与 798 单人样本；Pro 三个场景均首次通过，suite verifier 独立核对精确场景、provider/预算一致性与 artifact 唯一，只报告 3 个 accepted/first-pass count，不计算成功率。`DPSK_MODEL` 改为必填并暂定后续有界试验优先 Pro。artifact 不保留 prompt、raw model output、generated plan 或 credential；最小候选池只有 2 个，也不具备 plan-quality、签名 provider、价格或用户结果证据。

### v6.10：Evidence-bound Live Plan Quality

- 第一轮 live suite 暴露 798 只有 2 个候选；根因是 demo replacement generator 未把 798 纳入核心覆盖。将其加入与其他核心片区一致的 food/scenic/shopping/museum/sports 最低候选门后，固定场景候选由 2 增至 21；
- `no_spicy/light_diet` 不再只写进 prompt：餐饮候选必须存在 confidence≥0.6、未标 review 的正向 structured UGC taste tag；缺证据不等于满足约束，非餐饮候选不受错误过滤；
- 同一机制扩展到所有显式 diet flag：需要每个 tag 的正向证据交集；缺任一证据时 food 分支为空并写入 `diet_evidence_unavailable`。模型输出契约另校验候选类别，`meal/snack` 只能绑定 food POI，避免模型把景点改标为餐饮绕过降级；
- 新增脱敏 `live-plan-quality` artifact，只保存固定 synthetic scenario、选中 POI facts/tags、无自由文本 plan projection、路线与时间轴摘要；固定 policy 不能由 artifact 自行放宽，重新哈希篡改 diet tag、policy 或 free-text 字段都会被 verifier 拒绝；
- 数据修正后重新运行三例 Pro：候选 26/16/21，三里屯与家庭首轮接受、798 一次修复后接受；固定质量代理分别通过 9/9、12/12、11/11 个必需检查，0 项不可评估；
- 边界：三例仍各跑 1 次，POI/UGC/路线是 synthetic，walking 采用 1.5× radius 的明确 proxy；不保存或评判 rationale/summary，不是成功率、真人偏好、用户 outcome、provider 签名或生产 freshness 证据。

### v6.11：Localhost Socket Acceptance

- 保留原有 in-process ASGI benchmark 作为快速回归，同时新增独立 Uvicorn 子进程，经 `127.0.0.1` TCP 请求同一 `/v1/plans` 主链；不再把 ASGI transport 直接调用称为 socket 证据；
- 子进程使用临时 feedback/job/clarification/tool-audit runtime，显式禁用本机 env 文件并剥离 provider/control credential；只在 `/readyz` 返回 ready 后计量；
- 工件保存逐请求 status/request ID/latency、startup 与 shutdown 证据；独立 verifier 要求 loopback-only、临时隔离、readiness 成功和 `SIGINT + wait` 退出码 0。首次实现因 `terminate()` 得到 `-15` 被门禁拒绝，修正为优雅退出后才接受；
- 第一次接入完整门禁时，进程内基准曾出现 1/20 的单发 422；后续 1,100 个同链路请求未复现，因此没有虚构根因或宣称“已修复”。runner 改为只保留 API 的安全 `error.code`（不保留 message/body），随后完整门禁以两条链路均 20/20、退出码 0 通过，未来若复发可从 artifact 继续定位；
- `pyproject.toml`、FastAPI health/OpenAPI 的版本统一为 6.11.0，新增一致性测试防止发布叙事与服务版本再次漂移；
- 边界：仍是单机、单进程、mock LLM、synthetic data；不含 TLS、反向代理、远程网络、多实例、真实 provider/model 或稳定隔离压测机，因此只称 localhost acceptance，不称生产压测或 SLA。

### v6.12：Privacy-minimized Tool-call Ledger

- 旧 logger 会把任意 params、dataclass response 和 `type + message` 异常直接写入 `tool_calls.db`；这虽然方便 demo，却让 prompt、用户文本、联系方式、provider response 或 credential 经可观测链路二次落盘；
- 新写入统一为 `tool_call_audit_v2`：递归投影有最大深度、集合条数和安全文本长度，敏感 key、credential-like value、邮箱/手机号和未知自由文本只留下 redaction/type/length；异常只留稳定 `error_code`，不保存 message；
- 每个 session 的 sequence 与 previous SHA 在 `BEGIN IMMEDIATE` 中分配，行体生成 SHA-256；partial unique index 防重复，trigger 拒绝 v2 行 UPDATE/DELETE。`clear_session` 改为追加 reset marker，当前 UI segment 可清空而完整证据链不被删除；
- additive migration 不重写历史行；默认 fetch 会隐藏 legacy payload，避免 UI/CLI 再次展示，但数据库原字节仍存在。4-case artifact 和独立 verifier 复算投影、错误码、链、mutation 拒绝、reset 语义及 legacy hiding，并拒绝重签后的 marker 注入、伪造 mutation 成功和截断链；
- 边界：固定攻击 marker 不是完整 DLP；本地 SQLite 未加密、没有远端 WORM、访问审计、retention scheduler 或 retroactive erase，文件 owner 仍可删除整库，因此不称生产合规审计。

### v6.13：Independent Tool-audit Runtime Store

- 实库审计发现根目录 `tool_calls.db` 同时包含 `user_memory`、`user_memory_events`、`plan_trace`、`prediction_log`、`plan_outcome` 与工具日志；把它当作可清理日志会误伤业务/学习状态，继续共库也会让诊断 retention 与用户数据 lifecycle 无法独立治理；
- 新工具事件默认只写 `runtime/tool_audit.db`，可由 `BJ_PAL_TOOL_AUDIT_DB` 覆盖。初始化自动创建父目录且 clean-start 只创建 `tool_calls`；既有业务模块仍可使用旧共享库，但 tool-audit 切换不读取、复制、迁移或删除其中的 legacy tool rows，保留数据破坏边界；
- `footprint` 不再持有自己的根目录 DB 常量，而是跟随同一个动态路径并只聚合 `tool_call_audit_v2`，避免 legacy JSON 绕过 `fetch_calls` 的隐藏策略；真实 Uvicorn socket benchmark 也把审计库重定向到临时 runtime，父进程数据库不会成为 benchmark 副作用；
- 5-case artifact 新增 storage-isolation case：对旧共享库写前/写后做 byte-level SHA-256，对新旧库分别盘点用户表，并验证旧 marker 不进入新库；独立 verifier 重算六项 rate，伪造旧库未变也会被拒绝；
- 边界：这是默认本地文件路径隔离，不是 database RLS、tenant isolation、encryption、WORM 或 backup deletion。旧足迹不会自动出现在新库，operator 仍可显式把环境变量指回旧路径；受控 legacy 脱敏迁移/擦除仍未实施。

### v6.14：Plan-evidence Store Ownership and Verified Copy

- 实库只读盘点发现旧共享库已有 75,022 条 `plan_trace` 与 1,312 条 `plan_outcome`；直接把常量改成新路径会让历史解释与 calibration join 静默归零，因此业务状态不能沿用 tool audit 的 no-copy 策略；
- `state_layout_v1` 把 trace/outcome 定义为一个 `plan_evidence` domain。`plan_tracer` 是路径 owner，`calibration_history` 始终跟随同一 resolver；clean install 使用 `runtime/plan_evidence.db`，现有安装在有效 migration receipt 出现前继续读 legacy；
- operator CLI 默认 dry-run，apply 需要精确 `--confirm-domain plan_evidence`。迁移用 read-only source transaction 和显式列复制保留 ID，把旧 schema 缺失的 outcome classification 标为 `legacy_unclassified`，对每张表计算 count + logical SHA，再做 destination `quick_check`、receipt SHA、0600 临时文件和 atomic rename；目标已存在、copy 不一致、source bytes 改变或 WAL mode 都失败关闭，旧库不删除；
- 本机 operator migration 已把 75,022/1,312 初始快照复制到独立库，源/目标 logical SHA 相同且旧文件 byte SHA 不变；这是当前机器运行态事实，不写入 portable artifact。公开 3-case synthetic artifact 复算 dry-run、copy、receipt、domain isolation 与旧分类，六项 rate 均为 1.000；
- 首次完整门禁虽然没有改 trace/outcome，却暴露 memory/prediction 测试仍在写真实旧库；不能把“目标表没变”冒充为“状态库无副作用”。本轮为 user memory 与 prediction 增加显式路径覆盖，pytest session 和完整 `make check` 分别使用临时/保留测试库并做精确 cleanup；第二次完整门禁前后 legacy shared DB 与 dedicated plan-evidence DB 的文件 SHA 都不变；
- 两条 benchmark 同样使用隔离 runtime。边界：user memory 与 prediction feedback 的正式数据仍在旧库，本轮只阻止测试污染；首次复核写入的行无法仅凭数量证明归属，因此没有猜测性删除。单机 copy 不是在线迁移、RLS、加密、backup deletion 或未来行不可变证明。

### v6.15：Domain-driven Verified Copy and Prediction-feedback Ownership

- 把 plan 专用迁移中的 source snapshot、逻辑摘要、metadata receipt、0600 原子发布与 WAL 拒绝下沉为 `DomainSpec + verified_copy`，plan-evidence 原有契约回归保持通过；
- 将 `prediction_log` 定义为独立 `prediction_feedback` owner。它不是 append-only 日志：正常路径先 INSERT prediction，再 UPDATE 最近未配对记录的 actual，定向清理还会 DELETE，因此迁移测试同时覆盖 NULL/稀疏 ID 保留与迁移后的可变续写；
- 本机 operator migration 非破坏复制 33,791 行，其中 11 行已有 actual，ID 范围 1–41,762；源/目标 logical SHA 相同、旧库 byte SHA 不变、receipt 有效、`quick_check=ok`、新文件 mode 0600。resolver 随后选择 `runtime/prediction_feedback.db`；
- 4-case synthetic artifact 从 raw rows 独立复算 dry-run、source preservation、copy/receipt、domain isolation、post-migration UPDATE/DELETE 与 WAL fail-closed，七项 rate 均为 1.000。边界是旧表未删、无在线双写/cutover、跨实例锁、加密、RLS 或远端不可变存储；11 条 actual 也不足以支持 calibration 或业务效果结论。

### v6.16：User-memory Pair Migration and Privacy Lifecycle

- 实库审计确认 `user_memory` 是可变当前态，`user_memory_events` 是同一领域的生命周期事件：create/reinforce/replace/confirm/soft-forget 需要同事务更新，hard delete 还必须同时清除 state 与 hash-only event，不能按两张独立日志迁移；
- 通用 `DomainSpec` 增加每表稳定排序键，支持 state 的 `id` 与 event 的 `event_id`。两表从同一 read-only source snapshot 复制并分别核对 count/logical SHA，trigger、索引、receipt、0600 atomic publish、目标不覆盖与 WAL fail-closed 继续由共享内核保证；
- 本机 operator migration 非破坏复制 2,783 条 state 与 5,572 条 event，保留稀疏主键；两表 source/destination logical SHA 一致，旧共享库 byte SHA 不变，新库 `quick_check=ok`、mode 0600、receipt 有效。resolver 随后选择 `runtime/user_memory.db`；
- 4-case synthetic artifact 的九项 rate 全为 1.000：独立 verifier 重算固定两表 digest、receipt、domain isolation、迁移后 replace/event append、用户级 hard delete、事件 UPDATE 拒绝与 WAL fail-closed。边界是旧两表未删、当前 988 个 namespace 不能证明 988 个真人、全部 state 已 forgotten 也不等于完成备份擦除；专用 SQLite 仍无加密、RLS、跨设备同步或在线 cutover。

### v6.17：Legacy Retirement Audit and Strict Readiness

- 迁出全部业务 owner 后，兼容 resolver 仍可能在 receipt 被删或专用路径误配时静默回到 `tool_calls.db`；仅凭“迁移命令曾成功”不能证明当前部署仍处于分库状态；
- 新增 payload-free retirement registry/audit，逐项核对旧库 quick-check、六张已知表、三类 resolver、专用 metadata receipt、当前 legacy count/logical SHA 与 receipt source snapshot 的持续绑定，以及 tool-audit 独立库只能拥有诊断表；
- 默认 `compatibility` 仍允许尚未迁移的旧安装运行。operator 在三次迁移与 `audit_legacy_retirement.py --require-ready` 通过后可设置 `BJ_PAL_STATE_LAYOUT_POLICY=dedicated_required`；此时 `/readyz` 把任一 legacy fallback、source drift、unknown table、receipt/integrity 失败转为 503，而不是继续接流量；
- 真实本机 audit 的 18 项检查和 strict readiness 全为 `ok`。4-case synthetic artifact 的 verified-owner acceptance、source-drift detection、unknown-table detection、missing-receipt detection 与 payload exclusion 五项 rate 均为 1.000。边界是显式 registry 只覆盖已登记 owner；这不是在线 cutover、备份删除、加密/RLS 或静态分析证明。

### v6.18：Reproducible Release Candidate Boundary

- 直接把数百个 dirty-tree 文件写进提交说明既不可复核，也容易把 runtime、数据库、评测结果或本机路径混入发布；发布边界必须从 Git 的字节安全状态流生成，而不是靠 shell 空格分割或人工计数；
- `release_candidate_manifest_v1` 以 NUL-safe porcelain 为输入，把每个候选绑定到相对路径、XY status、实现/文档组、size、Git executable mode 和 SHA-256，并同时绑定 branch、HEAD、`origin/main` 及 ahead/behind；
- 主生成器执行明确 allowlist/denylist，拒绝 env/state/generated/binary/symlink/non-UTF8/大文件/本机绝对路径；独立 verifier 重新读取当前 Git 状态和文件字节，复算分组、状态、总字节、mode、文件 SHA 与 artifact SHA，不信任生成器自报摘要；
- 当前 manifest 精确覆盖 333 项：315 implementation、18 documentation，60 modified、273 untracked，逐项字节数及总量以每次重建的 gitignored artifact 为准，0 违规。它为两个原子提交提供可执行边界，但不扫描 Git 历史、不证明代码语义正确。repository owner 已确认旧 LongCat Key 在 provider 侧撤销；这属于 owner attestation，不是 provider 签名回执，真实 DeepSeek API 调用也不是撤销证据。

### v6.19：Durable-job Incident Diagnosis

- 新增 `job_incident_diagnosis_v1`，从 tenant-scoped job 与完整 append-only event chain 生成 14 类稳定 failure signature 和 recommended action；排队/执行 deadline 由 claim evidence 区分，retry/lease recovery 保持为非 terminal 诊断；
- 诊断 read model 只暴露 job ID、status、allowlisted error code、事件类型/attempt/相对时间、计数和双 SHA；request/tenant/principal/worker、原始 payload/error message 与未知 error 原值不进入输出；
- 事件链要求 submitted 起点、单调 ID/时间、attempt 边界和 terminal matching；服务达到 1,000 项时探测 overflow 并失败关闭，不在截断证据上分类；
- tenant-scoped HTTP 复用 `jobs:read`，本地 CLI 只创建 0600 新文件；job smoke 实际走 SQLite submit/claim/success、dead-letter 和 queue timeout 三条路径；
- 14-case hand-authored synthetic artifact 覆盖全部分类，独立 verifier 从 raw case 重算 classification/action、phase、sanitized event SHA、inner/outer SHA 和隐私约束。它是 deterministic triage contract，不是生产 root-cause accuracy、事故频率或建议修复率。

### v6.20：Durable Workload Health

- 新增 `durable_workload_health_v1`：只接受单 tenant、timezone-aware、最长 31 天且已经闭合的 `[start,end)`，未来 end 失败关闭；repository 以同一 read snapshot 读取窗口内创建的 job 和 `created_at < end` 的 event prefix，再从 prefix 重建 as-of status，晚到 lifecycle event 不改写历史快照；
- 固定 1,000 job/10,000 event 上限，overflow 不发布；七类 status、terminal/active/job/event、各 rate 的分母显式返回，空窗口 rate 为 `null`；
- queue/run/time-to-terminal 使用固定事件边界和 nearest-rank p50/p95/p99，同时返回 sample count/min/max，避免把无 claim 的 queue timeout 塞进 run latency；
- evidence/artifact 双 SHA 不公开实体 ID；HTTP 复用 `jobs:read`，CLI 只创建 0600 新文件，不输出 tenant/principal/request/job/worker/payload/error；
- 2-case mixed/empty synthetic artifact 和独立 verifier 重算窗口、聚合、quantile、哈希与隐私，并拒绝重签后的 rate、p95 和 job ID 注入。它不连接 OTLP，也不证明生产 SLO、容量、事故频率或告警质量。

### v6.21：Privacy-minimized OTLP Export

- 将旧 `otel` console adapter 改为声明依赖的 OTLP/HTTP protobuf batch exporter；只在显式 endpoint 与 `http/protobuf` 配置有效时启用，不再静默 fallback JSONL；
- JSONL/OTLP 共用 allowlist 投影：运行 ID、低基数数值、GenAI operation/provider/provider-reported usage 和稳定 error type；session/user/location/plan/POI/decision/prompt/tool args/model output/error message 不导出；
- exporter exception/failure 只更新有界计数和 error code，不将 trace I/O 异常抛回业务；受 `jobs:read` 保护的健康端点不返回 collector URL/header，只返回 origin SHA、policy、processor、state 和计数；
- 2-case artifact 实际走 loopback HTTP + protobuf，独立 decoder 复核 resource/span tree/GenAI attributes/privacy，再用注入失败证明 business isolation。这不是远程 vendor receipt、生产投递、告警/SLO、retention、多实例或真实用户证据。

### v6.22：Operational Alert Contract

- 新增 `portfolio_operational_alert_policy_v1`，只消费 v6.20 的 integrity-checked workload snapshot 和 v6.21 的 payload-free trace status，不建立另一套采集或指标定义；
- terminal failure、queue wait p95、retry rate 分别要求 20 个 terminal、20 个 queue sample、20 个 job；样本不足是 `insufficient_data`，数值达到阈值才 firing，未配置 OTLP 只 disabled 对应规则；
- 总状态固定按 `firing > insufficient_data > healthy > disabled` 归并；source/policy/snapshot 分层 SHA 阻止阈值、来源或判断被无痕替换；
- tenant-scoped HTTP 复用 `jobs:read`；离线 CLI 显式读取两个已有 JSON source，以 O_EXCL 创建 mode-0600 结果，避免新进程把空 exporter monitor 伪装成服务状态；
- 4-case artifact 覆盖健康、四规则 firing、小样本和 OTLP off，独立 verifier 重算来源 rate、规则、总状态和哈希。这是 fixed synthetic decision contract，不是生产 baseline、连续窗口、Alertmanager delivery、SLO 或事故处置效果。

### v6.23：Bounded Live-provider Acceptance

- 新增显式 CSSwitch credential handoff：只有费用确认、credential source 与 model 同时声明时才读取本机配置；拒绝 symlink、非普通文件、非当前用户 owner、group/other 权限、超大/非法 schema、active profile 歧义、非 DeepSeek/Anthropic format 与非 HTTPS endpoint；
- API Key 只在 context manager 内进入 `DPSK_*`，不进入 CLI、Agent message、repr/equality、配置路径或 evidence；runner 退出后恢复原环境，并避免与 LongCat/Anthropic/DEEPSEEK alias 混用；
- 真实调用仍走 canonical `PlanningService`、fixed scenario、strict model-output contract、quality proxy、trace usage 与 request-local budget；bundle 目录必须不存在，创建为 0700，observation/quality/acceptance 以 O_EXCL/0600 写入；
- 2026-07-21 一次三里屯 synthetic 场景首轮接受：1 个 LLM call、53 input + 1411 output = 1464 provider-reported token，canonical execution 约 28.9 秒，quality hard gate 通过；实际 Key 在三份 linked artifact 中 exact-match count 为 0；
- 独立 verifier 复用既有 observation/quality verifier，再自行重算 credential preflight、usage 加总、execution budget SHA/count、linked artifact、六项 acceptance checks 与 outer SHA。它不能证明签名 provider/external execution、成功率、发票金额、价格版本或服务端 credential lifecycle。
- 首次完整门禁发现 OTLP artifact verifier 仍硬编码 v6.22；修复后 verifier 从 `pyproject.toml` 读取声明版本，并由 package/app/core 版本一致性测试约束。最终 557-file secret gate、903 collected / 900 passed / 3 skipped、ASGI/TCP 各 20/20，完整门禁退出码为 0。
- [PR #10](https://github.com/estelledc/bj-pal/pull/10) 已合并；最终 `main` [Ubuntu Core workflow](https://github.com/estelledc/bj-pal/actions/runs/29796656281) 包含 Docker build 与 checked-in acceptance receipt 的离线复核。CI 不会再次调用真实 API，因此仍不能把离线复核写成新的 provider execution。

## 3. 当前执行链

```text
HTTP / CLI / Streamlit
  -> PlanRequest
  -> PlanningPreflight
      -> RequirementNormalizer
      -> ConstraintNormalizer
      -> proceed / explicit reversible assumption
      -> clarification_required (persist request/decision/options; stop before fan-out)
          -> typed resolution + fenced continuation
          -> same PlanRequest re-enters PlanningPreflight
  -> PlanningService
      -> server-owned ExecutionBudget
          -> pre-call LLM/data/tool N+1 gate
          -> bounded transport retry policy
          -> post-call provider-reported token gate
          -> safe-checkpoint wall-clock gate
      -> PlanningDataProvider.collect
          -> UGC summary
          -> query-specific UGC retrieval
          -> food/scenic/landmark/museum/shopping branches
          -> weather snapshot (offline fixture by default)
          -> deterministic merge + provenance/issues
      -> Planner LLM selects / orders / explains candidates
      -> strict model-output contract
          -> accept first response
          -> or one budget-bound provider repair
          -> or invalid_model_output (stop before route/trace)
      -> fail-closed route snapshot
      -> travel-aware schedule reconcile
      -> AvailabilityProbe
      -> local Replanner
          -> full route refresh after replacement
          -> schedule reconcile after replacement
      -> final trace + confidence factors
      -> execution observation v2 + budget/span SHA
  -> PlanResult
  -> HTTP response or durable artifact
```

LLM 只负责理解、选择、编排和解释。程序控制 schema、候选、硬约束、状态转移、idempotency 和 artifact integrity。

## 4. 数据面

`PlanningDataSnapshot` 包含：

```text
area_summary
candidates[category]  -> tuple[POI, ...]
retrieved_evidence[]  -> text/score/algorithm/features/expanded terms
evidence[]             -> source/classification/freshness/bookable/warnings
issues[]               -> code/retryable/required/message
```

本地 adapter 使用独立 SQLite 连接在共享有界执行器中读取，之后按请求 category 顺序单点合并。单类失败不会改写其他结果，也不会被当成成功；全部候选为空时 fail closed。有界 executor 能证明线程资源不会随请求数乘法增长；它不等于跨机器容量提升。

## 5. Durable job

job store 保存规范化 request JSON、SHA-256 和服务端认证上下文：

- 同一 tenant 内，相同 idempotency key + 相同 request hash/deadline 秒数/priority：返回已有 job；
- 同 tenant 下相同 key + 不同 request hash 或任一 job policy：409 conflict；不同 tenant 可复用 key；
- worker 事务 claim queued 或 lease 已过期的 running job；
- claim 按 `tenant_fair_priority_aging_v2` 选择：priority aging 优先、同有效优先级 tenant 最久未服务优先、tenant 内 eligible-time FIFO；event 保存 policy、tenant cursor、基础/有效优先级与 queue wait；
- submit/replay/clarification continuation 在单事务内执行 active/accepted-submission admission，所有 decision 进入追加式 tenant audit；
- active lease owner 周期 heartbeat，只有持有未过期 lease 的 owner 可以完成或安排重试；
- retryable 异常按指数退避重排，耗尽最大尝试次数进入 dead letter；
- queued/running job 可请求取消；绝对 deadline 到期进入 timed out；failed/dead-letter/timed-out 终态可创建带 lineage 的幂等 replay job；
- 成功结果生成 artifact ID/SHA-256；失败只保存稳定错误码和脱敏信息。

v6.27 把这套 transition policy 收敛为 `PlanningJobStore` Protocol：SQLite 仍是默认，PostgreSQL adapter 通过同一 schema 共享 job/event/admission/scheduler state。PostgreSQL 17 本机验收让 4 个独立 OS worker 进程领取 12 个 job 且无重复 claim，并在 8 路并发提交、active limit=3 时精确得到 3 admitted / 5 rejected；另验证过期 lease reclaim、旧 owner fencing、replay、append-only trigger 和 readiness probe。短 claim/admission transaction 使用 advisory lock，Planning 执行在锁外，因此优先确定性语义而非高吞吐。[PR #19](https://github.com/estelledc/bj-pal/pull/19)、main Core/Pages 与 OCI workflow 已通过，`v6.27.0` 公开镜像 digest 为 `sha256:9ff768ec8901b24e6f5ea79cf207e3a3cb6a5e58d32e9a88eb254a72a698ffe6`。

v6.28 候选在 store port 之上增加显式 offline cutover。CLI 不接受 DSN 参数，只从环境读取；dry-run 不创建 target。apply 要求精确 cutover 文本与 source-quiesced attestation，随后拒绝 WAL、running lease、legacy/损坏 source 和非空 target。SQLite `BEGIN IMMEDIATE` 与 PostgreSQL transaction advisory lock 构成停写窗口；job/event/admission/scheduler state 按稳定 sequence 分批复制，保留 row/event ID，并在同一 target transaction 完成 sequence reset、跨库 count/digest 和 append-only receipt。真实 PostgreSQL 17 测试注入 events 后失败，证明 job/event 部分行与 receipt 一起回滚；并发 SQLite writer 在 copy 窗口收到 locked。重复 apply 只在 source、target、receipt 三者一致时返回既有 receipt。切换后 target 发生 claim/success，verify 明确将 rollback 判为不安全，避免切回陈旧 source。

这提供 SQLite 单机或 PostgreSQL cross-process 的 at-least-once 恢复、持久 deadline、priority aging、tenant admission/fairness 和静态 principal/scope/tenant/cap 控制语义，但不等于生产队列或企业 IAM：没有外部 IdP、动态 RBAC、credential 过期/轮换/撤销、数据库 RLS、网关 raw-attempt abuse protection、audit retention、SQLite 在线迁移、连接池/故障恢复/容量证据或 exactly-once fencing token。取消和 deadline 都不能强杀已进入的单次模型/provider 调用。真实 side effect 不能直接复用这套自动恢复语义；v6.2 使用独立 operation/receipt/reconciliation 状态机，目前只允许 sandbox。

每次状态迁移还会在同一 store transaction 中追加 `planning_job_events`。SQLite/PostgreSQL 事件表都由 trigger 保证 append-only，失败事件只存稳定错误码。HTTP 客户端既可用 JSON cursor 回放，也可连接 bounded SSE；两者读取同一张持久表，SSE `Last-Event-ID` 映射到 store `event_id`，断线不会改变 job 生命周期。

用户记忆与 job 状态分开存储，但使用同样的原则：状态转移由程序拥有，写入和事件在一个 `BEGIN IMMEDIATE` 事务内完成。Memory event 不复制原值，只保存 SHA-256；Planner 不把未确认候选当事实。hard delete 同时清除状态和事件，因此它不是不可删除的合规审计系统。

## 6. HTTP 契约

| Route | 语义 |
|---|---|
| `GET /healthz` | 进程存活 |
| `GET /readyz` | manifest 与主数据 SQLite 可用 |
| `POST /v1/plans` | 同步执行 PlanningService |
| `POST /v1/clarifications/{continuation_id}/plan` | 解析一项 typed option 并从持久原请求继续；同答案幂等 |
| `POST /v1/planning-jobs` | `jobs:submit` + priority/admission cap；在 principal tenant 内原子持久化 queued job 与 decision |
| `POST /v1/clarifications/{continuation_id}/planning-job` | `jobs:submit` + tenant/priority/admission cap；保留原 policy 并幂等入队；429 后 session 可恢复 |
| `GET /v1/planning-jobs` | `jobs:read`；按 tenant/status/after_job_id 查询轻量摘要 |
| `GET /v1/planning-admission-events` | `jobs:read`；按 cursor 读取当前 tenant 的 append-only admission decision |
| `GET /v1/planning-jobs/{job_id}` | `jobs:read`；查询同 tenant 状态和完成 artifact |
| `POST /v1/planning-jobs/{job_id}/cancel` | `jobs:control`；持久化同 tenant 协作取消 |
| `POST /v1/planning-jobs/{job_id}/replay` | `jobs:replay` + admission cap；从同 tenant 终态创建幂等 replay job |
| `GET /v1/planning-jobs/{job_id}/events` | `jobs:read`；按 cursor 回放同 tenant 持久事件 |
| `GET /v1/planning-jobs/{job_id}/events/stream` | `jobs:read`；按 `Last-Event-ID` 续读同 tenant bounded SSE |
| `POST /v1/operations` | `operations:request`；以 `Idempotency-Key` 创建 quote-bound sandbox operation |
| `GET /v1/operations/{operation_id}` | `operations:read`；读取同 tenant 状态与 receipt |
| `POST /v1/operations/{operation_id}/approve` | `operations:approve`；由非 requester principal 审批精确 fingerprint |
| `POST /v1/operations/{operation_id}/deny` | `operations:approve`；由非 requester principal 拒绝并保存 reason code |
| `GET /v1/operations/{operation_id}/events` | `operations:read`；按 cursor 回放同 tenant append-only decision/execution event |
| `POST /v1/operations/{operation_id}/reconcile` | `operations:reconcile`；仅以绑定 provider reference 做只读状态核对，不重试写操作 |
| `GET /v1/operations/{operation_id}/reconciliations` | `operations:read`；按 cursor 回放同 tenant append-only lookup evidence |

响应附 `X-Request-ID`。HTTP schema 拒绝未知字段；错误响应不暴露原始 exception、provider secret 或 control credential。同步 plan continuation 与公开 `/v1/plans` 相同，以短期 capability ID 访问；job continuation 和其他 job route 使用服务端 registry 决定的 principal/tenant/scope。跨 tenant 统一 404，不接受调用方自报 tenant header。这只证明本地静态的应用层授权，不证明外部身份、动态授权或存储层隔离。

## 7. 验证

公开验证分为：

1. 单元/契约：Provider、HTTP、Job、Application、confidence、eval artifact；其中 Job 覆盖 heartbeat、fencing、retry、dead letter、queued/running/expired cancel、queued/running deadline、cancel-timeout 竞态、timed-out replay、lineage、游标、并发 claim、v4.3/v4.7/v4.8 schema 迁移与事务回滚；HTTP 另覆盖 auth fail-closed、OpenAPI security 和 token 不回显；
2. 历史 acceptance：旧 `t1_*` 函数通过适配器纳入 pytest；
3. runtime smoke：同步 HTTP、Bearer fail-closed、principal/tenant/priority/admission cap、admission audit、bounded SSE、cancel/list/deadline 控制面、transient retry/heartbeat、dead-letter/timed-out replay worker、双人审批 sandbox operation/receipt、provider-bound reconciliation、CLI；
4. public eval：L1/L2/L3 保存 raw artifact 并复算；
5. retrieval eval：legacy/candidate 使用同一 golden set，输出逐例 Recall/MRR/多样性；
6. requirement eval：20 条 synthetic case 输出逐例 decision、误澄清和补充后 gate 状态并独立复算；
7. constraint eval：30 条 synthetic case 输出逐字段文本值、生效值、冲突、rewrite 和 round-trip 并独立复算；
8. clarification eval：16 条 synthetic case 输出原请求、decision/options、resolution、恢复/重放与 conflict fencing，并独立复算；
9. observability eval：3 条 synthetic contract case 输出 raw span observation，由独立 verifier 重算树、计数、token 语义、敏感标记排除与 SHA；
10. tool-call audit eval：5 条 synthetic contract case 输出隐私投影、稳定错误码、session hash chain、mutation/reset/legacy read 和 storage-isolation outcome，由独立 verifier 重算 6 项指标并拒绝重签篡改；
11. state-layout eval：3 条 synthetic contract case 输出 dry-run/source/destination logical hash、migration/receipt、表集合与旧 classification，由独立 verifier 重算 6 项指标并拒绝重签篡改；
12. execution-budget eval：6 条 synthetic contract case 输出 completed/terminated budget snapshot，由独立 verifier 重算 LLM/data/tool N+1、reported-token、wall-clock、终止后代码未执行、敏感标记排除与双层 SHA；
13. scheduling eval：4 条 synthetic contract case 输出候选时间戳、优先级、tenant cursor 和 claim event，由独立 verifier 重算排序、aging、queue wait、backoff exclusion 与 tenant fairness；
14. access-control/admission eval：6 条真实 ASGI + SQLite contract case 输出 raw HTTP/audit evidence，由独立 verifier 重算 scope、priority、tenant isolation、active/rate admission、audit 与 continuation recovery；
15. side-effect safety eval：5 条真实 SQLite contract case 输出 raw operation/event/receipt/reconciliation，由独立 verifier 重算职责分离、approval 绑定、幂等/tenant、过期、receipt、append-only、sandbox、uncertain no-retry、status lookup binding/resolution/audit；
16. outcome evidence eval：4 条真实 SQLite contract case 输出 capability/artifact 绑定、幂等/phase/schema、过期、append-only、secret minimization 和最小样本门，由独立 verifier 重算 8 项 rate；不生成真人 report；
17. trial evidence eval：6 条真实 SQLite contract case 输出 notice/cohort/enrollment/participant/report/withdrawal/snapshot/purge receipt，由独立 verifier 重算 13 项 consent、tenant、uniqueness、withdrawal、cutoff、retention 和目标清除事务契约；不生成真人 participant；
18. trial operator contract：验证批量事务回滚、secret 不进 stdout/SQLite、bundle SHA/0600/防覆盖、精确关闭确认、低样本门、到期/冻结/attestation gate、目标隔离、trigger 恢复、foreign-key check、WAL fail-closed、清除 rollback、receipt 防篡改和重复请求幂等；只使用临时数据库；
19. HTTP performance regression：进程内 ASGI 与独立 Uvicorn/localhost TCP 两条链路都输出逐请求 raw evidence 并独立复算；
20. container build：由 CI 在干净上下文构建。

评价口径严格分开：test 证明代码契约；trace 证明执行记录；eval 证明固定 case 行为；benchmark 证明特定负载和环境下的观测；用户/业务 outcome 才证明真实价值。当前已有本机 in-process ASGI 与独立 localhost TCP 两种证据，但没有 TLS/反向代理、真实模型/provider、多实例、线上流量或业务 outcome。

## 8. 安全与边界

- 数据、数据库和 runtime job state 默认 gitignored；
- Docker context 排除本机数据、环境变量和测试产物；
- 容器非 root；
- 公开 demo `bookable=false`；
- 当前只有静态哈希 principal registry 和短期 sync continuation capability；job/operation 控制面已按 scope/tenant 隔离，PostgreSQL job store 也仍是应用层 tenant 条件。系统仍无外部 IdP、动态 RBAC、数据库 RLS、token 生命周期、入口 raw-attempt limiter、网关 quota、audit retention、原请求加密、PII policy、secret manager 或公网部署证据；
- legacy mock helper 与 v6.2 sandbox operation/status lookup 都不触发真实外部状态；默认 CLI/UI 预订路径已不再直调 `mock_book`；
- v6.7 请求级预算可以在 N+1 前阻止新的逻辑调用并约束 retry，但不能强杀已经阻塞的网络调用；reported-token gate 依赖 provider usage，且不是金额预算或生产 billing；
- v6.8 编排对照只证明 deterministic mock 下 3 个同构分支没有质量增益却增加调用；不能据此断言真实模型、多 Agent 或并行执行普遍无效；
- v6.9-v6.10 模型输出契约会拒绝 schema/候选/类别/序列越界并限制一次修复，但 13+4 条 synthetic/scripted case 不是线上幻觉率、真实修复率或方案质量证据；
- v6.12-v6.13 工具日志对新写入做有界投影和链式完整性，并默认切到独立 runtime store；legacy payload 未擦除，新旧 SQLite 都未加密且整文件可删除，不是 DLP、RLS、WORM 或合规审计；
- v6.14-v6.16 已依次迁出 plan evidence、prediction feedback 与 user memory/events，v6.17 可在部署 readiness 禁止回退；legacy rows 均保留。migration receipt 与 retirement audit 证明本地 snapshot/owner 绑定，不证明 backup 删除、未来数据不可变、跨实例一致性或在线双写切换；
- v6.6 记录精确 consent notice、退出、retention deadline 和本地 purge receipt，但 participant capability 不等于身份，且没有托管调度、取证级擦除或独立数据库/备份删除证明；
- v6.5 operator bundle 含原始一次性加入码，不属于 evidence artifact；代码强制 0600/防覆盖/不回显，但实际分发和删除仍由受信 operator 负责；
- 仓库尚未选择开源许可证。

## 9. 主要限制与下一步

最重要的证据缺口是：v6.10 修复 live proxy 暴露的候选/忌口问题，v6.21-v6.22 补 OTLP 与单快照告警，v6.23 完成一次显式 0600 CSSwitch handoff 的 DeepSeek usage/质量验收，v6.27 补 PostgreSQL shared job store 的本机独立进程验收和公开发布链；但真实 participant/report 仍为 0。单次 1464 provider-reported token、32/32 fixed synthetic 必需检查、cross-process job test、socket/日志/迁移/告警样本都不能替代真人采纳、成功率、账单金额或生产容量。技术缺口包括 legacy rows 受控清理、托管 purge/备份删除，天气宣传用途授权或 self-hosted acceptance，POI/路线合法 live provider；PostgreSQL 在线迁移/容量/故障恢复，服务端 credential 过期/轮换/撤销、外部 IdP/动态 RBAC、数据库 RLS、入口 abuse protection、价格/cache 计价、跨实例 cost controller、tenant 金额预算、billing reconciliation、audit retention 与在线 reprioritize。booking 仍缺真实供应商授权/查询/补偿/客服/签名回执；另外还缺远程 collector、连续告警、TLS/反向代理与跨主机真实模型负载测试。

正确顺序是：执行一个有界知情 cohort 并获得真实 badcase → 授权天气环境与 live acceptance → 真实 failure/freshness 样本 → 下一类 provider → 合法 booking 测试环境 + 真实状态查询/签名回执 acceptance → 独立重新审批的补偿 operation/客服 handoff → 外部 IdP/动态授权/存储层隔离/入口与跨实例 quota → OTLP/metrics + TLS/reverse-proxy + multi-instance store/load test。见 [ROADMAP.md](ROADMAP.md)。
