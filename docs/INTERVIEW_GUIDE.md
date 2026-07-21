# BJ-Pal 求职与面试指南

> 读者：准备 AI 应用研发、后端工程、Agent 工程或全栈岗位的项目作者。所有回答以当前公开代码为准。

## 1. 先选你的项目定位

不要把同一个项目讲成四种岗位的万能答案。按岗位选择主线：

| 岗位 | 主叙事 | 次要证据 |
|---|---|---|
| AI 应用研发 | 受控候选 + LLM 合成 + Probe/Reroute + eval | provider、support score、fallback |
| Agent 工程 | 显式状态、工具边界、bounded workflow、durable job | 不盲用 MCP/A2A/图框架 |
| 后端工程 | FastAPI 契约、幂等、SQLite transaction、worker lease、artifact | 错误语义、Docker、CI |
| 全栈/产品工程 | Streamlit + API + 可解释 UI + 产品 demo | 研究到实现、证据边界 |

## 2. 60 秒项目介绍

> BJ-Pal 是一个北京短时活动规划 Agent。用户输入自然语言需求后，系统先用 Requirement Gate 区分可直接执行、透明默认假设和必须澄清，再用 Constraint Ledger 把人数、预算、时间、时长和忌口等文本约束映射到 typed preferences 并检查显式字段冲突，然后从受控 POI/UGC/路线/天气数据中并行取候选和 evidence。LLM 只负责选择、编排和解释；它的输出仍视为不可信，必须通过 strict schema、请求 persona/area、候选 ID/名称和步骤序列校验，第一次失败最多修复一次，第二次失败直接终止。为了把黑客松原型变成可放简历的工程项目，我统一了应用层，增加类型化 data provider、可解释检索、约束保持型重规划，以及带 lease/deadline/replay、租户准入/公平调度、append-only event/audit 的 durable job。v6.7 以服务端 request-local budget 限制逻辑 LLM/data/tool 调用、transport retry、实报 token 和安全检查点耗时；v6.8 用同输入 single/multi artifact 证明当前 mock 多分支没有质量增益却消耗 3 倍 LLM/data 调用，所以主链保持单分支；v6.9 再让模型输出和一次修复共享该预算，并用独立 verifier 重算 12 条对抗 payload 与 4 条生命周期 case。对高风险预订另建 approval-gated operation，调用后不明时不自动重试；对普通方案另建 capability-bound 结果证据链，并用 tenant-scoped 知情 cohort 约束试用分母。公开仓库默认使用 synthetic 数据，副作用只允许 deterministic sandbox，真实 participant/report 仍为 0，因此我不把契约成绩说成实时能力、幻觉率、真实预订或用户成功率。

如果面试官只给 20 秒：

> 我把一个旅行规划黑客松 Demo 重构成了可复现的 Agent 服务：LLM 只做受控决策，数据与规则可追溯；同步 API 和 durable worker 共用主链；任务、错误、artifact 和评测都有明确证据边界。

## 3. 三分钟架构讲法

按“入口—数据—决策—可靠性—证据”讲，不要照目录背：

1. 入口：Streamlit、CLI、同步 `/v1/plans`、异步 `/v1/planning-jobs` 都映射成 `PlanRequest`。
2. 数据：`PlanningDataProvider` 并行读取 area summary、各类 POI、query-specific UGC evidence 和天气快照，返回独立结果、检索特征、provenance 和 partial issues。
3. 决策：LLM/Mock 只能从候选池选地点；路线、硬约束、风险 Probe 和局部 Replan 由程序处理。
4. 可靠性：同步接口适合短请求；durable job 先落库，worker claim 后 heartbeat，异常按策略 retry/dead-letter，绝对 deadline 到期进入 timed_out；调用方经静态哈希凭证映射到 principal/tenant，按 scope、priority cap 和一致的 tenant admission policy 提交、筛选、协作取消或幂等重放；有效优先级相同后轮转最久未服务 tenant；状态、job event 与 admission audit 都由 SQLite 事务保护，SSE 只投影同 tenant 持久事件。副作用使用另一套 operation 状态机，以精确 approval SHA、单次执行、receipt 和 terminal uncertain 避免重复预订。
5. 证据：每个 step 有支持因子；每个 plan 有数据来源；行为 eval 和检索 baseline 都保留 raw cases/hash。结果反馈与精确 plan artifact 绑定，令牌原文不落库，每 phase 少于 5 份时比例为 null；当前 report 为 0，不能把契约测试写成线上效果。
6. 记忆：Planner 只读已确认且未过期的条目；显式异值写入开启 revision，未确认冲突不能覆盖，用户可以永久删除状态和审计 hash。

## 4. 高频系统设计问答

### Q1. 这到底是 Agent 还是普通工作流？

准确答案：它是“确定性工作流 + 有界 LLM 决策节点”。LLM 根据上下文选择候选、组织顺序、生成解释；工具调用、硬约束和状态转移由程序拥有。不能因为有多个 `agent.py` 文件就称为分布式多 Agent 系统。

证据：`src/application/planning_service.py`、`src/agents/planner.py`。

### Q2. 为什么不做多个 Agent 互相讨论？

当前问题主要是数据可信、约束和恢复，不是角色数量。历史 ToT 也只是同一个 Planner 的 3 个提示词分支，不是 3 个 Agent。v6.8 用相同 mock、数据和规则 scorer 跑 3 个场景：多分支质量提升率与语义输出变化率都是 0，LLM/data 调用都是 3 倍；默认服务端 data-batch budget 还会拒绝第二个分支。因此主链保持单分支，旧多分支只作为有界实验入口。

边界必须主动说：mock 忽略 branch hint，3-case quality score 只是规则代理，所以这不是“多 Agent 永远无效”的结论。拿到真实 badcase/outcome 后应同口径重跑；只有出现独立部署、跨团队复用或动态协议互操作需求时，才考虑远程 Agent/A2A。

### Q3. LLM 和程序的边界怎么划？

LLM 擅长模糊意图和语言表达；程序擅长可验证约束、状态和副作用。BJ-Pal 把候选检索、预算/地理过滤、POI grounding、job 状态、幂等和 hash 留给程序。

反例：让 LLM 直接编商家价格或把预算字段乘 0.8，不等于真实重规划。

具体到输出边界，prompt 里的 JSON schema 不是验证器。v6.9 在构造 `Plan` 前拒绝未知/缺失字段、类型强转、候选 ID 幻觉、名称错配、重复地点和非法 depart/时间序列；第一次失败只允许调用同一 provider 修复一次，仍失败则同步 502 或 durable terminal failure。修复也受 request LLM budget 约束，不会无限自愈。

边界必须说：13 条 adversarial payload 和 4 条 scripted lifecycle case 只能证明这些越界会被拦截，其中包括 `meal/snack` 绑定非 food 候选。2026-07-20 的 DeepSeek Flash 坏例驱动 prompt 修正；同场景 Flash/Pro 各 1 个样本只支持后续优先 Pro。第一轮 3-case suite 又暴露 798 仅 2 个候选，因此 v6.10 先补齐 replacement coverage，并把 `no_spicy/light_diet` 从 prompt 文案升级为 structured positive UGC evidence filter，再重新运行三例 Pro。运行时进一步对任意显式 diet flag 缺证据时省略餐饮并给出 typed warning。新样本候选为 26/16/21，模型结果是首轮/首轮/一次修复后接受；脱敏 quality artifact 复算 9/9、12/12、11/11 个必需约束检查。可以说“建立了真实配置模型的坏例—修正—数据缺口—约束证据—固定质量代理闭环”，但不能写 Pro 100% 成功、Flash 不可用、满意度或计划质量提升。

