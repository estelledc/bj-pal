# BJ-Pal 工程证据地图

> 目的：让招聘者和面试官能从主张直接跳到实现、测试和限制，避免 README 叙事大于代码事实。

## 1. 参考研究如何落到本项目

本轮重构参考了 2026-07-17 固定快照的多 Agent 旅行规划生态研究，重点吸收以下模式：

- 目标样本 `bcefghj/multi-agent-travel-planner`：串行、并行和循环易讲，但共享可变 state、随机 Mock、错误字符串和“预算直接改价”不适合作为生产答案。
- TripSage 类产品项目：类型化工具、Schema gate、缓存/限流、审批、持久化与可观测性应是平台能力。
- TripSage 的 bounded tool loop，以及跨项目比较中的“最大轮次 + token/时间预算 + 失败状态”：BJ-Pal 不复制动态多 Agent 循环，而是在现有单 Agent + tools 主链增加服务端 request-local budget 和可校验终止原因。
- 跨项目比较中的“单 Agent + tools 是否足够、额外 token/延迟/故障面是否有收益证据”：v6.8 不凭框架名作判断，而以同输入 single/multi artifact 复算质量、调用与失败边界；当前 synthetic mock 证据支持保持单分支默认。
- Multi-Agent AI Travel Advisor：供应商读取与格式归一留在 data plane，LLM 只做意图、补充知识和合成。
- MAF/A2A 类实现：长任务需要 durable job、run/artifact 身份和恢复语义；SSE 是投影，不是任务本身。
- LangGraph/Agent Service Toolkit 类实现：业务图与 HTTP route 解耦，持久资源有明确生命周期。

BJ-Pal 没有复制三语言、A2A 或某个编排框架；只采纳能解决当前已知问题的模式。

## 2. 主张、证据与边界

