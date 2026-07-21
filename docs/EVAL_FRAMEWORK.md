# BJ-Pal 评测体系 · L1/L2/L3 三层防火墙

> 设计目标：公开 CI 每次跑完确定性的 L1/L2/L3 回归并生成可验证 artifact；带真实模型或用户 outcome 的在线评测单列，不能与 mock 结果混写。
> 灵感来源：视频评测实践中的三阶段防火墙 + TravelPlanner（NeurIPS 2024, arxiv:2402.01622）4 指标。

## 1. 为什么要分三层

100 个虚拟用户 × 5 强信号 × 每 commit 跑 = LongCat token 成本不小。一刀切的代价：

- 全量跑：成本高（~30 min / 次），CI 卡顿
- 子集跑：信号弱，看不出退化

**对策**：按"频率 × 规模 × 信号强度"分三层。

| 层 | 频率 | 规模 | 单次耗时 | 跑哪个 LLM | 信号强度 | 用途 |
|---|---|---|---|---|---|---|
| **L1 anchor** | 每次公开 CI | 5 case | 本地 <1s | mock | 单信号冒烟 | "5 强信号入口还在不在" |
| **L2 integration** | 每次公开 CI / 模块改动 | 5 模块 × 5 case = 25 | 本地 <1s | mock | 行为基线 | weekday / time_bucket / text_intake / convergence / memory 回归 |
| **L3 full** | 每次公开 CI | 100 case / 260 个适用信号检查 | 本地约 4s | mock | 分布回归 | S1-S5 的确定性检查；不是实际用户成功率 |

LongCat / 真实 API / 人工验收属于单独的 online track。它们需要凭证、预算、数据版本和原始 run artifact，默认不进入零凭证公开门禁。

## 2. L1 anchor — 每 commit 跑

**位置**：`evals/behavioral/run_l1.py` + `evals/behavioral/anchor_cases.py`

**5 个 anchor case，对应 5 强信号 S1-S5**：

| Case | 信号 | 干啥 | 通过条件 |
|---|---|---|---|
| `responsibility_trace` | S1 责任承担 | 跑一次 plan，看 `plan_tracer` 是否每步都有 `(decision, confidence, fallback_action)` | `coverage_rate == 1.0` 且 `has_fallback == True` |
| `red_flags_visible` | S2 红旗可见 | 拉一组候选 POI，调 `extract_red_flags` 看是否能拉出负面 aspect | `len(red_flags) >= 1` |
| `apology_after_fails` | S3 道歉容忍 | 模拟连续 2 次 reroute 失败，看 `apology_card` 是否触发 | `card.tone == "apology"` |
| `weekday_context` | S4 周末聚焦 | 输入"明天去玩"在工作日时段，看 `detect_weekday_context` 是否触发澄清 | `needs_clarification == True` |
| `screening_mode` | S5 重要场合 | 输入"老人首次见面"，看 `detect_screening_mode` 是否切到筛选 | `mode == "screening"` |

**全用 mock LLM**（`BJ_PAL_LLM=mock`）保证可重复 + 离线 + 30s 内跑完。

**退出码**：全过 `0`，任一失败 `1`，CI 直接拦下。

## 3. L2 integration — 每周扫行为基线

**位置**：`evals/behavioral/run_l2.py` + `evals/behavioral/L2_integration/`

**5 个模块 × 5 个 case = 25 行为基线**：

| 模块 | 文件 | 测什么 |
|---|---|---|
| weekday | `weekday_cases.py` | 工作日 / 周末输入下的澄清逻辑 |
| time_bucket | `time_bucket_cases.py` | 4 时段画像（工作日早 / 晚 / 周末上午 / 下午）打分差异 |
| text_intake | `text_intake_cases.py` | 自然语言意图抽取 + 槽位补全（v2.5） |
| convergence | `convergence_cases.py` | 4 成员模式群偏好收敛（反复横跳 / 沉默 / 隐性领导 / 正常） |
| memory | `memory_cases.py` | 跨 session、否定语义、异值冲突、确认/过期 gate、用户隔离 |

公开 CI 全量跑 25 case，固定 `BJ_PAL_LLM=mock`。需要 LongCat 时单独运行并用不同 artifact 名称，不能把两种 backend 的数字拼成一个通过率。

## 4. L3 full — 每 release 全量打分

**位置**：`evals/behavioral/run_l3.py` + `evals/behavioral/L3_full/`

