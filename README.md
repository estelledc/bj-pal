# BJ-Pal · 周六下午的决策解药——一句话替你扛下选错的责任

> 美团黑客松 2026 短时活动规划 Agent 实现
>
> 命题：`docs/task.md`
> 设计：`docs/DESIGN.md`（≤ 2 页）
> 用户研究：`docs/USER_RESEARCH_FINDINGS.md`（100 条 AI 访谈，5 强信号）
> 改进计划：`docs/IMPROVEMENT_PLAN.md`（P0 + P1 落地清单）
> 调研：`../../ideas/meituan-trip-planner-ideas.md` + `meituan-trip-planner-plan.md`

## 元定位（来自 100 条 AI 用户访谈）

> **用户付钱不是为"规划方案"，是为"AI 替我扛了选错的责任"。**
> 这是一个**情绪基础设施**，不是工具产品。

100 条访谈得到的 5 强信号：
1. "选错的责任"才是群决策真正的痛（4/5）
2. 必须看到吐槽，不只是分数（5/5）— 已落 P0.1 red_flags 面板
3. 选错容忍度 = 2 次（5/5）— 已落 P0.5 错误自承认 apology
4. 工作日不属于这个 App（4/5）— 主台词聚焦周六下午
5. 重要场合 = 工具不是代理（5/5）— 已落 P0.2 筛选模式

## 架构一图

```
用户一句话 + 偏好
    ↓
[偏好镜子] ← 反问澄清（"减脂是低糖还是低油?"）
    ↓
[Planner LLM] ← amap_search + ugc_signals + rank_fuse 喂候选
    ↓ Plan v1（5-7 步 JSON）
[AvailabilityProbe] ← trap POI / UGC risk_tags / 高峰期启发式
    ↓ 触发风险
[Replanner] ← 同片区同类 ranking top1 替换 failed step
    ↓ Plan v2 + RerouteEvent
[mock_book] ← 餐厅预订 + 蛋糕配送
    ↓
[mock_message] ← 话术化 IM 卡片 → 微信发送（mock）
    ↓
[Tool Call Log] ← 全程 SQLite 留痕，UI Trace 侧栏可展开
```

## 项目结构

```
bj-pal/
├── data/                              # gitignored, 86M
│   ├── amap/{merged,routes,...}       # 5,656 POI + 52 路线
│   ├── ugc/                            # 89 张大众点评截图
│   └── manual_ugc_seed.jsonl          # 37 条 UGC aspect 切片
├── src/
│   ├── loader.py                      # SQLite 索引 + query_*
│   ├── tools/
│   │   ├── types.py                   # POI / Aspect / Reason / RankedPOI
│   │   ├── amap_search.py             # 片区半径 + 类目 + 营业时间过滤
│   │   ├── ugc_signals.py             # aspect 聚合 + risk + scenario_fit
│   │   ├── rank_fuse.py               # L1 硬过滤 + L2 加权 + reasons
│   │   ├── availability_probe.py      # 余位探针（trap POI + UGC 触发）
│   │   ├── mock_book.py               # 餐厅预订 + 蛋糕配送
│   │   ├── mock_message.py            # IM 话术化卡片
│   │   └── tool_call_log.py           # SQLite 调用日志
│   ├── agents/
│   │   ├── llm_client.py              # mock / longcat / anthropic 抽象
│   │   ├── types.py                   # Plan / Step / UserPreferences
│   │   ├── planner.py                 # Plan-and-Execute Planner
│   │   ├── replanner.py               # 局部 reroute
│   │   └── preference_mirror.py       # 反问澄清
│   ├── ui/
│   │   ├── app.py                     # Streamlit 主入口
│   │   ├── timeline.py                # 时间轴组件
│   │   └── map_view.py                # folium 地图
│   └── demo_cli.py                    # CLI e2e demo
├── tests/
│   ├── smoke_test.py                  # W1 D1：data loading
│   ├── test_tools.py                  # W1 D2：amap + ugc
│   ├── test_planner.py                # W1 D3：Plan-and-Execute
│   ├── test_ranking.py                # W1 D4：fuse_and_rank
│   ├── test_reroute.py                # W1 D5-D6：probe + replan
│   └── test_preference_mirror.py      # W2 D4：偏好镜子
├── docs/
│   ├── task.md                        # 命题原文
│   ├── DESIGN.md                      # 设计文档（≤ 2 页）
│   ├── MOCK_API_README.md             # mock 接口生产对接路径
│   ├── DEMO_SCRIPT.md                 # 90s pitch + 5min 现场 demo
│   └── QA_PREP.md                     # 评委 Q&A 15 题
├── requirements.txt
├── bj_pal.db                          # gitignored，运行时构建
└── tool_calls.db                      # gitignored，trace 日志
```

