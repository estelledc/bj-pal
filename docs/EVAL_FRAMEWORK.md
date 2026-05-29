# BJ-Pal 评测体系 · L1/L2/L3 三层防火墙

> 设计目标：**评测频率 × 信号强度** 解耦 — 每 commit 跑核心信号、每周扫行为基线、每 release 全量打分。
> 灵感来源：video-eval-agent gstack 三阶段防火墙（intern-journal memory `project_video_eval_agent_constraints.md`）+ TravelPlanner（NeurIPS 2024, arxiv:2402.01622）4 指标。

## 1. 为什么要分三层

100 个虚拟用户 × 5 强信号 × 每 commit 跑 = LongCat token 成本不小。一刀切的代价：

- 全量跑：成本高（~30 min / 次），CI 卡顿
- 子集跑：信号弱，看不出退化

**对策**：按"频率 × 规模 × 信号强度"分三层。

| 层 | 频率 | 规模 | 单次耗时 | 跑哪个 LLM | 信号强度 | 用途 |
|---|---|---|---|---|---|---|
| **L1 anchor** | 每 commit | 5 case | ~30s | mock | 单信号冒烟 | "5 强信号都还在不在" |
| **L2 integration** | 每周 / 每改 5 模块 | 5 模块 × 5 case = 25 | ~5min | mock + 抽样 LongCat | 行为基线 | "weekday / time_bucket / text_intake / convergence / memory 是否符合预期行为" |
| **L3 full** | 每 release / 每周末 | 100 case × 5 信号 = 280 检查 | ~30min | LongCat | 全量分布 | TravelPlanner 4 指标 + 5 信号通过率 |

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
| memory | `memory_cases.py` | 跨 session 偏好记忆（v2.7 user_memory）record/get/forget/infer/merge |

**每周跑 + 每改 5 模块之一就跑**。混合 mock + 抽样 LongCat（重要 case 真跑、冒烟 case mock）。

**当前状态**：L2 evals 跑通但**结果未归档**（`evals/results/` 只有 L1 + L3 的 JSON）— 后续可加 `--save-json` flag 落归档。

## 4. L3 full — 每 release 全量打分

**位置**：`evals/behavioral/run_l3.py` + `evals/behavioral/L3_full/`

**结构**：

- `fixtures.py::build_all_cases()` — 100 个 case = persona × scenario 矩阵
- `signal_checks.py::check_s1 ~ check_s5` — 5 个信号检查函数，每 case 跑一遍 = **500 个信号 + 80 个邻接条件 = ~280 检查**

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
| **v3.1 D7 校准后** | 100 case × 5 信号 | — | — | — | **100% pass + ECE ↓** | 滑窗 ECE 演化收敛 |

**v3 final_pass 0.470 vs v2 0.275，相对提升 +71%**（n=100 比 n=40 更稳健的统计样本）。

详见 `docs/eval-100-results.md`。

## 6. 怎么跑

```bash
# L1：每 commit 跑（30s，mock，CI 拦截）
python3 evals/behavioral/run_l1.py
echo "exit code: $?"   # 0 = pass, 1 = fail

# L2：每周扫（5min，混合）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l2.py

# L3：每 release（30min，LongCat 全跑）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l3.py

# 单独跑 v3 100 场景 LongCat baseline
BJ_PAL_LLM=longcat python3 scripts/run_longcat_eval100.py
python3 scripts/eval_compare.py   # 自动生成对比表
```

## 7. 结果归档

约定：`evals/results/L{1,3}_<git_sha7>_<unix_ts>.json`

当前归档（5/29 status）：

| 文件 | 是 |
|---|---|
| `L1_4051abd_1779697379.json` | v2.4 D5 接入 broadcast 后基线 |
| `L1_f7f8a62_1779702022.json` | v2.4 D1+D5 UI 加 trust_panel 后 |
| `L3_6206e26_1779709810.json` | v3.0 L2 评测落地后首跑 L3 |

**L2 未归档** — 当前 `run_l2.py` 不写 JSON，仅 stdout。后续 ROADMAP 项。

## 8. 防火墙原则（不可破坏）

参考 video-eval-agent `project_video_eval_agent_constraints.md`：

1. **fixture 与 production prompt 分库** — fixture 反向训练 LLM 是评测污染，必须隔离
2. **mock 优先** — L1 全 mock，L2/L3 也保留 `BJ_PAL_LLM=mock` 兜底，离线/限流时仍可跑
3. **plan 缓存可观察** — `_PLAN_CACHE` 的 hit/miss 在 trace 里能看到，避免"看起来跑了但其实在缓存"
4. **ECE 是连续指标，不是 boolean** — D7 校准要追"AI 说 70% 确定时是不是真的 70% 对"，不是简单 pass/fail

## 9. 常见误用

| 反模式 | 为什么不行 | 对策 |
|---|---|---|
| L1 加场景到 20 个 | 每 commit 跑会从 30s 涨到 2min，CI 体验崩 | 加场景去 L2，L1 永远只 5 anchor |
| L3 直接覆盖 L2 | L3 全用 LongCat，开发期跑不起 | 改一个模块只跑对应 L2 子模块 |
| 用 LLM judge 替代 5 信号检查 | judge 飘忽，无法稳定退化告警 | 信号检查必须 deterministic（基于 plan_tracer / detect_* 函数返回值） |
| 把 production prompt 放进 fixture | 评测污染，pass 不代表真有效 | fixture 用通用语境 query，prompt 改动只影响 production，不影响 fixture |

## 10. 与外部参考

- **TravelPlanner**（NeurIPS 2024, arxiv:2402.01622）— 4 指标范式
- **gstack 三阶段防火墙**（video-eval-agent infra）— L1/L2/L3 隔离 + fixture 分库
- **LangSmith / Langfuse / Braintrust** — 工业评测平台，未来可挂 score 到 trace

---

> 维护：每加一个 v3.x 新模块，应同时在 L2 加一个 5-case 子模块；每加一个新行为信号，应同时加 L3 信号检查 (S6, S7...)。
