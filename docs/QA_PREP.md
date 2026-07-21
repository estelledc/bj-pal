# BJ-Pal 演示与答辩 Q&A

> 本文只保留当前公开仓库能支撑的回答。求职面试的系统设计追问见 [INTERVIEW_GUIDE.md](INTERVIEW_GUIDE.md)。

## Q1. 这和“GPT 套地图 API”有什么区别？

核心区别不是 prompt，而是边界：候选、约束、风险、状态和数据来源由程序控制；LLM 只从受控候选中做选择、排序和解释。系统还保留初版/终版、局部 reroute、provider warnings 和 artifact，而不是只输出一段文案。

## Q2. 现在用的是真实数据吗？

公开默认不是。`demo` profile 和默认天气 fixture 都是 deterministic synthetic；`real-cache` 只允许本机 POI 缓存，其他数据仍可能 synthetic/estimated，因此是 `mixed`。每个结果返回 per-domain provenance 和 `bookable=false`。Open-Meteo live adapter 存在不等于本项目已完成商业授权或 live acceptance。

## Q3. clone 后能否运行？

可以运行公开离线链路：

```bash
make setup
make bootstrap-demo PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
make audit-release-candidate PYTHON=.venv/bin/python
```

这证明控制流、契约和回归可复现，不证明真实世界结果。

## Q4. 为什么叫 Agent？

更准确的工程定义是“确定性工作流 + 有界 LLM 节点”。Planner 能基于目标和候选做决策，Probe 根据环境信号调整计划；但它不是多个远程 Agent 自由对话系统。

## Q5. 为什么不增加更多 Agent？

当前瓶颈是数据可信、失败恢复和副作用安全。历史 ToT 也只是 3 个同构 planner 分支，不是 3 个 Agent。v6.8 的 3-case deterministic mock 对照中，多分支质量提升率和语义输出变化率都是 0，LLM/data 调用都是 3 倍，默认服务端预算还会拒绝第二个 data batch，因此主链保持单分支。这个结论只适用于当前 mock/数据/规则 scorer；拿到真实 badcase/outcome 后应同口径重跑。

## Q6. LLM 输出不合法怎么办？

HTTP 输入和 Planner 输出是两条不同的信任边界。v6.9 的 `model_output_contract_v1` 在构造 `Plan`、查路线和写 trace 前检查 strict 字段/类型、请求 persona/area、当前候选 ID/名称、重复地点、depart/index 和时间序列；未知字段、类型强转和本地补残 JSON 都不能静默通过。首次失败最多调用同一 provider 修复一次，并和首次生成共享 request LLM budget；第二次仍失败时同步/澄清 continuation 返回脱敏 502 `invalid_model_output`，durable job terminal failed 且不走普通 retry。

当前 12 条 hand-authored adversarial payload 与 4 条 scripted lifecycle case 全通过，只证明这些失败边界和调用次数，不代表真实模型幻觉率、修复率或方案质量。

## Q7. 某个数据源失败怎么办？

候选类别独立返回。单类失败记录为 retryable optional `ProviderIssue` 并进入 `data_warnings`；全部候选为空则 fail closed。缺失数据不会按零成本或成功处理。

## Q7.1. 为什么正餐不会被换成咖啡馆？

Replanner 不再直接对数据库同类候选排名。`constraint_preserving_replan_v1` 先做 hard filter：meal 保持正餐、snack/rest 排除正餐、雨天户外跨类切有遮蔽地点；事件返回每个过滤阶段的候选数。没有合法替补时只 warning，不伪造成功。

## Q7.2. 换了中间 POI 后，下一段路线会不会还是旧的？

不会静默沿用。v5.2 把路线当作完整 plan snapshot：换点后先清空旧路线，再重算所有真正相邻的 POI leg；进入新站和离开新站的两段都会更新。若缺坐标或 lookup 失败，字段保持空，`route_refresh_v1` 返回 partial warning，也不会跨过缺数据站点连线。当前来源只有 `cached/estimated`，不是实时导航。

## Q7.3. 为什么计划里的时间不会再和路程重叠？

`start_time` 统一表示到站时间。路线刷新后，Schedule Reconciler 按“上一站到达 + 停留 + 当前站入站路程”级联重排。超出用户窗口时先按版本化最小停留压缩 rest/culture 等柔性时长；仍放不下就返回 `overrun`。缺路线证据则是 `partial`，不会把 0 分钟当已验证成本。