**结构**：

- `fixtures.py::build_all_cases()` — 100 个 case = persona × scenario 矩阵
- `signal_checks.py::check_s1 ~ check_s5` — 按 case 的适用信号运行；当前公开矩阵合计 **260 个检查**（S1 100 / S2 80 / S3 40 / S4 20 / S5 20）

**5 个信号检查**（与 L1 同源但定量化）：

| 信号 | 检查函数 | 通过条件 |
|---|---|---|
| S1 责任承担 | `check_s1` | `plan_tracer.coverage_rate == 1.0` 且 `any(t.fallback_action for t in traces)` |
| S2 红旗可见 | `check_s2` | `len(extract_red_flags(候选)) >= 1` |
| S3 道歉容忍 | `check_s3` | 模拟 ≥ 2 次失败后 `apology_card` 触发，且 `tone == "apology"` |
| S4 周末聚焦 | `check_s4` | `detect_weekday_context(input)` 在跨语境时返回 `needs_clarification=True` |
| S5 重要场合 | `check_s5` | "老人 / 初次 / 家宴 / 纪念日" 关键词触发 `detect_screening_mode == "screening"` |

**Plan 缓存**：同 `(persona, query)` 不重复 plan（mock 也省 30%+ 时间）。

**TravelPlanner 4 指标**（`evals/eval_plans.py`）：

| 指标 | 计算 |
|---|---|
| delivery_rate | 输出 plan 非空 + JSON 合法 / 总场景 |
| commonsense_pass | 时间逻辑 + 地理顺序 + 餐时合理 |
| hard_constraint_pass | 预算 / 餐饮限制 / 时段 / 人数硬约束全部满足 |
| **final_pass** | 4 项全过 |

## 5. 通过率演进表

| 阶段 | 数据规模 | delivery_rate | commonsense | hard_constraint | **final_pass** | 备注 |
|---|---|---|---|---|---|---|
| v1 baseline | 40 场景 | 0.975 | 0.475 | 0.675 | **0.275** | 仅手工 prompt |
| v2 ([73][75]) | 40 场景 | 0.975 | 0.575 | 0.650 | **0.275** | 鲁棒性升级（Outlines + RPM 限流） |
| **v3 ([11]+[12]+[88])** | **100 场景** | **1.000** | **0.810** | 0.610 | **0.470** | ToT + LongCat eval100 + OTel |
| mock_v3 | 100 场景 | 1.000 | 0.580 | 0.820 | 0.400 | 离线对照 |
| **v3.0 L3 全量** | 100 case × 5 信号 | — | — | — | **100% pass** | 5 信号全过 |
| **v3.1 D7 历史校准演示** | 100 case × 5 信号 | — | — | — | **100% pass** | outcome 由 synthetic seed 生成，只能演示图表，不能声称真人 ECE 下降 |

**v3 final_pass 0.470 vs v2 0.275，相对提升 +71%**（n=100 比 n=40 更稳健的统计样本）。

详见 `docs/eval-100-results.md`。

## 6. 怎么跑

