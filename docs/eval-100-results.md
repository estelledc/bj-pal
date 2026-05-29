# Eval 对比 — v1 / v2 / v3 / v3.0 L3 / v3.1 D7

> 自动生成自 `scripts/eval_compare.py`（TravelPlanner 4 指标）+ `evals/behavioral/run_l3.py`（5 信号检查）+ v3.1 calibration 输出
>
> 配套文档：`EVAL_FRAMEWORK.md`（评测体系设计）/ `100-improvements.md`（改进编号映射）

## 1. TravelPlanner 4 指标（v1 → v3）

| 配置 | delivery | commonsense | hard_constraint | **final_pass** |
|---|---|---|---|---|
| v1 (40, baseline) | 0.975 (39/40) | 0.475 (19/40) | 0.675 (27/40) | **0.275 (11/40)** |
| v2 (40, [73][75]) | 0.975 (39/40) | 0.575 (23/40) | 0.650 (26/40) | **0.275 (11/40)** |
| v2_run2 (40) | 0.975 (39/40) | 0.575 (23/40) | 0.650 (26/40) | **0.275 (11/40)** |
| **v3 (100, [11]+[12]+[88])** | **1.000 (100/100)** | **0.810 (81/100)** | 0.610 (61/100) | **0.470 (47/100)** |
| mock_v3 (100) | 1.000 (100/100) | 0.580 (58/100) | 0.820 (82/100) | **0.400 (40/100)** |

**v3 vs v2 final_pass 相对提升 +71%**（n=100 vs n=40，更稳健的统计样本）。

详细分析：
- delivery 0.975 → 1.000：[73][75][88] 鲁棒性 + 重试 + 限速治理见效
- commonsense 0.575 → 0.810：[15] dedup 在真 LongCat 上从 mock-only 兑现到生产
- hard_constraint 0.650 → 0.610 略降：100 场景含更多极限 case（极低预算 / 婴幼儿 / 高奢、雨天约束更难满足）；mock_v3 在同样场景下 0.820 说明结构 OK，差距来自 LongCat 在硬约束上的偏移空间
- final_pass 47/100：几乎覆盖一半场景全过 4 项检查
- 总耗时 3297s（avg 33s / 场景）

## 2. v3.0 L3 全量评测（5 信号 / 100 case / 280 检查）

> 归档：`evals/results/L3_6206e26_1779709810.json`（v3.0 L2 评测落地后首跑 L3）

| 信号 | 检查函数 | 100 case 通过 | 通过率 |
|---|---|---|---|
| S1 责任承担 | `check_s1` — `plan_tracer.coverage_rate == 1.0` 且有 `fallback_action` | 100/100 | **100%** |
| S2 红旗可见 | `check_s2` — `len(extract_red_flags) >= 1` | 100/100 | **100%** |
| S3 道歉容忍 | `check_s3` — 模拟 ≥ 2 次失败后 `apology_card.tone == "apology"` | 100/100 | **100%** |
| S4 周末聚焦 | `check_s4` — 跨语境时 `detect_weekday_context` 触发 `needs_clarification` | 100/100 | **100%** |
| S5 重要场合 | `check_s5` — 关键词触发 `detect_screening_mode == "screening"` | 100/100 | **100%** |
| **合计** | | **280/280** | **100%** |

**为什么 5 信号能全过**：

S1-S5 是 deterministic 检查（不依赖 LLM judge），只要：

- `plan_tracer.record_plan()` 接入主路径（v2.4 D1 已落）
- `extract_red_flags` 候选池有负面 aspect（v2.2 数据扩展 + R6-R100 跨片区已保证）
- `apology_card` 触发函数（v1 已有）
- `detect_weekday_context` / `detect_screening_mode` 关键词正则匹配（v2.4 S4 已覆盖 100%）

→ 检查就 pass。这不是"算法多牛"，而是"行为基线 + 关键词触发器都接好了"。

**风险**：100% pass 看起来理想但**不能证明产品做对了**——pass 只证明"关键词触发器没坏"。真实价值要看 L2 5 模块和真实用户访谈。

## 3. v3.0 L2 集成评测（5 模块 × 5 case = 25 行为基线）

> 当前 `run_l2.py` 不写 JSON 归档（仅 stdout），归档化是 ROADMAP 项。下表为最近一次 stdout 摘录。

| 模块 | 测什么 | 5 case 通过 | 备注 |
|---|---|---|---|
| weekday | 工作日 / 周末输入下的澄清逻辑 | 5/5 | S4 detect_weekday_context 在所有跨语境 case 触发 |
| time_bucket | 4 时段画像（工作日早 / 晚 / 周末上午 / 下午）打分差异 | 5/5 | weekend_afternoon 在 1297 条 intensity ≥ 0.7 上加权显著 |
| text_intake | 自然语言意图抽取 + 槽位补全（v2.5） | 5/5 | text_intake.py 槽位补全率 100%，多模态 fallback 正常 |
| convergence | 4 成员模式群偏好收敛 | 5/5 | 反复横跳 / 沉默 / 隐性领导 / 正常各 1 case + 1 混合 |
| memory | 跨 session 偏好记忆（v2.7 user_memory） | 5/5 | record/get/forget/infer/merge 五件套各 1 case |
| **合计** | | **25/25** | **100%** |

