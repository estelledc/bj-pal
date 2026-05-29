# BJ-Pal · 北京下午活动管家 · 设计文档

> 美团黑客松 2026 短时活动规划 Agent / 命题：`task.md` / 交付：Demo + Tool 实现 + 设计文档（≤ 2 页）
> 配套：`EVAL_FRAMEWORK.md`（评测体系）/ `ROADMAP.md`（v3.1 → v4.0 路线）/ `100-improvements.md`（6 路调研 100 条）

## 1. Planning 策略

**Plan-and-Execute + 主动 Probe + 局部 Replan + v3.0 三分支 + v2.4 履约 trace + v3.1 ECE 校准**

```
用户一句话 → Planner → Plan v1 (5-7 步 JSON)
                ↓
          AvailabilityProbe (扫每一步)
                ↓ 触发风险
          Replanner (局部替换 failed step)
                ↓
          Plan v2 + RerouteEvent
                ↓
          Mock 下单 + IM 话术化卡片
```

- **Planner**：候选池由 `amap_search` + `ugc_signals` 拉好喂进 prompt，LLM 只做"挑哪个 + 编排顺序 + 写 rationale"，不让 LLM 编造 POI（用 `mock_uses_real_pois` 测试守门）
- **AvailabilityProbe** 三层触发：① 4 个 hardcoded trap POI（demo 兜底）→ ② **动态 trap 评分** `compute_dynamic_trap_score` = amap 评分 ≥ 4.7 + UGC negative crowd/queue/booking_risk 交叉 + 老字号关键词加权（≥ 0.5 即视为 trap）→ ③ UGC negative+conf≥0.7 软触发；其余用 rating + 高峰期启发式给温和 wait_min
- **Replanner**：不重规划全 plan（避免 demo 卡顿），只把 failed_step 替换为同 area_anchor + 同 category 的 ranking top1，避开 trap_set
- **L2 Ranking 公式**（每步候选都过 + Task 1.2 时段加权 + P0.1 时效衰减）：
    `score = 0.35 · amap_rating + 0.30 · ugc_soft⁺ + 0.15 · budget_fit + 0.10 · distance + 0.10 · crowd_penalty`
    其中 `ugc_soft⁺` = `Σ (sign · confidence · 2 · weekend_afternoon_intensity)` — 周六下午强相关 aspect（intensity ≥ 0.7）影响 ranking 更多，负相关（< 0.4）几乎不参与
    每条候选附 `reasons[(factor, contrib, evidence)]`，evidence 直接引 UGC 原文 + intensity 最高那条优先选作 evidence，可解释
- **P0.1 时效衰减 + red flags 面板**（信号 2/6）：
    每条 Aspect 附 `evidence_age_days` + `evidence_source_count` + `decayed_confidence`（按品类半衰期：餐饮 30 天衰减 50%、景点 90 天衰减 30%、文化场所 180 天衰减 20%）
    UI 每张 POI 卡片必显示 1 条最关键吐槽（`extract_red_flags`）— 即使整体推荐这家
    当 `confidence < 0.5` 或 `evidence_age_days > 30`，UI 该标灰、降权但保留可见

### 1.1 v3.0 三分支 Planner 选择树

| 分支 | 入口 | 算法 | 何时选 |
|---|---|---|---|
| **普通** | `planner.plan()` | LLM 单轮 + rank_fuse 候选池 | 默认；query 简单 / 群人数 ≤ 2 / 时间富裕 |
| **ToT 分支** | `planner.plan(branch_hint="balanced/culture_first/food_first", temperature=0.7)` → 内部调 `planner_tot.plan_tot()` | Tree of Thoughts（arxiv:2305.10601）K=3 候选并发 + 自评分（commonsense + hard_constraint + utility + diversity + rationale_quality） | 复杂约束 / 用户提了 ≥ 2 个偏好维度 / 评委 demo 想看"算法选择" |
| **OPTW 分支** | `planner.plan_optw()` → `optw_solver.solve_optw()` | OPTW + OR-Tools CP-SAT，5s timeout | 候选池 ≥ 30 / 强时间窗约束 / "多 POI 最优访问序列"问题 |