## 跑通

```bash
# 1. 装依赖
pip3 install --user -r requirements.txt

# 2. 建数据索引（首次 ~0.5s）
python3 src/loader.py

# 3. 跑全套测试
for t in tests/smoke_test.py tests/test_tools.py tests/test_planner.py \
         tests/test_ranking.py tests/test_reroute.py tests/test_preference_mirror.py; do
    python3 "$t" || break
done

# 4. CLI e2e demo（mock LLM，离线可跑）
python3 src/demo_cli.py --book --with-cake

# 5. Web UI
python3 -m streamlit run src/ui/app.py

# 6. 切真实 LongCat
BJ_PAL_LLM=longcat python3 src/demo_cli.py --book --with-cake
BJ_PAL_LLM=longcat python3 -m streamlit run src/ui/app.py
```

## 进度（v1 14 天 + v2.1 改善 11 项 + v2.2 数据扩展全部完成）

### v1 基础（已完成）
- [x] **W1** loader / amap_search / ugc_signals / Planner / rank_fuse / availability_probe / Replanner
- [x] **W2** demo_cli / mock_book / mock_message / Tool Call Log / Streamlit UI / 偏好镜子 / 文档

### v2.1 改善（已完成）
- [x] **改 1** 真实路由时间衔接 — `tools/route_lookup.py` + Plan schema 加 travel_time_min
- [x] **改 2** 群发投票场景 — `mock_message.broadcast` + 4 头像状态 + 1 否决重 reroute
- [x] **改 3** mock 真实感升级 — 菜单 / 座位号 / 等位数 / 真延迟（300-1200ms）/ 真照片 URL
- [x] **改 4** 多种 reroute — queue / weather / closed / user_dissent 4 类触发因子
- [x] **改 5** UI 品牌升级 — 宫墙红 + 米白主题 / 卡片时间轴 / emoji marker / 自定义字体
- [x] **改 6A** UGC 截图上传 + vision 抽取 — `agents/vision_extractor.py` + LongCat vision API
- [x] **改 6B** 多模态路由对比 — 4 模式 emoji 紧凑展示 + agent 推荐
- [x] **改 7** AddOn Agent — guided_tour / umbrella / water_bottle / snack_break / merch / early_pickup
- [x] **改 8** 朋友 4 人偏好调和 — `agents/group_harmony.py` 4 sub-ranker + min/avg pareto
- [x] **改 9** vs 朴素 GPT 对照 — split view toggle
- [x] **改 10** 剧场化开场 — 微信对话 hero 区 + CSS 动画
- [x] **改 11** reasons 雷达图 — 5 维 SVG spider chart

### v2.2 数据扩展（已完成）⭐
- [x] **Task 1.1** UGC **37 → 1,102 条 / 8 → 103 片区**（5 类来源透明区分）
  - manual_v1（截图抽取）37 + Class A 公开评论汇总 479 + Class B amap 属性推理 333 + Class C 场景主题 137 + Round 5 跨片区主题 116
- [x] **Task 1.2** 时段画像 — `weekend_afternoon_intensity` 列 100% 覆盖（HIGH 215 / MID 764 / LOW 123），ranking 公式按 intensity 加权
- [x] **Task 1.3** 动态 trap 评分 — `compute_dynamic_trap_score`：amap 评分 ≥ 4.7 + UGC negative crowd / queue 交叉触发，**不再硬编码**
- [x] **Task 1.4** routes **52 → 1,892 条**（1,840 estimated_v2 + 52 amap cache，覆盖 150 核心 POI × 5 nearest）
- [x] **持久化** — `expanded_v2.jsonl` 进 git，loader 多源加载 + intensity 自动 fallback；`bj-pal-data-roadmap.md` 路线图 + `bj-pal-amap-heat-research.md` 高德接入调研

### 测试（12 套件全过）
- v1: smoke_test / test_tools / test_planner / test_ranking / test_reroute / test_preference_mirror
- v2: test_route_lookup / test_v2_mock_reroute_addon / test_v2_broadcast / test_v2_vision / test_v2_group_harmony
- v2.2: **test_data_coverage**（4 章节 16 断言：UGC ≥ 1000 / intensity 100% / 动态 trap / routes ≥ 1000）