## Q7.4. 用户说“还是上次那个地方”，系统会不会直接猜？

不会。`requirement_gate_v1` 在 Planner/tool fan-out 前识别无法解析的历史/序号指代、相对位置缺参考和片区冲突，返回结构化 `409 clarification_required`、一个问题及 2-3 个选项；durable submit 不会先把它排队。普通可逆缺省只记录 assumption 后继续，避免过度追问。20 条 synthetic case 的 false clarification rate 为 0，但这不是开放域或真实用户准确率。

## Q7.5. 用户文本写两个人，表单或默认值写三个人时怎么办？

`constraint_ledger_v1` 在 Requirement Gate 后把支持的文本约束映射为 typed preferences，并保存文本值、最终值、来源、evidence 和 merge outcome。文本与调用方显式人数、预算、时间、时长或 persona 冲突时返回结构化 409，Planner 不运行、durable job 不入队；忌口限制取安全并集。30 条 synthetic case 当前 extraction、preservation、conflict recall、rewrite 和 round-trip 均为 1.000，但只覆盖固定规则，不代表开放域中文理解。

## Q7.6. 409 后如何保证继续的是原请求，而且不会重复执行？

服务把原 `PlanRequest`、request/decision SHA、typed options、delivery/deadline/priority policy 和 TTL 保存为 clarification session；用户选择形成 `clarification_resolution_v1`，再进入同一个 `PlanningPreflight`。同一答案重放返回缓存同步结果或同一 job，不同答案返回冲突，并发执行由 lease owner fencing。多项冲突按层处理，父 session 固定指向同一个下一问。同步计算在结果落库前崩溃仍可能重算，因此不宣称 exactly-once；真实副作用仍需 operation id 和 receipt。16 条 synthetic case 的续跑/有效值/指纹/恢复/重放指标为 1.000，同冲突复发率为 0，但没有真实用户多轮满意度证据。

## Q8. 为什么有同步和异步两套接口？

同步接口方便短请求和现场演示；durable job 先落库、再由独立 worker 处理，可应对断线和进程中断。两者共用同一个 PlanningService，因此业务行为不分叉。

## Q9. 如何防重复任务？

`Idempotency-Key` 在 tenant namespace 内与规范化请求 hash、deadline、priority 策略绑定；同 tenant 下策略相同返回原 job，任一变化返回 409，不同 tenant 可复用同一 key。worker 用事务和 lease claim；当前仍是 at-least-once，真实副作用还需 operation id 和 receipt。

## Q9.1. 高优先级任务会不会让普通任务永远排不到？

不会只按固定 priority 排。`tenant_fair_priority_aging_v2` 先用 `priority_aging_v1` 在任务 eligible 后每等待 60 秒提升一级、最高 9；同有效优先级时先选最久未获服务 tenant，再按 eligible time FIFO。retry backoff 到期前不参与竞争，claim event 保存 priority/fairness policy、tenant cursor、基础/有效优先级和 queue wait，4-case verifier 独立复算。v5.9 的 principal `max_priority` 防越权，v6.0 的 tenant admission 防合法请求无限占槽。边界是没有空闲 worker 时仍不保证启动时间，新 tenant 有一次初始机会，也没有在线 reprioritize 或多实例全局调度。

## Q9.2. tenant 配额怎么保证并发下不超卖？这算限流吗？

普通 submit、manual replay 和 job clarification continuation 都在同一个 SQLite `BEGIN IMMEDIATE` 中检查 queued/running active 数与过去 60 秒 accepted new job 数，再创建 job 和 admission event；两个并发提交不会都读到旧计数。匹配幂等重试直接复用原 job，不消耗新 quota。active/rate 拒绝返回不同 429 code，rate 场景给 `Retry-After`，所有 admitted/rejected/reuse decision 都进入 append-only tenant audit。准确边界是“单库原子准入”，不是公网 raw-attempt rate limiter：被拒绝请求不计 accepted-submission 窗口，audit 也还没有 retention，跨实例需要外部协调存储。

## Q10. 置信度可信吗？

字段当前表示 `evidence_support_v1`，不是成功概率。它透明记录 grounding、rating、UGC、route、risk 和 profile 等因子；synthetic/mixed profile 会封顶。没有真实 outcome 配对时不报告当前 ECE。

## Q11. L1/L2/L3 100% 代表什么？

