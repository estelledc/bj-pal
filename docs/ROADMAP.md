# BJ-Pal 路线图

> 当前主线：把黑客松原型推进为可信的简历项目。优先补证据、边界和可靠性，不以 Agent 数量、页面数量或历史数据规模衡量完成度。

## 1. 当前状态

| 里程碑 | 状态 | 已有证据 | 未覆盖 |
|---|---|---|---|
| v4.0 可复现底座 | 已实现 | deterministic demo profile、manifest、Python 3.11、pytest/CI | 真实 provider |
| v4.1 应用层 | 已实现 | `PlanRequest → PlanningService → PlanResult`，UI/CLI 共用 | 部分历史 helper 仍可直接调工具 |
| v4.2 证据支持与 eval artifact | 已实现 | per-step factors、raw cases、双 SHA、独立 verifier | 真实 outcome calibration |
| v4.3 服务与可靠性 | 已实现 | FastAPI、typed provider、partial failure、durable job、artifact hash、Dockerfile | 本机 Docker daemon 未运行，镜像待 CI 实际 build |
| v4.4 可回放与检索证据 | 已实现并通过门禁 | fail-closed readiness、append-only event/cursor、query UGC retrieval、19-case baseline eval | synthetic 样本；无 SSE/真实 provider |
| v4.5 用户记忆生命周期 | 已实现并通过门禁 | additive migration、source/confirmation/expiry/revision、conflict event、hard delete、L2 memory | 本地单用户；无 auth/加密/跨设备同步 |
| v4.6 有界并发与性能证据 | 已实现并通过门禁 | shared 8-worker data executor、atomic plan trace、request-local tool log、raw HTTP benchmark、独立 verifier | 仅 in-process ASGI/mock/synthetic；无 socket/多实例/真实模型容量证据 |
| v4.7 Durable recovery | 已实现并通过门禁 | legacy migration、heartbeat/fencing、bounded retry/dead letter、SSE/Last-Event-ID、atomic event tests | 单机 SQLite；无取消、人工重放、鉴权或多实例存储 |
| v4.8 任务控制面 | 已实现并通过门禁 | 轻量状态列表、协作取消、幂等人工重放、lineage、v4.7 migration、事务回滚测试 | 无控制面鉴权、任务 deadline 或多实例存储 |
| v4.9 认证与 deadline | 已实现并通过本地完整门禁 | 32+ 字符 fail-closed Bearer、OpenAPI security、绝对 deadline、timed_out/SSE/replay、cancel-timeout 竞态、v4.8 migration；529 collected / 526 passed / 3 skipped | 静态共享 token；无身份/RBAC/租户/轮换；deadline 不强杀单次调用；无多实例存储 |
| v5.0 天气 Provider | 已实现并通过本地完整门禁 | Open-Meteo typed adapter、usage/date fail-closed、TTL/stale、Planner/Probe 同快照、室内跨类 reroute、offline acceptance + verifier、opt-in live smoke；541 collected / 538 passed / 3 skipped | fixture 是 synthetic；未执行 live smoke；无适用于宣传部署的商业授权或自托管验收 |
| v5.1 约束保持型 Replan | 已实现并通过本地完整门禁 | `ReplacementPolicy`、meal/snack/rest/weather hard filters、候选阶段计数、HTTP/durable event evidence、真实 demo meal→meal；543 collected / 540 passed / 3 skipped | 规则表规模小；无真实 reroute 接受率/撤销率 |
| v5.2 Replan 路线一致性 | 已实现并通过本地完整门禁 | fail-closed full-plan route refresh、incoming/outgoing leg 回归、缺坐标/非 POI 不跨站、HTTP/durable route evidence；548 collected / 545 passed / 3 skipped | 缓存/确定性估算，不是实时路况；尚无 live route provider |
| v5.3 路线感知时间轴 | 已实现并通过本地完整门禁 | travel-aware reflow、minimum dwell policy、partial/overrun/rollover evidence、HTTP/durable schedule contract；552 collected / 549 passed / 3 skipped | 不自动删站；最小停留为小型规则；无实时 ETA |
| v5.4 执行前需求门控 | 已实现并通过本地完整门禁 | `requirement_gate_v1`、字段来源、同步/持久任务 fail-before-fan-out、20-case golden artifact + verifier；567 collected / 564 passed / 3 skipped | 规则只覆盖高精度缺口；synthetic 标签；无真实澄清满意度 |
| v5.5 自然语言约束账本 | 已实现并通过本地完整门禁 | `constraint_ledger_v1`、统一 preflight、typed value/source/evidence/outcome、显式冲突 409、30-case artifact + verifier；584 collected / 581 passed / 3 skipped | 高精度确定性规则；不覆盖开放域 NLU、相对预算或真实用户理解率 |
| v5.6 澄清后可续跑 | 已实现并通过本地完整门禁 | `clarification_continuation_v1`、typed resolution、SQLite TTL/完整 hash chain、lease + cached result、sync/job continuation、Streamlit/CLI、16-case artifact + verifier；609 collected / 606 passed / 3 skipped | 单机明文 SQLite、短期 capability ID；无身份/加密/多租户/真实多轮满意度；同步端不承诺 exactly-once |
| v5.7 可验证执行观测 | 已实现并通过本地完整门禁 | `execution_observation_v1`、request/job correlation、隐私最小化 span tree、真实 token completeness、业务计数、双层 SHA、3-case 独立 verifier；616 collected / 613 passed / 3 skipped | in-process capture；无 OTLP collector、队列等待、多实例聚合、真实 provider 成本或生产负载证据 |
| v5.8 公平 durable 调度 | 已实现并通过本地完整门禁 | `priority_aging_v1`、0-9 基础优先级、60 秒 aging、eligible-time FIFO、retry backoff 排除、claim queue-wait evidence、旧库 additive migration、3-case 独立 verifier；625 collected / 622 passed / 3 skipped | 单机 SQLite；只保证选择顺序，不是启动 SLA；共享 token 无 priority RBAC；无在线 reprioritize/多实例队列 |
| v5.9 身份感知任务控制面 | 已实现并通过本地完整门禁 | `identity_scope_v1`、静态哈希 credential registry、submit/read/control/replay scope、principal priority cap、tenant-scoped job/event/control/continuation/idempotency、v5.8 保留式迁移、4-case 独立 verifier；634 collected / 631 passed / 3 skipped | 不是外部 IdP/动态 RBAC/数据库 RLS；无 token 生命周期、tenant quota/公平调度、加密或多实例存储 |
| v6.0 租户准入与公平调度 | 已实现并通过本地完整门禁 | `tenant_admission_v1`、active/60 秒 accepted-submission cap、原子 admission audit、submit/replay/continuation 全路径、`tenant_fair_priority_aging_v2`、v5.9 additive migration、4-case scheduling 与 6-case access-control verifier；644 collected / 641 passed / 3 skipped | 单机 SQLite；不是 raw-attempt rate limiter；无跨实例配额、严格全局公平、audit retention/storage-DoS 防护或启动 SLA |
| v6.1 审批式沙箱副作用 | 已实现并通过本地完整门禁 | `approval_gated_operation_v1`、quote/条款/动作 fingerprint、请求/审批职责分离、tenant-local idempotency、独立 worker、receipt SHA、append-only event、过期/不确定态 fail closed、4-case 独立 verifier；656 collected / 653 passed / 3 skipped | 只允许确定性 sandbox；无真实供应商、订单状态查询、补偿、客服 handoff、签名回执、PII/secret 生产治理 |
| v6.2 不确定副作用只读核对 | 已实现并通过本地完整门禁 | `side_effect_status_lookup_v1`、provider operation reference 绑定、raw lookup payload/evidence SHA、append-only reconciliation、`operations:reconcile`、CLI/UI 分阶段安全演练、5-case/12-metric 独立 verifier；664 collected / 661 passed / 3 skipped | 查询仍是确定性 sandbox；无真实供应商 acceptance、签名回执、自动补偿、客服 handoff、PII/secret 生产治理 |
| v6.3 用户结果证据链 | 已实现并通过本地完整门禁 | `plan_feedback_report_v1`、精确 plan artifact + capability SHA 绑定、decision/outcome 分阶段 append-only、枚举原因、5 份最小样本门、HTTP/Streamlit 入口、4-case/8-metric 独立 verifier；675 collected / 672 passed / 3 skipped；真实 report 数为 0 | 仅证明 synthetic contract；自报且未经核验，不是满意度、因果效果或 step-level calibration；无身份、知情同意记录、生产加密/retention |
| v6.4 知情试用与冻结证据 | 已实现并通过本地完整门禁 | tenant-scoped cohort、精确 notice SHA 同意、operator 单次加入码、匿名 participant capability、每参与凭证/phase 唯一、退出排除、cutoff snapshot、5-case/12-metric 独立 verifier、HTTP/Streamlit/CLI 排练；687 collected / 684 passed / 3 skipped | 不验证真人身份；真实 participant/report 仍为 0；retention 到期只标记 purge due，无托管删除调度、生产加密或备份删除证明 |
| v6.5 真实试用运营面 | 已实现并通过本地完整门禁 | 原子批量 enrollment、create/issue/status/close operator CLI、mode-0600 secret bundle、gitignore、stdout redaction、精确 trial-ID 冻结确认、门槛不足二次确认、重复关闭幂等；691 collected / 688 passed / 3 skipped；完整门禁默认反馈库 164→164 | 本地 privileged CLI，不是远程 IAM；secret bundle 需组织者安全分发/删除；真实 participant/report 仍为 0 |
| v6.6 到期原子清除 | 已实现并通过本地完整门禁 | frozen + retention gate、精确 trial-ID/secret/backup disposition、`BEGIN EXCLUSIVE`、`secure_delete=ON`、目标行计数、trigger 恢复、foreign-key check、append-only purge receipt、WAL fail-closed、事务回滚、6-case/13-metric verifier；696 collected / 693 passed / 3 skipped；默认反馈库 164→164 且 purge receipt 表未迁入 | 只证明当前 SQLite live-table 删除契约；不是托管调度、取证级擦除、secret/备份删除外部证明；真实 participant/report 仍为 0 |
| v6.7 请求级执行预算 | 已实现并通过本地完整门禁 | `execution_budget_v1`、服务端 policy、ContextVar 请求隔离、LongCat/DPSK/Anthropic 的 LLM/data/tool N+1 pre-call gate、transport retry 单一 owner、provider-reported token post-call gate、安全检查点 wall-clock、同步 429、durable terminal no-retry、`execution_observation_v2` 双层 SHA、6-case/4-rate 独立 verifier；708 collected / 705 passed / 3 skipped；HTTP 20/20、默认反馈库 164→164 | 检查点不能强杀已阻塞调用；缺 usage 时不能执行 token gate；不是金额成本、跨实例 quota、生产 billing 或负载证据；真实 participant/report 仍为 0 |
| v6.8 编排选型对照 | 已实现并通过本地完整门禁 | 旧 ToT 明确降级为最多 3 个同构 planner 的实验多分支，`copy_context` 传播 request budget/trace/capture，预算异常不再被吞；3-case single/multi raw artifact、规则质量分解、budget snapshot、故障注入、默认预算拒绝和独立 verifier；713 collected / 710 passed / 3 skipped；HTTP 20/20、默认反馈库 164→164 | deterministic mock 忽略 branch hint；当前 0 质量提升/0 输出变化与 3× LLM/data 只能支持本项目维持单分支默认，不是对真实模型、多 Agent 或生产延迟的普遍结论；真实 participant/report 仍为 0 |
| v6.9 模型输出失败关闭 | 已实现并通过本地完整门禁 | `model_output_contract_v1` strict schema/候选/序列绑定、禁止 silent coercion/extra/drop/local partial recovery、最多一次同 provider 修复、request budget 约束、同步 502 与 durable terminal no-retry；12-case adversarial payload + 4-case lifecycle artifact、独立规则重算与自重哈希篡改拒绝；current-tree secret gate 覆盖 449 个文件；762 collected / 759 passed / 3 skipped；HTTP 20/20、默认反馈库 164→164 | secret gate 不扫描 Git 历史；synthetic fixture 不代表真实模型错误分布或修复成功率；schema/grounding 通过不代表方案质量、provider freshness 或用户满意度；真实 participant/report 仍为 0 |
| v6.9 live contract observation | 已实现坏例、模型对照与 3-case Pro acceptance | 5 条经配置 DeepSeek client observation；同场景 Flash 2 次后拒绝、Pro 首次通过；固定 Pro registry 的三里屯朋友、五道营家庭、798 单人场景均首次通过。单 artifact、pair、suite 均独立复算；live runner 需费用确认，`DPSK_MODEL` 必填 | 每场景仅 1 次，798 候选仅 2 个；不是 signed provider receipt，不能证明成功率、延迟分布、计划质量、金额成本或用户 outcome |
| v6.10 证据绑定的 live plan 质量代理 | 已实现并通过 3-case 真实配置 Pro 复核 | 798 进入核心 replacement coverage，候选由旧样本 2 增至 21；live 家庭场景的 `no_spicy/light_diet` 餐饮只保留 structured positive UGC evidence；运行时进一步对所有显式 diet flag 采用同一正向证据交集，缺证据时省略 food 并产生 `diet_evidence_unavailable`，且输出契约禁止非 food POI 伪装为 `meal/snack`。新三例候选 26/16/21，脱敏质量代理独立复算 9/9、12/12、11/11 必需检查 | 每场景仍只跑 1 次；未登记 evidence tag 的忌口会安全降级而不是给出餐饮推荐；synthetic proxy 不评判 rationale、真实 freshness、用户偏好或 outcome |
| v6.11 localhost socket acceptance | 已实现并通过本地完整门禁 | 独立 Uvicorn 子进程只绑定 `127.0.0.1`，临时隔离 runtime，阻止加载本机 env 并剥离 provider/control credential；readiness 后经真实 TCP 并发请求，逐请求证据由现有 verifier 独立复算，要求 `SIGINT + wait` 退出码 0；package/FastAPI/OpenAPI 版本统一为 6.11.0；452-file secret gate、772 collected / 769 passed / 3 skipped、ASGI 与 TCP 均 20/20 且默认反馈库 164→164 | 仍是本机单进程、mock LLM、synthetic data；无 TLS/反向代理、远程网络、多实例、真实 provider/model 或生产容量结论 |
| v6.12 隐私最小化工具调用账本 | 已实现并通过本地完整门禁 | `tool_call_audit_v2` 对 params/response 做有界结构投影，敏感 key/value、PII-like 标记和未知自由文本不落库；异常只留稳定 code；session 内单调 sequence + SHA-256 chain，v2 行 UPDATE/DELETE 由 trigger 拒绝；reset 只追加 marker；历史 payload 默认读取隐藏；4-case artifact 的 5 项指标均为 1.000；459-file secret gate、780 collected / 777 passed / 3 skipped、ASGI 与 TCP 均 20/20、默认反馈库 164→164 | 固定 synthetic marker 不能覆盖全部敏感数据；本地 SQLite 未加密、未远端不可变，operator 仍可删除整文件；历史行未自动重写/擦除；不是合规审计、DLP 或 retention 系统 |
| v6.13 独立工具审计存储 | 已实现并通过本地完整门禁 | 默认 `runtime/tool_audit.db` + `BJ_PAL_TOOL_AUDIT_DB`；clean-start 只建诊断表；旧共享库 no-auto-copy；footprint 仅聚合 v2；socket 临时重定向；5-case/6-rate artifact 全 1.000；460-file secret gate、785 collected / 782 passed / 3 skipped、ASGI 与 TCP 均 20/20、默认反馈库 164→164 | 默认路径隔离不是 RLS/加密/WORM；旧库未擦除，旧足迹不自动迁移，operator 仍可误配回旧路径 |
| v6.14 Plan-evidence 非破坏分库 | 已实现并通过本地完整门禁 | `state_layout_v1` 将 trace/outcome/calibration 绑定到单一 owner；dry-run 默认、显式确认、read-only snapshot、count/logical SHA、receipt、quick-check、0600 atomic publish、WAL fail-closed；3-case/6-rate artifact 全 1.000；本机 75,022/1,312 copy 完成且旧库未删；memory/prediction/plan-evidence 测试路径隔离；472-file secret gate、797 collected / 794 passed / 3 skipped、ASGI 与 TCP 均 20/20、真实共享库和 plan-evidence 库 SHA 不变、反馈库 164→164 | 只迁出 plan evidence；memory/prediction 仍在 legacy；单机 copy 不是 RLS/加密/在线迁移/备份删除；首次复核暴露的测试污染行未凭猜测删除 |
| v6.15 Prediction-feedback 独立存储 | 已实现并通过本地完整门禁 | `DomainSpec + verified_copy` 复用迁移内核；prediction resolver 在有效 receipt 后切换；保留 33,791 行、稀疏 ID、NULL 与 11 条 actual；迁移后 INSERT/UPDATE/DELETE 只写新库；4-case/7-rate artifact 全 1.000；482-file secret gate、806 collected / 803 passed / 3 skipped、ASGI 与 TCP 均 20/20、三份真实状态库 SHA 不变、反馈库 164→164 | user memory 仍在 legacy；旧 prediction 行未删；单机 snapshot 不是在线双写、RLS/加密/远端不可变存储，11 条 actual 不能证明预测校准 |
| v6.16 User-memory 成对分库 | 已实现并通过本地完整门禁 | `DomainSpec` 支持每表稳定排序键；`user_memory + user_memory_events` 同快照复制，resolver 仅在有效 receipt 后切换；本机 2,783 state + 5,572 event copy 完成，保留稀疏 `id/event_id`；迁移后 replace/event append/soft forget/hard delete 只写新库；4-case/9-rate artifact 全 1.000；491-file secret gate、816 collected / 813 passed / 3 skipped、ASGI 与 TCP 均 20/20、四份真实状态库 SHA 不变、反馈库 164→164 | 旧 memory/event 行未删；988 个 namespace 不等于真人；当前 state 全 forgotten；hard delete 不是备份/取证擦除，单机 snapshot 不是在线双写、RLS/加密/跨设备同步 |
| v6.17 Legacy retirement 与 strict readiness | 已实现并通过本地完整门禁 | payload-free audit 核对六张已知 legacy 表、三类 receipt/source snapshot/resolver、专用库完整性和 tool-audit owner；`dedicated_required` 把 fallback/drift/unknown/receipt failure 接入 `/readyz`；真实本机 18 项 audit + strict readiness 全 ok；4-case/5-rate artifact 全 1.000；500-file secret gate、826 collected / 823 passed / 3 skipped、ASGI 与 TCP 均 20/20、四份真实状态库 SHA 不变、反馈库 164→164 | 显式 domain registry 不是静态形式化 owner 证明；旧行未删；不证明在线 cutover、备份擦除、加密/RLS 或跨实例一致性 |
| v6.18 Release candidate manifest | 已通过 [PR #5](https://github.com/estelledc/bj-pal/pull/5) 合并 `main` | NUL-safe 读取 Git porcelain；逐文件绑定相对路径、状态、实现/文档分组、大小、Git mode 与 SHA；拒绝 env/runtime/DB/result/binary/symlink/大文件/本机绝对路径；独立 verifier 复核 Git HEAD/branch/divergence 与全部字节；发布前 333 项 = 315 implementation + 18 documentation，60 modified + 273 untracked，0 违规；506-file secret gate、834 collected / 831 passed / 3 skipped、ASGI 与 TCP 均 20/20 | manifest 只证明当时未提交工作树边界；credential literal 由独立 secret gate 负责；不读取既有 Git 历史，也不能自行证明旧 Key 已失效 |
| v6.19 Durable-job incident diagnosis | 已通过 [PR #6](https://github.com/estelledc/bj-pal/pull/6) 合并 `main` | 从 tenant-scoped job + 完整 append-only event chain 生成 `job_incident_diagnosis_v1`；14 类 signature/action、阶段耗时、重试/lease/heartbeat 计数、事件链与 artifact 双 SHA；HTTP/CLI、0600 新建输出、1,000-event fail-closed；14-case 独立 verifier 全通过；516-file secret gate、856 collected / 853 passed / 3 skipped、ASGI/TCP 均 20/20 | hand-authored synthetic contract，不是生产根因分析或 incident 分布；未知错误只报告 `runtime_or_dependency_unknown`/`unclassified_failure`；不保存 request、tenant/principal、worker、原始 payload/message |
| v6.20 Durable workload health | 已通过 [PR #7](https://github.com/estelledc/bj-pal/pull/7) 合并 `main` | tenant-scoped closed `[start,end)`；截止 end 的 event prefix + as-of status 重建；固定 status/terminal 分母；nearest-rank queue/run/terminal p50/p95/p99；retry/lease/timeout/dead-letter/cancel rates；1,000 job/10,000 event fail-closed；HTTP/0600 CLI 与双 SHA；2-case 独立 verifier 全通过；526-file secret gate、868 collected / 865 passed / 3 skipped、ASGI/TCP 均 20/20 | fixed synthetic windows，不是生产 SLO、事故率、容量或 OTLP；当前 SQLite 单实例；不输出实体 ID 也不等于完成 retention/access audit |
| v6.21 Privacy-minimized OTLP export | 已通过 [PR #8](https://github.com/estelledc/bj-pal/pull/8) 合并 `main` | 声明 OTel SDK + OTLP/HTTP protobuf exporter；显式 endpoint/protocol validation；batch export + non-fatal failure monitor；GenAI 语义 allowlist；JSONL/OTLP 共用 privacy projection；`jobs:read` 受控健康快照；2-case loopback/failure artifact 与独立 protobuf verifier；534-file secret gate、877 collected / 874 passed / 3 skipped、ASGI/TCP 均 20/20，8 份保留 DB SHA 不变 | fixed synthetic span + 本机 receiver，不是远程 vendor、生产投递、告警/SLO、retention、多实例或真实用户证据；prompt/tool args/content 刻意不采集 |
| v6.22 Operational alert contract | 已通过 [PR #9](https://github.com/estelledc/bj-pal/pull/9) 合并 `main` | `operational_alert_snapshot_v1` 复用闭合 workload 与 payload-free trace status；固定 4 条低基数规则、20 样本门、`firing/healthy/insufficient_data/disabled` 四态、source/policy/artifact SHA；受控 HTTP、0600 离线 CLI、4-case 独立 verifier；546-file secret gate、893 collected / 890 passed / 3 skipped、ASGI/TCP 均 20/20 | fixed portfolio threshold 不是生产 SLO；无连续窗口、迟滞、Alertmanager/远程投递、事故响应 outcome、多实例或真实流量证据 |
| v6.23 Bounded live-provider acceptance | 已通过 [PR #10](https://github.com/estelledc/bj-pal/pull/10) 合并 `main` | 显式费用确认 + owner-only regular 0600 CSSwitch active DeepSeek profile；拒绝 symlink/宽权限/非 HTTPS/隐式模型/覆盖；固定三里屯一次首轮通过，1 LLM call、53 input + 1411 output = 1464 provider-reported token、约 28.9 秒；observation/quality/acceptance 三份 0600 工件 Key 精确命中 0；独立 verifier 重算 usage/budget/quality/link/gate；557-file secret gate、903 collected / 900 passed / 3 skipped、ASGI/TCP 均 20/20 | 单次 configured-client operator observation，不是 signed provider receipt、成功率、延迟分布、发票或币种成本；不等于 KMS、服务端过期/轮换/撤销、多租户金额预算或 billing reconciliation |
| v6.24 OCI release gate | 已发布 | tag/version 双源核对、credential-free build、hardened container smoke、release/SHA/latest tag 与匿名 digest 验证 | 单架构 mock/synthetic 镜像，不是公网 API、生产部署或 SLA |
| v6.25 Public demo isolation | 已发布 | mock/synthetic 启动前 gate、public 3-path OpenAPI、raw-attempt/concurrency/body guard、无 feedback/clarification state、不可变 OCI digest | 进程级 aggregate limiter；无可信 IP、WAF、长期 HTTPS、多实例或 SLA |
| v6.26 HTTP composition root | 已通过 [PR #17](https://github.com/estelledc/bj-pal/pull/17) 发布 | 5 个 domain router + shared response/public-surface policy；`app.py` 3,099→303；32-path 去版本 OpenAPI SHA 不变；public 3-path；936 passed / 3 skipped；main Core/Pages/OCI 均通过 | 行为等价与结构证据，不是 API 永久兼容；jobs router 仍较大；无长期 HTTPS API |
| v6.27 PostgreSQL durable-job store | 已通过 [PR #19](https://github.com/estelledc/bj-pal/pull/19) 发布 | `PlanningJobStore` port + 显式 factory；SQLite 默认/PostgreSQL 可选；PostgreSQL 17 下 4 个独立 worker 进程无重复领取 12 job，8 路提交精确执行共享 active cap；lease reclaim/旧 owner fencing/replay/append-only trigger/readiness probe；954 collected / 951 passed / 3 skipped；main Core/Pages/OCI 均通过 | 短 transaction advisory lock 优先正确性而非吞吐；无 SQLite 在线迁移、容量/故障恢复、RLS、broker 或生产多实例负载证据 |
| GitHub 发布 | 已公开发布 v6.27 | `main` 发布提交为 `400b92d`；[Core](https://github.com/estelledc/bj-pal/actions/runs/29807147818)、[Pages](https://github.com/estelledc/bj-pal/actions/runs/29807147836)、[v6.27 Release](https://github.com/estelledc/bj-pal/releases/tag/v6.27.0) 与 [OCI](https://github.com/estelledc/bj-pal/actions/runs/29807340262) digest `9ff768…98ffe6` 均已复核；description、homepage 与 topics 已设置 | 许可证仍未选择；Pages 是静态案例而非 API 部署；真实试用仍为 0 |

## 1.1 v6.18 GitHub 发布门

发布记录：v6.18-v6.23 已按 PR #5-#10 的依赖顺序合并到 `main`。发布前 `make audit-release-candidate` 将当时 NUL-safe Git 状态绑定为 333 个候选条目；随后每层 PR 均保持增量 diff，最终集成提交 `e136b04` 重新通过完整 Core workflow 与 Pages 部署。该历史 manifest 不再代表当前 clean `main` 文件计数。

发布时按以下顺序执行并留证：

1. 安全门：当前 `.env_example` 只保留占位符；此前 `HEAD/main` 出现的是 LongCat credential-like 字面值。2026-07-20 repository owner 明确确认该旧 Key 已在 provider 侧撤销，这是 owner attestation，不是 provider 签名回执。当前 DeepSeek 配置属于另一 provider，真实 API 调用不作为撤销证据。是否清理公开 Git 历史仍是独立的破坏性操作；当前发布不改写历史，任何工件不得记录旧值。
2. 边界门：运行 `make audit-release-candidate PYTHON=.venv/bin/python`，要求 333 个候选条目无 `.env`、数据库、runtime、`_site`、评测结果、operator secret bundle、本机绝对路径、symlink、binary 或大体积生成物；manifest 精确记录 status/mode/size/SHA，逐文件 allowlist，不使用宽泛暂存。
3. 本地门：重新运行 `make check PYTHON=.venv/bin/python`、`git diff --check`、Python compile 和 workflow YAML parse；保留测试数、benchmark 环境和未覆盖风险。
4. 提交 A（315 个 implementation 条目）：`重构 BJ-Pal 可复现核心：统一应用、可靠性、评测与测试`。包含 packaging/CI、`src/`、`scripts/`、`tests/`、`evals/` 及其直接运行配置，形成一个可独立执行完整门禁的 release cut。
5. 提交 B（18 个 documentation 条目）：`更新 v6.18 设计、证据与面试材料`。包含 README、设计/路线图/证据/面试文档、submission 与展示材料；所有能力陈述必须能回指提交 A 的代码、测试或 artifact。
6. 远端门：经用户明确授权后 push `codex/reproducible-core-v4`，从 PR #5 开始逐层 review/retarget/merge；每层核对增量文件数、精确 head SHA 与已通过 workflow。
7. 发布后门：最终 `main` Core workflow 与 Pages 部署均通过后，再把 README、description、homepage 与 topics 更新为公开状态。许可证选择、API 部署和真实试用仍分别治理。

退出条件：两个 v6.18 提交边界、后续五层增量 PR、分支/主线 CI、Pages 与公开仓库状态均已复核。旧 LongCat 凭证失效仍只有 owner attestation；历史重写、许可证、API 部署和真人试用不由本次发布自动完成或证明。

## 1.2 v6.19 Durable-job Incident Diagnosis

目标：把“任务失败后人工翻完整事件与原始异常”收紧成可版本化、可复算且默认不泄漏请求内容的诊断边界。分类器只消费持久 job status、稳定 error code 与 append-only event type/timestamp/attempt；不把未知 runtime/provider 失败猜成数据库、网络或模型根因。

- `PlanningJobService.diagnose` 先按 tenant 取 job，再读取完整事件链；超过 1,000 项时检查下一项并拒绝截断，避免在不完整证据上给出结论；
- `job_incident_diagnosis_v1` 覆盖 active/retry/lease recovery、completed/cancelled、queue/execution deadline、persisted request、clarification、budget、model output、worker lease、runtime unknown 和 unclassified 共 14 类；
- 输出只含 job ID、状态、稳定分类依据、有界事件投影、阶段耗时/计数及双 SHA，不含 request ID/payload、tenant/principal、worker、异常 message 或未知 error 原值；
- `GET /v1/planning-jobs/{job_id}/diagnosis` 复用 `jobs:read` 和 tenant namespace；本地 CLI 只创建 mode-0600 新文件且不覆盖；
- 14-case synthetic artifact 保存原始固定证据与产品观察值，独立 verifier 重算分类、action、阶段、event SHA、inner/outer artifact SHA 和隐私边界，并拒绝重签后的事件/分类/私密 payload 篡改。

退出条件：目标测试、job smoke、独立 artifact、完整 `make check`、secret gate、PR #6 review/merge 与最终 `main` workflow 已完成。文档始终称 failure signature/triage，不称 root-cause engine、生产事故准确率或真实用户证据。

## 1.3 v6.20 Durable Workload Health

目标：把“有 event log，所以应该能看错误率和延迟”推进为有精确窗口、分母、采样覆盖和哈希证据的聚合契约，同时避免聚合接口成为 tenant/job/request 标识的导出旁路。

- HTTP/CLI 必须显式提供 timezone-aware `[start,end)`，最长 31 天且 end 不得晚于服务端当前时间；未来窗口拒绝，避免同一快照随时间变化；
- repository 在同一个 SQLite read snapshot 中按 tenant 选择窗口内创建的 job，只读取 `event.created_at < window_end` 的事件前缀并重建 as-of status；晚到 claim/terminal 不改写历史快照，超过 1,000 job 或 10,000 prefix event 直接拒绝，不用截断样本计算看似精确的比率；
- 固定输出七类 status count、terminal/active/job/event 分母，terminal success/failure、dead-letter、timeout、cancel、retry-job 与 lease-recovery rate；分母为 0 时输出 `null`；
- queue wait、run duration、time-to-terminal 分别绑定 submitted→first claimed、first claimed→matching terminal、submitted→terminal，分位数固定 nearest-rank，并公开 sample count/min/max；
- evidence 只使用 job ID 的 SHA 与 event type/attempt/time 投影；HTTP 复用 `jobs:read`，CLI 只创建 0600 文件，公开输出不含 tenant/principal/request/job/worker/payload/error；
- 2-case mixed/empty synthetic artifact 由独立 verifier 重算窗口、顺序、分母、rate、quantile、inner/outer SHA 和隐私边界，并拒绝重新签名后的聚合/分位数/ID 注入。

退出条件：定向测试、job smoke、独立 artifact、完整 `make check`、secret gate、PR #7 review/merge 与最终 `main` workflow 已完成。对外只称“可复算 workload snapshot”，不称生产 SLO、容量、事故率或告警效果。

## 1.4 v6.22 Operational Alert Contract

目标：把 v6.20 的闭合窗口指标和 v6.21 的 OTLP sink 健康从“可查看的两个 JSON”推进为可复算的运行决策，同时显式阻止小样本被写成健康、固定阈值被写成生产 SLO。

- `portfolio_operational_alert_policy_v1` 固定 terminal failure rate、queue wait p95、retry job rate 与 OTLP export health 四条规则；前三条分别要求至少 20 个 terminal、20 个 queue sample、20 个 job，达不到门槛一律是 `insufficient_data`；
- 数值规则以 `>=` 触发，trace 规则区分 OTLP healthy/degraded、configured-but-unproven 与未配置；未选择 OTLP 只禁用 trace 规则，不伪造 exporter 健康；
- 总状态优先级固定为 `firing > insufficient_data > healthy > disabled`，因此任意 firing 不被小样本掩盖，任意未满足样本门也不允许整体声称 healthy；
- `GET /v1/operational-alerts` 复用 `jobs:read` 和 tenant-scoped workload reader；离线 CLI 只接受已有 workload snapshot 与 trace-status JSON，并以 O_EXCL 创建 mode-0600 工件，避免另起进程伪称读取了服务端 exporter counter；
- 输出不含 tenant/principal/request/job/worker、collector URL/header、prompt/content/tool args/error message，只绑定 workload artifact SHA、trace-status SHA、policy SHA 与自身 SHA；
- 4-case authored synthetic artifact 覆盖健康、四规则触发、小样本和 OTLP 未配置；独立 verifier 不调用产品 evaluator，重算 workload rate、规则、四态、总状态、source binding 与多层 SHA，并拒绝自重签名后的阈值/规则/source/ID 注入。

边界：当前阈值是作品集中的固定演示策略，不是来自流量基线或错误预算；单个 snapshot 没有连续窗口、迟滞、抑制、路由和处置闭环，也没有 Prometheus/Alertmanager 或远程 collector acceptance。对外只能称 deterministic alert decision contract，不能称“生产告警系统”或“SLO 达成”。

## 1.5 v6.23 Bounded Live-provider Acceptance

目标：把“本机配过 DeepSeek、跑出过几个结果”推进为一次可复核、不会把 Key 混进消息/命令/工件的真实 provider 验收，同时不把 token observation 伪装成账单或生产成本治理。

- opt-in runner 必须同时收到 `--ack-provider-cost`、`--credential-source csswitch` 和显式 `--model`；默认离线门禁绝不读取本机配置或发起 API 调用；
- CSSwitch loader 只接受当前用户持有的 regular file、无 group/other 权限、最大 64 KiB、schema v2、唯一 active DeepSeek/Anthropic profile 和 credential-free HTTPS endpoint；symlink、宽权限、歧义、非 HTTPS、空 Key 均 fail closed；
- Key 只在 context manager 生命周期内映射到 `DPSK_*`，同时剥离 LongCat/Anthropic/DeepSeek 混用变量，退出后精确恢复原环境；secret field 不参与 repr/equality；
- 真实调用复用 canonical `PlanningService`、strict model-output contract、quality projection、execution observation 和固定 request-local budget；输出目录必须不存在，新建为 0700，三份 JSON 以 O_EXCL/0600 写入；
- acceptance receipt 绑定 live-model observation、live-plan quality、execution budget 与 provider-reported usage：独立 verifier 复算 53 + 1411 = 1464 token、1 LLM/1 data batch、completed budget、quality hard gate、linked SHA 和六项总决策；
- 2026-07-21 固定三里屯 synthetic 场景一次首轮接受，canonical execution 约 28.9 秒；三份 linked artifact 对实际 Key 的 exact-match count 均为 0。

边界：configured provider/model 与 external execution 没有签名回执；单次 synthetic scenario 不能估计成功率或延迟分布。当前不记录价格版本、cache hit/miss 计价、invoice、币种金额，也未实现服务端 secret manager、凭证过期/轮换/撤销、tenant spend ledger、跨实例 cost controller 或 billing reconciliation。

## 1.6 v6.12-v6.13 Tool-call Audit 验收

目标：把“为了排障把整个工具输入/输出写进 SQLite”收紧成隐私最小化、可发现篡改的本地诊断账本，避免可观测性成为用户文本、联系方式、provider credential 或异常原文的第二泄漏面。

验收契约：

- 新写入统一为 `tool_call_audit_v2`；dict/list/dataclass 只保留深度、条数和文本长度有界的结构投影，敏感 key、credential-like literal、邮箱/手机号及未知自由文本改为类型/长度或 redaction marker；
- `status` 只允许 `ok/error`，latency 必须有限且非负；异常消息不落库，只从异常类型导出稳定 `error_code`；非法 tool name 或状态 fail closed；
- 每个 session 在 `BEGIN IMMEDIATE` 内分配单调 sequence，绑定 previous-event SHA 后计算本行 SHA；partial unique index 防止重复 sequence，trigger 拒绝 v2 行 UPDATE/DELETE，独立 verifier 从 raw projected event 重新计算整条链；
- `clear_session` 不删除历史，只追加 `audit.session_reset`；默认读取只显示 marker 后的当前 segment，完整链仍可导出复核；legacy 行保留在原文件中但 `fetch_calls` 默认返回隐藏占位，避免 UI/CLI 再次展示旧 payload；
- 默认新写入落到 `runtime/tool_audit.db`，支持 `BJ_PAL_TOOL_AUDIT_DB`；clean-start 只创建诊断表，tool-audit 切换不自动读取/复制/删除旧库中的 legacy tool rows；footprint 只聚合 v2，socket benchmark 使用临时审计库；
- 5-case synthetic contract 覆盖敏感投影、稳定错误码、UPDATE/DELETE 拒绝、强制篡改检测、reset 可见性、legacy hiding 和 storage isolation；独立 verifier 在 artifact 自重签名后仍拒绝私密 marker、伪造 mutation 成功、截断链和伪造旧库未变。

边界：它不能识别所有业务敏感语义，也没有 retroactive erase、数据库加密、密钥管理、RLS、远端 WORM、访问审计或 retention scheduler。SQLite 文件 owner 可以删除整文件；旧行若曾含敏感值，仍需独立备份盘点与受控清理，不能用“默认隐藏”或“默认换库”声称已经删除。

## 2. v6.9 Model-output Fail-closed Contract 验收

目标：把“LLM 会按 JSON schema 返回且只选候选点”从 prompt 期望推进为进入 `Plan` 前的强制边界，保证未知字段、类型漂移、残缺 JSON、候选幻觉、名称错配、重复地点和非法时间序列不会被静默修正后继续执行。

验收契约：

- `model_output_contract_v1` 使用 strict typed model，拒绝未知字段、缺字段、类型强转和非法 literal；`Plan.from_dict` 不再承担不可信模型输出校验；
- persona 与 area 必须和本次请求精确一致；所有非 depart step 的 POI ID 必须来自本次候选映射且名称完全匹配，重复 ID fail closed；depart 唯一、必须在末尾、无 POI 且时长为 0，step index 连续、时间不得重叠；
- `repair_json` 的本地补残结果带 `_repaired` 标记并被 strict contract 拒绝，不能把截断输出伪装成完整方案；首次校验失败只允许调用同一 provider 修复一次，第二次仍失败则抛出 `invalid_model_output`；
- 修复调用与首次生成共用 request-local execution budget；正常路径只执行 1 次模型正文，修复路径至多 2 次，预算只有 1 次时第二次在 provider body 前终止；流式状态与最终方案共用首次 event stream，不再额外做 LLM preflight；
- 同步与澄清 continuation 将最终失败映射为脱敏 502，durable job 持久化 terminal failed 且不走普通 execution retry；快照只含版本、状态、attempt、candidate count、问题码和 SHA，不保存 prompt、用户输入或模型值；
- 12 条 hand-authored adversarial payload 和 4 条 deterministic lifecycle case 保存原始 fixture、判定、预算与模型契约快照；独立 verifier 不调用生产 validator，而是重算 schema/grounding/sequence、调用计数、双层 SHA 与指标，并拒绝自重哈希伪造问题码和 body count。

边界：这些样本是受控攻击面，不是线上真实模型错误分布；deterministic scripted repair 的 1.000 只证明生命周期契约，不代表真实修复成功率。候选 grounding 也不证明 POI 数据新鲜、真实可订或方案有用。真实 5-10 人知情 cohort 仍是下一外部里程碑。

补充 live observation：2026-07-20 在用户授权下，使用本机已配置但不写入仓库的 DeepSeek Anthropic 兼容凭证运行固定 synthetic 场景。修正前 Flash 在首轮与一次修复后因 `depart_duration_invalid/schema_literal_invalid` 被拒绝；该坏例驱动 prompt 消除竖线伪 enum 并明示 depart 字段。修正后同场景/26 候选/同预算下，Flash 仍因 extra/missing/type schema 问题在 2 次、67,772.391ms 后拒绝，Pro 在 1 次、46,773.039ms 首次通过。独立 pair verifier 验证输入边界一致，但每档只有 1 个修正后样本，因此只支持“下一轮有界试验优先 Pro”；`DPSK_MODEL` 设为必填，不写 100% 成功率、延迟提升或成本结论。checked-in artifact 只声明 operator observation，provider/model 来自 client config 而非签名回执；verifier 不能证明外部调用发生。最初另一次运行因临时观察脚本把 dict 当对象而未形成完整安全快照，不计入样本。

## 2.1 v6.8 Orchestration Decision Evidence 验收

目标：把“为什么不为了标签拆多 Agent”从口头判断推进为同输入、同数据、同质量规则下可独立复算的架构对照，同时修复历史并发多分支绕过请求 ContextVar 的缺口。

验收契约：

- `plan_tot` 明确是同一 planner 的多提示词分支，不称为多个自治 Agent，也不接入 HTTP/job 的 canonical `PlanningService`；生产主链和默认执行预算保持不变；
- 分支数与 worker 数都 fail closed 在 1-3，标签唯一、temperature 有界；测试/评测可注入 branch planner，但产品代码保持默认实现；
- 每个线程用独立 `copy_context()` 继承同一 request-local budget、trace parent 和 capture sink；共享 tracker/capture 自身带锁，`ExecutionBudgetExceeded` 必须向上抛出，不能被普通分支降级吞掉；
- 3 个 hand-authored synthetic 场景分别运行单分支和 3 分支，artifact 保存脱敏 plan projection、确定性规则质量分解、branch accounting 与完整 budget snapshot；独立 verifier 重算 SHA、质量 delta、输出指纹、调用/数据倍率和 decision；
- 另以一个 post-generation 分支故障证明 2/3 成功时故障可见且仍能返回，以默认 server policy 串行执行证明第二个 data batch 在 body 前终止；自重哈希伪造 LLM call count 会被 verifier 拒绝；
- 当前 deterministic mock 忽略 branch hint，因此 3 个场景的质量提升率与语义输出变化率都是 0，LLM call/data batch 都是 3 倍；本轮 decision 为 `single_branch_default`。

边界：quality score 是规则代理，不是用户成功率；mock 无 provider token/金额价格，也不能代表真实模型分支多样性。`observed_elapsed_multiplier` 只保存本机诊断值、不设性能门槛。若未来真实 badcase/outcome 显示多分支有稳定收益，应以同一 artifact 重新评估，而不是把本次 0 提升外推成永久结论。

下一外部里程碑仍是由用户确定 URL/渠道/时间窗后执行真实 5-10 人知情 cohort；内部架构对照不能替代真实 outcome。

## 2.2 v6.7 Request-level Execution Budget 验收

目标：把“观测到一次请求用了多少”推进为“在第 N+1 次昂贵操作前停止”，同时让成功和预算终止都产生隐私最小化、可校验的 policy/usage/termination evidence。

验收契约：

- `ExecutionBudgetPolicy` 只由服务端构造或从受控环境变量读取，客户端请求不能逐次抬高上限；非法配置启动时 fail closed；
- 默认每次 `PlanningService.execute` 最多 2 个逻辑 LLM call、1 个 data-provider batch、8 个 instrumented tool call、每逻辑 LLM call 4 次 transport attempt、32768 个 provider-reported token 与 120000ms 安全检查点 wall-clock；
- LLM/data/tool 计数在进入 span body 前增加并拒绝 N+1，因此被拒绝操作的 body 不执行；LongCat/DPSK 关闭 SDK 内建 retry，由应用 `retry_with_backoff` 单独持有次数，避免 retry 乘法；
- token 只累计 provider 实际回报，mock/缺失 usage 不估算；超过 token limit 时在该 call 返回后停止后续阶段，并明确已经花掉的 token 不可追回；
- budget 用 ContextVar 隔离并发请求；成功快照进入 `execution_observation_v2`，同步终止返回结构化 429 + hash snapshot，durable job 直接 failed 且不进入 retry/dead-letter 重放；
- 6-case synthetic contract 覆盖正常完成、LLM/data/tool N+1、reported-token overrun 与 wall-clock checkpoint，独立 verifier 重算 snapshot/artifact SHA、limit 语义、终止后代码未执行和敏感标记排除；另有线程隔离、HTTP、job、非法环境配置和篡改拒绝测试。

边界：wall-clock 是协作式 checkpoint，不会中断已经进入 socket/SDK 的阻塞调用；真实中止仍依赖 provider timeout。provider 不回报 usage 时 token gate 无法生效，因此系统只写 `unavailable`。当前也没有模型价格表、tenant 金额账户、跨实例全局预算或生产 billing reconciliation。

下一外部里程碑仍是由用户确定 URL/渠道/时间窗后执行真实 5-10 人知情 cohort；继续增加内部 contract 不能替代真实 badcase/outcome。

## 2.3 v6.6 Retention-due Atomic Purge 验收

目标：在启动真实试用前补齐 notice 已承诺的 retention 生命周期，使到期数据有受控、可回滚、可留证的本地删除路径，同时不把 SQLite 行删除夸大为取证级擦除或备份删除证明。

验收契约：

- purge 只接受已冻结且超过 retention deadline 的精确 trial/tenant；CLI 必须复述 trial ID、确认 secret bundle 已处置，并选择 `no_managed_backups` 或 `operator_attested_backups_purged`；
- repository 使用 `BEGIN EXCLUSIVE`，先验证 cohort/snapshot/evidence root；仅允许 `DELETE/TRUNCATE` journal mode 并开启 `secure_delete=ON`，WAL 或不能开启覆盖语义时 fail closed；
- 同一事务内暂时移除相关 DELETE trigger，按外键顺序只删除目标 trial 的 report、plan invitation、participant event、participant、enrollment、snapshot 与 cohort，随后核对 row count、恢复 trigger 并执行 `foreign_key_check`；
- 删除、trigger DDL、外键检查和 append-only receipt 任一步失败时整事务回滚；测试通过注入非法 trigger SQL 验证目标数据和 trigger 都恢复；
- receipt 只保留随机 trial/receipt ID、tenant/operator SHA、cohort/snapshot/evidence-root hash、retention/purge 时间、删除计数和 disposition；重复 purge 返回原 receipt，receipt 被改写时 fail closed；
- 6-case/13-metric trial verifier 增加 retention purge transaction，独立核对前后计数、目标隔离、外键、trigger、幂等和 receipt SHA；所有测试与 eval 只使用临时数据库，默认运行库不得减少或增加。

边界：`secure_delete=ON` 改善 SQLite 页覆盖语义，但 receipt 不是取证级擦除、文件系统快照、secret 文件或外部备份删除证明；本地 CLI 也不是远程 IAM 或托管 scheduler。

下一外部里程碑：由用户明确决定 participant URL、招募渠道与时间窗后，创建一个真实 cohort 并逐人分发 5-10 个加入码。未获该授权前不写真实证据库、不联系任何人。

## 2.4 v6.5 Trial Operator Workflow 验收

目标：把 v6.4“协议和 API 已存在”推进为组织者真正能执行的有限工作流，消除手写 SQL、逐个复制 curl、secret 意外回显和误冻结批次的操作风险，但不替用户创建真实批次或联系参与者。

验收契约：

- `scripts/manage_trial.py` 提供 create/issue/status/close 四个窄命令，所有真实写操作都可显式指定 evidence DB、tenant 和 operator；
- repository 可在同一个 `BEGIN IMMEDIATE` 内原子签发 1-100 个一次性 enrollment capability，任一插入失败则整批不提交，数据库仍只保存 capability SHA；
- issue 必须显式传 `--confirm-secret-output`，且只写入不存在的新文件；使用 `O_EXCL` 防覆盖，mode 固定为 0600，stdout 只返回数量、bundle SHA 和路径，不返回任何 capability；
- secret bundle 被 `.gitignore` 排除，并明确标为 `sensitive_operator_handoff_not_evidence`；一个码只发给一位预期参与者，组织者副本应在分发完成或到期后按试用策略删除；
- status 只读取门控汇总；close 必须用 `--confirm-trial-id` 精确复述目标，decision/outcome 任一 phase 未达门槛时还需 `--allow-insufficient-evidence`，已冻结批次的精确重试返回原 snapshot；
- 测试覆盖 secret 不进 stdout/数据库、bundle hash/0600/唯一性、缺确认失败关闭、不覆盖既有文件、低样本冻结门和重复关闭幂等；合成测试、smoke 和 benchmark 继续使用临时反馈库，不污染默认运行库。

边界：CLI 使用本地数据库权限，绕过远程 HTTP IAM，因此只适合单机受信 operator；无法证明实际分发给了不同真人，也没有 secret escrow、自动分发、托管删除或外部招募证据。

下一外部里程碑：由用户明确决定 participant URL、招募渠道与时间窗后，创建一个真实 cohort 并逐人分发 5-10 个加入码。未获该授权前不写真实证据库、不联系任何人。

## 2.5 v6.4 Consent-bound Trial Evidence 验收

目标：把“找 5 个用户试试”从无法审计的口号变成有限协议。普通反馈与试用批次分开汇总；组织者按 tenant 创建批次并逐人发一次性加入码，参与者必须对精确 notice SHA 明示同意，之后生成的 plan invitation/report 才带 cohort + participant binding。

验收契约：

- notice 使用 canonical JSON 与 SHA，明确用途、收集/不收集字段、自愿参与、退出、试用窗口和 retention deadline；participant、invitation 与 report 均绑定同一个 notice SHA；
- `trials:manage` 和 `trials:read` 受 tenant 隔离，notice 可公开读取；operator enrollment code 只能使用一次，数据库只保存 SHA，participant capability 只交付给客户端 session；
- 同一 participant capability 每个 phase 最多写一条；这是“不同匿名参与凭证”，不是经身份核验的不同真人；
- 退出作为 append-only event 保存，阻止新计划/反馈，并从仍开放批次的后续聚合排除；关闭批次后冻结 cutoff-bound immutable snapshot；
- trial-bound report 不进入旧 `/v1/feedback-summary`；每 phase 少于 5 个有效 participant 时隐藏 rate、value 和 reason distribution；
- `make demo-trial` 默认使用临时 SQLite 和 synthetic participant，输出不含原始 capability；排练得到的 0.8 不能写成真人采纳率或完成率；
- 5-case/12-metric artifact 从 raw notice、cohort、enrollment、participant、report、withdrawal 和 snapshot 重算 consent binding、单次加入、最小化、phase 唯一、tenant 隔离、未分组排除、退出排除、关闭失败关闭、append-only、最小样本门、snapshot 完整性和 retention 边界，12 项均为 1.000；
- 当前真实 participant/report 仍为 0；`raw_purge_due` 只是到期信号，不是物理删除、备份删除或合规证明。

下一外部里程碑：创建一个有截止时间的真实试用批次，向 5-10 位明确知情的试用者分发独立加入码，至少获得 5 份 decision、5 份 outcome 和 5-10 个可归类 badcase。对外只能称“不同匿名参与凭证的自报结果”，不能称已验证的不同真人、满意度或因果提升。

## 2.6 v6.3 Human Outcome Evidence 验收

目标：补齐“方案生成后用户是否采纳、是否真正执行、失败集中在哪里”的安全采集入口，同时阻止 synthetic seed、历史未分类 outcome 和真人证据混写。该里程碑建立证据管道，不虚构真实用户。

验收契约：

- 每次交付为精确 final plan canonical JSON 计算 SHA，并发放 14 天限时 capability；SQLite 只保存 capability SHA，原文仅存在 HTTP 响应或 Streamlit session；plan/capability 不匹配统一 404，过期 410；
- decision 仅允许 `accepted/requested_change/rejected`，outcome 仅允许 `completed/partially_completed/abandoned`；负向值必须选择受控 reason code，正向值不得夹带原因，不接收自由文本、姓名、电话或邮箱；
- report 以 `(plan_id, plan_artifact_sha256, phase)` 唯一，既允许同一 plan ID 的后续 revision 独立记录，又防止相同 artifact 重复投票；相同 idempotency key + 相同 payload 返回原记录，不同 payload 409；
- invitation/report 都有 canonical SHA，SQLite trigger 禁止 UPDATE/DELETE；公开 summary 始终标为 `self_reported_unverified`，每个 phase 少于 5 份时对应比例为 `null`；
- Streamlit 把采纳决定和实际结果拆成两阶段 form；同步 `/v1/plans` 的 feedback capability 不写回 canonical `PlanResult` 或 clarification cached artifact；
- 旧 `plan_outcome` additive migration 增加 `evidence_classification`。seed 默认 `synthetic_test`，历史行 `legacy_unclassified`；诊断 UI 只读取 `human_verified_step`，且明确 plan-level feedback 不能用于 step-level ECE；
- 4-case synthetic contract artifact 与独立 verifier 覆盖 capability/artifact binding、hash integrity、idempotency/phase conflict、schema、expiry、append-only、privacy minimization 和 minimum-sample gate，8 项 rate 均为 1.000；
- 完整 `make check` 通过：pytest 672 passed / 3 skipped，API/job/operation/reconciliation smoke、普通/澄清 CLI、showcase、全部离线 artifact/verifier 与 HTTP benchmark 均通过；Docker daemon 仍未运行，live weather smoke 未执行；
- 当前没有真实用户 report。下一外部里程碑是用知情试用获得至少 5 份 decision、5 份 outcome 和 5-10 个可归类 badcase；在此之前不报告采纳率、完成率、满意度或提升幅度。

## 2.7 v6.2 Provider-bound Reconciliation 验收

目标：让调用后不明的副作用在不重放写操作的前提下收敛；同时把 CLI/Streamlit 的旧直接 mock 预订替换为可见的请求、独立审批、worker、回执链，确保简历演示与底层安全主张一致。

验收契约：

- `uncertain` 只有在已持久化 provider operation ID 时才能进入状态核对；lease 到期但没有 reference 的操作 fail closed，必须人工处置；
- `side_effect_status_lookup_v1` 严格绑定 operation ID、request SHA、provider、provider operation ID 与 sandbox flag，并对 raw provider payload 计算 SHA；篡改任一 reference 时拒绝落库；
- `confirmed/rejected` 生成与 lookup response SHA 绑定的 receipt 并解析为 `succeeded/failed`；`still_unknown/not_found` 保持 uncertain，任何分支都不自动重试原写操作；
- reconciliation 使用独立 `operations:reconcile` scope；跨 tenant 返回 404，状态不适用或缺 reference 返回 409，evidence 可通过只读 endpoint 回放；
- `side_effect_operation_reconciliations` 只追加，保存 actor、outcome、provider reference、完整 lookup evidence/evidence SHA 与可选 receipt SHA；event 再绑定 lookup evidence SHA；
- CLI 的 `--book` 只创建待审批请求，必须显式追加 `--approve-sandbox-booking` 才由不同演示 principal 审批并让 worker 执行；Streamlit 用三个按钮分别展示请求、审批、执行，对 uncertain 另给只读核对按钮；旧 `mock_book` 不再是默认 UI/CLI 预订入口；
- 5-case artifact 与独立 verifier 新增 status resolution、lookup binding、reconciliation audit 三项指标，连同 v6.1 指标共 12 项均为 1.000；定向测试 74 项通过；
- 完整 `make check` 通过：pytest 661 passed / 3 skipped，API/job/operation/reconciliation smoke、普通/澄清 CLI、showcase、全部离线 artifact/verifier 与 HTTP benchmark 均通过；Docker daemon 仍未运行，live weather smoke 未执行；
- 补偿仍未实现。未来取消必须是带 `compensates_operation_id`、新 quote、独立 approval/idempotency/receipt 的 `restaurant_cancellation` 写 operation，不能被 reconciliation 或自动 retry 隐式触发；
- 边界：provider execute/lookup 都是本地确定性 sandbox，不能把状态核对成功写成真实订单恢复率，也没有第三方签名、真实客服 handoff、PII vault、secret manager、retention 或多实例执行证据。

## 2.8 v6.1 Approval-gated Sandbox Side Effects 验收

目标：把旅行规划研究里“推荐是读操作，预订是高风险写操作”的边界落实为可演示状态机；先在完全无真实外部副作用的沙箱里证明审批绑定、幂等、审计、回执和不确定态语义，不把 mock 成功字符串冒充订单。

验收契约：

- 只接受 `restaurant_booking + provider=bj-pal-sandbox + sandbox=true`；action、quote reference/有效期/币种/金额/terms hash 与 policy 共同生成 request SHA，再与 operation/tenant/approval expiry 绑定 approval SHA；任何真实 provider 在持久化前拒绝；
- `operations:request/read/approve` scope 独立；requester 与 approver 必须是同 tenant 的不同 principal，自批 403，跨 tenant 404，指纹变化 409，报价或 approval TTL 到期 410；
- tenant-local `Idempotency-Key` 只在完整 fingerprint 相同才复用；已批准 operation 由独立 worker 单次 claim，调用前失败为 failed/no receipt，provider 明确拒绝为 failed/receipt，成功为 succeeded/receipt；
- worker 调用后结果不明或 execution lease 过期进入终态 `uncertain`，不会自动 reclaim/retry；这是为了避免真实语义中的重复预订，后续必须由订单查询或人工处置收敛；
- receipt 绑定 operation ID、request SHA、provider、sandbox、provider operation ID、outcome、executed time 与 provider response hash，并对 canonical envelope 再计算 SHA；operation event trigger 禁止 UPDATE/DELETE；
- 4-case artifact 由独立 verifier 从 raw operation/event/receipt 复算职责分离、approval 绑定、幂等、tenant 隔离、过期 fail closed、receipt、append-only、sandbox enforcement 和 uncertainty no-retry；9 项指标均为 1.000；
- 完整 `make check` 通过：pytest 653 passed / 3 skipped，API/job/operation smoke、普通/澄清 CLI、showcase、全部离线 artifact/verifier 与 HTTP benchmark 均通过；Docker daemon 仍未运行，live weather smoke 未执行；
- 边界：没有真实餐厅/支付/消息调用；raw provider response 未持久化，当前 verifier 只能复核 receipt envelope 与绑定字段，不能验证第三方签名；无订单状态查询、补偿、客服 handoff、PII redaction、secret manager、retention 或多实例执行。

## 3. v6.0 Tenant Admission and Fair Scheduling 验收

目标：防止单个 tenant 用合法高优先级请求占满单机 durable queue，并在不破坏 priority 语义的前提下让同有效优先级 tenant 轮流获得 worker；借鉴本仓 travel-planner 生态研究里“平台层拥有 schema、限流、幂等、审批、持久化与审计”的边界，但不为简历关键词盲目引入 Redis。

验收契约：

- credential registry 可为 tenant 声明 `tenant_active_job_limit` 和 `tenant_submission_limit_per_minute`；同 tenant 多 principal 必须使用一致策略，否则 503 fail closed；缺省为 100 active、60 accepted submissions/minute；
- submit、manual replay 与 job clarification continuation 都在 `BEGIN IMMEDIATE` 内先结算过期 deadline，再原子计算 queued/running active 数和过去 60 秒已接受新 job 数；active cap 优先，滑动窗口拒绝时返回 429 与 `Retry-After`；
- 相同 tenant/idempotency policy 的重试返回已有 job，不消耗新 quota；admitted、rejected 和 idempotent reuse 都追加到 `planning_job_admission_events`，trigger 禁止 UPDATE/DELETE，`jobs:read` 只能按本 tenant cursor 读取；
- `tenant_fair_priority_aging_v2` 先按 effective priority 降序，再按 tenant 的 `last_claimed_event_id` 升序，最后按 eligible/created/job FIFO；scheduler state 与 claim event 在同一事务更新，event 保存 priority/fairness policy 和选择前 cursor；
- v5.9 schema 以 additive migration 新增 admission audit 与 tenant scheduler state，不重写 job/event history，foreign-key check 保持通过；
- 4-case scheduling artifact 和 6-case access-control/admission artifact 均由独立 verifier 从 raw candidate、HTTP outcome 与 audit event 复算；前者 6 项、后者 10 项指标均为 1.000；
- 完整 `make check` 通过：pytest 641 passed / 3 skipped，API/job smoke、普通/澄清 CLI、showcase、全部离线 artifact/verifier 与 HTTP benchmark 均通过；Docker daemon 仍未运行，live weather smoke 未执行；
- 边界：submission rate 只计已接受的新 job，不是抗攻击的 raw-attempt limiter；audit 无 retention，恶意拒绝流量仍可能放大存储；SQLite 事务只保证单库单机一致性；新注册 tenant 会先获得一次轮转机会，当前静态 registry 假设 tenant 创建受控；不保证跨实例全局配额、严格公平、吞吐或启动 SLA。

## 4. v5.9 Identity-aware Control Plane 验收

目标：把共享管理员 token 收敛为可审计的 principal/tenant/scope/priority 边界，避免一个凭证读取或控制所有任务，也避免普通调用方任意提交 priority 9；不把本地 registry 冒充企业 IAM。

验收契约：

- `BJ_PAL_CONTROL_PRINCIPALS_JSON` 只保存原 token 的 SHA-256，并严格映射 `principal_id`、`tenant_id`、scope 与 `max_priority`；缺失/非法 registry 503，错误凭证 401，scope/cap 越权 403；旧 `BJ_PAL_CONTROL_TOKEN` 仅保留为 `default/legacy-control` 兼容模式；
- route 固定拆成 `jobs:submit/read/control/replay`，job continuation 使用 submit scope；tenant/principal 由认证上下文注入，不接受客户端自报；
- job/summary/submitted event 持久化 `tenant_id/submitted_by`；list/get/event/SSE/cancel/replay/continuation 和 idempotency key 全部 tenant-scoped，跨 tenant 统一 404，外租户 continuation/cap 失败不会消费 session；
- v5.8 及更早数据库保留 job row、event ID/history 和 foreign key，旧行映射 `default/legacy-migration`，全局 unique idempotency 改为 tenant-local partial unique；
- 4-case synthetic artifact 通过真实 FastAPI + SQLite 生成 route scope、priority admission、tenant isolation 与 continuation isolation raw outcome；独立 verifier 从 principal policy 重算状态/error/identity，校验 artifact SHA，并扫描测试凭证是否泄露；
- 该门只证明单机应用层隔离：worker 仍跨 tenant 全局 claim；没有 OAuth/OIDC、动态授权、数据库 RLS、token 过期/轮换/撤销、tenant quota/公平性、加密或多实例证据。
- 完整 `make check` 已通过：pytest 631 passed / 3 skipped，API/job smoke、普通/澄清 CLI、showcase 29/29、行为/检索/需求/约束/澄清/观测/调度/访问控制/天气 artifact 与独立 verifier 均通过；HTTP benchmark 20/20、request ID mismatch 0。

## 5. v5.8 Fair Durable Scheduling 验收

目标：让紧急任务可优先处理，同时用 aging 防止普通任务长期饥饿，并把选择依据持久化为可独立复算的事件证据。

验收契约：

- job 接受 0-9 的基础 `priority`；幂等键重用时 priority 变化视为不同操作，clarification continuation 与 replay 保留原策略；
- `priority_aging_v1` 从任务真正 eligible 的时刻开始，每等待 60 秒把有效优先级提升 1，最高 9；同级按 `eligible_at → created_at → job_id` FIFO；
- retry backoff 到期前不进入候选集，也不累计 queue wait；lease reclaim 从原 lease 到期时开始计算等待；
- claim/lease-reclaim 事件保存 policy、base/effective priority、eligible time 和 `queue_wait_ms`；旧 v4.3-v5.7 数据库默认迁移为 priority 0 并保留 row/event；
- 3-case synthetic artifact 覆盖优先级抢占、低优先级在默认 deadline 前 aging 至 9 后胜出、以及高优先级 backoff 排除；verifier 不调用生产排序函数，而是从 raw candidate timestamps 独立复算；
- aging 约束的是有 worker 领取时的选择顺序，不保证任务在某个时刻启动；v5.9 加入 principal priority cap，v6.0 又在同有效优先级内加入 tenant 轮转与单机准入，但仍没有在线 reprioritize、跨实例队列或启动 SLA。
- 完整 `make check` 已通过：pytest 622 passed / 3 skipped，API/job smoke、普通/澄清 CLI、showcase 29/29、行为/检索/需求/约束/澄清/观测/调度/天气 artifact 与独立 verifier 均通过；HTTP benchmark 20/20、request ID mismatch 0。

## 6. v5.7 Execution Observation 验收

目标：把分散的本地 trace、tool log 和 job event 补成 canonical 请求级观测证据，同时不虚构 mock token/成本，也不把内存 capture 冒充生产监控。

验收契约：

- `PlanningService` 为 preflight、generate、probe/replan、plan-trace persistence 和 data-profile load 建立单根父子 span tree；原 Planner/LLM span 自动挂入同一 trace；
- 同步 HTTP 用 `X-Request-ID`、durable worker 用 `job_id` 关联 execution；结果进入同步响应、澄清缓存结果和 job artifact；
- 公开 observation 排除 span attributes、prompt、用户输入和 user ID，只保存名称、父子 ID、相对耗时、状态、计数及 provider 实际回报的 token；
- token completeness 明确区分 `complete / partial / unavailable / not_applicable`；mock 未回报时保持 `unavailable`，不按字符数估算 token 或成本；
- `artifact_sha256` 绑定 observation 全字段；3-case synthetic contract verifier 独立复算父子树、操作计数、token 语义、SHA 和敏感标记排除；
- optional JSONL/OTel exporter 不影响业务结果；OTel adapter 改为 span 开始时显式传递 parent context，但本轮未安装 collector 或宣称线上 trace 已验收；
- v5.7 observation 本身仍不聚合 queue wait；v5.8 只在 durable claim event 保存单任务等待，仍无多实例 trace 汇聚、provider freshness dashboard、真实模型成本、错误/重试率聚合或生产 SLO。
- 完整 `make check` 已通过：pytest 613 passed / 3 skipped，API/job smoke、普通/澄清 CLI、showcase 29/29、行为/检索/需求/约束/澄清/观测/天气 artifact 与独立 verifier 均通过；HTTP benchmark 20/20、request ID mismatch 0；`git diff --check` 和 Python compileall 通过。Docker daemon 当前不可用，Open-Meteo live smoke 未执行。

## 7. v5.6 Clarification Continuation 验收

目标：让 `clarification_required` 不再是要求客户端手工重建请求的一次性错误，并保证用户选择可追溯、可重放、不会再次触发同一冲突。

验收契约：

- 原请求、request SHA、decision/options SHA、delivery、job deadline/priority policy 和 TTL 原子落入独立 SQLite session；
- 答案形成 `clarification_resolution_v1`，Requirement Gate/Constraint Ledger 只消费 code/field 匹配且类型合法的 resolution；
- 同一答案重复提交返回缓存计划或同一 job，不同答案返回 409；执行 lease 防止并发 owner 同时续跑；
- 多个冲突逐层处理，父 continuation 固定指向同一个下一问；completed session 在原 TTL 到期后也变为 410；
- 同步、durable job、Streamlit 与 CLI 均有可操作入口；job continuation 保留原 deadline/priority policy 并受控制面 Bearer 保护；
- 16-case synthetic artifact 由独立 verifier 复算一步续跑、有效值、同冲突复发、指纹、恢复、选项、重放与不同答案 fencing；当前成功率指标 1.000、同冲突复发率 0；
- 当前只证明确定性 preflight continuation，不代表开放域多轮对话、用户满意度、加密持久化或 exactly-once 副作用。
- 完整 `make check` 已通过：pytest 606 passed / 3 skipped，API/job smoke、普通/澄清 CLI、showcase、行为/检索/需求门控/约束账本/澄清续跑/天气 artifact 与 HTTP benchmark 均通过；`git diff --check` 和 Python compileall 通过。

## 8. v5.5 Constraint Ledger 验收

目标：不再让自然语言里的明确人数、预算、时间、时长和忌口在进入 Planner 时退回 schema 默认值，并让每个生效值可追溯。

验收契约：

- 同步、durable submit、worker、UI 和 CLI 共用 `PlanningPreflight`；
- 支持 persona、人数、儿童/年龄、忌口、步行半径、人均预算、开始时间和时长；原始输入保持不变；
- 每个字段记录 effective/text value、source、evidence、hardness 和 outcome；文本派生值不污染 `provided_fields`；
- 显式字段与文本冲突时返回一个结构化 409，Planner/tool 不运行且 job 不入队；忌口取安全并集；
- 规范化请求 durable round-trip 后再次预检保持幂等；
- 30-case synthetic artifact 由独立 verifier 复算 extraction、false extraction、constraint preservation、conflict、rewrite 和 idempotency；当前所有成功率指标 1.000、false rate 0；
- 完整 `make check` 已通过：pytest 581 passed / 3 skipped，API/job smoke、CLI、showcase、行为/检索/需求门控/约束账本/天气 artifact 与 HTTP benchmark 均通过；`git diff --check` 通过。

## 9. v5.4 需求门控验收

目标：不再让历史指代、相对位置缺上下文或片区冲突静默进入 Planner，同时不把每个可逆默认值都升级为追问。

验收契约：

- decision 只有 `proceed / proceed_with_assumptions / clarification_required` 三种状态；
- 文本片区可规范为 canonical area，普通缺省片区形成透明 assumption；
- 无法解析的历史/序号指代、相对位置和字段冲突在任何 Planner/tool 调用前停止；
- 每次只返回一个 uncertainty layer，问题带 2-3 个选项；
- 同步 API 与 durable submit 都返回结构化 `409 clarification_required`，不能把待澄清请求排队；
- worker 遇到异常持久的待澄清请求 non-retryable failed；
- 20-case synthetic golden artifact 保存逐例原始 decision，由 verifier 复算 trigger rate、false clarification rate、required recall 和补充后 gate executability；
- 完整 `make check` 已通过：pytest 564 passed / 3 skipped，API/job smoke、CLI、showcase、行为/检索/需求门控/天气 artifact 和 HTTP benchmark 均通过；`git diff --check` 通过。

## 10. v5.3 时间轴一致性验收

目标：让 `start_time` 成为数学上可执行的到站时间，不再把路程挤进上一站停留时间，也不把超出用户窗口的方案描述为合规。

验收契约：

- 相邻 POI 满足 `current.start >= previous.start + previous.duration + current.travel`；
- 默认 4 小时 fixture 在显式最小停留下压缩到窗口内；
- 未验证 route 使 schedule 为 `partial`，不是零成本成功；
- 安全压缩后仍放不下时返回 `overrun_minutes`，不自动删站；
- 初始 Planner 与每次成功 Replan 都刷新 schedule；
- `schedule_reconcile_v1` 同时进入 Plan、RerouteEvent、HTTP 和 durable artifact；
- 完整 `make check` 已通过：pytest 549 passed / 3 skipped，API/job smoke、CLI、showcase、行为/检索/天气 artifact 和 HTTP benchmark 均通过；`git diff --check` 通过。

## 11. v5.2 路线一致性验收

目标：修复局部换点后路线 snapshot 自相矛盾的问题，不让新站缺路线、下一站却沿用旧 POI leg。

验收契约：

- 中间 POI 变化后，进入该站和离开该站的 leg 都重新计算；
- 刷新开始时先清空整份计划的 route fields，backend 失败不能恢复旧值；
- 缺 POI/坐标的 step 会截断相邻关系，不允许从前一站跨过去连接后一站；
- `route_refresh_v1` 同时进入 `Plan.route_context`、`RerouteEvent.route_refresh`、同步 HTTP 和 durable artifact；
- Planner 初次路线与 Replanner 路线共用一个可注入入口；
- 证据明确标记 `cached/estimated`，不把本地路线称为实时导航；
- 完整 `make check` 已通过：pytest 545 passed / 3 skipped，API/job smoke、CLI、showcase、行为/检索/天气 artifact 和 HTTP benchmark 均通过；`git diff --check` 通过。

## 12. 已完成的 v4.9 验收记录

目标：关闭匿名 durable control plane，并为任务增加独立于 lease/HTTP timeout 的持久生命周期上限；不把静态 token 伪装成用户权限系统，也不把 deadline 伪装成强杀外部调用。

验收：

- `make check PYTHON=.venv/bin/python` 全通过；
- `git diff --check` 通过；
- OpenAPI 包含 Bearer security 和 job list/cancel/replay/event/SSE/deadline 契约；
- control token 未安全配置时 503，缺失/错误时 401；token 不进入响应、job 或 event；
- job list 使用稳定插入游标并返回轻量摘要，不内联 artifact；
- queued cancel 立即终止；running cancel 在 workflow 边界、worker 返回或 lease 过期后收敛；
- cancelled job 不再 claim，取消优先于 success/retry；
- queued 到期不 claim，running 到期在 heartbeat/finish/retry/scan/safe boundary 进入 timed_out；
- cancel/deadline 竞态按持久时间先后决定，timed-out job 不接受 late success/retry；
- failed/dead-letter/timed-out replay 创建新 job、保留 source lineage、继承 deadline 秒数并获得新绝对截止时间，独立 key 保证操作幂等；
- 旧 v4.3/v4.7/v4.8 job/event schema 可迁移并保留状态，旧 job 不虚构过期时间；
- cancel/timeout/replay 状态和 append-only event 同事务提交或回滚；
- `api-smoke` 覆盖 auth/list/cancel/deadline/SSE；`job-smoke` 覆盖 dead-letter/timed-out replay/cancel；
- retrieval eval 固定 golden hash、legacy/candidate raw cases、Recall/MRR/多样性和边界说明；
- Memory L2 固定覆盖跨 session、否定语义、冲突 revision、确认/过期 gate 和用户隔离；
- performance artifact 保存逐请求 status/request ID/latency，独立复算 error rate、throughput 和 nearest-rank p50/p95/p99；
- 性能 gate 不使用绝对延迟阈值，也不把进程内 mock 结果写成生产 SLA；
- CI 配置构建非 root demo container；
- README、DESIGN、证据地图与实际代码一致；
- 不新增“真实、实时、生产级”但无验收依据的表述。

退出条件：本地门禁通过后，等待用户决定 commit、push、PR、远端 description/homepage 和 license。

## 13. P0：v5.0 天气 Provider 与合规验收

本地实现不等待外部凭证，但严格拆成两个证据等级：公开门禁只证明 offline contract；取得合法授权后才能提升到 live acceptance。

范围：

1. 已实现 `WeatherProvider`、`WeatherRequest/Hour/Snapshot` 和 Open-Meteo adapter，不新增 Weather Agent。
2. 已实现 bounded timeout、429/5xx/4xx/schema/unit taxonomy、共享 TTL cache 和显式 `stale_if_error`。
3. 已把 weather provenance、attribution、`retrieved_at/valid_until`、model/reference 和小时 decision context 接入 Plan。
4. 已让 Planner ranking 与 Probe/Reroute 复用同一快照，并保存每步 deterministic shelter class。
5. 已增加 authored synthetic fixture、`offline_contract_only` artifact、独立 verifier 和默认 CI gate。
6. 已增加 opt-in live smoke；免费端点须 noncommercial ack，宣传/商业部署须 customer key 或 self-hosted endpoint。
7. 待外部条件：适用授权、费用预算、可保存的脱敏 live sample；本次不运行 live smoke，不提升 acceptance level。

验收不是“接口返回 200”。v5.0 本地退出条件是完整门禁、artifact 可复算和所有 live 主张 fail closed；真正 live 退出条件仍是重复合法访问、时效/来源正确、失败样本可分类且符合授权与限流。

## 14. P2：durable execution 继续生产化

在真实模型或 provider 让单次任务明显变长后启动：

- 已完成：单机 lease heartbeat、过期 owner fencing、指数退避、最大尝试次数、dead-letter 状态；
- 已完成：heartbeat/retry/dead-letter durable events 和 `Last-Event-ID` SSE 投影；
- 已完成：dead-letter 状态查询、幂等人工重放、queued/running/expired cooperative cancel 与 lineage events；
- 已完成：fail-closed 静态 Bearer 控制面、绝对 job deadline、timed_out event/SSE/list/replay 与确定性 cancel race；
- 已完成：带 aging 的 0-9 优先级、eligible-time FIFO、retry backoff 排除、claim queue-wait event 与独立 scheduling verifier；
- 已完成：静态哈希 principal registry、route scope、tenant namespace、priority cap、旧库迁移与独立 access-control verifier；
- 已完成：单机 tenant active/accepted-submission 准入、append-only admission audit、同有效优先级最久未服务 tenant 轮转及独立 verifier；
- 待完成：外部 IdP/动态 RBAC、token 过期/轮换/撤销、数据库 RLS 或等价存储隔离、跨实例全局配额/调度、audit retention/abuse protection、在线 reprioritize 与单次 provider/model 调用的主动中止；
- 扩展现有 event log：细粒度业务进度；
- 为 SQLite→PostgreSQL 增加显式迁移/cutover/rollback，并补连接池、容量、故障恢复与多主机负载证据；
- 已完成本地请求级 execution observation、request/job correlation、阶段 span、调用/业务计数和真实 token completeness；
- 待完成经授权的远程 OTLP collector 与 metrics backend：持续汇聚 queue wait、run latency p50/p95/p99、error/retry、provider freshness 和真实成本。

验收：杀死 worker 后纯计算 job 能恢复；断开 SSE 后重连不丢事件。真实副作用不能复用 job 的自动 reclaim 语义，必须走 v6.2 operation/reconciliation 状态机。

## 15. P3：真实预订副作用

触发条件：已取得真实供应商接口、测试环境、业务授权和可回滚策略。

必须按顺序实现：

1. 已完成 sandbox：quote/reference/validity/terms 契约；
2. 已完成 sandbox：请求/审批职责分离与 approval fingerprint；
3. 已完成 sandbox：`operation_id`、tenant-local 幂等键和单次执行；
4. 已完成 sandbox：side-effect receipt 与 append-only audit 独立持久化；
5. 已完成 sandbox：provider-reference-bound 只读状态核对、raw evidence SHA 与 uncertain 不自动写重试；
6. 待完成真实环境：供应商授权、测试环境、真实订单查询 acceptance 和第三方签名回执；
7. 待完成真实环境：独立重新审批的补偿 operation、审计 retention 和客服 handoff；
8. 待完成真实环境：PII redaction、secret 管理和数据保留策略。

Checkpoint 不能代替 receipt；模型说“已下单”也不是订单证据。

## 16. P4：真实用户与业务证据

- 从 5-10 个真实用户的任务失败中建立 acceptance/golden set；
- 明确定义 task success、人工通过、满意度和转化，不混成一个分数；
- 用 pairwise 或人工 rubric 比较版本，报告样本量与置信区间；
- 将真实 failure case 回流 L2/L3，而不是只添加容易通过的 fixture；
- 对 provider latency、freshness、reroute 接受率和放弃率做看板。

AI 模拟访谈和 mock 回归只作为假设/工程信号，不能提升真实用户验证状态。

## 17. 延后或明确不做

| 项目 | 当前决定 |
|---|---|
| 三语言重写 | 不做；增加维护成本，不能提升本项目核心证据 |
| 多 Agent debate | 不做；没有证明收益，增加延迟与失败面 |
| A2A | 延后到确有独立部署/跨团队互操作需求 |
| MCP | 延后到 provider/tool 需要跨进程或跨语言复用 |
| LangGraph | 延后到出现复杂循环、人工审批和 checkpoint 需求 |
| 多城扩展 | 在单城真实 provider/acceptance 闭环之后 |
| 自动“学习用户” | 在明确授权、forget/retention 和真实反馈质量门建立之后 |

## 18. 需要用户授权的外部动作

以下不属于本地实现的默认授权范围：

- commit、push、创建 PR；
- 修改 GitHub description、homepage/topics；
- 选择并添加开源许可证；
- 部署在线 API 或修改 GitHub Pages；
- 配置真实 provider/LLM 凭证；
- 接入真实预订或发送外部消息。

本地实现和验证完成后，应单独确认这些动作。