三分支 entry 都过 `plan_tracer.record_plan()`，下游 reroute / probe / 群投票链路一致。

### 1.2 v2.4 履约 trace + v3.1 ECE 校准

**plan_tracer**（`agents/plan_tracer.py`）：

每步 plan 落 SQLite 时记录 `(step_id, decision, confidence, fallback_action)`。`coverage_rate(plan_id, expected_steps)` 必为 1.0；`iter_steps(plan_id)` 返回所有 trace 用于 trust_panel 展示。

**calibration_history**（`agents/calibration_history.py`，v3.1）：

跑完每批 plan 后用 ECE（Expected Calibration Error）量化"AI 说 70% 确定时是不是真的 70% 对"。**滑窗 ECE 演化**：每 N 个 plan 算一次 ECE，绘 7 天滑窗曲线；**置信度直方图**：把所有步骤按 `confidence` 分 10 桶（0.0-0.1 ... 0.9-1.0），看每桶实际 pass 率与 桶中位数对齐度。目标 ECE ≤ 0.15。

落库表：`calibration_history(window_id, ece, plan_count, computed_at)` + `confidence_buckets(window_id, bucket_idx, predicted_mid, actual_pass_rate, n)`。

UI：`ui/calibration_panel.py` 侧栏可展开，给评委看"我们不是黑盒打分，每周都在校准自己有多准"。

### 1.3 v3.0 群投票（Kemeny + Borda + Pareto）

**voting**（`agents/voting.py`，11/11 测试）：

| 算法 | 用途 | 复杂度 |
|---|---|---|
| Borda | N 人对 K 候选粗排 | O(NK) 快 |
| Kemeny-Young | 最小 Kendall tau 共识精排（ILP，`pulp`） | O(K!N) 准 |
| Borda + Kemeny 两段 | 第一轮 Borda 粗筛 top-7 → 第二轮 Kemeny 精排 | 互补：快 + 准 |
| 4 sub-ranker Pareto | `group_harmony.py`：每成员一个 sub-ranker，min/avg pareto 前沿 | 群体偏好可视化 |

接入点：`mock_message.broadcast` 收到投票结果 → `voting.aggregate(votes, method="kemeny")` → 反馈给 `replanner` 做"1 否决重 reroute"。

护城河话术："美团黑客松首次用社会选择理论（Kemeny-Young）"。

## 2. 工具调用链路

| 阶段 | Tool | 输入 | 输出 |
|---|---|---|---|
| 候选检索 | `amap_search.search_pois(area, category, constraints)` | 片区/类目/预算/亲子/营业时间 | List[POI]（按 rating） |
| UGC 信号 | `ugc_signals.fetch_aspects/risk_signals/summarize_area` | area / poi | aspect 切片 |
| 融合排序 | `rank_fuse.fuse_and_rank(candidates, constraints, center)` | 候选池 + 约束 + 中心点 | List[RankedPOI] with reasons |
| 偏好澄清 | `preference_mirror.clarify_preference(raw)` | 用户原话 | needs_clarification + 选项 / 已提取约束 |
| 可达性 | `availability_probe.probe(poi, party, time)` | POI / 人数 / 时间 | status / wait_min / risk_tags / fallback_action |
| 余位重选 | `agents.replanner.replan_step(plan, idx, probe_result)` | 原 plan + idx + probe | new Plan + RerouteEvent |
| 下单 | `mock_book.book_restaurant / book_cake_delivery` | poi + time + party | BookingResult / CakeDeliveryResult |
| 沟通 | `mock_message.render_im_card / send_via_wechat_mock` | Plan + audience + contact | MessageCard + SendResult |
| 调用日志 | `tool_call_log.timed_call` 上下文管理器 | tool_name + params | 落 `tool_calls.db`，UI Trace 侧栏可查 |

每次工具调用都通过 `tool_call_log.timed_call(...)` 记录到 SQLite，包含 timestamp / params / response / latency / status / error，供 UI Trace 面板和评委 Q&A 时一键展开。

## 3. 异常处理机制