只代表 deterministic mock regression case 通过。L1 检入口，L2 检模块，L3 检分布；verifier 从 raw cases 重算摘要。它不代表用户满意度、准确率或商业转化。

## Q11.1. 现在有用户 outcome 了吗？

有“收集与知情试用机制”，没有真实样本。v6.3 为精确 plan artifact 发放限时 capability，分开收 decision/outcome；v6.4 再用 tenant-scoped cohort、精确 notice SHA、单次加入码、匿名 participant capability、退出排除和冻结 snapshot 约束试用分母；v6.6 增加到期本地清除事务。4-case/8-metric outcome 与 6-case/13-metric trial 的 1.000 都只证明 synthetic contract，不是采纳率或完成率。当前真实 participant/report 数均为 0。

## Q11.2. 为什么这些 outcome 不能直接算 ECE？

它们是整份 plan 的自报结果，不是每个 step 的同定义概率标签。项目把旧 outcome 分类为 synthetic、legacy 和 human-verified step；诊断 UI 只读取最后一种。把一个 `completed` 复制到所有 step 会制造伪校准，因此明确禁止。

## Q11.3. 5 个 participant capability 等于 5 位真人吗？

不等于。它只能证明 5 个不同匿名参与凭证各自完成了精确 notice SHA 同意，且每个 phase 最多提交一次；没有账号、实名或外部 IdP 时，同一个人仍可能持有多个凭证。项目因此把聚合称为 `self_reported_unverified`，简历和面试只能说“不同参与凭证”，不能说“5 位已验证真人”。

## Q11.4. 真实试用怎么避免 operator 自己泄露加入码或误冻结？

v6.5 的 `manage_trial.py` 不在 stdout 输出原始码；批量码只允许写入不存在的新文件，权限为 0600 且文件名被 gitignore。冻结必须精确传回 trial ID，任何 phase 未达门槛时默认拒绝，只有显式接受低样本才会生成 snapshot。它仍是本地 privileged 工具，组织者必须逐人分发并删除自己的 secret bundle 副本。

## Q11.5. retention purge 能否证明数据不可恢复？

不能。v6.6 证明的是一个本地 SQLite live-table 删除事务：已冻结且到期、精确 trial-ID/secret/backup disposition、排他锁、`secure_delete=ON`、目标行计数、trigger 恢复、foreign-key check、hash receipt 和故障回滚。WAL 模式会拒绝执行。但 `secure_delete` 和 operator attestation 仍不能独立证明文件系统快照、secret 文件、复制库或外部备份已被取证级擦除。

## Q12. 真实预订为什么还没接？

因为真实副作用不能只换一个 HTTP client。v6.2 已先实现完全离线的安全骨架：provider quote/reference/validity/terms 与 action 绑定、请求/审批职责分离、tenant-local 幂等 operation、独立 worker、side-effect receipt、append-only event/reconciliation，以及调用后不明时进入 `uncertain` 且不自动重试。已有 provider-reference-bound 只读状态核对，但 execute/lookup 都被硬限制为 `bj-pal-sandbox`，没有真实餐厅、支付或消息调用。接真实环境前仍需要供应商授权与测试环境、真实订单查询 acceptance、补偿 operation、客服 handoff、第三方签名回执、PII/secret 和 retention。

## Q12.1. 为什么副作用 worker 失联后不自动 reclaim？

纯计算 job 重算通常只是浪费算力，真实预订重放却可能重复下单。v6.2 因此把 executing lease 过期收敛为 `uncertain`，而不是沿用 planning job 的 at-least-once retry；有 provider operation ID 时只读查询并校验绑定证据，没有 reference 时交给人工处理。这是“宁可暂停确认，也不盲目重复写”的安全取舍。

## Q13. 为什么没用 LangGraph/MCP/A2A？

当前线性有界工作流用 Application Service 更容易验证；同进程 provider 用 Protocol 足够；没有远程 Agent 互操作需求。复杂审批循环、跨进程工具或独立 Agent 服务出现后再引入对应框架/协议。

## Q14. 当前最大的技术风险？