### Q4. 为什么 provider 要返回独立结果再 merge？

共享可变 state 的并行分支依赖“大家只写自己的字段”这一隐含约定。独立 `tuple[POI, ...]` 让所有权清晰，失败可单独表示，merge 顺序可测试。

证据：`src/providers/sqlite_demo.py`、`tests/test_data_provider.py`。

### Q5. partial failure 怎么处理？

单个候选类别或 UGC summary 失败属于 optional degradation：规划可以继续，但必须把 `ProviderIssue` 返回给调用方。所有候选都为空属于 required failure，系统 fail closed，不能把缺失结果当 0 元或成功。

### Q6. 为什么同时有同步 API 和 job API？

同步 API 简单、延迟低，适合 demo；长 LLM 请求需要 durable ownership。两者共用 `PlanningService`，差别只在交付和生命周期，不复制业务逻辑。

### Q7. 为什么不直接使用 BackgroundTasks？

Web 进程退出后，BackgroundTasks 没有持久状态、lease owner 和恢复语义。job store 先写 queued，再由独立 worker claim；连接断开不决定 job 的生命。

### Q8. job 如何防止重复执行？

提交层用 `Idempotency-Key + request_sha256` 防重复创建；执行层用 SQLite `BEGIN IMMEDIATE` 和 lease owner 防同时 claim。lease 过期允许恢复，因此严格意义上仍是 at-least-once；真实副作用必须再加 operation id 和 side-effect receipt。

这是重要的面试点：checkpoint 证明控制流状态，不证明外部下单是否已经发生。

### Q9. 为什么分 request_id/job_id/plan_id/artifact_id？

它们回答不同问题：哪次 HTTP 调用、哪个可恢复任务、哪份业务方案、哪份持久结果。混成一个 ID 会让重试、追踪和一对多关系模糊。

### Q10. lease 有什么缺陷？

当前 worker 会在 lease 有效期内 heartbeat，repository 只接受 active owner 的续租、完成和重试；lease 过期后其他 worker 可 reclaim，旧 owner 会被 fencing 条件拒绝。但这里仍是单机 SQLite 的 owner + expiry 条件，不是跨实例单调 fencing token；worker 失联与提交最终结果之间仍可能发生重复计算，真实副作用必须用 operation id 和 receipt 幂等化。

### Q11. 数据 provenance 有什么用？

它把“数据从哪里来”和“能证明什么”放进机器可读契约。`bookable=false` 能阻止 UI 把 synthetic 或普通 API 数据渲染成可预订报价；`retrieved_at/valid_until` 为空会暴露时效缺口。

### Q12. 真实 API 是否等于真实数据？

不等于。真实 API 可能返回缓存、搜索摘要或无库存候选。要声称“可预订”，至少需要 provider reference、报价有效期、币种、库存语义和下单 acceptance evidence。

### Q13. confidence 怎么算？

当前是 `evidence_support_v1`，由 grounding、rating、UGC 深度、路线、rationale、风险、reroute、booking 和 profile 等透明因素组成。它不是概率；synthetic/mixed 上还会封顶。

### Q14. 为什么不能拿它算 ECE？

ECE 需要预测概率和同定义的真实 outcome 成对。ToT utility 或规则支持度不是成功概率；没有公开 paired artifact 时，历史 ECE 只能当历史快照，不能当当前证据。

### Q14.1. v6.3 收了 plan outcome，为什么仍不能算 step-level ECE？

粒度和定义不一致。`accepted/completed` 是整份方案的用户自报结果，可能同时受天气、同行人、临时变更等影响；step support 则是某一步候选证据的解释分。把一个 plan outcome 复制给每一步会制造伪标签。项目因此把旧表分为 `synthetic_test / legacy_unclassified / human_verified_step`，诊断 UI 只允许最后一种进入逐步 calibration；v6.3 的 plan-level report 只做采纳/完成聚合。

### Q14.2. 为什么反馈要 capability、append-only 和最小样本门？

capability 把提交权绑定到精确 plan artifact，原文只交给客户端、数据库只存 SHA；append-only 和 `(plan artifact, phase)` 唯一约束防止事后覆盖；枚举原因避免自由文本 PII；最小样本门防止 1/1 就展示“100%”。这仍不是统计显著性或身份认证，只是作品集阶段的防误导底线。

### Q14.3. v6.4 为什么还要 cohort 和 consent notice？

v6.3 能证明“一份报告绑定一版方案”，却不能证明分母是谁、是否知情、退出后是否仍被计入。v6.4 让 operator 按 tenant 发一次性加入码，参与者只在同意精确 notice SHA 后得到匿名 capability；同一凭证每 phase 一条，退出者从开放汇总排除，关闭批次冻结 cutoff snapshot。边界是 capability 只代表不同匿名参与权，不是外部身份认证，也不能证明不同真人。

### Q14.4. 为什么还要单独做 operator CLI？

协议正确不等于操作不出错。手工 curl 容易把 capability 留在终端日志、覆盖码表或误关错误批次。v6.5 将批量签发放在同一 SQLite 事务，只允许把原始码写入新建的 0600/gitignored bundle，stdout 只给 hash；close 要精确复述 trial ID，低样本还需二次确认。边界是本地 CLI 依赖文件系统权限，不是远程 IAM，也不会替组织者安全分发或招募真人。

### Q14.5. retention 到期为什么不直接定时 `DELETE`？

因为 append-only evidence、外键、SQLite journal 和备份会让“删几行”产生误导。v6.6 只允许清除已冻结且到期的精确 cohort：operator 复述 trial ID 并确认 secret/backup disposition；repository 用排他事务验证 hash chain，要求 `DELETE/TRUNCATE` journal、开启 `secure_delete`，按外键顺序删除目标行，再恢复 trigger、检查 row count/foreign key 并写 append-only receipt。注入任何中途失败都会整事务回滚。边界是 receipt 证明 live-table 删除契约，不是取证级擦除或外部备份删除证明；当前也没有 hosted scheduler。

### Q15. L1/L2/L3 分别验证什么？

- L1：关键入口和强信号是否还存在；
- L2：文本抽取、时段、记忆、群偏好等模块行为；
- L3：100 个 mock case 的分布回归。

它们都是 deterministic regression，不是业务 benchmark。真实 track 必须独立记录凭证、数据版本、预算和 raw outcome。

### Q16. 为什么 artifact 要双 SHA？

payload SHA 检查完整 JSON 是否被改；semantic SHA 检查关键语义摘要。verifier 还会从 raw cases 重算 L1/L2/L3 摘要，避免只改 summary 伪造通过。

### Q17. 为什么用 SQLite？

目标是零外部依赖的作品集和单机恢复语义。SQLite transaction 足以证明 schema、idempotency 和 lease 机制。它不适合高吞吐多实例队列；下一步会迁移到具备行锁/队列语义的存储，而不是假装 SQLite 已生产可扩展。

### Q18. 为什么没用 LangGraph？

当前主链是清晰的线性流程加一次有界 replan，Application Service 更少依赖、更易测试。出现复杂循环、人工审批、checkpoint 和事件回放时，图框架才可能降低复杂度。