| 故障类型 | 触发位置 | 检测信号 | 处置 |
|---|---|---|---|
| LLM 输出非 JSON | Planner / Replanner / PrefMirror | `_safe_parse_json` 返回 None | 抛 RuntimeError + 完整 LLM 文本，前 300 字给开发者；UI 上层 catch 退到 mock client |
| LLM 漏字段 | Planner | Step.from_dict 缺字段 | dataclass 字段全部带默认值，缺什么用默认 |
| POI 编造 | Planner | `mock_uses_real_pois` 测试 + 上线后 SQL 反查 | 候选池在 prompt 里只给真实 ID，prompt 强约束"只能选给定 POI"，运行时丢弃不匹配 ID 的 step |
| 餐厅满位 | mock_book | `BookingResult.status=no_availability` | 触发 reroute 重选；返回 estimated_wait_min 让用户决定 |
| 商家拒单 | mock_book | `rejected_by_merchant` | 同上 |
| 网络 timeout | mock_book / LLM | `BookingResult.status=timeout` | 自动重试 1 次 → 仍失败提示用户切换 |
| 拥堵 / 排队 | availability_probe | trap POI / UGC negative / 高峰期启发式 | wait≥30 自动 reroute；wait<30 仅 warn |
| 无可用替补 | replanner | candidates 池为空 | 标记 step.is_rerouted + rationale "无法重选"，不删 step（保持 plan 完整性） |
| 多步连环失败 | probe_plan | 最多 reroute 2 步 | 防无限循环；超阈值 fallback 到 fallback_strategies 文本（child_tired / weather_bad） |
| LongCat key 缺失 | llm_client | `LONGCAT_API_KEY` 未设 | 友好错误提示 → 切换到 BJ_PAL_LLM=mock 离线模式 |
| Tool Call 异常 | tool_call_log.timed_call | 所有 tool 都包在 with 块里 | 异常被记录为 status=error 的 trace 条目，UI 仍能正常展示其他成功调用 |

## 4. 数据 / 安全

- **数据规模（v2.2）**：5,656 北京 POI（含 1,435 餐饮）+ **1,102 条 UGC aspect / 103 片区** + **1,892 条多模态路线**（步行/骑行/驾车/公交 × 473 leg）+ 89 张原始大众点评截图（仅本地，不入 git）
- **UGC 5 类透明区分**（每条都有 `dataset_version` + `extraction_status` + `privacy_status` 字段）：
  - `manual_ugc_seed_v1` (37) — 大众点评截图 GPT-4V 抽取
  - `synthetic_from_public_summaries_v2` (479 + 137 场景 + 116 主题 = 732) — Class A 公开评论汇总 LongCat 抽取
  - `derived_from_amap_attributes_v2` (333) — Class B 仅基于 amap 客观字段（评分 / 价格 / 类目）推理，禁止编造
- **时段画像**：1102 条 100% 填 `weekend_afternoon_intensity ∈ [0,1]` — HIGH 215（≥0.7 强相关）/ MID 764（中性）/ LOW 123（< 0.4 不适合下午）；纯规则填充，db rebuild 自动 fallback compute
- **routes 持久化**：1,840 条 estimated_v2（haversine × 1.3 detour + 4 模式标准速度）+ 52 条 amap cache，dump 到 `data/amap/routes/expanded_v2.jsonl`，clone 即可用
- **隐私**：UGC `privacy_status=identity_removed`（manual_v1）/ `public_review_aggregation_no_pii`（合成）/ `amap_objective_no_pii`（推理）；用户对话不持久化；Tool Call Log 仅存 session_id 哈希前 8 位
- **合规**：所有"下单 / 配送 / 微信发送"是 mock；生产路径见 `MOCK_API_README.md`，每个 mock 接口在注释里标了真实 API 对接路径
- **可扩展**：v1 北京试点；amap 抓取脚本一键扩到上海/杭州；UGC 抽取双链路通用 — `agents/vision_extractor.py` 截图入口 + `etl/text_aspect_extractor.py` 文本入口；Planner / Replanner / Ranking 完全城市无关
- **实时性路径（M1 Sprint）**：见 `explorations/ideas/bj-pal-amap-heat-research.md` — 高德组合 API（POI 详情 + 路况 + 天气）1 周 MVP 即可上线，ranking 加 `live_heat_score: 0.10` 分量

## 5. 八个差异化护城河（v3.1 升级版）