```bash
# 公开零凭证路径：固定 mock，跑三层、写 artifact、再独立复算
make eval-public PYTHON=.venv/bin/python

# 单独复核一个已有产物（不运行产品）
.venv/bin/python evals/verify_artifact.py evals/results/public-core.json

# 对比冻结 BM25 baseline 与当前检索器，并复核逐例结果
make eval-retrieval PYTHON=.venv/bin/python

# 复核执行前需求门控的触发、误澄清和补充后 gate 状态
make eval-requirements PYTHON=.venv/bin/python

# 复核自然语言 typed 约束、显式冲突、rewrite 与 durable round-trip
make eval-constraints PYTHON=.venv/bin/python

# 复核 execution span tree、调用/token 汇总、隐私标记排除与 SHA
make eval-observability PYTHON=.venv/bin/python

# 复核工具 payload 投影、稳定错误码、session SHA chain 与 reset/legacy 边界
make eval-tool-audit PYTHON=.venv/bin/python

# 复核 plan-evidence dry-run、非破坏 copy、logical hash、receipt 与领域隔离
make eval-state-layout PYTHON=.venv/bin/python

# 复核 prediction-feedback copy、稀疏 ID/NULL、领域隔离与迁移后 UPDATE/DELETE
make eval-prediction-state PYTHON=.venv/bin/python

# 复核 user-memory 两表成对 copy、隐私删除、事件不可更新与 WAL fail-closed
make eval-user-memory-state PYTHON=.venv/bin/python

# 复核 verified owner、legacy source drift、未知表和 receipt 丢失失败关闭
make eval-legacy-retirement PYTHON=.venv/bin/python

# 生成并独立复核当前 dirty-tree 的逐文件发布边界
make audit-release-candidate PYTHON=.venv/bin/python

# 复核 server-owned execution budget、N+1 gate、token/time 终止与 SHA
make eval-execution-budget PYTHON=.venv/bin/python

# 复核 strict model-output schema/candidate/category/sequence 与一次有界修复
make eval-model-output PYTHON=.venv/bin/python

# 同输入比较 single/multi branch 的质量代理、调用、故障与预算边界
make eval-orchestration PYTHON=.venv/bin/python

# 复核 durable priority ordering、aging、queue wait 与 backoff exclusion
make eval-scheduling PYTHON=.venv/bin/python

# 复核 principal scope、priority cap、tenant/idempotency/continuation isolation
make eval-access-control PYTHON=.venv/bin/python

# 复核用户结果证据的 capability 绑定、追加写入、隐私和最小样本门
make eval-outcomes PYTHON=.venv/bin/python

# 复核知情试用的 notice/participant 绑定、退出、分母门和冻结 snapshot
make eval-trials PYTHON=.venv/bin/python

# 生成并独立复核天气 provider 的离线契约，不触网
make eval-weather PYTHON=.venv/bin/python

# 真实 ASGI 主链的有界并发回归；随后独立复算 raw requests
make benchmark-http PYTHON=.venv/bin/python \
  PERFORMANCE_REQUESTS=50 PERFORMANCE_CONCURRENCY=8

# 独立 Uvicorn 子进程 + localhost TCP；同样要求独立复算与优雅退出
make benchmark-socket-http PYTHON=.venv/bin/python \
  PERFORMANCE_REQUESTS=50 PERFORMANCE_CONCURRENCY=8

# 调试单层
BJ_PAL_LLM=mock .venv/bin/python evals/behavioral/run_l1.py
BJ_PAL_LLM=mock .venv/bin/python evals/behavioral/run_l2.py
BJ_PAL_LLM=mock .venv/bin/python evals/behavioral/run_l3.py

# 单独跑 v3 100 场景 LongCat baseline
BJ_PAL_LLM=longcat python3 scripts/run_longcat_eval100.py
python3 scripts/eval_compare.py   # 自动生成对比表
```

## 7. 可验证 artifact

`evals/run_public.py` 生成 `evals/results/public-core.json`，内容包括：

- Python / platform / git SHA / dirty 状态 / mock backend / DataProfile provenance
- L1、L2 和 L3 的逐 case 原始结果；L3 不再只保存摘要
- 从 raw cases 复算出的分层 gate summary
- `payload_sha256`：任意字节级语义字段改动都会失效
- `semantic_sha256`：剔除 timing、plan_id 等运行噪声，用于比较两次执行的稳定行为证据

`evals/verify_artifact.py` 不调用 Planner，只从 raw cases 复算所有计数、分段、信号和 gate，并校验两个 SHA-256。`evals/results/*.json` 不提交到仓库；CI 上传 `bj-pal-offline-evidence` workflow artifact，避免把某次本地运行冒充长期源真相。

`evals/run_http_benchmark.py` 固定 mock LLM 和 synthetic profile，经 `httpx.ASGITransport` 请求真实 `/v1/plans` 链路；`run_socket_http_benchmark.py` 则在独立 Uvicorn 子进程中经 `127.0.0.1` TCP 请求同一端点。后者把 feedback/job/clarification/tool-audit runtime 指向临时目录，阻止子进程加载本机 env 文件并移除 provider/control credential，只在 readiness 成功后计量，最后要求 `SIGINT + wait` 返回 0。两者都保存每次请求的 status、request ID 回显、latency 和错误类型；`evals/verify_http_benchmark.py` 不重跑应用，独立复算错误率、吞吐、nearest-rank p50/p95/p99、进程生命周期约束与 SHA-256。它们仍排除 TLS/反向代理、真实模型/provider、多实例和线上流量，只用于单机回归，不是 SLA。