**总耗时**：~5min / 跑（混合 mock + 抽样 LongCat）。

## 4. v3.1 D7 校准结果（滑窗 ECE + 置信度分布）

> v3.1 commit `d4b1c50` 后真实数据（5/29 跑出）。`agents/calibration_history.py` 输出。
> 数据规模：**799 plans / 3,885 traces / 291 paired outcomes**

### 4.1 全局指标

**Global ECE = 0.1089**（目标 ≤ 0.15，**达成**）

可信度：291 paired (trace ↔ outcome) 样本计算，覆盖 v2.4 D1 接入主路径之后所有 plan。

### 4.2 滑窗 ECE 演化（window_size=20，14 窗）

```
window  n  ts_range          ece    mean_conf  mean_acc
1       20 [t0, t0+25min]    0.34   0.77       0.85
2       20 [t0+25, +1h57]    0.35   0.79       0.80
3       20 [+1h57, +3h17]    0.34   0.76       0.85
4       20 [+3h17, +3h33]    0.25   0.76       0.95   ← 第一次降到 0.25 以下
5-8     20 [+3h33 短窗连测]   0.06   0.74       ~0.74  ← 短期内稳定低位
9       20                  0.29   0.74       0.45    ← 反弹（部分 outcome 失败）
10-13   20                  ~0.10  0.74       变动     ← 多数稳定 0.06-0.22
14      20                  0.25   0.75       0.95
```

**真实结论**：

- 开局 3 窗 ECE 0.34-0.35（v3.1 校准前 baseline）
- v3.1 校准时序接入后从 window 4 开始，ECE 开始低于 0.30，并多次低至 0.04-0.10
- **不是单调下降**——窗 9 反弹到 0.29 是因为该批次 mean_actual_success 跌到 0.45，置信度跟不上真实退化
- 全局聚合 ECE 0.1089，已达 D1 设定阈值

### 4.3 置信度直方图（10 桶 / 全量 3,885 trace）

| bucket | n | pct | 解读 |
|---|---|---|---|
| 0.0-0.1 | 0 | 0% | 无极低置信（合理——plan 不会出 < 0.1 step） |
| 0.1-0.2 | 0 | 0% | 同上 |
| 0.2-0.3 | 0 | 0% | 同上 |
| 0.3-0.4 | 0 | 0% | 同上 |
| 0.4-0.5 | 0 | 0% | 同上 |
| 0.5-0.6 | 0 | 0% | 同上 |
| 0.6-0.7 | 12 | 0.3% | 极少数低置信 step |
| **0.7-0.8** | **3,072** | **79.1%** | ⚠ **严重集中** |
| 0.8-0.9 | 23 | 0.6% | 很少 |
| 0.9-1.0 | 778 | 20.0% | 高置信占比次高 |

⚠ **诚实地说，这是个问题**：79.1% 的 trace 落在 0.7-0.8 桶——说明当前 `plan_tracer.record_step` 的 confidence 来源主要是 plan_tracer 默认值（约 0.74-0.78），LLM 输出的细粒度 confidence 还没真正注入。

**但 ECE 仍达标**：因为这批 step 的 actual_pass 率刚好接近 0.7-0.8（mean_actual_success 多数窗口在 0.7-0.95），plan_tracer 的"保守默认值"恰好打在真实表现附近。

**v4.0 改进项**（见 `ROADMAP.md`）：让 `planner_tot.py` / `planner.py` 把 ToT 自评分数（5 维加权）真正传到 plan_tracer.confidence，覆盖默认值，实现真正"诚实的置信度"。

### 4.4 路演话术（基于真实数据）

> "我们跑了 799 个 plan，3,885 步骤 trace，291 个真实 outcome 对照。Global ECE 0.1089——AI 自评的置信度和真实成功率，平均偏差 11 个百分点，达到 D1 校准目标。
>
> 但坦诚说，置信度还过于集中在 0.7-0.8——说明 LLM 自评的细粒度还没充分用上。这是 v4.0 的改进点。"

不藏拙是更可信的路演。

## 5. 怎么重跑

```bash
# v3 100 场景 LongCat baseline
BJ_PAL_LLM=longcat python3 scripts/run_longcat_eval100.py
python3 scripts/eval_compare.py        # 输出本文档第 1 节表

# L1 anchor（每 commit）
python3 evals/behavioral/run_l1.py

# L2 集成（每周）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l2.py

# L3 全量（每 release）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l3.py

# v3.1 校准重算（基于已有 prediction_log）
python3 -c "from agents.calibration_history import recompute_all; recompute_all()"
```

## 6. 历史归档

`evals/results/` 现存：

| 文件 | 是 |
|---|---|
| `L1_4051abd_1779697379.json` | v2.4 D5 接入 broadcast 后基线 |
| `L1_f7f8a62_1779702022.json` | v2.4 D1+D5 UI 加 trust_panel 后 |
| `L3_6206e26_1779709810.json` | v3.0 L2 评测落地后首跑 L3（本文第 2 节数据来源） |

L2 + v3.1 calibration 暂未归档 JSON（ROADMAP 待办）。
