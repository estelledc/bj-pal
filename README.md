# BJ-Pal · 周六下午的决策解药——一句话替你扛下选错的责任

> 美团黑客松 2026 短时活动规划 Agent 实现
>
> 命题：`docs/task.md`
> 设计：`docs/DESIGN.md`（≤ 2 页架构 + 异常处理）
> 评测：`docs/EVAL_FRAMEWORK.md`（L1/L2/L3 三层 + 5 信号检查）
> 用户研究：`docs/USER_RESEARCH_FINDINGS.md`（100 条 AI 访谈，5 强信号）
> 路线图：`docs/ROADMAP.md`（v3.1 → v4.0 + 已落地里程碑）
> 路演：`docs/DEMO_SCRIPT.md` / `docs/QA_PREP.md`（90s pitch + 18 题 Q&A）
> 物料：`promo/README.md`（pitch deck / landing page / 小红书 / one-pager 全套）
> 数据：`docs/DATA_INVENTORY.md` / `docs/100-improvements.md`（数据资产 + 100 条调研改进清单）
> 调研：`../../ideas/bj-pal-amap-heat-research.md` + `bj-pal-data-roadmap.md`

## 元定位（来自 100 条 AI 用户访谈）

> **用户付钱不是为"规划方案"，是为"AI 替我扛了选错的责任"。**
> 这是一个**情绪基础设施**，不是工具产品。

100 条访谈得到的 5 强信号：

1. "选错的责任"才是群决策真正的痛（4/5）— v2.4 D1 plan_tracer + trust_panel
2. 必须看到吐槽，不只是分数（5/5）— P0.1 red_flags 面板
3. 选错容忍度 = 2 次（5/5）— P0.5 错误自承认 apology
4. 工作日不属于这个 App（4/5）— 主台词聚焦周六下午 + v2.6 4 时段画像
5. 重要场合 = 工具不是代理（5/5）— P0.2 筛选模式

5 信号在 v3.1 的代码落地见 `docs/USER_RESEARCH_FINDINGS.md` 验证表。

## 架构一图（v3.1）

```
用户一句话 + 偏好
    ↓
[偏好镜子 / text_intake / multimodal_intake]   ← v2.5 多模态首屏
    ↓
[Planner LLM 三分支]
    ├─ 普通分支：amap + ugc + rank_fuse → Plan v1（5-7 步）
    ├─ ToT 分支：K=3 候选并发自评 → 选最优分支          ⭐ v3.0
    └─ OPTW 分支：OR-Tools 全局最优访问序列              ⭐ v3.0
    ↓
[plan_tracer]  每步落 (decision, confidence, fallback) ⭐ v2.4
    ↓
[AvailabilityProbe] ← trap POI / UGC risk / 高峰启发式 / 等位预测
    ↓ 触发风险
[Replanner] ← 同片区同类 ranking top1 替换 failed step
[group_convergence] ← v2.4 群偏好收敛（4 成员模式）
[voting]            ← v3.0 Kemeny / Borda + Pareto 群投票
    ↓ Plan v2 + RerouteEvent
[calibration_history]  ← 滑窗 ECE + 置信度分布直方图    ⭐ v3.1
    ↓
[mock_book] → 餐厅预订 + 蛋糕配送
[mock_message] → 话术化 IM 卡片 → 微信发送（mock）
    ↓
[Tool Call Log + tracing OTel] ← 全程 SQLite 留痕，UI Trace 侧栏可展开
```

## 项目结构