`evals/run_weather_acceptance.py` 只读取 authored synthetic fixture，保存 fixture SHA、公开片区请求范围、provider attribution、小时快照和室内/户外决策；`verify_weather_acceptance.py` 重新解析 fixture 并复算语义。artifact 固定 `acceptance_level=offline_contract_only`、`live_network_used=false`、`live_provider_accepted=false`。真实环境只允许手动运行 `weather-live-smoke`，不进入零凭证 CI，也不能把一次 200 响应写成预报准确率或商业授权。

`evals/run_requirements.py` 对 20 条 hand-authored synthetic case 运行纯确定性的 Requirement Gate，保存逐例 expected/observed status、是否触发、unresolved code 和补充后状态；`verify_requirements.py` 不重跑产品，只从 raw cases 复算 trigger rate、false clarification rate、required recall、decision accuracy 和 post-clarification gate executability，并校验 golden/artifact SHA。最后一项只表示通过门控，不表示完整计划、预订成功或用户满意。

`evals/run_constraints.py` 对 30 条 hand-authored synthetic case 运行 `constraint_ledger_v1`，保存逐字段 expected/observed text value、最终生效值、显式冲突、rewrite fragment 和序列化重放结果；`verify_constraints.py` 不重跑产品，只从 raw cases 复算 field precision/recall/F1、false extraction、hard-constraint preservation、conflict recall/false conflict、rewrite coverage 与 round-trip idempotency，并校验 golden/artifact SHA。当前全通过只证明固定中文短语和已声明字段的确定性行为，不是开放域 NLU 准确率。

`evals/run_clarifications.py` 对 16 条 hand-authored synthetic case 走真实 SQLite continuation：保存原请求、request/decision SHA、delivery/job policy、typed options、resolution、恢复后的 session、幂等重放、post-preflight 与另一答案冲突。`verify_clarifications.py` 不重跑产品，而是独立复算两层 fingerprint、option/resolution 绑定、最终 effective value、同冲突复发、durable restore、round-trip 和各项指标。当前成功率指标 1.000、同冲突复发率 0，只证明这些固定 ambiguity pattern 的状态机，不代表开放域多轮理解、完整计划质量或用户满意度。

`evals/run_observability.py` 生成 3 条 synthetic contract case，覆盖 provider-reported token、mock usage 缺失和无 LLM 路径；raw observation 保存父子 span、相对耗时、操作/业务计数、token completeness 和内层 SHA。`verify_observability.py` 不调用 `ExecutionObservation.verify_integrity()` 或重跑产品，而是独立重算 observation/artifact 两层 SHA、根与 parent 引用、阶段覆盖、LLM/data/tool 调用数、token 汇总和敏感标记排除。它只验证本地契约，不代表 OTLP collector、真实成本或生产 telemetry。

`evals/run_workload_health.py` 生成 mixed terminal/active 与 empty 两个 fixed synthetic window；raw records 只含 synthetic job ID、as-of status、created time 和窗口截止前的 event type/attempt/time。产品输出固定分母、rate、nearest-rank queue/run/terminal p50/p95/p99 和双 SHA；独立 `verify_workload_health.py` 不调用产品聚合器，重算窗口边界、event prefix、as-of status、顺序、终态、分母、分位数、evidence/artifact SHA 与递归隐私约束，并拒绝重新签名后的 rate、p95 或嵌套 ID 注入。2/2 只证明 contract，不是生产 SLO、容量、事故率、告警或真实用户证据。

`evals/run_otlp_export.py` 不使用 mock exporter 伪造成功：第一例启动临时 `127.0.0.1` HTTP receiver，让产品 `OTLPSpanExporter + BatchSpanProcessor` 实际 POST protobuf，artifact 保存 raw request bytes 的 base64、content type 和 payload-free health snapshot；第二例注入 exporter failure，检查业务结果仍成功且健康状态为 `degraded`。`verify_otlp_export.py` 不重跑产品，独立解码 `ExportTraceServiceRequest`，验证 service resource、三个 span 的父子树、GenAI operation/provider/usage、稳定 error type、属性 allowlist、endpoint digest 和 success/failure counter；重签名后注入 prompt 或改父子边也会失败。它仅是 synthetic loopback protocol acceptance，不是远程 collector、生产告警/SLO、retention、多实例或真实流量证据。