1. **UGC 软信号融合 ranking + 可解释 reasons + 5 类来源透明**：每个 POI 选择附 3 条原因 + 真实 UGC 原文片段 + dataset_version 溯源；评委质问"这条 evidence 哪来的"一键展开 raw_text_excerpt 字段。v3.0 数据规模 **8,666 条 / 6300+ POI 信号网**（5/21 全天 R6-R100 共 95 轮扩展 +2366 条）
2. **主动 reroute + 动态 trap（不是硬编码）**：`compute_dynamic_trap_score` 基于 amap 评分 + UGC negative 交叉触发，全聚德等老字号自动识别；UGC 8666 条交叉验证，不再是"演脚本"
3. **时段画像 4 bucket + weekend_afternoon_intensity**：1102 条 100% 填 [0,1] 强度（v2.2 原数据）+ v2.6 4 时段扩展（工作日早 / 工作日晚 / 周末上午 / 周末下午），ranking 公式按 bucket × intensity 双重加权 — "周六下午"差异化有数据支撑，不是 prompt 写死
4. **群发投票 + Kemeny-Young + Borda + 4 人 Pareto + 1 否决重 reroute**：`agents/voting.py`（v3.0）+ `mock_message.broadcast` + `group_harmony.py` 4 sub-ranker pareto；社会选择理论首次用于本地生活场景；命题字面要求 + 无人做
5. **三分支 Planner 选择**（v3.0，详见 §1.1）：普通 / ToT / OPTW 三入口，按 query 复杂度自动切换；`planner_tot.py` 5/5 测试，`optw_solver.py` 7/7 测试 + 端到端（4 步 POI 路线 5s 出 FEASIBLE 解）；arxiv:2305.10601 + Vansteenwegen 2011 论文级别方法
6. **L1/L2/L3 三层评测 + 5 信号检查 + ECE 校准**（v2.4 D3 + v3.0 + v3.1，详见 `EVAL_FRAMEWORK.md`）：每 commit 跑 anchor 5 case / 每周扫 5 模块 25 case / 每 release 跑 100 case × 5 信号 = 280 检查；v3.1 D7 滑窗 ECE 演化 + 置信度分布直方图，"AI 不是黑盒打分，每周都在校准自己"
7. **plan_tracer 履约 trace 内核 + trust_panel 可视化**（v2.4 D1）：每步落 `(decision, confidence, fallback_action)`，UI 侧栏可展开 — "AI 这步 70% 确定，因为 UGC 厚度只 5 条"；不是空头 SLA，是可验证的"承诺-兑现"链条
8. **stateful 跨 session 记忆 + AddOn 主动建议**（v2.7 user_memory + v2 改 7 addon_agent）：record / get / forget / infer / merge_into_prompt 五件套；"它在学你"叙事；同行人状态变化（带娃 / 老人）触发 facility / accessibility 信号

## 6. v3.0 评测体系（详见 `EVAL_FRAMEWORK.md`）

**为什么分三层**：评测频率 × 信号强度解耦。一刀切要么贵（全量 30min/次）要么弱（子集信号差）。

| 层 | 频率 | 规模 | 耗时 | LLM | 用途 |
|---|---|---|---|---|---|
| L1 anchor | 每 commit | 5 case | ~30s | mock | 5 强信号冒烟 |
| L2 integration | 每周 / 改模块 | 5 模块 × 5 = 25 | ~5min | 混合 | 行为基线 |
| L3 full | 每 release | 100 × 5 = 280 | ~30min | LongCat | 全量分布 + TravelPlanner 4 指标 |

**5 信号检查（S1-S5）**：S1 责任承担（plan_tracer 覆盖 + fallback）/ S2 红旗可见（extract_red_flags）/ S3 道歉容忍（apology_card 触发）/ S4 周末聚焦（detect_weekday_context）/ S5 重要场合（detect_screening_mode）。

**通过率演进**：v1 0.275 → v2 0.275 → v3 0.470 → v3.0 L3 全量 100% pass → v3.1 ECE 进一步收敛。

**防火墙原则**（参考 video-eval-agent gstack）：fixture 与 production prompt 分库 / mock 优先 / plan 缓存可观察 / ECE 是连续指标。