| 主张 | 实现入口 | 自动验证 | 当前边界 |
|---|---|---|---|
| Delivery 共用主链 | `src/application/contracts.py`、`planning_service.py` | `tests/test_application_service.py` | 部分历史 helper 仍可直接调用 agent/tool |
| 执行关键缺口先澄清 | `src/application/requirement_gate.py`、`PlanRequest.provided_fields` | `tests/test_requirement_gate.py`、HTTP/job integration | 高精度确定性规则，不覆盖开放域指代和真实会话历史解析 |
| 澄清策略可量化 | `evals/requirements/golden.json`、`run_requirements.py` | `tests/test_requirement_eval.py`、`make eval-requirements` | 20 条 hand-authored synthetic case；通过门控不等于完整计划或用户满意 |
| 自然语言约束有 typed ledger | `src/application/constraint_ledger.py`、`preflight.py`、`PlanResult.constraints` | `tests/test_constraint_ledger.py`、HTTP/job integration | 只覆盖当前声明字段与高精度中文规则，不是开放域 NLU |
| 约束抽取与冲突策略可量化 | `evals/constraints/golden.json`、`run_constraints.py` | `tests/test_constraint_eval.py`、`make eval-constraints` | 30 条 hand-authored synthetic case；全指标 1.000 不代表真实用户理解率 |
| 澄清可从原请求继续 | `src/clarifications/*`、`ClarificationResolution`、两个 continuation route | resolution/session/TTL/lease/HTTP/UI/CLI tests、`make demo-clarification` | 单机明文 SQLite；sync 依赖 capability ID；job continuation 已 tenant-scoped，sync 仍无用户身份；不承诺 exactly-once |
| 澄清续跑证据可复算 | `evals/clarifications/golden.json`、runner/verifier | `tests/test_clarification_eval.py`、`make eval-clarifications` | 16 条 hand-authored synthetic case；不是开放域多轮理解或真实用户满意度 |
| HTTP 契约稳定且 composition root 有界 | `src/http_api/app.py`、`routes/{system,planning,jobs,operations,outcomes}.py`、`responses.py`、`public_surface.py`、`schemas.py` | `test_http_api_architecture.py` 固定 5 个 router 的 method/path/name、32-path 去版本 OpenAPI SHA、public 3-path surface 与 `app.py <= 400`；另有 `test_http_api.py` 和 API smoke | 证明本次重构行为等价与结构边界；API v1 仍未承诺长期兼容期，单个 domain router 仍可继续按职责细分 |
| 数据面类型化 | `src/providers/contracts.py`、`weather.py` | `tests/test_data_provider.py`、`test_weather_provider.py` | POI/UGC/路线仍是 SQLite；天气 live 未授权验收 |
| 并行结果独立、单点合并 | `src/providers/sqlite_demo.py` | provider merge/partial failure tests | 共享 8-worker executor 只约束单进程资源，不代表多实例容量 |
| Query UGC 检索可解释 | `src/retrieval/ugc.py` | `tests/test_ugc_retrieval.py` | 领域扩展是小型规则表，不是通用语义检索 |
| 检索改动可量化 | `evals/retrieval/golden.json`、`run_retrieval.py` | `tests/test_retrieval_eval.py`、`make eval-retrieval` | 19 条 synthetic case；不代表线上准确率 |
| 数据 provenance 可见 | `DataEvidence`、`Plan.data_provenance` | 默认 HTTP E2E 断言四类来源 | demo weather/POI 仍是 synthetic，不含 live retrieved_at/valid_until |
| 天气配置失败关闭 | `OpenMeteoConfig`、`create_weather_provider` | free/commercial/self-hosted configuration tests | 只能证明本地 gate，不证明调用者的法律身份或授权 |
| 天气 freshness 不伪装 | `OpenMeteoWeatherProvider` shared TTL/stale cache | cache hit、stale-if-error、429/unit drift tests | 进程内 cache；未用真实 failure sample 验收 |
| Planner/Probe 共用天气证据 | `Plan.weather_context`、`Step.weather_shelter` | HTTP E2E、weather provider/acceptance tests | 默认 synthetic fixture；没有预报准确率证据 |
| 天气 artifact 可复算 | `evals/weather/*` | `tests/test_weather_acceptance.py`、`make eval-weather` | `offline_contract_only`，明确不是 live acceptance |
| Replan 保持活动语义 | `ReplacementPolicy`、`replan_step` | `tests/test_replan_policy.py`、demo rehearsal | 正餐/加餐/休息/天气四类规则；不是开放域语义理解 |
| Replan 过滤过程可解释 | `RerouteEvent.replacement_policy` | HTTP/durable contract tests | 保存计数和策略，不代表替补被用户接受 |
| Replan 后路线不沿用旧值 | `tools/route_enricher.py`、`Plan.route_context`、`RerouteEvent.route_refresh` | `tests/test_route_enricher.py`、`test_replan_policy.py`、HTTP E2E | 本地缓存/估算；不是实时路况，缺数据时显式 partial |
| 时间轴计入路程并尊重窗口 | `agents/schedule_reconciler.py`、`Plan.schedule_context`、`RerouteEvent.schedule_refresh` | `tests/test_schedule_reconciler.py`、Application/HTTP/Replan tests | 最小停留是版本化规则；不自动删站，未知路线 partial，放不下 overrun |
| provider 部分失败不吞掉 | `ProviderIssue`、`Plan.data_warnings` | `test_provider_preserves_partial_failure...` | required/optional 分类仍较粗 |
| durable job 可恢复 | `src/jobs/ports.py`、SQLite/PostgreSQL repositories、`service.py` | heartbeat/fencing/retry/dead-letter/deadline；PostgreSQL 17 下 4 个独立进程无重复 claim、job smoke | SQLite 默认；PostgreSQL 适配器不是 broker，也未做生产容量/故障恢复或 SQLite 在线迁移 |
| durable claim 有优先级且同级轮转 tenant | `tenant_fair_priority_aging_v2`、`priority_aging_v1`、两类 store transaction | priority/preemption/starvation/backoff/tenant-rotation tests、`make eval-scheduling`、PostgreSQL cross-process test | PostgreSQL 用短 advisory-lock transaction 换确定性；不是严格公平、启动 SLA、高吞吐队列或在线 reprioritize |
| 调度证据不信任生产排序自报 | `evals/scheduling/*` | `tests/test_scheduling_eval.py`、独立 verifier | 4 条 synthetic contract fixture；无多实例争用、队列吞吐或真实 workload 证据 |
| job 事件与状态原子落库 | `planning_job_events`、backend-neutral store transaction | retry/cancel/timeout/replay event 强制失败 rollback、SQLite/PostgreSQL append-only trigger tests | 无远端 broker、pub-sub fanout 或签名日志 |
| event cursor 与 SSE 可恢复 | JSON `events` + SSE `events/stream` | `Last-Event-ID`、query precedence、bounded timeout、timed-out terminal tests、API smoke | 最长 30 秒连接；无多实例 fanout |
| job 可查询和控制 | list/cancel/deadline/replay repository + HTTP routes | 轻量列表、queued/running/expired cancel、deadline 结算、竞态优先级、幂等 replay、lineage/rollback tests | 取消/deadline 不能强杀单次外部调用 |
| 控制面默认失败关闭 | `src/http_api/auth.py`、job route dependencies | 503 unconfigured、401 missing/wrong、403 scope/cap、constant-time hash compare、token 不回显、API smoke | 静态本地 registry；无外部 IdP、动态 RBAC、过期/轮换/撤销 |
| job 控制面按 tenant 隔离 | tenant-aware repository/service + HTTP dependencies | 同 key 跨 tenant、foreign list/cursor/get/event/SSE/cancel/replay/continuation/admission-audit tests、`make eval-access-control` | 应用层条件，不是 PostgreSQL RLS；静态 credential registry 仍非企业 IAM |
| 新 job 创建受原子 tenant admission 约束 | `tenant_admission_v1`、`planning_job_admission_events` | active/rate/idempotent/concurrent submit、replay、continuation；PostgreSQL 8 路提交 3 admitted/5 rejected | accepted-submission 不是网关 raw-attempt limiter；audit 无 retention/storage-DoS 防护；无容量结论 |
| 访问控制与准入证据可独立复算 | `evals/access_control/*` | `tests/test_access_control_eval.py`、artifact verifier | 6 条 synthetic contract case/10 metrics；不证明外部身份系统、入口 abuse protection 或多实例 quota |
| 副作用 approval 与动作/报价绑定 | `src/operations/repository.py`、operation HTTP routes | self-approval、fingerprint tamper、expiry、HTTP scope/tenant tests | 只允许 sandbox；静态 principal 不是企业审批系统 |
| 副作用幂等且 receipt 可校验 | `SideEffectOperationRepository`、`side_effect_receipt_v1` | operation repository/provider outcome tests、`make eval-side-effects` | receipt 绑定 envelope，但没有第三方签名或 raw provider response 复核 |
| 不确定执行不自动重试 | `SideEffectOperationService`、execution lease settlement | lease-expiry、post-invoke ambiguity、no-auto-reclaim tests | 无 provider reference 时仍需人工处置；没有写补偿 |
| 不确定状态只能按 provider reference 只读核对 | `side_effect_status_lookup_v1`、`reconcile_uncertain`、reconciliation HTTP routes | binding tamper、confirmed/still-unknown、scope/tenant、HTTP smoke | provider lookup 仍是确定性 sandbox，不是第三方订单证据 |
| 副作用审计只追加且强制沙箱 | operation event/reconciliation triggers、quote validation | event/evidence append-only mutation、live-provider rejection、独立 verifier | 单机 SQLite；无 retention、签名日志或多实例存储 |
| 副作用安全证据可独立复算 | `evals/side_effects/*` | `tests/test_side_effect_eval.py`、artifact verifier | 5 条 synthetic contract case/12 metrics；不是预订成功率、真实恢复率或生产安全认证 |
| 用户结果与精确方案版本绑定 | `src/outcomes/*`、HTTP feedback routes、Streamlit 结果反馈 tab | `tests/test_plan_feedback.py`、`test_http_api.py`、`test_ui_refactor.py` | capability 原文不落库、每 artifact/phase 只追加一次；当前真实 report 为 0，自报未经核验 |
| 小样本不展示比例 | `PlanFeedbackRepository.public_summary`、`/v1/feedback-summary` | minimum-sample tests、`make eval-outcomes` | 门槛固定为每 phase 5 份，只防明显误导，不代表统计显著性或样本代表性 |
| 用户结果契约可独立复算 | `evals/outcomes/*` | `tests/test_outcome_eval.py`、artifact verifier | 4 条 synthetic contract case/8 metrics；只证明绑定、幂等、过期、append-only、隐私最小化和门控，不包含真人 outcome |
| 知情试用绑定精确同意版本 | `TrialCohort`、`TrialParticipant`、trial HTTP/Streamlit routes | `tests/test_trial_evidence.py`、`test_http_api.py`、`test_ui_refactor.py` | notice/purpose/window/retention SHA 可核对；不采集身份，参与凭证不等于真人 |
| 试用分母、退出与冻结可审计 | `src/outcomes/repository.py` trial tables/events/snapshot | participant-phase uniqueness、withdrawal、closure、tenant isolation tests | 退出只影响开放/未来汇总；关闭后按 cutoff 冻结；真实 participant/report 均为 0 |
| 试用证据契约可独立复算 | `evals/trials/*`、`scripts/rehearse_trial.py` | `tests/test_trial_eval.py`、`test_trial_rehearsal.py`、`make eval-trials` | 6 条 synthetic contract case/13 metrics；含 retention signal 和目标清除事务，不证明真人、物理取证擦除或备份删除 |
| 真实试用操作有安全门 | `scripts/manage_trial.py`、`issue_trial_enrollments`、`purge_trial` | `tests/test_trial_operator_cli.py`、trial repository regression | 批量签发同事务、secret bundle 0600/不覆盖/不回显、低样本冻结二次确认；清除要求冻结/到期/精确目标/secret+backup attestation，并验证隔离、trigger/foreign key/rollback/receipt；本地 DB 权限不是远程 IAM |
| Synthetic outcome 不进入真人校准 UI | `plan_outcome.evidence_classification`、`agents/calibration_history.py`、`ui/calibration_timeline.py` | `tests/test_calibration_evidence_classification.py` | plan-level self-report 不能替代逐 step 的 `human_verified_step`；当前真人逐步样本为 0 |
| 旧 job schema 可迁移 | `PlanningJobRepository._migrate_legacy_schema` + v5.9 identity/v6.0 additive migration | v4.3/v4.7/v4.8 row/event preservation、v5.7 priority default、v5.8 identity/index、v5.9→v6.0 admission/scheduler table migration、foreign-key check | 只覆盖仓库已有 schema，不是通用迁移框架；旧 job 映射 default/legacy-migration，不补虚构 deadline |
| readiness fail closed | `src/data_profile.py` | manifest/metadata/count/SQLite 损坏 tests | 只检查本地运行数据，不检查外部依赖 |
| Planner 不隐式写 Memory | `planner.plan`、`infer_from_user_input` | `tests/test_user_memory_llm_intake.py` | 当前没有服务端 API 或跨设备入口 |
| Memory 冲突与生命周期显式 | `src/agents/user_memory.py` | `tests/test_user_memory_state.py`、L2 memory 5/5 | 规则状态机，不解决开放域事实消歧 |
| Memory 可永久删除 | `delete_memory` / `delete_all` | privacy purge tests | 本机文件备份若存在需由备份策略另行删除 |
| 幂等提交 | job repository + HTTP `Idempotency-Key` | repository/HTTP 同 tenant conflict 与跨 tenant reuse tests | tenant-local namespace；没有跨服务 operation registry |
| artifact 可验完整性 | job result canonical JSON + SHA-256 | artifact persistence test | 没有签名或远端不可变存储 |
| 规划结果可解释 | `src/agents/confidence.py`、`plan_tracer.py` | `tests/test_confidence_provenance.py` | 支持度不是概率 |
| 整份 plan trace 原子替换 | `plan_tracer.replace_steps` | `tests/test_plan_trace_atomic.py` | 只协调当前进程和 SQLite；不是分布式 trace store |
| Plan trace/outcome/calibration 有单一存储 owner | `storage/state_layout.py`、`plan_tracer.database_path`、calibration 跟随 resolver、`BJ_PAL_PLAN_EVIDENCE_DB` | `tests/test_plan_evidence_storage.py`、`test_calibration_evidence_classification.py` | 旧 plan rows 仍留在 shared DB；专用库仍是单机 SQLite |
| 既有 plan evidence 非破坏迁移 | dry-run 默认、显式 domain confirmation、read-only snapshot、count/logical SHA、receipt、quick-check、0600 atomic publish、WAL fail-closed | `scripts/migrate_plan_evidence.py`、`tests/test_plan_evidence_storage.py`、`make eval-state-layout` | receipt 证明一次 copy snapshot，不保证未来行不可变；不删除 legacy/backup，不是远端在线迁移 |
| 多状态域共享同一 verified-copy 内核 | `DomainSpec` 描述 owner/table/columns/schema/每表稳定排序键/legacy default；通用 snapshot/digest/metadata/atomic publish | `storage/verified_copy.py`、plan/prediction/memory 三组迁移测试 | 仍是进程停写假设下的单机 snapshot；不是通用在线 schema migration framework |
| Prediction feedback 独立 owner 且保留可变续写 | `prediction_feedback` resolver；prediction INSERT、actual UPDATE、定向 DELETE 均跟随 verified store | `tests/test_prediction_feedback_storage.py`、`make eval-prediction-state` | 本机迁移 33,791 行且旧表未删；只有 11 条 actual，不能声称预测已校准或业务有效 |
| User memory state/event 成对独立 owner | `storage/user_memory.py` resolver；state 与 event 同快照复制；replace/forget/delete 均跟随 verified store | `tests/test_user_memory_storage.py`、`make eval-user-memory-state` | 本机迁移 2,783 state + 5,572 event；旧两表未删；hard delete 不证明备份/取证擦除 |
| 部署可禁止 legacy shared-state 回退 | `storage/legacy_retirement.py` 注册 owner，核对已知表/receipt/source snapshot/resolver/专用库/tool-audit owner；`dedicated_required` 接入 `/readyz` | `tests/test_legacy_retirement.py`、`make eval-legacy-retirement`、`make audit-legacy-retirement` | 只读 audit 不删除旧行；domain registry 是显式清单，不是静态代码形式化证明；不覆盖在线 cutover、加密或备份擦除 |
| 本地发布边界逐文件可复核 | NUL-safe Git status；明确 release roots/deny rules；每项绑定 status/group/size/git mode/SHA；manifest 绑定 HEAD/branch/divergence；独立 verifier 重读字节复算 | `src/release_candidate.py`、`evals/release_candidate/verify.py`、`tests/test_release_candidate.py`、`make audit-release-candidate` | 当前只证明 333 个 dirty-tree 候选没有命中边界违规；secret/history/code review/remote CI/旧 Key 撤销仍是独立门 |
| 完整门禁不污染持久业务状态 | pytest session 临时 plan/memory/prediction 路径；`make check` 三个精确保留路径首尾清理；前后 legacy/dedicated 文件 SHA 对照 | `tests/conftest.py`、`tests/test_mutable_state_isolation.py`、`Makefile` | 只隔离验证写入；首次发现的污染行未猜测性删除，旧 shared rows 仍保留 |
| tool log session 不跨请求串号 | `tools/tool_call_log.py` ContextVar | `tests/test_tool_call_log_concurrency.py` | 新线程若需要继承上游 context，仍应显式传递 session |
| 工具日志持久化不复制任意自由文本/凭证 | `tool_call_audit_v2` 有界投影、稳定 error code、session sequence/SHA chain、append-only trigger、reset marker、legacy default hiding | `tests/test_tool_call_audit.py`、`test_tool_audit_eval.py`、`make eval-tool-audit` | 5-case artifact 中的固定隐私样本不等于完整 DLP；本地 SQLite 未加密/未远端不可变，整文件仍可删除；legacy 原字节未擦除 |
| 诊断日志与业务/学习状态物理隔离 | 默认 `runtime/tool_audit.db`、`BJ_PAL_TOOL_AUDIT_DB`、footprint 仅 v2、socket 临时重定向、旧共享库 no-auto-copy | `tests/test_tool_audit_storage.py`、`test_socket_http_benchmark.py`、5-case tool-audit verifier | 单机文件边界，不是数据库 RLS/加密/WORM；旧库仍在原位且不会自动擦除，operator 仍可把配置指回旧路径 |
| Durable job 失败签名可复算且不复制原始 payload | tenant-scoped 完整 event chain；14 类版本化 signature/action；未知 error 统一脱敏；1,000-event 拒绝截断；sanitized event/diagnosis 双 SHA；HTTP 与 0600 CLI | `src/jobs/diagnostics.py`、`PlanningJobService.diagnose`、`GET /v1/planning-jobs/{job_id}/diagnosis`、`scripts/diagnose_job.py`、`make eval-job-diagnostics`、`tests/test_job_diagnostics.py` | 14 条 synthetic fixture 只证明 deterministic triage；`runtime_or_dependency_unknown` 不是 root cause，未接生产 incident/SLO backend，也不证明建议能修复故障 |
| Durable workload 指标按闭合窗口可复算 | tenant-scoped `[start,end)`；只消费 `event.created_at < end` 的 prefix 并重建 as-of status；固定 status/terminal 分母；nearest-rank queue/run/terminal p50/p95/p99；retry/lease/timeout/dead-letter/cancel rates；1,000 job/10,000 event 拒绝截断；双 SHA、HTTP 与 0600 CLI | `src/jobs/workload_health.py`、`PlanningJobRepository.workload_evidence`、`GET /v1/planning-job-health`、`scripts/snapshot_job_health.py`、`make eval-workload-health`、`tests/test_workload_health.py` | 2 个 fixed synthetic window 只证明定义、聚合、历史稳定、空窗口和隐私契约；不输出实体 ID，但也不是 OTLP、多实例聚合、生产 SLO/容量/事故频率或告警验证 |
| 请求级执行观测可校验 | `agents/tracing.py`、`application/execution_observation.py`、`PlanResult.execution` | `tests/test_execution_observation.py`、HTTP/job integration、`make eval-observability` | `execution_observation_v2` 是与 exporter 独立的 in-process capture；无多实例聚合/生产 SLO；不保存 prompt/用户输入；mock token 明确 unavailable |
| OTLP 导出协议与隐私边界可验收 | `agents/tracing.py`、`agents/trace_export.py`、`GET /v1/trace-export-status` | `tests/test_trace_export.py`、`tests/test_otlp_export_eval.py`、`make eval-otlp-export`、独立 protobuf verifier | 本机 loopback 真实接收 OTLP/HTTP protobuf，且失败注入不影响业务；只证明 fixed synthetic protocol acceptance，不是远程 collector 回执、生产投递/告警/SLO/多实例；不采集 prompt/tool args/content |
| 运行告警决策可复算且小样本不冒充健康 | `src/monitoring/operational_alerts.py`、`GET /v1/operational-alerts`、`scripts/evaluate_operational_alerts.py` | 16 条 core/HTTP/CLI/eval 定向测试、`make eval-operational-alerts`、独立 verifier | 4 个 authored synthetic case 证明固定 policy、20 样本门、四态、source binding 与隐私；单 snapshot/fixed threshold 不是生产 baseline/SLO，无连续窗口、迟滞、Alertmanager delivery、事故处置或多实例证据 |
| 观测汇总不信任生产代码自报 | `evals/observability/*` | `tests/test_observability_eval.py`、独立 verifier | 3 条 synthetic contract fixture，只证明结构、计数、token 语义、SHA 和敏感标记排除 |
| 请求级执行预算在 N+1 前终止 | `agents/execution_budget.py`、`agents/tracing.py`、`PlanningService` | `tests/test_execution_budget.py`、HTTP 429、durable terminal no-retry | server-owned、ContextVar 隔离；默认限制逻辑 LLM/data/tool、每 call retry、实报 token 和安全检查点耗时；不能强杀已阻塞调用 |
| 执行预算证据可独立复算 | `evals/execution_budget/*` | `tests/test_execution_budget_eval.py`、`make eval-execution-budget` | 6 条 synthetic contract case；不证明真实模型金额、provider usage 完整性、跨实例 quota 或生产 billing |
| 模型输出在进入 Plan 前严格绑定 | `agents/model_output_contract.py`、`agents/planner.py`、`Plan.model_output_context` | `tests/test_model_output_contract.py`、HTTP/job integration | strict schema、request persona/area、候选 ID/名称/类别、去重、depart/时间序列；`meal/snack` 必须来自 food 候选；最多一次同 provider 修复且共用 request budget，仍失败则 non-retryable |
| 模型输出失败边界可独立复算 | `evals/model_output/*` | `tests/test_model_output_eval.py`、`make eval-model-output` | 13 条 hand-authored payload + 4 条 deterministic lifecycle case；独立 verifier 不调用生产 validator，但仍不证明真实模型错误分布、修复率或计划质量 |
| live 模型坏例、同口径选型与 3-case acceptance 可独立复核 | `evals/live_model/*` | `tests/test_live_model_observation.py`、`make verify-live-model-observation` | 历史 5 份经配置 DeepSeek client observation；pair verifier 证明同场景/预算 Flash 拒绝、Pro 首次通过，第一轮 suite 暴露 798 仅 2 个候选。每场景只跑 1 次，不能证明外部调用、签名身份、成功率、延迟分布或费用 |
| CSSwitch 凭证交接、provider usage 与质量 gate 可绑定复核 | `evals/live_provider/*`、`evals/run_live_provider_acceptance.py` | `tests/test_live_provider_acceptance.py`、`make verify-live-provider-acceptance` | 显式费用确认后只接受 owner-only regular 0600 active DeepSeek profile、Anthropic format、credential-free HTTPS 和显式模型；2026-07-21 一次固定场景首轮通过，1 call/1464 provider-reported token/约 28.9 秒，Key 在三份 0600 linked artifact 中精确命中 0。receipt/verifier 不能证明签名 provider、账单币种、成功率或服务端 credential 轮换/撤销 |
| 固定 live plan 的约束质量代理可复算 | `evals/live_model/quality.py`、`quality_verify.py`、`quality_artifacts/*` | `tests/test_live_plan_quality.py`、`tests/test_diet_evidence_filter.py`、`make verify-live-plan-quality` | v6.10 修复 798 候选覆盖与 `no_spicy/light_diet` 证据型餐饮过滤后，3 个新 Pro 样本候选为 26/16/21；独立 CLI 从脱敏 plan projection、POI facts、路线/时间轴和固定 policy 复算 9/9、12/12、11/11 必需检查，0 项不可评估。仍是 synthetic proxy，不评判 rationale、真实 freshness、用户偏好或 outcome |
| 编排选择可独立复算 | `agents/planner_tot.py`、`evals/orchestration/*` | `tests/test_planner_tot.py`、`tests/test_orchestration_eval.py`、`make eval-orchestration` | 旧 ToT 只是最多 3 个同构 planner 分支；线程显式继承 budget/trace/capture。3-case mock 当前 0 质量提升、0 输出变化、3× LLM/data，支持本项目保持 single default；不代表真实模型或全部多 Agent 方案 |
| 公开评测可复算 | `evals/run_public.py`、`verify_artifact.py` | `tests/test_eval_artifacts.py` | mock regression，不是线上 benchmark |
| HTTP 并发回归可复算 | `evals/run_http_benchmark.py`、`run_socket_http_benchmark.py`、`verify_http_benchmark.py` | `tests/test_http_performance_artifact.py`、`test_socket_http_benchmark.py`、`make benchmark-http benchmark-socket-http` | 同时覆盖 in-process ASGI 与独立 Uvicorn/localhost TCP；socket 强制 loopback、临时 runtime、子进程凭证剥离、readiness 与优雅退出。仍无 TLS/反向代理、多实例、真实模型/provider 或生产 SLA |
| OCI 发布在推送前失败关闭 | `Dockerfile`、`.dockerignore`、`compose.public.yaml`、`scripts/verify_release_tag.py`、`scripts/smoke_deployed_api.py`、`.github/workflows/publish-container.yml` | `tests/test_container_release.py`；`v6.27.0` workflow 全绿，hardened-container health/readiness/OpenAPI/fixed-plan smoke 通过；release/SHA/latest 与 anonymous manifest 同指向 digest `sha256:9ff768…98ffe6` | 单 runner、amd64、mock/synthetic 镜像，不是公网 API、TLS、多实例或 SLA；anonymous manifest HTTP 200 不是长期 availability；license 为 NOASSERTION |
| 公网 demo 与完整控制面失败关闭隔离 | `http_api.public_app`、`public_demo.py`、`public_server.py`、`public_healthcheck.py`、`docs/PUBLIC_DEMO.md` | 17 条定向测试、本机 Uvicorn smoke、PR/main Core Docker build 与 `v6.27.0` hardened OCI smoke；release/SHA/latest 和 anonymous manifest 绑定 `sha256:9ff768…98ffe6` | 进程级 aggregate limiter 不信任 proxy identity，也不跨实例；没有长期 HTTPS URL、WAF、外部 IdP、远程平台回执或 SLA；Cloudflare Quick Tunnel 本轮被本机策略 SIGKILL |