```
bj-pal/
├── data/                                # gitignored, 86M
│   ├── amap/{merged,routes,...}         # 5,653 POI + 1,892 路线
│   ├── ugc/                              # 89 张大众点评截图
│   ├── manual_ugc_seed.jsonl            # 37 条原始 GPT-4V 抽取
│   ├── heritage_brands.json             # 20 老字号信任度
│   ├── heritage_reservations.json       # 30+ 限流景点配额
│   └── holiday_calendar_2026.json       # 7 法定节假日 + tier
├── src/
│   ├── loader.py                        # SQLite 索引 + query_*
│   ├── tools/                            # 工具层（17 个）
│   │   ├── types.py
│   │   ├── amap_search.py / ugc_signals.py / rank_fuse.py
│   │   ├── availability_probe.py / mock_book.py / mock_message.py
│   │   ├── tool_call_log.py / route_lookup.py
│   │   ├── ugc_bm25.py / wait_predictor.py            # ⭐ v3.0 hybrid retrieval
│   │   ├── time_bucket.py                              # ⭐ v2.6 4 时段
│   │   ├── heritage_brand.py / reservation.py          # ⭐ v3.0 北京特色
│   │   ├── seasonal.py / facilities.py
│   │   ├── weather_shelter.py / crowd_forecast.py
│   │   ├── poi_graph.py / audience_segment.py / parking.py
│   ├── agents/                           # agent 层（22 个）
│   │   ├── llm_client.py / llm_robust.py / types.py
│   │   ├── planner.py / planner_tot.py / replanner.py # ⭐ v3.0 ToT 分支
│   │   ├── voting.py / optw_solver.py                  # ⭐ v3.0 Kemeny + OPTW
│   │   ├── group_convergence.py / group_dynamics.py / group_harmony.py
│   │   ├── preference_mirror.py / text_intake.py       # ⭐ v2.5 意图抽取
│   │   ├── vision_extractor.py / addon_agent.py
│   │   ├── plan_tracer.py / calibration_history.py     # ⭐ v2.4 + v3.1 履约
│   │   ├── user_memory.py                              # ⭐ v2.7 stateful
│   │   ├── tracing.py / opportunity_cost.py
│   ├── ui/
│   │   ├── app.py / timeline.py / map_view.py
│   │   ├── trust_panel.py                              # ⭐ v2.4 履约面板
│   │   └── ...
│   ├── etl/                              # 数据扩展管道（R1-R100）
│   └── demo_cli.py                       # CLI e2e demo
├── tests/                                # 43 个测试套件
├── evals/                                # ⭐ v3.0 三层行为评测
│   ├── eval_plans.py                    # TravelPlanner 风格 4 指标
│   ├── behavioral/
│   │   ├── run_l1.py / run_l2.py / run_l3.py
│   │   ├── anchor_cases.py              # L1: 5 anchor case
│   │   ├── L2_integration/              # L2: 5 模块 × 5 case
│   │   │   ├── weekday_cases.py / time_bucket_cases.py
│   │   │   ├── text_intake_cases.py / convergence_cases.py
│   │   │   └── memory_cases.py
│   │   └── L3_full/                     # L3: 100 case × 5 信号
│   │       ├── fixtures.py              # build_all_cases(100)
│   │       └── signal_checks.py         # check_s1 ~ check_s5
│   └── results/                          # JSON 归档：L{1,3}_<sha>_<ts>.json
├── promo/                                # ⭐ 路演物料（promo/README.md 详见）
│   ├── pitch-deck.html / .pdf           # 10 张横屏 + PDF 备份
│   ├── landing-page.html                # GitHub Pages 主页
│   ├── xhs-carousel.html / xhs-png/     # 小红书图文 9 张
│   ├── one-pager.html / .pdf            # A4 评委简介
│   ├── readme-hero.html / hero-png/     # GitHub README banner
│   ├── architecture.md                   # mermaid 架构源
│   └── README.md
├── docs/
│   ├── task.md                           # 命题原文
│   ├── DESIGN.md                         # 架构 + 异常处理
│   ├── EVAL_FRAMEWORK.md                # ⭐ 评测体系
│   ├── DATA_INVENTORY.md                # 数据资产盘点
│   ├── DEMO_SCRIPT.md                    # 路演脚本
│   ├── QA_PREP.md                        # 评委 Q&A
│   ├── ROADMAP.md                        # ⭐ v3.1 → v4.0 路线
│   ├── USER_RESEARCH_FINDINGS.md        # 100 AI 访谈 5 信号
│   ├── 100-improvements.md              # 6 路调研 100 条改进
│   ├── eval-100-results.md / .html      # 评测对比
│   ├── MOCK_API_README.md               # mock 接口生产路径
│   └── archive/V2.4_ITERATION_PLAN.md   # ⭐ 归档 10 轮收敛
├── requirements.txt
├── bj_pal.db                             # gitignored，运行时构建
└── tool_calls.db                         # gitignored，trace 日志
```

## 跑通

