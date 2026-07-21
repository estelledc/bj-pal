# AI 应用岗位面经证据与 BJ-Pal 方向选择

> 快照日期：2026-07-21。用途是决定项目投入顺序，不是统计招聘市场，也不把社区帖子当官方岗位标准。

## 1. 结论

当前样本支持把 BJ-Pal 定位为“旅行领域的受控 Agent 应用 + 后端可靠性工程”，而不是通用 RAG 聊天机器人或多 Agent 框架展示。

优先级从高到低是：

1. 讲清真实问题、完整数据/控制链、评测集来源、坏例分布和用户结果；
2. 能解释 RAG/检索的召回、排序、评测和坏案例，而不把框架当黑盒；
3. 能解释 Tool Schema、超时、错误、幂等、队列、事件、SSE 和监控；
4. 能处理长期记忆的冲突、来源、置信度、过期和删除；
5. 保留算法与后端基本功，如 LRU、TopK、JSON 解析、异步任务、线程锁和常见数据结构；
6. MCP、多 Agent、图框架只在问题需要时使用，不能替代前五项。

## 2. 调研口径

### 证据等级

| 等级 | 含义 | 本轮材料 |
|---|---|---|
| A | 固定源码、测试或可复现实验 | 本项目代码；本地多 Agent 旅行规划生态研究 |
| B | 候选人自述的具体面试问题或完整项目复盘 | 小红书、牛客面经；B 站项目复盘 |
| C | 观点帖、面试题汇总、搜索标题 | 只用于交叉发现，不单独决定方向 |

社区材料可能有幸存者偏差、转述失真、营销改写和岗位差异。本轮不根据点赞数推断真实性，也不把某家公司一次面试等同于行业统一标准。

### 样本