## 3. 失败语义检查表

| 场景 | 当前语义 | 是否持久化 |
|---|---|---|
| HTTP 字段非法 | 422 + `invalid_request` | 否，只写日志关联 ID |
| 执行关键上下文缺失 | 409 + `clarification_required`，返回一个问题、2-3 个 typed option、decision SHA 和 continuation URL | 独立 clarification session 保存；未进入 plan/job |
| 相同澄清答案重放 | 返回缓存同步结果或同一个 job；不再次 fan-out/入队 | resolution、result/job reference 保存 |
| 同一澄清改用另一答案 | 409 `clarification_resolution_conflict`，不覆盖第一次决议 | 原 resolution 保留 |
| 同步规划超过 server execution budget | 429 `execution_budget_exceeded` + privacy-minimized hash snapshot | 不持久化业务结果；只返回 policy/usage/reason，不含 prompt/用户输入 |
| durable 规划超过 server execution budget | terminal `failed` + `execution_budget_exceeded`，不走普通 execution retry | job 状态/事件持久化；当前只保存稳定错误码和脱敏 reason，不保存完整 budget snapshot |
| 首次模型输出 schema/候选/序列越界 | 只调用同一 provider 修复一次；仍受本请求 LLM budget 限制 | 成功时 `Plan.model_output_context=accepted_after_repair`；只保留稳定问题码和 SHA，不保存模型原值 |
| 第二次模型输出仍越界 | 同步/澄清 continuation 返回 502 `invalid_model_output`；durable job terminal `failed` 且不进入普通 retry | 同步不持久化业务结果；job 保存稳定错误码/脱敏 reason；路线、schedule 和 plan trace 不执行 |
| 澄清执行并发或 TTL 到期 | lease owner 外返回 409；到期返回 410，completed 也不例外 | session 状态/lease/expiry 保存 |
| 应用请求非法 | 422 + `invalid_planning_request` | 同步端点否；job 在 submit 前拒绝 |
| 单一候选类别失败 | 方案继续，`data_warnings` 标 retryable optional degradation | job 产物中会保留 |
| 显式忌口缺少正向结构证据 | food 候选 fail closed 为空，`diet_evidence_unavailable` 标 non-retryable optional degradation；模型契约禁止用非 food POI 补成 `meal/snack` | 方案可继续为无餐饮行程；不声称满足了数据库尚未覆盖的忌口语义 |
| 天气 timeout/429/5xx | 可选分支降级，稳定 retryable code 进入 `data_warnings` | job 产物中会保留 |
| 天气 4xx/schema/unit drift | 可选分支降级，non-retryable code 进入 `data_warnings` | job 产物中会保留 |
| 天气刷新失败且旧值仍在 stale window | 明确返回 `stale_if_error` 和 warning，不标 fresh | plan provenance/job artifact 保留 |
| 所有候选为空 | retryable execution failure；退避后重试，耗尽进入 dead letter | 是 |
| 路线坐标缺失或 lookup 失败 | 先清除全部旧 leg；不跨站连接，`route_context.status=partial` 并保存 warning | plan/job artifact 保留 |
| 路线未知或总时长放不下 | 未验证 leg 使 schedule partial；安全压缩后仍超窗则 `overrun`，不伪造可执行 | plan/job artifact 保留 |
| Web 客户端断线 | 同步请求可能丢响应；durable job 不丢 ownership | durable job 是 |
| worker 进程中断 | heartbeat 停止；lease 过期后可 reclaim，旧 owner 被 fenced | 是 |
| worker retryable 异常 | queued + available_at；指数退避，耗尽后 dead_lettered | 是 |
| 持久请求非法 | non-retryable failed + sanitized code/message | 是 |
| 异常持久的待澄清请求 | non-retryable failed + `clarification_required`，不重试 | 是 |
| queued job 被取消 | 同事务直接 cancelled；不会被 worker claim | 是 |
| running job 被取消 | 先 cancel_requested；workflow 安全边界/worker 返回/lease 过期后 cancelled | 是 |
| queued/running deadline 到期 | 不 claim/不重试/不覆盖完成；原子进入 timed_out | 是 |
| cancel 与 deadline 竞争 | 比较两个持久时间，较早信号决定 cancelled/timed_out | 是 |
| failed/dead-letter/timed-out 手动重放 | 原 job 不变；原子创建带 lineage 和新 deadline 的 queued job | 是 |
| event 行被修改/删除 | SQLite trigger 拒绝操作 | 原事件保留 |
| SSE 客户端断线 | 用 `Last-Event-ID` 从同一 store event_id 继续读取 | 是 |
| scope 或 priority cap 越权 | 403；不提交 job，job continuation 不消费 pending session | 否 |
| tenant active job cap 已满 | 429 `tenant_active_job_limit_exceeded`；幂等复用不误拒；continuation 释放 lease 后可重试 | rejected/idempotent/admitted decision 追加保存 |
| tenant 60 秒 accepted-submission cap 已满 | 429 `tenant_submission_rate_exceeded` + `Retry-After`；窗口边界后可重试 | rejected decision 追加保存；raw HTTP attempt 不计数 |
| 跨 tenant job/event/control/continuation | 统一 404，不泄露资源存在性；continuation 保持 pending | 否 |
| admission audit 被修改/删除 | SQLite trigger 拒绝；read route 只返回当前 tenant | 原 decision 保留 |
| operation 请求者尝试自批 | 403 `operation_self_approval_forbidden`；状态不变 | pending operation 与 attempted HTTP 结果分离；不追加伪 approval |
| operation 报价/审批已过期 | 410 `operation_approval_expired`，或 worker claim 前结算为 `expired` | operation 与 expired event 保留 |
| operation approval fingerprint 被篡改 | 409 `operation_approval_conflict`，不执行 | 原 request/approval SHA 保留 |
| sandbox provider 明确拒绝 | `failed` + rejection receipt | receipt/event 保留 |
| provider 调用后结果不明或 execution lease 过期 | 终态 `uncertain`，不自动 retry/reclaim | operation/event 保留，等待未来状态查询/人工处置 |
| 未确认 Memory 冲突 | 保留现值，写 hash-only `conflict_rejected` | 是，直到用户 hard delete |
| Memory 过期 | 不再进入 Planner；UI 仍可查看和清理 | 状态保留，除非 hard delete |
| 同 tenant 下同 key 不同请求、deadline 或 priority 策略 | 409 conflict | 原 job 保持不变；不同 tenant 可复用 key |
| artifact contract 损坏 | GET 返回 500 `invalid_job_artifact` | 原始行仍保留供诊断 |
| feedback capability 与 plan 不匹配 | 统一 404 `feedback_not_found`，不泄露 capability 属于哪个 plan | 否 |
| feedback capability 过期 | 410 `feedback_expired`；不追加 report | invitation 保留用于审计，原 capability 未落库 |
| 同一 plan artifact/phase 再提交不同内容 | 409 `feedback_phase_conflict`；相同 idempotency key 的精确重试返回原 report | 原 report 保留，禁止覆盖 |
| feedback reason/value 不符合 phase schema | 422；负向值无原因、正向值夹带原因或自由文本 reason 均拒绝 | 否 |
| feedback phase 样本少于 5 | 只返回 count，对应 acceptance/completion rate 为 `null` | 原始枚举 report 保留；公开汇总不伪造比例 |
| trial 加入码重复使用或 notice SHA 不匹配 | 409 / 422；不签发 participant capability | enrollment invitation SHA 保留；原始加入码不落库 |
| participant 已退出或 trial 已关闭 | 后续规划/反馈 fail closed；关闭后不再改变 snapshot | withdrawal event 与 cutoff snapshot 保留 |
| trial phase 少于 5 个有效 participant | 返回 count，但隐藏 value/reason/rate | trial-bound report 仍只追加，且不进入旧公开反馈 summary |
| trial retention deadline 到期 | summary 标记 `raw_purge_due`；不自动删除 | 受信 operator 可显式 purge；未确认前原始行保留 |
| operator issue 未确认、目标文件已存在 | fail closed，不签发或覆盖；stdout 不返回 capability | 既有文件与数据库保持不变 |
| operator close 未精确确认 trial ID 或低于 phase 门槛 | fail closed；低样本只有显式 override 才冻结 | 已有 cohort/report 保留，未产生 snapshot |
| operator purge 未冻结、未到期、目标不匹配、缺 secret/backup attestation 或 DB 为 WAL | fail closed，不删除任何 trial row，不写 receipt | 原 cohort/snapshot 与 append-only trigger 保留 |
| purge 删除计数、trigger 恢复或 foreign-key check 失败 | `BEGIN EXCLUSIVE` 整事务 rollback | 目标数据、DDL 和 receipt 一起回滚；测试注入坏 trigger SQL 复现 |
| purge 成功 | 仅目标 trial 的绑定行删除，写 append-only hash receipt；其他 cohort 与 legacy feedback 保留 | `secure_delete=ON` 不是取证级擦除；secret/backup disposition 仍是 operator attestation |