```bash
# 1. 装依赖
pip3 install --user -r requirements.txt

# 2. 建数据索引（首次 ~0.5s，自动加载 expanded_v2.jsonl）
python3 src/loader.py

# 3. L1 anchor evals（每 commit 都该跑，~30s）
python3 evals/behavioral/run_l1.py

# 4. CLI e2e demo（mock LLM，离线可跑）
python3 src/demo_cli.py --book --with-cake

# 5. Web UI
python3 -m streamlit run src/ui/app.py

# 6. 切真实 LongCat
BJ_PAL_LLM=longcat python3 src/demo_cli.py --book --with-cake
BJ_PAL_LLM=longcat python3 -m streamlit run src/ui/app.py

# 7. L2 集成 evals（5 模块 × 5 case，~5min）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l2.py

# 8. L3 全量 evals（100 case × 5 信号 = 280 检查，~30min）
BJ_PAL_LLM=longcat python3 evals/behavioral/run_l3.py
```

## 进度（v1 → v3.1 + promo）

### v1 基础（已完成）
- [x] **W1** loader / amap_search / ugc_signals / Planner / rank_fuse / availability_probe / Replanner
- [x] **W2** demo_cli / mock_book / mock_message / Tool Call Log / Streamlit UI / 偏好镜子 / 文档

### v2.1 改善 11 项（已完成）
- [x] 真实路由衔接 / 群发投票场景 / mock 真实感升级 / 4 类 reroute 触发 / UI 品牌升级 / UGC 截图 + vision 抽取 / 多模态路由对比 / AddOn Agent / 4 人偏好调和 / vs 朴素 GPT 对照 / 剧场化开场 / reasons 雷达图

### v2.2 数据扩展（已完成）
- [x] **Task 1.1** UGC 37 → 1,102 条 / 8 → 103 片区（5 类来源透明：manual_v1 + Class A 公开评论 + Class B amap 推理 + Class C 场景 + Round 5 跨片区主题）
- [x] **Task 1.2** 时段画像 — 1102 条 100% 填 `weekend_afternoon_intensity ∈ [0,1]`
- [x] **Task 1.3** 动态 trap 评分 — `compute_dynamic_trap_score`：amap 评分 ≥ 4.7 + UGC negative 交叉触发，**不再硬编码**
- [x] **Task 1.4** routes 52 → 1,892 条（150 核心 POI × 5 nearest）

### v2.4 D3+D5+D1 三件套（已完成 5/25）⭐
- [x] **D3** 分层行为评测 — L1 anchor 5 case + L2 集成 5 模块（weekday / time_bucket / text_intake / convergence / memory）+ 5 强信号通过率基线 ≥ 60%
- [x] **D5** 群偏好收敛器 — `agents/group_convergence.py` + `group_dynamics.py`：4 成员模式（反复横跳 / 沉默 / 隐性领导 / 正常）+ broadcast 主路径编排
- [x] **D1** 履约 trace 内核 — `agents/plan_tracer.py` + `ui/trust_panel.py`：plan trace 完整覆盖 100% / 置信度 ECE ≤ 0.15
- [x] **S4** detect_weekday_context — L1 evals 推到 100%
- [x] 10 轮收敛日志归档于 `docs/archive/V2.4_ITERATION_PLAN.md`

### v2.5 多模态首屏（已完成）⭐
- [x] `agents/text_intake.py` — 自然语言意图抽取 + 槽位补全
- [x] `multimodal_intake` UI — 截图 / 语音 fallback 降级到 text

### v2.6 时段画像扩展（已完成）⭐
- [x] 4 个 `time_bucket`：工作日早 / 工作日晚 / 周末上午 / 周末下午
- [x] 不动数据层，纯派生信号；ranking 公式按 bucket 加权

### v2.7 stateful agent（已完成）⭐
- [x] `agents/user_memory.py` — 跨 session 偏好记忆（record / get / forget / infer / merge_into_prompt）
- [x] persona summary 压缩 + 隐私 toggle

### v2.8 D7 可视化升级（已完成）⭐
- [x] 路线可惜度（opportunity_cost）— 3 条候选路线对比 + 多目标 Pareto
- [x] 群体共识进度 bar — sub-ranker 收敛动画