### Q19. 为什么没用 MCP？

Provider 当前与应用同进程，Python Protocol 已能完成类型隔离。MCP 的进程、认证、网络和错误成本只有在工具需要跨语言或独立部署复用时才划算。

### Q20. 为什么没用 A2A？

没有远程 Agent 发现、跨框架互操作和独立部署需求。A2A 也不替代 data provenance、durable job 或 booking idempotency。

### Q21. SSE 如何保证断线续读？

SSE 不拥有任务状态，只投影 `planning_job_events`。SQLite `event_id` 同时作为 SSE `id`；客户端重连时把最后收到的 ID 放入 `Last-Event-ID`，服务端映射成 `after_event_id` 继续查询。显式 query cursor 优先，终态发完即关闭，非终态连接最长 30 秒并用 comment 超时，不伪造业务事件。断线不会取消 job；当前 control plane 已有静态 principal/scope/tenant 边界，但仍无外部 IdP、动态 RBAC、数据库 RLS、多实例 fanout 和长连接容量证据。

### Q22. 真实预订怎么接？

安全骨架已在 v6.2 以 sandbox 实现：报价 reference/validity/amount/terms 和 action 先绑定 request SHA，再绑定 operation/tenant/approval TTL；请求者不能自批，独立 worker 只执行已批准 operation；成功/明确拒绝有 receipt，调用后结果不明进入 uncertain 且不自动重试；已有严格绑定 provider operation ID 的只读状态核对。真实接入顺序仍是：取得授权 provider 和测试环境 → 验证真实订单查询与第三方回执 → 实现独立重新审批的补偿 operation/客服 handoff → 完成 PII/secret/retention。不能直接把 `mock_book.py` 换成 HTTP 请求。

### Q23. 如何扩展到多城？

先把 provider 的 city/locale/currency 放入 request 和 cache key，再新增城市 acceptance set。仅把 `city=上海` 传给抓取脚本，不足以证明约束、规则和评测仍成立。

### Q24. 如何做可观测性？

v5.7 先关闭“日志存在但一次请求无法核账”的缺口：Application Service 为每次成功规划生成单根 `execution_observation_v1`，同步请求关联 `X-Request-ID`、worker 关联 `job_id`，保存父子 span、阶段耗时、LLM/data/tool 调用数、reroute/provider issue 等业务计数和 SHA。公开 observation 不保存 prompt、用户输入或 user ID；token 只用 provider 回报值，mock 标 `unavailable`。3-case 独立 verifier 会重算树、汇总、token 语义和敏感标记排除。

v6.7 再解决“看见了但阻止不了”的缺口：`execution_budget_v1` 在 span body 前限制逻辑 LLM/data/tool N+1，统一持有每 LLM call 的 transport retry 上限，并在 provider usage 返回后累计 token、在每个安全检查点检查 wall-clock。成功快照进入 `execution_observation_v2`；超限同步返回 429，durable job 直接 failed 且不重试。6-case verifier 独立复算 limit+1、token/time 越界、post-limit work 未执行、敏感 marker 和 SHA。

v6.8 进一步处理线程扇出的预算旁路：Python `ContextVar` 不会自动进入线程池，旧 ToT 分支可能脱离 request tracker。现在每个分支使用独立 `copy_context()` 继承同一 budget/trace/capture，budget exception 必须向上抛出；测试验证 3 个并发分支被计为 3 次 LLM/data，而默认 policy 会结构化终止，不会把预算超限当普通分支失败后继续。

v6.9 处理“输出已经回来但内容不可信”的边界：正常路径一次模型正文，首次校验失败最多再调用一次；预算为 1 时第二次在 provider body 前停止。成功或拒绝快照只保留 attempt/candidate count、稳定问题码与 SHA，不保留 prompt 或模型原值。live observation 也只保留配置来源、固定场景 ID、执行上限、耗时、稳定问题码和双层 SHA。v6.10 另建只允许固定 synthetic registry 的 plan-quality artifact：保存无 rationale/summary 的步骤 projection、POI facts/tags 与路线/时间摘要，并绑定 observation SHA；固定 policy 不能由 artifact 自行放宽。verifier 能复算自洽、约束代理与隐私边界，仍不能证明 API 调用发生、provider 身份或真人偏好。

v6.12 关闭“trace 本身泄漏”的缺口：旧工具 logger 会直接落任意 params/response/异常原文，新账本只保存有界结构投影和稳定 error code；每个 session 用 sequence + previous SHA 形成链，v2 行拒绝 UPDATE/DELETE，reset 追加 marker，legacy payload 默认隐藏。4-case verifier 会拒绝重签后的私密 marker、伪造 mutation 成功和截断链。它只能证明固定攻击样本和本地完整性约束，不是 DLP、加密或合规审计。

v6.13 再关闭“安全日志与用户状态共库”的边界：新诊断事件默认写 `runtime/tool_audit.db`，旧 `tool_calls.db` 不自动迁移，footprint 只读 v2 投影行，socket 子进程把审计库也放进临时 runtime。第 5 条 case 证明一次新写入前后旧共享库 SHA 完全相同、新库只有诊断表；这证明本地默认路径隔离，不证明 RLS、加密、WORM 或历史擦除。

v6.14 处理“业务状态不能像日志一样直接丢弃”：实库有 75,022 条 trace 和 1,312 条 outcome，直接切路径会让 calibration 静默归零。项目把二者定义为一个 `plan_evidence` owner，现有安装只有在显式 copy 的 count/hash/receipt/quick-check 全通过后才切到新库；旧库不删除，WAL source 失败关闭。3-case verifier 证明固定 synthetic 迁移契约，本机另完成一次 75,022/1,312 非破坏 copy；它不是远端在线迁移、加密或 RLS。

v6.15 进一步避免“每拆一张表就复制一套迁移脚本”：把 snapshot、logical SHA、receipt、atomic publish 与 WAL 拒绝抽成 `DomainSpec + verified_copy`。`prediction_log` 虽名为 log，实际有 INSERT prediction、UPDATE actual 和 DELETE 指定历史三种生命周期；迁移因此既要保留 33,791 行、稀疏 ID、NULL 与 11 条 actual，也要证明迁移后续写只进入新库。4-case verifier 的七项指标均为 1.000，但 11 条 actual 远不足以证明预测已校准。

v6.16 把同一方法扩到更难的双表状态机：`user_memory` 保存可变当前态，`user_memory_events` 保存 hash-only 生命周期事件，hard delete 还要求两表一起清除。通用迁移内核因此新增每表稳定排序键，显式区分 `id/event_id`，并在同一只读快照内复制两表。本机 2,783 state + 5,572 event 的 logical SHA 对齐后 resolver 才切库；4-case verifier 的九项指标覆盖迁移后 replace、事件追加、privacy delete、事件不可更新与 WAL fail-closed。面试时应强调：这证明的是可审计 cutover 契约，不是备份擦除、跨设备同步或生产数据合规。

v6.17 继续追问“receipt 有过以后，部署会不会又静默回旧库”。答案是把 owner 状态放进 readiness：retirement audit 只读核对旧库已知表、三份 source snapshot 绑定、resolver、专用库完整性和 tool-audit owner；`dedicated_required` 下任何 fallback、drift、未知表或 receipt 丢失都会让 `/readyz` 返回 not-ready。真实本机 18 项检查全过，4-case verifier 还分别注入 drift、未知表和缺 receipt。这个故事适合回答迁移、灰度和可运维性问题，但不能夸成在线双写、自动清库或合规擦除。