`evals/run_operational_alerts.py` 生成 4 个 authored synthetic case：足量健康、四规则同时触发、小样本、OTLP 未配置。产品 snapshot 固定三条 workload 数值规则与一条 trace sink 状态规则，公开最小样本、阈值、观察值、四态和 source/policy/artifact SHA。`verify_operational_alerts.py` 不调用产品 evaluator，独立复算 workload rate、`>=` 比较、样本门、trace 状态、总状态和多层 hash，并拒绝自重签名后的 rule/policy/source/identifier 篡改。4/4 只证明 deterministic decision contract，不是生产阈值、连续窗口、告警投递、SLO、事故处置效果、容量或真实用户证据。

`evals/run_tool_audit.py` 在临时 SQLite 中生成 5 条 synthetic contract case：敏感 key/credential/email/未知文本投影与稳定错误码、v2 行 UPDATE/DELETE 拒绝、session reset 前后链连续且只显示新 segment、legacy payload 默认读取隐藏，以及新审计库只含诊断表且旧共享库写前/写后 SHA 不变。artifact 保存 projected events、mutation outcome、chain snapshot 和去路径化的表/SHA 证据；`verify_tool_audit.py` 不调用产品 hash helper，而是复算 sequence/previous SHA/event SHA 和 6 项指标，并拒绝重签后的私密 marker、伪造 mutation 成功、截断链与伪造存储隔离。它不证明完整 PII 检测、历史擦除、数据库加密、远端不可变或 retention 合规。

`evals/run_state_layout.py` 在临时 SQLite 中生成 3 条 synthetic contract case：dry-run 不创建目标且源文件 SHA 不变；apply 只复制 plan trace/outcome、源/目标 count 与 logical digest 相同、`quick_check` 和 migration receipt 有效、memory/tool 私密 marker 不进入目标；旧 outcome 缺 classification 时明确迁为 `legacy_unclassified`。独立 verifier 重算 artifact/preview/migration/receipt SHA 和六项 rate，并拒绝重新签名后的源修改或 receipt 篡改。它不证明某台机器的真实迁移已执行，也不证明未来行不可变、数据库加密或 tenant isolation。

`scripts/audit_release_candidate.py` 不生成质量分数，而是生成当前未提交工作树的 release boundary：NUL-safe 读取 Git 状态，逐项记录相对路径、XY status、implementation/documentation 分组、size、Git mode 与 SHA，并绑定 HEAD/branch/base divergence。`evals/release_candidate/verify.py` 独立重读 Git 与字节，拒绝环境文件、runtime/state/result、symlink、binary、非 UTF-8、大文件、本机绝对路径及任意 drift。manifest 写入 gitignored `runtime/`，作为独立提交前门执行；clean checkout 没有 dirty candidate，因此该目标不属于默认 `make check`。它与 credential scan 互补，但两者都不扫描历史或证明旧 Key 已撤销。

`evals/run_execution_budget.py` 生成 6 条 synthetic contract case：正常完成、LLM/data-provider/tool N+1、provider-reported token 超限和 wall-clock 安全检查点超限。raw case 保存服务端 policy、usage、termination reason、post-limit work 是否执行和 snapshot SHA；`verify_execution_budget.py` 不调用产品 tracker，而是独立复算 artifact/snapshot SHA，并按终止原因检查计数是否恰为 limit+1、token/elapsed 是否越界、终止后的代码是否未执行以及私密 marker 是否排除。它不证明能强杀已经阻塞的网络调用，也不把缺失 usage 估成 token 或金额成本。

`evals/run_model_output.py` 保存 12 条 hand-authored adversarial payload 和 4 条 deterministic Planner lifecycle case：exact valid、extra/type drift、候选 ID 幻觉、名称错配、重复地点、depart/index/time/binding 错误、本地补残标记，以及首次通过、一次修复成功、修复耗尽和预算阻止第二次正文。`verify_model_output.py` 不调用生产 validator，而是独立实现 strict schema/候选/步骤规则，重算 observed issue、模型契约与执行预算 SHA、provider body count、脱敏 marker 和全部指标；自重哈希伪造 issue code 或调用数也会失败。该 artifact 只证明失败关闭和最多一次修复的生命周期，不代表真实模型错误分布、真实修复率、候选数据新鲜度或用户满意度。