### v3.0 L2/L3 全量评测 + 算法跃迁（已完成）⭐
- [x] **L2 集成评测** — 5 模块 × 5 case = 25 行为基线
- [x] **L3 全量评测** — 100 case × 5 信号（S1-S5）= 280 检查（**全过 100%**）
- [x] **数据层升级** — UGC 1102 → **8666 条**（5/21 全天 95 轮 R6-R100 共扩 +2366）；派生信号网覆盖 4417 → **5198 POI**
- [x] **算法升级** — Tree of Thoughts (`planner_tot.py`) / OPTW + OR-Tools (`optw_solver.py`) / Kemeny + Borda 群投票 (`voting.py`)
- [x] **数据深度** — 古建预约 / 老字号识别 / 季节限定 / facilities / weather_shelter / 节假日预测 / poi_graph / audience_segment / parking 9 个北京特色信号

### v3.1 D7 校准时序（已完成）⭐
- [x] `agents/calibration_history.py` — 滑窗 ECE 演化 + 置信度分布直方图
- [x] `plan_count_by_day` 统计 + UI 校准面板

### promo 物料（已完成 5/26）⭐
- [x] `pitch-deck.html` 10 张横屏 + PDF 备份（路演主屏）
- [x] `landing-page.html`（GitHub Pages 部署）
- [x] `xhs-carousel.html` 9 张图文 + `xhs-png/` 截图（小红书引流）
- [x] `one-pager.html` A4 + PDF（评委桌摆）
- [x] `readme-hero.html` + `hero-png/01.png`（GitHub banner）
- [x] `architecture.md` mermaid 源
- [x] 详见 `promo/README.md`

剩余只有用户实操项：录屏 + 路演排练 + GitHub Pages 部署（见 `docs/DEMO_SCRIPT.md`）。

### 测试（21+ 套件全过）
- v1: smoke / tools / planner / ranking / reroute / preference_mirror
- v2: route_lookup / v2_mock_reroute_addon / v2_broadcast / v2_vision / v2_group_harmony
- v2.2: data_coverage（4 章节 16 断言）
- v3.0: voting (11) / optw_solver (7) / planner_tot (5) / ugc_bm25 (9) / wait_predictor (15) / reservation (9) / heritage_brand (13) / seasonal (11) / facilities (11) / weather_shelter (14) / crowd_forecast (12) / poi_graph (10) / audience_segment (16) / parking (17) — 共 + 160 测试

## 8 个差异化触点（评委 5 分钟必看）

| # | 触点 | 来源 | 护城河 |
|---|---|---|---|
| 1 | UGC 软信号融合 ranking + reasons + 5 类来源透明 | v1 + v2.2 + v3.0 | 🥇 数据稀缺（8666 条 / 5198 POI 信号网） |
| 2 | 主动 reroute + 4 类触发因子 + 动态 trap | v1 + v2 改4 + v2.2 Task 1.3 | 🥇 命题字面（amap 评分 + UGC 交叉触发） |
| 3 | 群发投票（Kemeny + Borda + Pareto）+ 1 否决重 reroute | v2 改2 + v3.0 voting.py | 🥇 命题字面 / 社会选择理论首用 |
| 4 | 时段画像 4 bucket + weekend_afternoon_intensity | v2.2 Task 1.2 + v2.6 | 🥇 命题字面（"周六下午"画像有真证据） |
| 5 | ToT / OPTW / 普通三分支 Planner 选择 | v3.0 planner_tot + optw_solver | 🥇 算法跃迁（arxiv:2305.10601 + Vansteenwegen 2011） |
| 6 | L1/L2/L3 三层评测 + 5 信号 + ECE 校准 | v2.4 D3 + v3.0 + v3.1 | 🥇 评测体系完整（参考 video-eval-agent 防火墙） |
| 7 | 偏好镜子（反问澄清）+ text_intake 多模态首屏 | v1 + v2.5 | 🥈 agent-native |
| 8 | stateful 跨 session 记忆 + AddOn 主动建议 | v2.7 + v2 改7 | 🥉 加分项 |

## 技术决策（已锁定）

- **LLM**：LongCat（Anthropic 兼容协议 + Bearer 认证，复用 activity-planner 接入方式）；多模型 fallback 见 `agents/llm_robust.py`
- **语言**：Python 3.9+
- **UI**：Streamlit + folium
- **数据**：SQLite 三表（pois / routes / ugc_aspects）+ jsonl 持久化（`expanded_v2.jsonl` 进 git）+ JSON 配置（heritage_brands / reservations / holiday_calendar）
- **Agent 模式**：Plan-and-Execute（不是 ReAct，可视化强）+ ToT 分支（v3.0）+ OPTW 全局最优（v3.0）
- **评测**：分层 evals（每 commit / 每周 / 每 release）+ TravelPlanner 4 指标 + 5 行为信号