v6.18 回答“数百个本地改动如何保证提交边界可审计”。不要背文件名：生成器从 NUL-safe Git porcelain 得到候选，把每项的 status、实现/文档组、字节数、Git mode 和 SHA 与 HEAD/branch/divergence 一起签入 gitignored manifest；独立 verifier 重读工作树并复算。当前精确结果是 333 项 = 315 实现 + 18 文档，支持先提交可独立跑门禁的实现，再提交只引用实现证据的文档。要主动说清它不是 secret-history scanner 或 code review；repository owner 已确认旧 LongCat Key 在 provider 侧撤销，但这仍是 owner attestation，不是 provider 签名回执，真实 DeepSeek API 调用也不是撤销证据。

v6.19 回答“任务失败后如何快速、可靠地定位下一步”。系统不读取 prompt 或原始异常来猜根因，而是从 tenant-scoped job 与完整 append-only event chain 生成版本化 failure signature：区分 retry pending、lease recovery、queue/execution deadline、persisted request、clarification、budget、model-output、worker-lease 和 unknown runtime 等 14 类，并给出受控 action。事件链和结果分别带 SHA，超过 1,000 项拒绝截断；HTTP 复用 read scope，CLI 只新建 0600 文件。关键边界是 `runtime_or_dependency_unknown` 仍然不知道究竟是模型、网络、数据库还是依赖，必须继续查健康状态，不能在面试中说成自动根因分析。

v6.20 再回答“如何从一堆 job 形成可核账指标”。不是直接 `COUNT(*)` 后写成 SLO：先固定单 tenant、最长 31 天且已闭合的 `[start,end)`，选择窗口内创建的 job，只消费 `created_at < end` 的 event prefix 并重建 as-of status，避免晚到 terminal 改写历史；再算 queue/run/terminal 三种延迟和 nearest-rank p50/p95/p99。success/failure/dead-letter/timeout/cancel 以 terminal count 为分母，retry/lease 以 job count 为分母，空分母返回 `null`。超过 1,000 job/10,000 prefix event 拒绝截断，输出只留聚合与双 SHA。面试必须主动说明 2 个 synthetic window 证明的是定义和实现，不是生产事故率、SLO 达成或容量。

v6.21 把这个回答向前推了一步：不再把 console exporter 叫 OTel，而是真实使用 OTLP/HTTP protobuf batch exporter。面试时要能说清三个决策：第一，endpoint/protocol 缺失时失败关闭为 degraded no-op，不悄悄改写文件；第二，用 allowlist 只导出 operation/provider/usage 与稳定 error type，不导出 prompt/tool args/session/user/decision/error message；第三，telemetry sink 失败不让业务失败，但受权状态端点必须暴露 healthy/degraded 和计数。loopback artifact 的独立 protobuf 解码能证明协议与隐私投影，不能证明远程投递、告警或 SLO。

v6.22 再把“operator 可单独告警”落实成确定性规则，而不是用一句话带过：terminal failure、queue wait p95、retry rate 各有 20 个样本门，OTLP 状态单独处理；任何样本不足都是 `insufficient_data`，不能因 1 个成功任务显示绿色。整体优先级是 firing 高于 insufficient，再高于 healthy；规则、policy 和两个 source 都有 SHA，4-case verifier 独立重算。面试必须主动说明这些阈值只是作品集固定策略，没有生产 baseline、连续窗口、迟滞、Alertmanager delivery 或 incident outcome，所以不能写“搭建生产 SLO 告警平台”。

v6.23 回答“你说接了真实模型，Key 怎么进程、实际用了多少、结果凭什么可信”。不要回答“放 `.env` 然后看输出”：opt-in runner 只接受 owner-only regular 0600 CSSwitch active DeepSeek profile、credential-free HTTPS、显式 model 和费用确认；Key 只在 context manager 内进入 `DPSK_*`，不进 CLI、Agent message、repr 或 artifact。一次固定三里屯运行首轮通过，1 个 LLM call 实报 53 input + 1411 output = 1464 token、canonical execution 约 28.9 秒，quality hard gate 和六项 acceptance check 均通过；独立 verifier 复算 observation/quality/budget/receipt SHA，三份 0600 工件 Key 精确命中 0。必须同时说清：这是一次 operator observation，不是成功率、延迟分布、签名 provider receipt、账单金额或服务端 credential rotation/KMS。

v6.24 回答“Dockerfile 能 build 和别人真的能复现有什么差别”。正式 tag 先与 package/service 双版本源对账，镜像再以 UID 10001、只读 rootfs、易失 runtime、无 capability 和 no-new-privileges 启动；health、readiness、OpenAPI 版本、固定 synthetic plan 与 request ID 全过后才登录 GHCR，最后同时保存 release tag、commit SHA tag 和 registry digest。构建阶段没有 provider/control token，OCI license 也明确是 `NOASSERTION`。在 tag workflow 真正成功前只能说 release contract 已实现；成功后也只能说“可拉取、可复现的单实例 mock/synthetic 镜像”，不能偷换成公网 API、生产部署、多架构或 SLA。

这仍不是完整生产可观测性或成本治理：wall-clock checkpoint 不能强杀已经阻塞的网络调用，token gate 在 provider 不回报 usage 时无法生效；v6.23 虽记录一次 provider-reported token，但没有价格版本、cache 计价拆分、tenant 金额账户、跨实例全局预算、invoice reconciliation 或服务端密钥轮换。v6.21 只在本机 loopback receiver 完成一次 synthetic OTLP 协议验收；v6.22 也只是把单个 closed window 和当前 sink 状态转成 deterministic decision，没有远程 collector 回执、多实例汇聚、持续 scrape、迟滞/抑制、告警 delivery、provider freshness、audit retention 或成本看板。正确下一步是接入经授权的远程 collector/metrics backend，再用连续可比较窗口和实际处置 outcome 校准阈值。

### Q25. 最大技术债是什么？

不是 UI。v6.23 已能从 durable evidence 生成 diagnosis/workload/OTLP/告警快照，并完成一次显式 CSSwitch handoff 的 DeepSeek usage/质量验收；但当前最大的证据债仍是真实 participant/report 为 0。用户无法提供真实试用者时，只能继续标为 synthetic，不能用多个模拟账号冒充真人分母。技术上还缺合法 live POI/路线与天气部署验收、远程 collector/metrics backend、多实例 event store、真实告警投递和处置 outcome。其次是托管 purge/备份删除证明、服务端 credential 过期/轮换/撤销、外部 IdP/动态 RBAC、数据库 RLS、入口 abuse protection、跨实例全局准入/调度、tenant 金额预算和 audit retention。副作用仍缺真实 provider 查询 acceptance、补偿 operation、客服 handoff 和签名回执。

### Q26. 你怎么评估 BM25 改动？

先冻结 legacy BM25，再用同一 19-case synthetic golden set 比较 top-5。逐例保存返回 POI、首个相关 rank、Recall 和延迟。新查询层没有提高 HitRate@5 或 MRR@5，但 Macro Recall@5 从 0.974 到 1.000，top-5 地点多样性从 0.705 到 1.000；收益来自领域词扩展和 POI 去重。样本小且是 synthetic，所以不能写成“RAG 准确率 100%”。

### Q27. 为什么现在不直接加向量库和 reranker？