live track 与上述 deterministic gate 分开。`evals/live_model/observations/2026-07-20-dpsk-flash.json` 记录一次经配置 DeepSeek client 的 operator-observed smoke：固定 synthetic scenario、2 次模型尝试、26 个候选，63,885.045ms 后仍因 `depart_duration_invalid` 和 `schema_literal_invalid` 被 `model_output_contract_v1` 拒绝。文件不保存 prompt、用户输入、原始模型输出、生成方案或 auth material；`verify_live_model.py` 复算外层/契约 SHA、attempt/repair/fail-closed 语义、HTTPS origin、执行上限与禁止字段。该 badcase 驱动生成/修复 prompt 消除竖线伪 enum 并明示 depart 字段；后续 observation 是新调用，原文件仍保留修正前事实。它不能独立证明外部请求发生，也不是 signed provider receipt、质量/失败率、费用或用户结果。

修正后又以完全相同的 synthetic 场景、26 个候选和执行上限各跑 1 次 Flash/Pro：Flash 在 2 次尝试、67,772.391ms 后因 extra/missing/type schema 问题拒绝；Pro 在 1 次、46,773.039ms 首次通过。pair verifier 只在场景、client、endpoint origin、预算和 candidate count 一致时输出 `two_single_sample_model_selection_signal`，decision 是“下一轮有界试验优先 Pro”，刻意不计算 1/1 成功率或延迟倍率。`DPSK_MODEL` 因此改为必填，避免部署时静默落到未经选择的档位。

Pro track 再固定为 3 个 scenario registry：三里屯朋友/预算/少走路、五道营家庭/儿童/忌口、798 单人/室内/少排队。三份 observation 均首次通过 strict contract，耗时为 46,773.039、55,030.015、23,629.700ms，候选数为 26、27、2。suite verifier 独立校验精确场景集合、artifact 唯一、client/model/origin/预算一致，并只输出 `accepted_count=3`、`first_pass_count=3` 与描述性 min/median/max；不输出 success rate。798 的 2-candidate pool 明确列为覆盖不足，且 suite 不保留 plan，不能判断方案是否真正好用。

v6.10 没有把“schema 通过”继续包装成质量：先将 798 加入 demo replacement coverage gate，使 food/scenic/shopping/museum/sports 达到与其他核心片区相同的最低候选数；再对 `no_spicy/light_diet` 餐饮执行证据型 fail-closed 过滤，只有 confidence≥0.6、未标 review 的正向 structured UGC taste tag 才能进入候选，不能以“没有负面记录”代替忌口证明。随后重新运行三例 Pro，候选数为 26/16/21；三里屯和家庭首轮接受，798 在一次有界修复后接受，耗时分别为 38,464.116、50,287.589、42,273.279ms。

运行时安全边界随后收紧为：任意显式 diet flag 都必须在同一餐饮 POI 上具备全部正向 tag，缺证据即省略 food 候选并产生稳定的 `diet_evidence_unavailable`；strict model-output contract 同时把 `meal/snack → food candidate` 纳入类别绑定。13 条静态对抗 case 中新增“非 food 候选伪装餐饮”，独立 verifier 自行复算，不复用生产 validator。未登记 tag 的忌口因此表现为可见的无餐饮降级，而不是被宣称已满足。

每个新调用同时生成独立的 `live-model observation` 与 `live-plan-quality` artifact。后者只保留固定 scenario ID、脱敏 plan projection、选中 POI 的 synthetic facts/positive tags、路线和时间轴摘要，不保留 request text、prompt、raw output、rationale、summary 或 auth material。`quality_verify.py` 从 raw projection 与不可放宽的固定 policy 复算 persona/area、最少活动数、depart/index、POI grounding、travel-aware timeline、duration、route completeness、walking-leg proxy、必需 kind、餐饮价格、忌口/亲子正向证据和室内文化活动：三例分别通过 9/9、12/12、11/11 个必需检查，0 项不可评估。suite 只报告 3 个 fixed case 的 count；这些 deterministic synthetic proxy 仍不评判 rationale、真实 freshness、主观偏好或用户 outcome，也不能作为成功率。

未来复跑必须由 operator 显式提供标准 `BJ_PAL_LLM=dpsk` 与 `DPSK_*` 环境变量，并执行 `make live-model-smoke ACK_PROVIDER_COST=1 PYTHON=.venv/bin/python`，用 `--scenario-id` 选择固定场景。真实调用目标不进入 `make check`；只有已脱敏 observation、同口径 pair/acceptance suite 和 3-case quality suite 的离线 verifier 进入默认门禁。runner 遇到契约拒绝会保留 observation，但不会伪造质量 artifact；未知基础设施异常也不伪造成功记录。