| 来源 | 样本与链接 | 可提取信号 | 可信边界 |
|---|---|---|---|
| 小红书 | [腾讯 AI 应用开发面经](https://www.xiaohongshu.com/explore/69b65f8e000000001a02f42b) | RAG 全链、混合检索/rerank、结构化输出、SSE、缓存/限流/监控、Agent/Workflow/Memory | 候选人自述，未获公司确认 |
| 小红书 | [字节 AI 应用开发三面经验](https://www.xiaohongshu.com/explore/6a50fa9a0000000008025607) | 业务痛点、项目链、动态 prompt、短/长期记忆、记忆冲突、从零 MVP | 候选人自述，单一样本 |
| 小红书 | [腾讯 Agent 应用开发面经](https://www.xiaohongshu.com/explore/6a57824100000000110176ab) | Agent+RAG+Tool+Workflow；LRU、TopK、JSON、异步队列、BFS/DFS、线程锁 | 候选人自述，题目未独立核对 |
| 小红书 | [腾讯 Agent 应用开发一面自述](https://www.xiaohongshu.com/explore/6a54ea7a000000000f02ade5) | 多维 query rewrite；改写信息不足时如何向用户补充；并行意图检测、RAG 全链与 context To-Do | 2026-07-13 候选人自述，未获公司确认 |
| 小红书 | [字节跳动 AI 应用开发一面已过](https://www.xiaohongshu.com/explore/6a4631320000000007029754) | 项目来源/最大挑战、幻觉与 prompt、短/长期 memory、Agent 模块、API timeout/error、token 消耗诊断，以及 Java/MySQL 事务基础 | 2026-07-02 候选人自述，未获公司确认；具体问题只作为 B 级方向信号 |
| 小红书 | [怎么在面试中讲好你的 AI 项目](https://www.xiaohongshu.com/explore/6a5b50b8000000000100f4f2) | 一句话定位、目标用户、原场景与痛点、方案、特殊设计、为什么这样设计、实现挑战；支持“机制 + 取舍 + 证据”叙事 | 2026-07-18 个人复盘，非招聘方标准；作为 C 级表达建议 |
| 小红书 | [淘天 AI Agent 三轮面经整理](https://www.xiaohongshu.com/explore/69aedd14000000001a035aad) | Agent vs LLM/workflow/RPA、tool/memory/planning loop、无限循环停止、checkpoint/resume、扩缩容、RAG 评测与延迟 | 内容较像汇总整理，真实性和岗位边界未独立核对；仅作 C 级交叉发现 |
| 小红书 | [需求澄清准确率观点帖](https://www.xiaohongshu.com/explore/697ac13a000000000a03c9d5)、[模糊输入回答模板](https://www.xiaohongshu.com/explore/6a5a58c5000000000903591c) | 只在多意图、执行关键参数缺失或失败成本高时追问；观察误澄清和补充后可执行 | 综合观点/回答模板，不视为公司面试事实，只用于设计指标 |
| 小红书 | [字节 AI Agent 评测实习生面经](https://www.xiaohongshu.com/explore/69c8cb210000000022024fe1) | 评测集设计、自动化、覆盖率/通过率、badcase 集中点与修复、线上用户反馈贡献、Agent 指标、Skill/Tool 区别、上线功能 ownership | 2026-03-29 候选人自述，未获公司确认；作为 B 级具体问题，不外推为统一题库 |
| 小红书 | [为什么简历不要只写 Coding Agent 和 RAG](https://www.xiaohongshu.com/explore/6a3d5e5e000000000803e05d) | 项目目的、产品价值、持续使用和工程取舍比教程复刻/关键词堆叠更有区分度 | 2026-06-26 观点帖，C 级，仅用于校验简历叙事方向 |
| 小红书 | [RAG 最怕一问评测](https://www.xiaohongshu.com/explore/6a008d220000000037035699) | 数据来源、问题分类、Recall@K、生成忠实度、同集对比；功能闭环不等于质量闭环 | 2026-05-10 观点帖，C 级；指标框架需由本项目 raw case 和 verifier 落实 |
| 小红书 | [面试｜不要堆技术栈，专注 BadCase](https://www.xiaohongshu.com/explore/69e077c8000000002202a148) | 项目区分度来自具体场景、badcase、取舍和指标，而不是技术栈清单 | 2026-04-16 个人观点，C 级；只用于叙事优先级，不当作招聘方标准 |
| 小红书 | [Agent 裸奔上线？可观测性了解一下](https://www.xiaohongshu.com/explore/6a599bdf0000000011007882)、[AI 项目全链路可观测](https://www.xiaohongshu.com/explore/69e2fa240000000023012e9e) | trace ID/span、结构化脱敏日志、成功率/错误率/成本/步骤指标与阈值告警；AI 链路还需区分检索、生成和工具信号 | 2026-07-17 与 2026-04-18 观点/题目转述，C 级；没有公司确认，也不据此声称某岗位必考或阈值适合生产 |
| 小红书 | [LoopX：超长程 Agent 运行 200+ 小时，状态不漂移](https://www.xiaohongshu.com/explore/6a5cc086000000000f01ef85) | 长程 Agent 要把状态、监督和规划外置化，不依赖有限模型上下文维持执行 | 2026-07-19 项目作者自述，运行时长和效果未在本轮独立复核；只作 C 级架构信号 |
| 小红书 | [AI Agent 面经](https://www.xiaohongshu.com/explore/6a5e35b2000000000f00664f) | 项目数字会被继续追问基线、上限和大规模边界；“压缩 token”若只有小样本或没有固定上限，无法支撑主张 | 2026-07-20 候选人自述，未获面试方确认；只作为 B 级“数字需实测口径”信号 |
| 小红书 | [美团大模型评测面试题拆解](https://www.xiaohongshu.com/explore/68af0ca4000000001b037061) | 多维指标、自动化评测、对比数据集、幻觉量化、对抗用例，以及“评测发现缺陷并推动改进”的闭环 | 2025-08-27 题目整理，未获公司确认；作为 C 级评测方向，不视为真实题库 |
| 牛客 | [携程 Agent 开发实习一面](https://www.nowcoder.com/feed/main/detail/30725ecc8ff04e11a27a7b803d537293) | 项目深挖、RAG 解释、向量与余弦相似度、检索多但答案差的诊断 | 自述原帖，信息较简短 |
| 牛客 | [字节暑期 Agent 开发一面](https://www.nowcoder.com/feed/main/detail/688deb12b60646a7b1b3519c29cd2b3f) | RAG 全流程、Agent 链路、Memory 痛点、多轮对话、时效数据、滑动窗口 | 自述原帖，信息较简短 |
| 牛客 | [快手 Data Agent 开发一面](https://www.nowcoder.com/discuss/904419777614049280) | ReAct、框架模块、MCP/Tool、三层记忆、输出质量、二分查找 | 自述原帖夹带作者总结 |
| 牛客 | [携程 AI Agent 开发二面](https://www.nowcoder.com/discuss/865519197839753216) | 完整 Agent 链、Agent vs RAG、BM25+dense、rerank、chunk | 文章化整理，低于直接凉经证据 |
| 牛客 | [快手 AI 应用开发二面](https://www.nowcoder.com/discuss/884088091374366720) | RAG 分层评测、claim grounding、长期记忆字段、受控 runtime | 文章化整理，作为结构化补充 |
| B 站 | [第一个 Agent 项目复盘：RAG + FunctionCall](https://www.bilibili.com/video/BV1ETvTzsEgP) | FastAPI AI 服务、FAQ/流程路由、手写 RAG、Tool registry、异步 HTTP、框架取舍 | 项目复盘，不是面试记录 |
| 本地研究 | 多 Agent 旅行规划生态研究快照（不随本独立仓库发布） | 类型化工具、显式状态、有界执行、durable job、artifact、审批、协议边界 | 固定源码快照；多数项目未实际部署运行 |
| GitHub / Microsoft Research | [AgentRx](https://github.com/microsoft/AgentRx) | 将 agent trajectory 规范化为 IR，用预声明/合成 invariant 做逐步检查、关键失败定位与错误分类 | 一手源码/论文实现，但本轮未安装或运行；仅借鉴诊断结构，不声称 BJ-Pal 已复现其结果 |
| 官方招聘页 | [Dynatrace OpenIngest Generative AI](https://www.dynatrace.com/careers/jobs/1399228700/)、[Bloomberg AI App Enablement & Observability](https://bloomberg.avature.net/careers/JobDetail/Senior-Software-Engineer-AI-App-Enablement-Observability/18854) | OTel trace/metric/log ingest、GenAI semantic conventions、telemetry pipeline、sampling/redaction/cardinality/cost、回归检测与告警 | 2026-07-20 访问的具体高级岗位样本，不代表校招统一要求；用于验证“协议 + 隐私 + 从 telemetry 到 action”是实际平台方向 |
| 官方招聘页 | [Booz Allen Agentic AI Platform Backend](https://careers.boozallen.com/jobs/JobDetail/Washington-Backend-Infrastructure-Agentic-AI-Platforms-Software-Development-Engineer-Senior-R0241917/125660)、[Travelers Legal & Compliance AI](https://careers.travelers.com/job/23160620/senior-software-engineer-legal-compliance-hartford-ct/) | 前者把 token economics、provider budget、secret/API-key rotation、可观测性与评测并列；后者要求追踪 token cost、latency、output quality，并具备 secrets management 与 eval/A-B 能力 | 2026-07-21 访问的两个高级岗位样本；页面直抓受 406/403 限制，内容经 Exa 官方页索引交叉获取，不外推为校招统一要求 |
| DeepSeek 官方文档 | [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/) | 官方 schema 当前列出 `deepseek-v4-flash/pro`、`max_tokens`，响应 `usage` 给出 prompt/completion/total token；tool 参数仍需应用自行校验 | 2026-07-21 当前文档快照；只支撑模型/usage 字段语义，不证明本项目请求、账单金额或 provider 签名 |

## 3. 交叉出现的能力主题

| 优先级 | 主题 | 为什么放在这一层 | BJ-Pal 当前证据 |
|---|---|---|---|
| P0 | 业务问题、端到端链路、难点与效果证据 | 多个样本都先深挖项目，不接受只报框架名 | 有统一应用主链、设计与证据地图；v6.9 补模型输出失败关闭，v6.8 用可复算 single/multi 对照解释编排选择，v6.7 补请求级执行预算，但仍缺真实用户 outcome |
| P0 | 评测集、badcase、自动化与用户结果 | 新增具体面经直接追问覆盖率、失败集中点、修复与线上反馈贡献；观点材料也反复区分功能闭环和质量闭环 | v6.9 新增一条经配置 DeepSeek client 的 operator-observed badcase：两次尝试后仍被 contract 拒绝；它是单样本 fail-closed 证据，不是质量率。v6.6 已有 capability-bound cohort，但真实 participant/report 仍为 0 |
| P0 | RAG/检索：切分、召回、混合、rerank、评测 | 高频追问集中在“为什么这样做、如何证明变好” | 已有 BM25；v4.4 新增 19-case golden set、baseline、Recall/MRR/多样性与 raw cases |
| P0 | Tool/Workflow：Schema、错误、超时、幂等、兜底 | 这是 AI 应用与普通 API 调用的工程分界 | v6.9 将模型原始 JSON 视为不可信：strict schema + request candidate/sequence binding、最多一次 budget-bound repair、502/job terminal no-retry；另有 partial provider issue、durable job、artifact，天气 live 授权验收仍未完成 |
| P0 | Query rewrite、slot/约束抽取与需求澄清 | 候选人自述直接追问多维 query rewrite 和缺信息补充；观点材料建议意图与 slot 一次结构化返回，但后者不是面试事实 | v5.6 已有 `requirement_gate_v1` + `constraint_ledger_v1`、typed 409 continuation/resolution、20-case gate、30-case constraint 与 16-case continuation artifact；只有 synthetic 多轮状态机，仍缺真实会话与用户满意度 |
| P0 | 后端可靠性：异步任务、事件、缓存、限流、监控 | 面经并未降低传统后端要求 | 有 lease heartbeat/fencing、有限重试/dead letter、协作取消、durable deadline/timed_out、lineage replay、priority aging + 同级 tenant 轮转、静态哈希 principal/scope/tenant/cap、active/accepted-submission 原子准入与 append-only audit、JSON/SSE replay、readiness audit；v6.27 用 PostgreSQL 17 验证独立进程 claim 与共享 cap，v6.28 再补 dry-run/apply/verify、跨库 count/digest、append-only receipt、故障回滚与 unsafe rollback denial。另有有界执行器、ASGI/TCP benchmark、execution observation、OTLP loopback 和带样本门的 deterministic alert snapshot；无外部 IdP/动态 RBAC、raw-attempt limiter、在线双写、RLS/数据库加密、audit retention、远程 collector/告警投递、跨主机或生产负载证据 |
| P0 | 有界执行、重试、凭证与 token/延迟成本 | 候选人自述会被追问 token 数字的基线/上限；两个官方高级岗位把 token economics、API-key rotation、latency/output quality 与评测列为同一工程面 | v6.7 有 request-local budget；v6.23 又以显式 CSSwitch 0600 handoff 做一次固定场景真实验收，绑定 1 call、1464 provider-reported token、约 28.9 秒、quality gate 与 Key 零命中。仍无金额价格表/cache 计价拆分、服务端轮换/撤销、跨实例 quota 或 billing reconciliation |
| P1 | Memory：短期/长期/工作记忆、冲突和删除 | 近期样本明确追问城市变更等冲突 | v4.5 已有 source/confirmation/expiry/revision、冲突拒绝和 hard delete；缺服务端身份与跨设备同步 |
| P1 | 模型基础、Prompt、结构化输出和上下文 | 用于解释模型边界与失败原因 | v6.9 用真实配置 Flash badcase 驱动 prompt 修正，同场景 Flash/Pro 对照支持后续优先 Pro；第一轮 3-case 暴露 798 仅 2 候选。v6.10 补齐数据覆盖和证据型忌口过滤后重跑三例，候选 26/16/21，结果为首轮/首轮/一次修复后接受，并以脱敏 projection 复算 32/32 个 fixed synthetic 必需检查；禁止报告成功率、真人质量、签名 provider 身份或金额成本 |
| P1 | 算法与并发基本功 | LRU、TopK、滑窗、锁、BFS/DFS 仍会单独考 | 项目有 TopK、线程池和 SQLite 事务；算法题应独立训练，不在仓库堆模板 |
| P2 | MCP、A2A、多 Agent、图框架 | 有时会问，但必须先证明需求 | 当前明确延后；v6.8 证明旧 ToT 只是同构多分支，并以 3-case mock 对照得到 0 质量提升、3× LLM/data 的本项目证据，但不把它外推为真实模型或所有多 Agent 架构无效 |

## 4. 本轮方向决策

### 已实施

1. 保留“确定性工作流 + 有界 LLM 节点”，不按文件数量宣传多 Agent。
2. 将 POI、UGC 和路线收敛到类型化数据面，独立读取、单点合并、部分失败显式返回。
3. 建立同步 API 与 durable job；任务先持久化，再由 worker lease claim。
4. 增加 append-only job event log，heartbeat、retry、回收、成功和 dead letter 与状态变更同事务落库；JSON cursor 与 bounded SSE 共用事件表，并支持 `Last-Event-ID` 续读。
5. 将 UGC BM25 包装成可解释查询层：确定性领域扩展、字段加分、POI 去重，检索证据进入 Planner 上下文。
6. 建立 19 条 synthetic golden set，对旧 BM25 和新检索器运行相同 top-5 评测。
7. 将长期记忆改为显式生命周期：同值强化、已确认异值替换、未确认冲突拒绝、过期/确认 gate、soft forget 与 hard delete；事件只存 value hash。
8. 增加两层有界 HTTP 并发回归：固定 8-worker 数据执行器、原子 plan trace、进程内 ASGI 与独立 Uvicorn/localhost TCP 的逐请求 raw latency/status/request ID，以及独立 verifier。
9. 补齐单机 worker 恢复语义：周期 heartbeat、过期 owner fencing、有上限指数退避、最大尝试次数、dead-letter 状态，以及旧 job/event schema 保留式迁移。
10. 增加任务控制面：轻量状态列表、workflow-boundary cooperative cancel、failed/dead-letter 幂等新 job 重放，以及 cancel/replay lineage 的原子事件。
11. 关闭匿名控制面并增加任务生命周期：32+ 字符静态 Bearer fail closed，绝对 deadline、timed_out event/SSE/list/replay、cancel-timeout 竞态优先级，以及 v4.8 schema 保留式迁移。
12. 在 Application Service fan-out 前增加 Requirement Gate：片区规范化、透明默认假设、执行关键缺口澄清；同步与 durable submit 共用 409，20-case artifact 独立复算误澄清与补充后 gate 状态。
13. 从真实 FastAPI 坏例增加 typed Constraint Ledger：人数、儿童、忌口、步行半径、人均预算、开始时间、时长和 persona 共用一套 preflight；显式字段冲突 fail closed，30-case artifact 独立复算抽取、约束保持、rewrite 和 durable round-trip。
14. 将分散 trace 收敛为 `execution_observation_v1`，并在 v6.7 升级为带 budget snapshot 的 v2：同步 request ID / durable job ID 关联、隐私最小化 span tree、调用与业务计数、真实 token completeness 和 SHA；3-case verifier 独立重算并拒绝 mock token 伪造。
15. 增加 `priority_aging_v1`：0-9 基础优先级从 eligible time 每 60 秒 aging、同级 FIFO，retry backoff 不计等待；claim event 保存 queue wait，3-case verifier 独立复算抢占、抗饥饿和 backoff exclusion。
16. 增加 `tenant_admission_v1` 与 `tenant_fair_priority_aging_v2`：submit/replay/job continuation 原子检查 active/60 秒 accepted-submission quota 并追加 tenant audit；同有效优先级按最久未服务 tenant 轮转；4-case scheduling 与 6-case access-control artifact 独立复算。
17. 将研究中的“推荐与预订风险不同”落实为 `approval_gated_operation_v1`：动作/报价/条款绑定 approval SHA，请求者与审批者职责分离，tenant-local 幂等、独立 worker、receipt SHA、append-only event/reconciliation 和 uncertain no-retry；provider-reference-bound 只读 lookup 可收敛不确定态，5-case artifact 独立复算 12 项安全契约，且 provider 强制为 sandbox。
18. 将“真实用户 outcome”从一句 roadmap 变成可用入口：同步 API 与 Streamlit 都为精确 plan artifact 发放限时 capability，原文不落库；采纳决定与实际完成分阶段只追加，负向原因必须来自枚举；每阶段少于 5 份时比例保持 `null`。同时给旧 `plan_outcome` 增加 `synthetic_test / legacy_unclassified / human_verified_step` 分类，seed 不再进入真人校准 UI。4-case artifact 只证明契约，不伪造真人结果。
19. 将“5 份反馈”的分母也纳入证据链：operator 按 tenant 创建有截止时间的 cohort，逐人发一次性 enrollment code；参与者对精确 notice SHA 明示同意后获得匿名 capability，同一凭证每 phase 只允许一条。退出者从开放汇总排除，关闭时冻结 cutoff snapshot；当前 6-case/13-metric verifier 还复算 retention purge transaction。它不做身份核验，不能把 5 个凭证写成 5 位已验证真人。
20. 将“协议存在”推进为可执行 operator 工作流：原子批量签发 enrollment，原始码只写 mode-0600/gitignored bundle 且不进 stdout；status 保持小样本门，close 需要精确 trial ID 和低样本二次确认。这样面试能讲清 secret delivery、不可逆操作和本地 privileged boundary，而不是只展示 API route。
21. 将 retention notice 从“到期标记”推进为可回滚删除事务：仅允许 frozen + due cohort，要求精确 trial ID 与 secret/backup disposition；在 `BEGIN EXCLUSIVE` 内验证 hash chain、开启 `secure_delete`、按外键顺序删目标行、恢复 trigger、检查外键并写 hash-only receipt。WAL、门槛或故障注入均 fail closed，且明确不把它说成取证级擦除或备份删除证明。
22. 将“观测调用量”推进为可执行 budget：服务端 policy 用 ContextVar 绑定单次 planning execution，在 LLM/data/tool N+1 前停止；LongCat/DPSK 关闭 SDK retry，应用层单独持有 transport attempt；实报 token 或安全检查点 wall-clock 超限时返回带 SHA 的 termination snapshot。同步 429、durable terminal no-retry，6-case verifier 独立复算且不伪造金额成本。
23. 将“工具调用可追踪”收紧为持久化安全边界：新日志只保留有界结构投影与稳定错误码，以 session sequence/previous SHA、append-only trigger 和 reset marker 保持本地证据连续；legacy payload 默认读取隐藏，5-case verifier 还验证默认存储隔离，并拒绝 marker 泄漏、伪造 mutation 与截断链，同时明确未实现 retroactive erase、数据库加密或远端不可变审计。
24. 将“配置过真实模型”推进为有界 live-provider acceptance：只有显式费用确认才读取 owner-only regular 0600 CSSwitch active DeepSeek profile，拒绝 symlink/宽权限/非 HTTPS/隐式模型/覆盖输出；一次固定三里屯运行绑定 model-output、quality、execution budget 与 provider-reported usage，三份 0600 artifact 中 Key 精确命中 0，独立 verifier 复算六项 gate。仍明确不是签名 provider、成功率、发票金额或服务端 credential lifecycle。

### 当前可复现结果

在 `ugc-retrieval-core-v1` 的 19 条 synthetic fixture 上：

| 指标 | 旧 BM25 | v4.4 检索器 | 差值 |
|---|---:|---:|---:|
| HitRate@5 | 1.000 | 1.000 | 0.000 |
| MRR@5 | 0.947 | 0.947 | 0.000 |
| Macro Recall@5 | 0.974 | 1.000 | +0.026 |
| top-5 unique subject ratio | 0.705 | 1.000 | +0.295 |

这组结果只能证明：在小规模、人工标注、合成且已有 area anchor 的样本上，查询扩展与 POI 去重改善了多意图覆盖和结果多样性；它不能证明线上准确率、真实评论检索质量或跨城市泛化。延迟记录是单机微基准，只用于回归观察，不写成 SLA。

## 5. 当前缺口与下一步

1. 当前检索只有 BM25 + 规则扩展，没有 dense retrieval 或 reranker。是否加入必须先用当前 golden/bad-case 证明 BM25 的语义召回缺口，再做离线消融。
2. Memory 仍是本地单用户 SQLite，没有服务端身份、加密、跨设备同步、备份删除证明或真实冲突样本。
3. 天气已有进程级 TTL/stale cache，durable control plane 也有 accepted-submission cap，v6.7 有单请求调用/retry/实报 token/checkpoint-time budget，v6.21-v6.22 补 OTLP 与单快照告警，v6.23 再补一次真实 DeepSeek 的本地凭证交接、usage/质量绑定；但仍没有公网 raw-attempt limiter、价格版本/cache 计价、tenant 金额账户、跨实例 cost/quota controller、billing reconciliation、远程 collector/连续告警、TLS/反向代理、多实例或真实模型负载测试。单次 1464 token observation 不能支持“生产级成本治理”。
4. heartbeat/retry/dead-letter/SSE/list/cancel/deadline/replay、priority aging、同级 tenant 轮转与静态 principal/scope/tenant/priority/admission 控制面已完成；v6.27 新增 PostgreSQL shared job/event/admission/scheduler store，并以本机独立进程 claim、并发 cap 和公开 CI/OCI 链路验收。下一步仍是 SQLite 在线迁移、容量/故障恢复、服务端 credential 过期/轮换/撤销、外部 IdP/动态 RBAC、数据库 RLS、入口 raw-attempt protection 与在线 reprioritize。
5. v6.7 已补请求级执行预算，v6.6 已具备有界知情 cohort、安全 operator 工作流和到期本地清除，但真实 participant/report 仍为 0。最优外部推进不是继续扩写架构，而是由用户确定 URL/渠道/时间窗后实际创建批次，招募 5-10 位明确知情的试用者；只报告不同匿名参与凭证的自报聚合与原因分布，不写已验证真人、满意度或因果贡献。offline contract 不能替代这一步。
6. 副作用已完成 sandbox 安全状态机，但还不能写“接入真实预订”：缺供应商授权/测试环境、订单查询、补偿、客服 handoff、第三方签名回执和 PII/secret/retention。

## 6. 简历与面试使用原则

可以写：

> 为北京短时活动规划 Agent 构建可解释 UGC 检索与离线评测：在 19 条 synthetic golden case 上对比 legacy BM25，引入确定性查询扩展和 POI 去重，使 Macro Recall@5 从 0.974 提升至 1.000、top-5 地点多样性从 0.705 提升至 1.000；保留逐例结果、数据 hash 和适用边界。

后端/Agent 平台岗位可改写为：

> 设计请求级执行观测契约，贯通同步 request ID 与 durable job ID，输出隐私最小化 span tree、阶段耗时、调用/业务计数和 provider-reported token completeness；用 artifact SHA 与独立 verifier 重算父子树和汇总，mock usage 缺失时 fail closed 为 unavailable。

也可改写为：

> 为 SQLite durable job 设计 `priority_aging_v1`：0-9 基础优先级按 eligible wait 每 60 秒 aging 并以 FIFO 解同分，retry backoff 到期前不参与竞争；将 base/effective priority 与 queue wait 写入 append-only claim event，并用 3-case artifact 独立复算排序与抗饥饿边界。

还可按后端安全方向改写为：

> 将共享任务 token 重构为静态哈希 principal registry，按 submit/read/control/replay scope、tenant namespace 与 priority cap 保护 durable job；迁移旧 SQLite schema 并把幂等键改为 tenant-local unique，用真实 ASGI + SQLite 的 4-case artifact 独立复算隔离结果并扫描凭证泄露。

v6.0 可追加一条后端可靠性版本：

> 为多租户 durable job 设计单机原子准入与公平调度：在 SQLite 写事务内统一约束 active job 和 60 秒 accepted submissions，覆盖 submit/replay/clarification continuation 与幂等复用，追加 tenant-scoped audit；保留 priority aging 优先语义并在同级轮转最久未服务 tenant，用 4-case scheduling 与 6-case access-control artifact 独立复算。

v6.2 可追加一条副作用安全版本：

> 将预订从可自动重试的 planning job 拆为 approval-gated operation：把 action、quote validity/amount/terms 与审批指纹绑定，强制 requester/approver 职责分离、tenant-local 幂等、单次执行、receipt SHA 与 uncertain no-retry；用 provider-reference-bound 只读 lookup 收敛不确定态，并以 5-case artifact 独立复算 12 项安全契约，明确仅限 deterministic sandbox。

v6.3 可追加一条评测/产品闭环版本：

> 为规划结果建立 capability-bound 用户证据链：反馈令牌只存 SHA，decision/outcome 与精确 plan artifact 绑定并按 phase 追加写入，负向原因限定为无 PII 枚举；公开比率在每阶段少于 5 份时 fail closed 为 null，并用 4-case artifact 独立复算绑定、幂等、过期、append-only、隐私最小化和样本门。当前只完成机制，真实用户报告仍为 0。

v6.4 可追加一条真实评测治理版本：

> 为用户评测建立 tenant-scoped 知情试用协议：用精确 consent notice SHA、operator 单次加入码和匿名 participant capability 绑定试用分母，限制每参与凭证每 phase 只追加一次，并将退出排除与关闭 cutoff snapshot 纳入证据 root；用 5-case artifact 独立复算 12 项契约。当前真实 participant/report 仍为 0，且 capability 不等于真人身份。

v6.5 可追加一条安全运营版本：

> 将知情试用落为可执行 operator 工作流：在单事务批量签发一次性 enrollment capability，数据库仅存 SHA，原始码只写入不可覆盖的 0600 secret bundle；对汇总保持 phase 门控，冻结要求精确 trial-ID 与低样本二次确认，并用自动化测试验证 secret 不进 stdout/DB、bundle hash/权限和重复关闭幂等。当前仍未创建真实 cohort。

v6.6 可追加一条生命周期治理版本：

> 为知情试用实现 retention-due 原子清除：仅允许已冻结到期 cohort，在排他 SQLite 事务内验证 snapshot/hash chain、按外键顺序删除目标行、恢复 append-only trigger 并检查外键，保留 hash-only receipt；以 WAL fail-closed、故障注入 rollback、跨 cohort 隔离和收据防篡改测试约束删除边界，明确不等同于取证级擦除或备份删除证明。

v6.7 可追加一条 Agent 可靠性版本：

> 为规划主链设计服务端 request-local 执行预算：在逻辑 LLM/data/tool 第 N+1 次调用前 fail closed，关闭 SDK retry 并由应用层统一限制 transport attempt；仅按 provider 实报 token 和安全检查点 wall-clock 终止后续阶段，同步返回 429、durable job terminal no-retry，并以 6-case artifact 独立复算 limit+1、终止后代码未执行、敏感标记排除与双层 SHA。该机制不等于强杀阻塞调用、金额预算或生产 billing。

v6.8 可追加一条架构决策版本：

> 将历史 ToT 收紧为最多 3 个同构 Planner 分支，修复线程池未继承 request-local budget/trace/capture 的旁路，并以 3-case synthetic artifact 比较同输入单分支与多分支：当前规则质量提升率和语义输出变化率均为 0，LLM/data 调用均为 3 倍，故保持单分支主链；独立 verifier 同时复算分支故障、默认预算拒绝、plan/budget SHA 和 decision。该结果不代表真实模型或全部多 Agent 架构。

v6.9 可追加一条模型可靠性版本：

> 将 Planner JSON 视为不可信边界：以 strict schema 和本次请求候选映射校验 persona/area、POI ID/名称/类别、去重、depart 与时间序列，拒绝字段丢弃、类型强转和本地补残；首次失败只允许一次 request-budget-bound provider repair，仍越界则同步 502 或 durable terminal no-retry，并以 13 条 adversarial payload、4 条 lifecycle case 和独立 verifier 重算双层 SHA/调用边界。该证据不代表真实幻觉率、修复率或用户结果。

若岗位更重视模型工程，可在面试口述补充而不塞进同一条简历：用真实配置 Flash badcase 定位 prompt enum/depart 歧义，修正后以同场景 Flash/Pro 单样本选择下一轮 Pro；第一轮固定 suite 发现 798 候选覆盖不足，再补数据 gate 与 `no_spicy/light_diet` 证据过滤并重跑。新三例候选为 26/16/21，三里屯/家庭首轮接受、798 一次修复后接受；observation 不保留 prompt/raw output/auth material，质量 artifact 只保存固定 synthetic projection，由 verifier 复算 32/32 个必需检查。必须同时说明每场景只运行 1 次，不能称成功率、人工质量或用户结果提升。

必须同时口头说明样本只有 19 条且是 synthetic。若简历空间不足，宁可删掉数字，也不要删掉评测口径。

不能写：

- “RAG 准确率 100%”；HitRate@5 不是生成准确率。
- “接入真实小红书/高德数据”；公开默认数据是 synthetic。
- “多 Agent 分布式协作”；当前没有远程 Agent。
- “生产级事件流”；当前只有最长 30 秒的单机 bounded SSE 和静态应用层 tenant 隔离，外部 IdP/动态 RBAC、数据库 RLS、多实例 fanout 和容量证据尚未完成。
- “用户采纳率/完成率已提升”或“验证了 5 位真人”；当前没有真实试用报告，v6.4 的 1.000 只属于 synthetic contract safety metrics。
- “模型幻觉率为 0”或“自动修复率 100%”；v6.9 的 1.000 来自 synthetic/scripted contract case，不是线上错误分布。