## 数据画像（v3.0 升级版）

| 资产 | 量级 | 关键能力 |
|---|---|---|
| amap POI | **5,653 条** | 评分 / 价格 / 坐标 / 营业时间 / photos |
| routes | **1,892 条** | 步行 / 骑行 / 驾车 / 公交 4 模式覆盖 150 核心 POI |
| **UGC aspects** | **8,666 条 / 6300+ POI 信号网** | 9 aspect_type + sentiment + confidence + weekend_afternoon_intensity + 4 time_bucket |
| heritage_brands | 20 老字号 | 真假分店识别 + 旗舰加权 |
| heritage_reservations | 30+ 限流景点 | 配额 / 开放时段 / 不可入园后换场 |
| holiday_calendar | 7 法定节假日 | tier 分级 + 人流 multiplier |
| UGC raw（仅本地） | 89 张点评截图 | 隐私已脱敏，不入 git |

主 demo 片区：**五道营-雍和宫**（19 条最厚）；其他高密度：王府井-东单 / 奥林匹克 / 安定门-雍和宫 / 三里屯。

## 评测体系一览（详见 `docs/EVAL_FRAMEWORK.md`）

| 层 | 名称 | 频率 | 规模 | 用途 |
|---|---|---|---|---|
| L1 | anchor | 每 commit | 5 case ~30s | 核心信号冒烟（红旗 / 否决 reroute / 重要场合工具 / 周末聚焦 / 道歉容忍） |
| L2 | integration | 每周 | 5 模块 × 5 case ~5min | weekday / time_bucket / text_intake / convergence / memory 行为基线 |
| L3 | full | 每 release | 100 case × 5 信号 ~30min | TravelPlanner 4 指标（delivery / commonsense / hard_constraint / final_pass）+ 5 行为信号 |

5 信号检查（S1-S5）：delivery / commonsense / hard_constraint / utility / rationale_quality

结果归档：`evals/results/L{1,3}_<git_sha>_<timestamp>.json`

## ETL pipeline（v3.0 全量数据扩展，可重跑）

```bash
# v2.2 5 类 UGC 扩展（共 700+ 条）
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas.py          # round 1
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round2.py
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round3.py
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round4.py
BJ_PAL_LLM=longcat python3 src/etl/expand_themes_round5.py
BJ_PAL_LLM=longcat python3 src/etl/expand_scenarios.py          # Class C
BJ_PAL_LLM=longcat python3 src/etl/batch_amap_inference.py --offset 0 --areas 25  # Class B
BJ_PAL_LLM=longcat python3 src/etl/batch_amap_inference.py --offset 25 --areas 20

# v3.0 R6-R100 全天扩展（5/21，总耗时 3h35min，+2366 条）
# Round 6-100 共 95 轮，详见 docs/100-improvements.md

# 时段画像（纯规则，离线可跑）
python3 src/etl/add_time_bucket_intensity.py

# routes 估算（150 POI × 5 nearest = 1840 条）
python3 src/etl/populate_estimated_routes.py --seed-limit 150 --k-nearest 5 --strategy mixed --max-km 12

# 持久化 dump（防止 db rebuild 丢数据）
python3 src/etl/dump_ugc.py
python3 src/etl/dump_routes.py
```

clone 后跑通：`python3 src/loader.py` 即可（loader 自动加载 `expanded_v2.jsonl`，无需重跑 LLM ETL）。

## promo 物料速查

| 场景 | 物料 | 用法 |
|---|---|---|
| 决赛主屏 | `promo/pitch-deck.html` | Chrome 全屏（F11），方向键翻页 |
| 现场设备故障 | `promo/pitch-deck.pdf` | U 盘备份 |
| 桌摆评委 | `promo/one-pager.pdf` | 打印 A4，桌上一份 |
| GitHub README | `promo/hero-png/01.png` | commit 到 `assets/hero.png` |
| 引流期 | `promo/xhs-png/` 9 张 | 小红书图文笔记 |
| 答辩补料 | `promo/architecture.md` | 手机渲染 mermaid 图 |

详见 `promo/README.md`。