当前 area anchor 已让 legacy BM25 在 19 条样本上达到 HitRate@5=1.000，尚未证明 dense retrieval 的成本有必要。正确做法是先积累真实同义改写和坏案例，再比较 BM25、BM25+dense、rerank 的 Recall/NDCG、延迟和成本；不能因为面经问 hybrid 就先堆模型。

### Q28. 30 天前用户说在北京，现在说在上海，怎么处理？

不能简单 `UPDATE value, mention_count +1`。BJ-Pal 把它视为同一 key 的新 revision：同值才强化；用户在显式记忆入口确认“上海”时替换“北京”、revision +1、mention count 重置。若只是模型推断出上海，系统记录 hash-only `conflict_rejected`，不覆盖已确认值。临时城市还应带 `expires_at`，过期后自动退出 Planner 上下文。

### Q29. Memory 的“忘记”和“删除”有什么区别？

soft forget 是可逆停用，便于用户重新明确记录；hard delete 会删除当前状态及该 key 的所有审计 hash。事件行禁止 UPDATE，但允许由 hard-delete API 成组清除，因为隐私删除权优先于永久审计。当前只证明本机 SQLite 行被删除；若有云端副本或备份，还必须提供备份 retention 与删除证明。

### Q30. 你如何证明并发链路稳定，为什么不能叫生产压测？

`run_http_benchmark.py` 用有界 semaphore 经 FastAPI ASGI 应用并发请求 `/v1/plans`；v6.11 的 `run_socket_http_benchmark.py` 另起 Uvicorn 子进程，只绑定 `127.0.0.1`，经真实 TCP 请求同一主链。两者都逐请求保存 status、request ID 回显和 latency，独立 verifier 从 raw requests 重算错误率、吞吐和 nearest-rank p50/p95/p99；socket 工件还必须证明临时 runtime、readiness 和优雅退出。首次用 `terminate()` 得到退出码 `-15` 时门禁拒绝，改为 `SIGINT + wait` 返回 0 后才接受，这比只看 20/20 更能说明失败标准。

它仍不能叫生产压测：虽然已有 localhost socket，但没有 TLS/反向代理、远程网络、真实模型/provider、多进程/多实例、稳定隔离的测试机和线上流量分布。绝对延迟不做 gate，只把零错误、ID 完整、进程生命周期和 artifact 可验真作为回归门。

### Q31. running job 的取消为什么不是直接改成 cancelled？

因为 worker 可能仍在写结果或执行不可中断的模型调用。BJ-Pal 先在同一状态行写 `cancel_requested_at`，Application Service 在 Planner 前后和 Probe 后检查；worker 完成事务也让取消优先于 success/retry。若 worker 失联，lease 过期扫描收敛为 cancelled。它是 cooperative cancellation，不是线程强杀；当前 adapter 没有 cancellation token 时，单次外部调用仍要等返回。

### Q32. dead-letter 重放为什么创建新 job？

原 job 的 attempt、错误和事件是事故证据，直接改回 queued 会破坏历史，也让重放次数不可审计。当前 failed/dead-lettered/timed-out replay 在一个事务内给原 job 追加 `replay_requested`，创建 attempt=0 且带 `replayed_from_job_id` 的新 job；新 job 继承 priority 与 deadline 秒数策略，但获得新的绝对截止时间。独立 `Idempotency-Key` 防止操作员重复点击，普通 submit 不能复用 replay key。

### Q33. deadline、lease 和 HTTP timeout 有什么区别？

lease 回答“当前哪个 worker 暂时拥有任务”，到期后允许恢复或 fencing；job deadline 回答“这项任务最晚何时还值得执行”，它随 job 持久化，到期进入 `timed_out`；HTTP/SSE timeout 只限制一次网络连接等待时间，不改变 durable job。BJ-Pal 在 claim、heartbeat、finish、retry 和扫描事务里统一结算 deadline，并按 `cancel_requested_at` 与 `deadline_at` 的先后解决竞态。它仍不会中断已经进入的单次模型/provider 调用，主动中止必须由 adapter 支持。

### Q34. 一个静态 Bearer token 算鉴权完成了吗？

v4.9 的单一 token 只关闭了匿名入口，不能算权限系统。v5.9 进一步把静态哈希凭证映射成服务端 principal/tenant，并按 `jobs:submit/read/control/replay` scope 和 `max_priority` 授权；job、事件、游标、取消、重放、continuation 与幂等键都 tenant-scoped，跨 tenant 统一 404。v6.0 又要求同 tenant principal 共享 active/rate policy，并把 admission audit 限定为同 tenant read。原 token 不写进 registry、job/event 或响应；非法 registry 503、错误凭证 401、越权 403、quota 拒绝 429。

但它仍不是企业 IAM：没有 OAuth/OIDC、动态角色、token 过期/轮换/撤销、数据库 RLS、入口 abuse protection、跨实例 quota、加密或多实例验证。准确表述是“实现了可独立验证的静态身份感知控制面与单机 tenant admission”，不能说“完成企业级 RBAC/分布式限流”。

### Q35. 你说接了 Open-Meteo，为什么又说不是真实天气能力？

要区分“真实 provider adapter 代码”和“本项目已完成 live acceptance”。代码已经实现官方 forecast schema、明确日期解析、16 天 horizon、timeout、429/5xx/4xx、单位漂移、TTL/stale、attribution，并让 Planner/Probe 共用快照；日期含糊时 fail closed，不拿今天冒充周末。公开 CI 则只读取 synthetic fixture。原因是免费端点仅限非商业用途，而作品集具有宣传属性。没有商业 key 或自托管环境并运行 opt-in smoke 前，只能写“实现 Open-Meteo adapter 和 offline contract”，不能写“已上线实时天气”。

这不是保守措辞，而是工程能力：配置错误会 fail closed，artifact 也固定 `live_provider_accepted=false`，避免代码存在就被误当成授权、可用性或准确率证据。

### Q36. 局部重规划为什么不能把失败 POI 换成评分最高的同类？

数据库大类不等于活动语义。完整 demo 曾把排队的正餐换成高分咖啡馆：接口成功、POI 不重复，但用户仍没吃到正餐。v5.1 因此先用 `ReplacementPolicy` 做 hard eligibility：meal 保持正餐，snack/rest 排除正餐，weather 则允许跨类但必须有遮蔽；之后才 ranking。

事件保存 raw、去重后、身份排除后、语义过滤后的候选数和策略版本。若最终为零就 `warn_only`，不能拿不合规候选伪造 reroute 成功。边界是这些仍是确定性规则，没有真实用户接受率时不能声称“重规划满意度提升”。

### Q37. 只换一个 POI，为什么还要重算完整路线？

中间站变化会同时让两条 leg 失效：进入新站和从新站去下一站。历史实现只构造新 `Step`，所以新站路线为空，下一站却静默保留旧 POI 的路线。v5.2 用 `route_refresh_v1` 把它改成 snapshot 更新：先清空所有旧 leg，再对完整短计划逐对重算；坐标或 lookup 失败就保留空值并返回 partial warning，不跨站连线。

这里选择全量而非增量，是因为典型方案只有 3-6 条 leg，正确性和可审计性比省几次本地 lookup 更重要。返回的来源是 `cached/estimated`，不能讲成实时导航。

### Q38. LLM 已经给了 start_time，为什么还要程序重排？