当前最大的证据风险仍是没有真实 participant/report：v6.9 完成模型输出失败关闭，v6.7 完成请求级调用/retry/实报 token/checkpoint-time budget；v6.23 又用 owner-only 0600 CSSwitch handoff 跑通一次固定 DeepSeek 场景，记录 1 call、1464 provider-reported token、约 28.9 秒与 quality gate，Key 在三份 linked artifact 中精确命中 0。但单次 configured-client observation 不是成功率、签名 provider、账单金额或真实用户结果。技术侧还缺托管 purge/备份删除证明、天气商业授权或自托管 live acceptance、POI/路线 live provider；控制面仍缺服务端 credential 过期/轮换/撤销、外部 IdP/动态 RBAC、存储层隔离、入口 raw-attempt abuse protection、跨实例全局准入/调度、tenant 金额预算和 audit retention；副作用没有真实 provider 查询 acceptance、补偿、客服 handoff 或签名回执。继续堆 UI 或 Agent 数量不会消除这些缺口。

## Q15. Docker 和 CI 到什么程度？

Dockerfile 使用 Python 3.11 slim，build 时生成 demo 数据，容器非 root 运行并配置 health check；CI 会运行测试、API/job smoke、公开 eval 并构建镜像。本机 Docker daemon 未运行时不能声称本地镜像 build 已通过。

## 答辩演练 checklist

- [ ] 60 秒说明问题、边界、主链和一个可靠性难点。
- [ ] 现场运行 `make api-smoke` 和 `make job-smoke`。
- [ ] 展示返回结果里的 `data_provenance`、`data_warnings` 和支持度因子。
- [ ] 用“还是上次那个地方”展示 Planner 调用前的结构化澄清，再用明确片区展示零额外摩擦。
- [ ] 用“2 人 / 15:00 / 3 小时 / 人均 100 / 不吃辣”展示 Constraint Ledger，再用文本 2 人、表单 4 人展示冲突 409 和零入队。
- [ ] 从该 409 选择 `use_text_value`，展示同一个请求继续、ledger source=`user_clarification`，再重复请求验证 plan/job ID 不变，并用另一答案展示 resolution conflict。
- [ ] 展示 weather provenance 与 `offline_contract_only` artifact，并主动说明未执行 live smoke。
- [ ] 运行 `make demo-trial` 与 `make eval-trials`，说明 synthetic 0.8 不是用户指标、participant capability 不是身份、purge receipt 不是取证级擦除或备份删除证明。
- [ ] 展示 `make trial-operator-help` 和 operator contract 测试，说明 0600 bundle 仍需人工安全分发/删除，purge 要冻结/到期/精确确认且 WAL fail closed，本地 CLI 不等于远程 IAM。
- [ ] 展示 `execution_observation_v2` 的 request/job correlation、span tree、token completeness、`execution_budget_v1` 和双层 SHA；运行 `make eval-execution-budget`，说明 N+1 在 body 前拒绝、mock token 为 unavailable、checkpoint 不能强杀已阻塞调用、本地 capture 不等于 OTLP/生产监控或金额成本治理。
- [ ] 运行 `make eval-otlp-export` 与 `make eval-operational-alerts`：先解释 loopback protobuf 只证明协议/隐私，再展示 20 样本门如何让小窗口保持 `insufficient_data`；主动说明 fixed threshold、单 snapshot、无 delivery/迟滞/处置 outcome，因此不是生产 SLO 告警。
- [ ] 运行 `make eval-model-output`，展示 hallucinated ID、名称错配、补残 JSON、一次修复和预算阻止第二次正文；说明 prompt schema 不等于 runtime validation，1.000 也不等于幻觉率为 0。
- [ ] 画出 queued → running → succeeded/failed/dead_lettered/cancelled/timed_out，以及 heartbeat、retry、cancel、deadline 和 lease recovery。
- [ ] 用两个不同 priority、一个 backoff job 和两个同优先级 tenant 解释 aging/tenant rotation/FIFO/eligible boundary，并明确“选择公平不等于启动 SLA”。
- [ ] 演示 job 控制面缺 registry 时 503、错凭证 401、缺 scope/越 priority cap 时 403、跨 tenant 时 404、active/rate admission 时 429；说明静态 principal registry 不等于外部 IdP/动态 RBAC/数据库 RLS。
- [ ] 查询 `/v1/planning-admission-events`，解释幂等复用为何不消耗新 quota，以及 accepted-submission cap 为何不等于 raw-attempt limiter。
- [ ] 运行 `make eval-access-control`，解释 verifier 为什么同时检查允许/拒绝路径、admission audit、continuation recovery 和 credential exclusion。
- [ ] 主动说明 demo/synthetic/real-cache/real bookable 的区别。
- [ ] 不引用无 raw artifact 的历史 ECE、真实数据规模或成功率。