`evals/run_orchestration.py` 在 3 个 hand-authored synthetic 场景上用相同 deterministic mock、demo SQLite 与规则 scorer 比较单分支和 3 个同构 planner 分支。raw case 保存脱敏 plan projection、质量分解、branch accounting 和完整 execution-budget snapshot；另有一个 post-generation 分支故障和一个默认预算拒绝 case。`verify_orchestration.py` 不调用 Planner，而是重算 artifact/snapshot/plan SHA、quality delta、constraint non-regression、输出变化、LLM/data 倍率、故障容纳和 decision，并拒绝自重哈希伪造调用数。当前 0 质量提升、0 输出变化、3× LLM/data 只支持本项目保持 `single_branch_default`；mock 忽略 branch hint，elapsed 只作本机诊断，因此不能外推真实模型质量、真实 token 金额或生产延迟。

`evals/run_scheduling.py` 通过真实 SQLite repository 生成 4 条 synthetic contract case：较高基础优先级抢占、priority 0 等待 541 秒后 aging 至 9 并以更早 eligible time 胜出、priority 9 重试任务在 backoff 到期前被排除，以及同有效优先级下先选择最久未获服务 tenant。raw case 保存全部候选的 base priority、eligible/created/deadline time、tenant last-claimed cursor、最近事件和最终 claim event；`verify_scheduling.py` 不调用生产 `compute_effective_priority()` 或重跑 claim，而是独立复算候选资格、有效优先级、tenant fairness、FIFO、queue wait 和 retry evidence。它只证明单机 SQLite 的组合选择契约，不代表启动 SLA、多实例队列吞吐或严格全局公平。

`evals/run_access_control.py` 通过真实 FastAPI ASGI + SQLite 创建四租户和多类 synthetic principal，生成 6 条 contract case，保存 route scope、priority admission、跨 tenant job/event/cancel/replay/list、tenant-local idempotency、active-job cap、60 秒 accepted-submission cap、append-only admission audit，以及 continuation 在 404/403/429 后的状态恢复。artifact 只保存 principal/admission policy 和测试 token 的禁止值 hash，不保存 token 本身。`verify_access_control.py` 不重跑产品，而是根据 principal scope/tenant/cap/quota、raw HTTP outcome 与 raw audit event 独立推导预期结果，复算 10 项指标并扫描 credential 泄露。它只证明单机应用层 `identity_scope_v1 + tenant_admission_v1`，不代表 OAuth/OIDC、动态 RBAC、数据库 RLS、credential 生命周期、raw-attempt abuse protection、跨实例 quota 或生产隔离。

`evals/run_side_effects.py` 通过真实 SQLite operation repository 生成 5 条 synthetic contract case：职责分离与精确 approval fingerprint、tenant-local idempotency/隔离、quote/approval 过期失败关闭、execution lease 过期进入 uncertain 且不自动重试，以及调用后不明时以 provider operation reference 做只读状态核对。raw case 保存 operation request/approval SHA、quote、事件序列、receipt envelope、sandbox enforcement 和完整 reconciliation evidence；`verify_side_effects.py` 不调用产品的 receipt/status lookup 校验 helper，而是独立重算 artifact/receipt/evidence/raw-provider-payload SHA、字段绑定、事件序列和 12 项指标。它只证明确定性 `bj-pal-sandbox` 状态机，不代表第三方订单存在、真实状态查询恢复率、预订成功率、补偿能力或生产安全认证。

`evals/run_outcomes.py` 通过临时 SQLite 生成 4 条 synthetic contract case：capability/plan artifact 绑定与 raw capability 不落库、幂等重试/phase 冲突/枚举 schema、过期失败关闭与 invitation/report append-only、每阶段 5 份的公开比例门。`verify_outcomes.py` 不调用产品 repository 的校验方法，而是从 raw case 独立重算 invitation/report/artifact SHA 和 8 项 rate。该 artifact 不创建真人 report，也不证明用户采纳、完成、满意或产品价值；真实数据只能进入 `self_reported_unverified` track，且 plan-level outcome 不参与 step-level ECE。