因为 LLM 的原始时间只覆盖停留，没有看到之后才查询出的 route。默认 demo 曾出现 14:00 停留 60 分钟、下一站 15:00 到达、但入站还需 5 分钟的重叠。v5.3 把 start 明确定义为到站时间，由确定性 Schedule Reconciler 做级联计算。

如果总时长超窗，程序只在 `minimum_dwell_v1` 下压缩柔性停留；仍放不下就返回 `overrun`，不自动删站。路线缺证据时返回 `partial`，也不把 0 分钟伪装成确定成本。这个设计把“生成看起来合理的时间”变成可验证约束。

### Q39. 模糊输入为什么不直接交给 LLM 猜，为什么也不每次都追问？

两端都不可靠。历史主链会把“还是上次那个地方”静默套用默认五道营并生成完整方案；反过来对预算、片区、时长逐项追问又会造成高 false clarification。v5.4 只把不可逆且执行关键的缺口升级为 `clarification_required`：无法解析的历史/序号指代、家/公司附近但无位置、文本片区和结构化字段冲突。普通缺省片区则记录 `proceed_with_assumptions`，让用户可覆盖但不阻塞。

这个 gate 在同步和 durable submit 的 Planner/tool fan-out 之前运行，返回一个问题和 2-3 个选项。20 条 synthetic golden case 当前 trigger rate 0.350、false clarification rate 0、required recall 1.000、补充后 gate executability 1.000。最后一项只表示通过需求门控；没有真实用户样本时不能声称“需求理解准确率 100%”。

### Q40. 已经有 Requirement Gate，为什么还要 Constraint Ledger？

Requirement Gate 只回答“请求是否足够明确到可以开始”，不保证文本里的具体参数真的进入 Planner。v5.4 后仍能复现：用户写 2 人、15:00、3 小时、人均 100、不吃辣，HTTP 返回 200，但 Planner 收到 3 人、14:00、4.5 小时、空预算和空忌口。这是静默约束漂移，输出文案再好也不算满足需求。

v5.5 把抽取放在唯一 preflight：每个支持字段记录文本值、最终值、来源、evidence 和合并结果；文本与表单显式值冲突时返回 409，忌口取安全并集，文本派生值不伪装成 `provided_fields`。30 条 synthetic case 的 extraction、constraint preservation、conflict recall、rewrite coverage 和 durable round-trip 当前均为 1.000，false extraction/conflict 为 0。这个数字只能说明小型确定性规则集，不是“中文意图识别 100%”。

### Q41. 返回 409 之后，客户端为什么不能直接再传一个字符串？

因为答案必须绑定到“哪份原请求、哪次 decision、哪个字段、哪些候选”。只回传“用 4 人”既无法审计，也可能被套到已变化的问题上；客户端重建请求还容易遗漏原 deadline/priority、字段来源或其他约束。v5.6 因此把原请求、typed options、delivery policy 和 decision SHA 存成有 TTL 的 continuation，答案形成 `clarification_resolution_v1` 后重新进入同一 preflight。

相同答案重放返回缓存计划或同一 job，不同答案被 409 fencing；若还有第二个冲突，父 continuation 固定指向同一个下一问。同步 planning 没有真实副作用，因此这里不宣称 exactly-once；真实预订仍需要 operation id/receipt。16 条 synthetic case 的一步续跑、有效值、指纹、恢复、重放和不同答案冲突指标为 1.000，同冲突复发率为 0，但这不是开放域多轮满意度。安全边界也要直说：当前是单机明文 SQLite，sync continuation ID 是短期 capability，不是用户身份。

### Q42. 有 priority 后，为什么普通任务不会一直饿死？

只按固定 priority 排序会饥饿，所以 v5.8 用 `priority_aging_v1`：任务真正可执行后，每等待 60 秒把有效优先级提升 1，最高 9。v6.0 的 `tenant_fair_priority_aging_v2` 保留有效优先级第一排序，再在同级选择 `last_claimed_event_id` 最小的 tenant，最后按 eligible time FIFO。默认 900 秒 deadline 下，priority 0 在 9 分钟后达到 9；retry backoff 到期前不算排队；claim event 保存 priority/fairness policy、tenant cursor、base/effective priority、eligible time 和 queue wait，4-case verifier 会从 raw candidate 独立重算。

这不是严格 SLA：没有空闲 worker 时任何任务都不会启动。高有效优先级仍先于低优先级，tenant 轮转只在同级生效；新 tenant 会先获得一次机会，SQLite state 不跨实例。因此简历应写“实现 priority aging 与同级 tenant 轮转的可审计单机调度策略”，不能写“严格公平”或“保证 9 分钟内执行”。

### Q43. 租户限流为什么放在 job repository，而不是网关或 Redis？

本轮问题是“同一 durable store 内不能让一个 tenant 的合法新 job 占满 active 槽”，所以准入判断必须与 job INSERT 原子。`tenant_admission_v1` 在 `BEGIN IMMEDIATE` 中先结算 deadline，再计算 queued/running 与过去 60 秒 accepted new job；submit、manual replay、job continuation 共用它，匹配幂等重试绕过 quota 但写 reuse audit。active/rate 拒绝分别返回稳定 429 code，rate 还返回 `Retry-After`；admitted/rejected/reuse 都进入 tenant-scoped append-only audit。

Redis 或网关适合多实例和 raw-attempt abuse protection，但当前没有多实例运行证据，引入它会制造新的运维主张。准确边界是：这里实现的是单 SQLite 文件的原子 admission，不是公网分布式 rate limiting；被拒绝 attempt 不计 accepted 窗口，audit 无 retention，后续产品化仍需入口 limiter、跨实例协调和存储治理。

### Q44. 为什么 operation 不复用 durable job 的自动重试？

planning job 失败后重算通常不会改变外部世界，预订重试却可能产生两张订单。v6.2 因此把副作用拆成独立状态机：批准只绑定一个精确 operation，worker 只尝试一次；调用后结果不明或 execution lease 过期进入 `uncertain`，不自动 reclaim。有 provider operation ID 时只能执行绑定到 operation/request/provider/reference 的只读 lookup；无 reference 时人工处置。收益是不会用“恢复能力”掩盖重复写风险。

### Q44.1. 为什么 reconciliation 不能顺便做补偿？

reconciliation 是读操作，只回答“原写操作到底发生了什么”；补偿是新的写操作，可能产生取消费、库存变化或客服影响。把两者合并会让一次状态查询隐式改变外部世界。正确设计是新增 `restaurant_cancellation`：绑定原 `compensates_operation_id`，重新获取取消 quote/条款，生成新的幂等键、approval fingerprint 和 receipt，并再次要求职责分离。v6.2 只记录了这条边界，没有假装已经实现取消。

## 5. 代码追问路线

面试前至少能手讲这五条调用链：