## 4. 未实现但不能省略的生产问题

这些是已知缺口，不应在面试时用“后面接一下 API 就行”带过：

1. 真实 provider：天气 typed adapter 已实现，但缺宣传/商业用途授权或自托管 live acceptance；POI/路线仍缺合法 live provider。报价类还需 currency/bookable/receipt。
2. Durable execution：SQLite 默认与 PostgreSQL shared-store adapter 已共用 heartbeat/retry/dead-letter/SSE/list/cancel/deadline/replay、priority aging、同有效优先级 tenant 轮转、active/accepted-submission admission 与 audit；PostgreSQL 已有独立进程 claim、全局 cap 和旧 owner fencing 的本机证据。仍缺 SQLite 在线迁移、入口 raw-attempt limiter、audit retention、在线 reprioritize、容量/故障恢复与跨主机负载证据。
3. 真实副作用：sandbox 已有双人审批、operation id、幂等执行、receipt、append-only audit、uncertain no-retry 与 provider-bound 只读 reconciliation；仍缺真实供应商授权和状态查询 acceptance、独立重新审批的补偿 operation、客服 handoff、第三方签名回执、PII/secret/retention 和多实例证据。
4. 访问控制：已有静态哈希 principal registry、route scope、tenant namespace、priority cap 与 tenant admission policy；sync continuation 仍使用短期 capability ID，原请求明文落 SQLite；PostgreSQL job adapter 也仍是应用层 tenant 条件。仍缺外部 IdP、动态 RBAC、数据库 RLS 或等价存储隔离、token 过期/轮换/撤销、网关级 quota、加密、PII redaction、secret 管理、定时清理与备份删除证明。
5. 可观测性：已完成 request/job 关联的本地 span artifact、阶段耗时、调用/业务计数、真实 token completeness、隐私最小化的新工具日志链、OTLP/HTTP protobuf loopback/failure 协议验收、单实例闭合窗口的 queue/run/terminal 分位数与错误/重试比率，以及带最小样本门的 deterministic alert snapshot；仍缺 legacy 日志受控清理、数据库加密/访问审计、经授权的远程 collector、多实例聚合、连续窗口/迟滞/告警 delivery、provider freshness、处置 outcome 和真实成本看板。
6. 真实评测：v6.6 已有 tenant-scoped 知情试用、精确 notice SHA、单次加入、退出排除、冻结 snapshot 和显式本地 retention purge，但当前 participant/report 仍为 0；检索、Requirement Gate、Constraint Ledger 与 Clarification Continuation golden set 也都是 synthetic。下一步需真正招募明确知情的试用者，积累真实失败/澄清/约束表达样本和 badcase 修复闭环；匿名参与凭证、自报完成不能直接叫不同真人、任务成功率或满意度。
7. Memory 服务化：当前 control-plane principal 未接入 Memory；身份认证、加密、跨设备同步、retention policy 和备份删除证明尚未实现。

## 5. 为什么当前不引入 MCP、A2A、LangGraph

- Provider 仍在同一个 Python 进程，普通 Protocol 已能隔离实现；MCP 会增加进程、认证和错误面而没有当前收益。
- 没有独立部署、跨团队复用或跨框架远程 Agent；A2A 不解决当前数据可信度和任务持久化问题。
- 当前控制流是清晰的线性主链加一次风险重规划；手写 Application Service 更容易验证。出现复杂分支、循环、checkpoint 和人工审批后再评估图框架。

这个选择不是否定框架，而是把复杂度与当前问题绑定。