`evals/run_trials.py` 通过临时 SQLite 生成 6 条 synthetic contract case，覆盖精确 notice SHA 同意、一次性 enrollment、capability 最小化、每参与凭证/phase 唯一、tenant 隔离、普通反馈排除、退出排除、关闭后失败关闭、append-only、最小 participant 门、cutoff snapshot、retention 到期信号和目标清除事务。`verify_trials.py` 不调用产品 integrity helper，而是从 raw notice/cohort/enrollment/participant/report/withdrawal/snapshot/purge receipt 独立重算全部 SHA、evidence root 和 13 项 rate。它不能证明不同真人、统计显著性、取证级擦除或备份删除；`scripts/rehearse_trial.py` 的 0.8 是固定 synthetic 负例排练，不是用户指标。

## 8. 防火墙原则（不可破坏）

参考 video-eval-agent `project_video_eval_agent_constraints.md`：

1. **fixture 与 production prompt 分库** — fixture 反向训练 LLM 是评测污染，必须隔离
2. **mock 优先** — L1 全 mock，L2/L3 也保留 `BJ_PAL_LLM=mock` 兜底，离线/限流时仍可跑
3. **plan 缓存可观察** — `_PLAN_CACHE` 的 hit/miss 在 trace 里能看到，避免"看起来跑了但其实在缓存"
4. **支持度不等于概率** — v4.2 `evidence_support_v1` 先透明记录来源和因子；只有与 outcome 配对后才能用 ECE 检查其概率校准关系
5. **Synthetic 与真人 outcome 分列** — seed 行必须标为 `synthetic_test`，历史行标为 `legacy_unclassified`；只有逐 step 独立核验的 `human_verified_step` 可进入真人 calibration，plan-level 自报只用于采纳/完成聚合
6. **参与凭证不等于真人身份** — trial 分母按不同匿名 participant capability 计数；没有外部身份核验时不得写成不同真人，retention 到期信号也不得写成删除证明

## 9. 常见误用

| 反模式 | 为什么不行 | 对策 |
|---|---|---|
| L1 加场景到 20 个 | 每 commit 跑会从 30s 涨到 2min，CI 体验崩 | 加场景去 L2，L1 永远只 5 anchor |
| L3 直接覆盖 L2 | L3 全用 LongCat，开发期跑不起 | 改一个模块只跑对应 L2 子模块 |
| 用 LLM judge 替代 5 信号检查 | judge 飘忽，无法稳定退化告警 | 信号检查必须 deterministic（基于 plan_tracer / detect_* 函数返回值） |
| 把 production prompt 放进 fixture | 评测污染，pass 不代表真有效 | fixture 用通用语境 query，prompt 改动只影响 production，不影响 fixture |
| 用 4-case outcome contract 的 1.000 写用户成功率 | 契约评测只证明管道安全，不包含一条真人结果 | 明确 report 数；每 phase 少于 5 份时比例保持 `null`，且始终标注 self-reported unverified |
| 用 trial rehearsal 的 0.8 写采纳率，或把 5 个 capability 写成 5 位真人 | synthetic participant 是固定测试输入；capability 没有身份核验 | 只报告 6-case/13-metric 契约与真实 participant/report 数；purge receipt 也只证明本地 live-table 删除事务；真人或合规删除结论必须另有证据 |
| 把 3 个 ToT 分支写成 3 个 Agent，或把 mock 的 0 提升外推成多 Agent 无效 | 分支共享同一个 Planner/状态/进程；mock 还忽略 branch hint | 称为实验多分支，只报告 3-case synthetic 对照；用真实 badcase/outcome 同口径复验后再调整主链 |
| 把 13+4 条模型输出契约的 1.000 写成“幻觉率为 0”或“修复率 100%” | payload 与 repair client 都是 hand-authored/deterministic，只覆盖声明攻击面 | 只声称 strict contract 会拒绝这些越界并最多修复一次；真实错误率与修复收益必须另建带模型/版本/预算/raw output 的 online track |

## 10. 与外部参考

- **TravelPlanner**（NeurIPS 2024, arxiv:2402.01622）— 4 指标范式
- **gstack 三阶段防火墙**（video-eval-agent infra）— L1/L2/L3 隔离 + fixture 分库
- **LangSmith / Langfuse / Braintrust** — 工业评测平台，未来可挂 score 到 trace

---

> 维护：每加一个 v3.x 新模块，应同时在 L2 加一个 5-case 子模块；每加一个新行为信号，应同时加 L3 信号检查 (S6, S7...)。