1. `http_api.app.create_plan → PlanningPreflight → RequirementNormalizer → ConstraintNormalizer → planner.plan → probe_plan`。
2. `planner → SQLitePlanningDataProvider.collect → POI/summary/retrieval futures → snapshot merge`。
3. `POST planning-jobs → credential hash match → scope/tenant/priority/admission policy → repository atomic quota + audit + submit → claim_next priority/tenant fairness → heartbeat → retry/dead-letter/cancel/timed_out 或 artifact/event`。
4. `PlanResult.to_dict → PlanCreateResponse.model_validate`。
5. `evals.run_public → artifact write → verify_artifact recompute`。
6. `evals.run_retrieval → legacy/candidate → per-case metrics → comparison artifact`。
7. `explicit memory intake → upsert_memory → conflict/revision event → confirmed active prompt`。
8. `run_http_benchmark → ASGI /v1/plans → raw requests → independent verifier`。
9. `events/stream → Last-Event-ID → repository.events → SSE id/event/data`。
10. `GET jobs(status) → lightweight cursor list → cancel/replay → lineage event/new job`。
11. `replan_step → constraint filter/rank → replace Step → refresh_plan_routes → route_refresh_v1`。
12. `route snapshot → reconcile_plan_schedule → duration/time adjustments → schedule_reconcile_v1`。
13. `evals.run_requirements → per-case decision/follow-up → metrics → independent verifier`。
14. `evals.run_constraints → per-field text/effective/conflict/round-trip → metrics → independent verifier`。
15. `409 → ClarificationRepository.issue → typed option → resolve_request → claim lease → same PlanningPreflight → cached plan / idempotent job`。
16. `evals.run_clarifications → raw request/decision/options/resolution → independent fingerprint/metric verifier`。
17. `X-Request-ID / job_id → capture_execution + execution_budget_v1 → planning/LLM/tool spans → execution_observation_v2 → independent observability/budget verifier`。
18. `submit priority/tenant → available_at → effective priority aging → tenant last-claim cursor → transactional claim/state event → independent scheduling verifier`。
19. `submit/replay/job continuation → atomic active/rate admission → 202 or 429/Retry-After → tenant admission audit → independent access-control verifier`。
20. `POST operations → quote/request/approval SHA → distinct approver → sandbox worker claim once → receipt 或 uncertain → provider-bound read-only reconciliation → independent side-effect verifier`。

不要背文件数量；要能解释每层谁拥有状态、异常在哪里转义、什么会持久化。

## 6. 现场验证命令

```bash
# 从零数据和全门禁
make bootstrap-demo PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python

# 重新生成并独立复核当前两提交边界
make audit-release-candidate PYTHON=.venv/bin/python

# 只演示 HTTP 契约
make api-smoke PYTHON=.venv/bin/python

# 只演示 durable job 生命周期
make job-smoke PYTHON=.venv/bin/python

# 生成并验公开评测产物
make eval-public PYTHON=.venv/bin/python

# 对比 legacy BM25 与当前可解释检索器
make eval-retrieval PYTHON=.venv/bin/python

# 复核需求门控的触发、误澄清与补充后状态
make eval-requirements PYTHON=.venv/bin/python

# 复核 typed 约束抽取、冲突、rewrite 与 durable round-trip
make eval-constraints PYTHON=.venv/bin/python

# 排练 409 后从原请求继续，并复核 16-case continuation artifact
make demo-clarification PYTHON=.venv/bin/python
make eval-clarifications PYTHON=.venv/bin/python

# 复核请求级 span tree、调用/token 汇总、隐私标记排除与 SHA
make eval-observability PYTHON=.venv/bin/python

# 复核工具日志隐私投影、append-only SHA chain、reset 与 legacy hiding
make eval-tool-audit PYTHON=.venv/bin/python

# 复核 priority ordering、aging、queue wait 与 retry backoff exclusion
make eval-scheduling PYTHON=.venv/bin/python

# 复核职责分离、approval binding、receipt 与 uncertain no-retry
make eval-side-effects PYTHON=.venv/bin/python

# 跑并独立复核进程内 ASGI 与 localhost socket 并发回归
make benchmark-http PYTHON=.venv/bin/python \
  PERFORMANCE_REQUESTS=50 PERFORMANCE_CONCURRENCY=8
make benchmark-socket-http PYTHON=.venv/bin/python \
  PERFORMANCE_REQUESTS=50 PERFORMANCE_CONCURRENCY=8

# 检查补丁格式
git diff --check
```

现场不要运行真实 LLM 或外部 provider，除非凭证、预算、网络和 fallback 都已提前验收。

## 7. 简历 bullet 模板

### AI 应用研发版

- 将北京活动规划原型重构为受控 Plan-and-Execute 工作流：LLM 仅从 grounded POI 候选中完成选择/编排/解释，程序负责地理、预算、风险 Probe 与局部 Reroute，并通过可解释证据因子避免将模型自评分包装为成功概率。
- 设计 L1/L2/L3 离线行为回归与可验证 artifact，保留 raw cases、运行环境、数据 provenance 和双 SHA-256，由独立 verifier 复算摘要。
- 设计执行前 Requirement Gate，将模糊请求分为直接执行、透明默认假设和必须澄清；以 20 条 synthetic case 约束误澄清率、关键缺口召回和补充后 gate executability，并在同步/持久任务 fan-out 前 fail closed。
- 从 HTTP 主链复现自然语言硬约束静默退回 schema 默认值，设计 typed Constraint Ledger 统一抽取、字段来源、冲突与安全合并；以 30 条 synthetic case 和独立 verifier 复算 extraction、preservation、rewrite 与 round-trip，不把固定规则成绩包装成开放域 NLU。
- 将一次性澄清 409 重构为有 TTL 的 SQLite continuation：用 request/decision SHA 绑定 typed resolution，以 lease、结果缓存和内部幂等键支持同步/job 重放与冲突 fencing；16 条 synthetic case 全量复算，明确不等同于真实多轮满意度或 exactly-once。
- 建立 19-case synthetic UGC golden set，对比 legacy BM25 与领域扩展/POI 去重检索器，使 Macro Recall@5 从 0.974 提升至 1.000、top-5 地点多样性从 0.705 提升至 1.000，并保留逐例结果与适用边界。
- 重构长期用户记忆状态机：显式来源、确认/过期 gate、同值强化、异值 revision、未确认冲突拒绝，以及 soft forget/hard delete；用 hash-only 事件保留可回放证据并避免复制敏感原值。

### 后端/Agent 平台版

