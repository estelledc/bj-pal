# BJ-Pal · 路线图（v3.1 → v4.0+）

> 历史：本文档前身是 `IMPROVEMENT_PLAN.md`（基于 100 条 AI 用户访谈的 P0+P1 落地清单），所有 P0+P1 项已在 v2.2-v3.0 全部完成。
> 当前定位：v3.1 已就绪 + promo 8 件套完整。本文记录已落地里程碑摘要 + v4.0 改进候选路线。
> 配套：`README.md` 进度段 / `docs/100-improvements.md` 6 路调研 100 条详单 / `docs/archive/V2.4_ITERATION_PLAN.md` v2.4 10 轮收敛日志

---

## 一、已落地里程碑摘要（v1 → v3.1）

| 版本 | 日期 | 核心交付 | 关键 commit |
|---|---|---|---|
| **v1** | 5/15 - 5/19 | 14 天基础链路：loader / amap / ugc / planner / rank_fuse / probe / replanner / mock_book / mock_message / Streamlit UI / 偏好镜子 | `9f8cded` 初始化 |
| **v2.1** | 5/19 - 5/20 | 11 项改善：真实路由 / 群发投票 / mock 真实化 / 4 类 reroute / UI 品牌 / vision 抽取 / 多模态对比 / AddOn / 4 人调和 / vs GPT 对照 / 雷达图 | — |
| **v2.2** | 5/20 - 5/21 | 数据扩展 4 件套：UGC 37 → 1102 条（5 类来源）/ time_bucket / 动态 trap / routes 52 → 1892 | — |
| **v2.2 P0+P1** | 5/21 | red_flags / screening / 预算私密 / reroute 分流 / apology / 老用户回顾 / 老年简化 / mock 显式标注 全部落地 | — |
| **v2.4 三件套** | 5/22 - 5/25 | D3 分层评测 + D5 群偏好收敛 + D1 履约 trace 内核 + S4 detect_weekday 推 L1 → 100% | `a628040` `c6e531c` `6f9deab` `f7f8a62` `647ae1c` |
| **v2.5** | 5/22 后 | 多模态首屏：text_intake + multimodal_intake UI | `5dda6f3` |
| **v2.6** | — | 4 个 time_bucket 派生信号扩展 | `6690bc2` |
| **v2.7** | — | stateful agent：跨 session 用户偏好记忆 | `8397fd2` |
| **v2.8** | — | D7 可视化：路线可惜度 + 群体共识进度 bar | `8987253` |
| **v3.0** | 5/26 前后 | L2 集成评测（5 模块×5 case）+ L3 全量评测（100×5=280 检查全过）+ ToT/OPTW/Kemeny+Borda 三算法落地 + 9 个北京特色派生信号 + UGC 扩到 8666 条 | `6206e26` `588863a` |
| **v3.1** | 5/27 | D7 校准时序：滑窗 ECE 演化 + 置信度分布直方图 / Global ECE 0.1089 达标 | `d4b1c50` |
| **promo** | 5/26 | 8 件套全自动生成：pitch deck / landing page / 小红书 / one-pager / hero / architecture | `ba8ab7e` |

**未落地的用户实操项**（不在代码侧）：录屏 + 路演排练 + GitHub Pages 部署。见 `docs/DEMO_SCRIPT.md`。

---

## 二、v4.0 改进候选（按优先级）

### P0 · 必做（决赛后第一周）

#### P0.1 · plan_tracer.confidence 真实化
**来源**：`docs/eval-100-results.md` §4 暴露的问题 — 79.1% trace 集中在 0.7-0.8 桶，是 plan_tracer 默认值，LLM 自评的细粒度未注入

**改动**：
- `src/agents/planner.py` / `planner_tot.py`：把 ToT 自评分（5 维：commonsense + hard_constraint + utility + diversity + rationale_quality）加权聚合后，作为 `step.confidence` 传给 `plan_tracer.record_step()`
- `src/agents/plan_tracer.py`：移除默认 0.74-0.78，要求所有上游显式传 confidence
- `evals/behavioral/run_l3.py`：S1 检查不变（仍验 coverage + fallback），但要新加 `check_s1_distribution`：confidence 分布散度 ≥ 0.15（避免再聚集）