剩余只有用户实操的：录屏 + 路演排练 + 部署云端兜底（见 `docs/DEMO_SCRIPT.md`）

## 7 个差异化触点（评委 5 分钟必看）

| # | 触点 | 来源 | 护城河 |
|---|---|---|---|
| 1 | UGC 软信号融合 ranking + reasons | v1 + v2.2 数据扩展 | 🥇 数据稀缺（103 片区 1102 条 5 类来源） |
| 2 | 主动 reroute + 4 类触发因子 + 动态 trap | v1 + v2 改4 + v2.2 Task 1.3 | 🥇 命题字面（amap 评分 + UGC 交叉触发） |
| 3 | 群发投票 + 1 否决重 reroute | v2 改2 | 🥇 命题字面 / 无人做 |
| 4 | 时段画像 weekend_afternoon_intensity | v2.2 Task 1.2 | 🥇 命题字面（"周六下午"画像有真证据） |
| 5 | 偏好镜子（反问澄清） | v1 | 🥈 agent-native |
| 6 | UGC 截图上传 + vision 抽取 | v2 改6A | 🥈 数据扩展通用链路 |
| 7 | AddOn 主动建议 | v2 改7 | 🥉 加分项 |

## 技术决策（已锁定）

- **LLM**：LongCat（Anthropic 兼容协议 + Bearer 认证，复用 activity-planner 接入方式）
- **语言**：Python 3.9+
- **UI**：Streamlit + folium
- **数据**：SQLite（POI + UGC + 路线 + tool_calls log 四表）+ jsonl 持久化（`expanded_v2.jsonl` 进 git）
- **Agent 模式**：Plan-and-Execute（不是 ReAct，可视化强）

## 数据画像（v2.2 升级版）

| 资产 | 量级 | 关键能力 |
|---|---|---|
| amap POI | 5,656 条（1,435 餐饮） | 评分/价格/坐标/营业时间/photos |
| **routes** | **1,892 条**（52 amap cache + 1,840 estimated_v2） | 步行/骑行/驾车/公交 4 模式覆盖 150 核心 POI |
| **UGC aspects** | **1,102 条 / 103 片区** | 9 aspect_type + sentiment + confidence + **weekend_afternoon_intensity** |
| UGC dataset_version | 5 类透明区分 | manual_v1(37) + Class A(479) + Class B(333) + Class C(137) + Round 5(116) |
| UGC raw（仅本地） | 89 张点评截图 | 隐私已脱敏，不入 git |

主 demo 片区：**五道营-雍和宫**（19 条最厚）；其他高密度：王府井-东单 20 / 奥林匹克 28 / 安定门-雍和宫 16 / 三里屯 14。

## ETL pipeline（v2.2 数据扩展可重跑）

```bash
# UGC 5 轮扩展（共 700+ 条）
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas.py          # round 1: 99 条 / 5 片区
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round2.py   # round 2: 184 条 / 15 片区
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round3.py   # round 3: 110 条 / 10 北京特色片区
BJ_PAL_LLM=longcat python3 src/etl/expand_ugc_areas_round4.py   # round 4: 122 条 / 12 公园+文化机构
BJ_PAL_LLM=longcat python3 src/etl/expand_themes_round5.py      # round 5: 116 条 / 11 跨片区主题
BJ_PAL_LLM=longcat python3 src/etl/expand_scenarios.py          # Class C: 137 条 / 12 场景主题

# Class B amap 属性推理（333 条）
BJ_PAL_LLM=longcat python3 src/etl/batch_amap_inference.py --offset 0 --areas 25
BJ_PAL_LLM=longcat python3 src/etl/batch_amap_inference.py --offset 25 --areas 20

# 时段画像（纯规则，离线可跑）
python3 src/etl/add_time_bucket_intensity.py

# routes 估算（150 POI × 5 nearest = 1840 条）
python3 src/etl/populate_estimated_routes.py --seed-limit 150 --k-nearest 5 --strategy mixed --max-km 12

# 持久化 dump（防止 db rebuild 丢数据）
python3 src/etl/dump_ugc.py
python3 src/etl/dump_routes.py
```

clone 后跑通：`python3 src/loader.py` 即可（loader 自动加载 `expanded_v2.jsonl`，无需重跑 LLM ETL）。