- 基于 FastAPI + Pydantic 建立同步/异步双入口，并设计 SQLite durable job：幂等提交、事务 claim、lease heartbeat、过期 owner fencing、有限指数退避、dead letter、协作取消、持久 deadline、lineage replay 与带 aging 的 0-9 优先级；同步与 worker 复用同一 Application Service，job 控制面默认由 Bearer token fail closed 保护。
- 设计 append-only job event log，将 heartbeat、retry、cancel、replay、lease 回收、成功和失败与状态变更同事务落库；JSON cursor 与 bounded SSE 共用 durable event，支持 `Last-Event-ID` 断线续读。
- 从 tenant-scoped durable job 与完整事件链构建 `job_incident_diagnosis_v1`：以 14 类稳定 failure signature 区分 queue/execution deadline、retry/lease/model-output/budget 等边界，未知 provider/runtime 错误不提升为根因；用双 SHA、1,000-event fail-closed、跨租户 HTTP 测试和独立 synthetic verifier 约束可复算性与隐私最小化。
- 设计 closed-window `durable_workload_health_v1`：从 `created_at < end` 的 event prefix 重建 as-of status，显式固定 status/terminal/job 分母，复算 queue/run/terminal nearest-rank p50/p95/p99 及 retry/timeout/dead-letter rates；1,000 job/10,000 event 超限拒绝截断，以双 SHA、tenant-scoped HTTP、0600 CLI 和 mixed/empty independent verifier 约束可审计性，并明确不称生产 SLO。
- 将 workload snapshot 与 payload-free OTLP sink health 组合为 `operational_alert_snapshot_v1`：为 failure/queue/retry 固定 20 样本门，输出 firing/healthy/insufficient/disabled 四态并绑定 source/policy/artifact SHA；用 4-case independent verifier 重算规则和总状态，主动说明它没有生产 baseline、连续窗口或告警 delivery。
- 设计 `priority_aging_v1`，从 eligible time 每 60 秒提升有效优先级并以 FIFO 解同分，retry backoff 不累计等待；claim event 固化 queue-wait 证据，3-case 独立 verifier 重算抢占、抗饥饿与 backoff 排除。
- 抽象类型化 data provider，将多类 POI/UGC 查询改为独立并行结果 + 单点 merge，显式返回 freshness、bookable、provider reference 和 partial failure。
- 实现 offline-first Open-Meteo adapter：三种 usage mode fail closed，覆盖 timeout/429/schema、共享 TTL/stale cache、attribution 和 Planner/Probe 同快照；用独立 verifier 证明离线契约，同时不把 synthetic fixture 冒充 live acceptance。
- 从端到端 demo 定位“meal→cafe”语义回归，设计 `ReplacementPolicy` 将硬资格过滤与 ranking 分离，并把四阶段候选计数透传至 HTTP/durable artifact。
- 将 per-request 数据线程池收敛为固定 8-worker executor，并把 plan trace 改为原子单事务替换；建立进程内 ASGI + 独立 Uvicorn/localhost TCP 两层逐请求 evidence，独立复算错误率、request ID、readiness 与优雅退出。
- 将历史共库的 `plan_trace + plan_outcome + calibration join` 收敛为单一 plan-evidence store；设计 dry-run 默认、显式确认、count/logical SHA、receipt、quick-check、mode-0600 atomic publish 与 WAL fail-closed 的非破坏迁移，避免 7.5 万条历史在改路径后静默消失。
- 将迁移机制下沉为领域描述驱动的 verified-copy 内核，并把 3.3 万条 prediction feedback 非破坏迁到独立 store；保留稀疏 ID、未配对 NULL 和 actual 更新语义，以 4-case verifier 复算 copy/receipt/领域隔离及迁移后 UPDATE/DELETE，同时明确旧行未擦除、11 条 actual 不构成校准结论。
- 设计 `execution_observation_v2 + execution_budget_v1` 贯通同步 request ID 与 durable job ID：输出隐私最小化 span tree、调用/业务计数和 provider-reported token completeness，并在 LLM/data/tool N+1 前终止、统一 transport retry、对 token/time 超限返回 hash snapshot；以独立 3-case observability + 6-case budget verifier 防止篡改和 mock token 伪造。
- 将任意 params/response/异常原文落库的旧 logger 收紧为 `tool_call_audit_v2`：敏感 key/value、PII-like 标记与未知自由文本只留有界投影，异常只留稳定 code；以 session sequence + SHA chain、append-only trigger 和 reset marker 约束本地完整性，并用 5-case 独立 verifier 拒绝重签泄漏、截断链与伪造存储隔离。
- 将 Planner 原始 JSON 视为不可信输入：以 strict schema 和请求级候选映射校验 persona/area、POI ID/名称/类别、去重、depart/时间序列，首次失败只允许一次 budget-bound provider repair，仍失败则同步 502 或 durable terminal no-retry；用 13+4 条 synthetic artifact 和独立规则实现拒绝同错同过。
- 从真实配置模型验收中定位 798 候选覆盖与忌口证据缺口：补齐核心片区 replacement gate，将 `no_spicy/light_diet` 餐饮收紧为 structured positive UGC evidence filter；重新运行 3 个固定 Pro 场景并绑定脱敏 plan projection，独立复算 32/32 个必需约束代理，同时明确它不是成功率、人工质量或用户 outcome。
- 将预订从可重试 planning job 拆为 `approval_gated_operation_v1`：绑定 action/quote/terms 与审批指纹，强制 requester/approver 职责分离、tenant-local 幂等、单次 worker 执行、receipt SHA 和 uncertain no-retry；用 provider-reference-bound 只读 lookup 收敛不确定态，以 5-case artifact 独立复算 12 项安全契约，明确仅限 sandbox。
- 为知情试用实现 retention-due 原子清除：仅对已冻结到期 cohort 执行排他事务，验证 snapshot/hash chain 后按外键顺序删除目标行，恢复 append-only trigger 并检查外键，保留 hash-only receipt；以 WAL fail-closed、故障注入 rollback、跨 cohort 隔离和收据防篡改测试约束边界，明确不等同于取证级擦除或备份删除证明。

### 不能写的版本

- “接入真实高德/美团，准确率 100%”：当前公开证据不支持。
- “22 个 Agent 分布式协作”：当前不是远程 Agent 系统。
- “ECE 0.1089 证明预测准确”：缺当前公开 paired outcome artifact。
- “生产级高并发”：已有单进程 in-process ASGI 与 localhost TCP、mock LLM、synthetic 数据的回归；没有 TLS/反向代理、远程网络、多实例、真实 provider/model 和线上指标。
- “自动学习用户且越用越准”：正常 Planner 不写记忆，也没有真实用户长期效果证据。
- “模型幻觉率为 0 / 自动修复率 100%”：当前 13+4 条是 synthetic/scripted 契约样本，不是线上真实模型分布。
- “已完成合规删除/不可恢复擦除”：当前只验证单个本地 SQLite live-table 清除事务，secret/backup disposition 是 operator attestation。

## 8. 面试前自检

- [ ] 90 秒内讲清问题、架构、一个难点和证据边界。
- [ ] 能解释为什么选择 Python Protocol/SQLite，而不是只报技术栈。
- [ ] 能画出 job 状态机和四类 ID。
- [ ] 能说出 at-least-once、lease 过期和 side-effect receipt 的区别。
- [ ] 能解释 status reconciliation 是只读证据链，为什么补偿必须是新的审批式写 operation。
- [ ] 能区分真实 API、真实数据、可预订报价和真实订单。
- [ ] 能区分 trace、metric、eval、benchmark 和业务 outcome。
- [ ] 能解释为什么 mock token 是 `unavailable`，以及本地 observation 不等于 OTLP/生产监控。
- [ ] 能解释 prompt schema、strict runtime validation 与一次 budget-bound repair 的区别，并主动说明 1.000 不是幻觉率。
- [ ] 能解释 Memory 同值强化、异值冲突、确认、过期、soft forget 与 hard delete。
- [ ] 能现场运行 `api-smoke`、`job-smoke`、`eval-retrieval`、`eval-requirements`、`eval-constraints`、`eval-side-effects`、`eval-weather` 与 `benchmark-http`。
- [ ] 能解释 cooperative cancel 与强杀调用的边界，以及为什么 replay 必须创建新 job。
- [ ] 能区分 lease、job deadline 与 HTTP/SSE timeout，并解释 cancel/deadline 竞态。
- [ ] 能解释静态 token 只关闭匿名控制面，不等于身份/RBAC/租户隔离。
- [ ] 能解释 resolution 为什么必须绑定 decision SHA、同答案重放与不同答案冲突，以及 capability ID/明文原请求的安全边界。
- [ ] 对尚未实现的外部身份/RBAC、多实例 store、真实 provider 查询 acceptance/补偿 operation、天气 live acceptance 和下一类 live provider 直接承认并给出正确顺序。