**验收**：跑完 100 plan 后，置信度直方图至少 4 个非零桶；Global ECE 不退化（仍 ≤ 0.15）

**工时**：8h

#### P0.2 · L2 evals 归档化
**来源**：`docs/EVAL_FRAMEWORK.md` §3 + Q29 提及，L2 当前只 stdout 不写 JSON

**改动**：
- `evals/behavioral/run_l2.py`：加 `--save-json` flag，写 `evals/results/L2_<sha7>_<ts>.json`，schema 跟 L1/L3 一致
- 5 模块子分解结果（weekday / time_bucket / text_intake / convergence / memory 各 5 case 通过率）写进 JSON
- `docs/eval-100-results.md` §3 的"5 模块通过率表"自动从最新 L2 JSON 拉

**验收**：跑一次 `run_l2.py --save-json` 后，`evals/results/L2_*.json` 出现且 schema valid

**工时**：3h

#### P0.3 · 真实 amap 实时数据接入（M1 Sprint）
**来源**：`bj-pal-amap-heat-research.md` 调研 — 高德组合 API（POI 详情 + 路况 + 天气）1 周 MVP 即可上线

**改动**：
- 新建 `src/tools/amap_realtime.py`：调高德三组 API（poi/detail / traffic/incident / weather/realtime）+ Redis 缓存 3min
- `src/tools/rank_fuse.py`：ranking 公式加 `live_heat_score: 0.10` 分量
- `src/tools/availability_probe.py`：实时拥挤度作为 trap 触发的第 4 维（前 3 维：trap POI + UGC negative + 高峰启发式）

**验收**：实测故宫国庆 14:00 live_heat_score 飙到 0.9+，ranking 自动降权；非高峰期 < 0.3 不影响

**工时**：5d（含申请 amap 商业 key）

### P1 · 应做（决赛后第一月）

#### P1.1 · 反馈学习闭环
**来源**：100-improvements [13] Reflexion 长记忆 + [85] Pairwise A/B + [89] Helicone + Langfuse score 闭环

**改动**：
- `src/agents/reflection_memory.py` 新增：每次 reroute 成功后让 LLM 写 ≤ 3 句 lesson 入 SQLite `reflections(query_hash, dimension, lesson, used_count)`
- 下次同区域同时段 query 时 retrieve top-3 注入 prompt
- UI 加"反馈"按钮，存 `user_feedback(plan_id, score, reason)`，进入 evals/golden 候选池

**工时**：1 周

#### P1.2 · 多城扩展（上海 / 杭州）
**来源**：100-improvements 第十二章 [100] 跨城 style_signature

**改动**：
- ETL 流水线参数化：`scripts/fetch_amap_pois.py --city shanghai --district-list xxx`
- UGC 抽取链路通用：vision_extractor + text_aspect_extractor 城市无关
- 新城 cold start：用北京已有的 style_signature（节奏 chill/紧凑、强度 walk_km）作 grounding，让 LLM 生成等价新城方案

**工时**：3 天 / 城（不含 amap key 申请）

#### P1.3 · agent SDK 抽离
**来源**：让 BJ-Pal 不只服务北京下午活动，也成为通用 "L1+L2+L3 评测 + plan_tracer + ECE" 的可复用 agent infra

**改动**：
- 抽 `src/agents/{plan_tracer, calibration_history, voting, planner_tot, optw_solver}` → 独立 package `bjpal-agent-sdk`
- 文档化 5 信号 + L1/L2/L3 防火墙模式
- 给 video-eval-agent / 视频评价框架 等姊妹项目复用

**工时**：1 周

### P2 · 可做（视决赛结果）

#### P2.1 · 真实美团商家 API 对接（如果黑客松后接进商家 SDK）
- 替换 `mock_book.py` 实现层；接美团商家开放 API + 哗啦啦 / 客如云
- `D1 SLA 外壳` 重启：mock 阶段说不清"赔付"，真实 API 后才能做信任档案
- 工时：跟商家谈通后 5d

#### P2.2 · 用户研究真实化
- 100 条 AI 访谈 → 招 10-20 真实北京用户做 1:1 50min 深访
- 验证 5 强信号在真实用户中的相对强度
- 触发器：决赛后 + 公司分配真实用户研究预算
- 工时：1 周（招募）+ 2 周（访谈 + 编码）

#### P2.3 · 反馈即学习（A/B + ELO）
- 新旧 prompt 各跑 golden set，judge prompt"两份周六行程哪份更合理？"
- 统计胜率 + 95% CI
- 改 prompt 不再"我觉得变好了"

---

## 三、明确不做（v3.x 已砍 / 性价比低）

| 项 | 原因 |
|---|---|
| ~~多 agent debate / RL~~ | 命题需要落地不是论文 hot 词；响应时间从秒级跳 30s+ 反扣分（QA Q4） |
| ~~D1 SLA 外壳（mock 阶段）~~ | mock 阶段说不清"赔付"，等真实美团商家 API 接通再做（P2.1） |
| ~~用户对话持久化~~ | 隐私优先，session_id 哈希前 8 位 + 滚动；触发器需要用户明示授权（QA Q11） |
| ~~分布式 agent 协作~~ | 本地生活短链路用 Plan-and-Execute 已够；分布式徒增复杂度 |
| ~~跨城实时 amap key 池~~ | 单城 RPM 30 已够 demo；多城商业化前不烧 key |

---

## 四、3 大不能踩的雷（写进 DESIGN.md "反模式"章节）

> 来自 100 条 AI 用户访谈 Session D Q5 高频；当前 v3.x 的代码侧防御见 `docs/USER_RESEARCH_FINDINGS.md` §6.2

1. **越推越窄的"美团模式"** — `tools/audience_segment` + `poi_graph` 邻居发现 + ToT diversity 维度
2. **重复推荐已消费商品** — `agents/user_memory.get_visit_history` + planner prompt 软约束
3. **假装懂用户其实是广告** — `dataset_version` 5 类透明 + 商家自填 vs UGC 视觉分层 + reasons 引用 raw_text_excerpt

---

## 五、决策追溯（v2.4 → v3.1 收敛要点）

完整 10 轮收敛日志见 `docs/archive/V2.4_ITERATION_PLAN.md`，关键决定：

| 决定 | 依据 | 在哪一轮 |
|---|---|---|
| D3 优先于 D1 | AI 用户访谈不是真信号，要先量化 | Round 2 |
| D1 砍 SLA 外壳 | mock 阶段说不清赔付 | Round 3 |
| 评测分 L1/L2/L3 | LLM 调用成本 vs 信号强度 | Round 4 |
| 聚焦群体型用户 | 命题字面"周六下午 4 个朋友" | Round 5 |
| D2 推 v2.5 | 对个人型用户增益高，但抗 LLM 侵蚀弱 | Round 5+6 |
| 加 ECE 等度量 | Boil the lake 应有定量目标 | Round 7 |
| v2.4 = D3+D5+D1 内核 | 5min demo 最小可信故事 | Round 10 |

---

## 六、测试基线（每个 P0 完成后必跑）

```bash
# v1 + v2 基础
for t in tests/smoke_test.py tests/test_tools.py tests/test_planner.py \
         tests/test_ranking.py tests/test_reroute.py tests/test_preference_mirror.py \
         tests/test_route_lookup.py tests/test_v2_mock_reroute_addon.py \
         tests/test_v2_broadcast.py tests/test_v2_vision.py tests/test_v2_group_harmony.py \
         tests/test_data_coverage.py; do
    python3 "$t" || { echo "FAIL: $t"; break; }
done

# v3.0 算法 + 数据深度（21 套件，160+ 测试）
python3 tests/test_voting.py
python3 tests/test_optw_solver.py
python3 tests/test_planner_tot.py
python3 tests/test_ugc_bm25.py
# ... 更多见 README §测试段

# v3.x evals（行为基线）
python3 evals/behavioral/run_l1.py
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l2.py
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l3.py
```

---

## 七、本路线图维护原则

- 每完成一个 P0/P1 项，在 §一 落地里程碑表加一行 + commit hash
- v3.x 期间不再加 P0（用 EVAL_FRAMEWORK 跑回归而不是跑改动）
- v4.0 P0 全部落地后切换到 v5.0 路线
- 决策追溯只追 v2.4 起的 10 轮收敛 + v3.x 关键 PR 描述，不重复历史
