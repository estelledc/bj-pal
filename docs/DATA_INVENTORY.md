# BJ-Pal · 数据资产全盘点

> 截至 2026-05-29（v3.1 + promo 完成后状态）。每次 v3.x 迭代后更新。
> 全盘代码：`source .venv/bin/activate && python3 src/etl/dump_and_verify_after_etl.py`
> 配套：`README.md`（数据画像速览段）/ `EVAL_FRAMEWORK.md`（评测数据归档约定）

## 一、SQLite 主表（运行时）

### 1.1 `pois` — amap POI 主表（**5,653 条**）

| 维度 | 数量 |
|---|---|
| 风景名胜 | 3,506 |
| 餐饮服务 | 1,435 |
| 购物服务 | 605 |
| 体育休闲 / 住宿 / 文化教育 / 政府 / 公司 / 商务住宅 / 地名 / 金融 等 | 107 |
| **有经纬度** | **5,653 (100%)** |
| **有评分** | **5,638 (99.7%)** |

字段 17 个：`id / name / category_lv1/2/3 / typecode / district / business_area / address / longitude / latitude / rating / avg_price / open_time / phone / photos_json / raw_json`

数据来源：高德开放平台 keyword search + place search（2026-05-03 抓取），覆盖北京六环内主要 POI。

### 1.2 `ugc_aspects` — UGC 软信号（**8,666 条**）

| 维度 | 数量 |
|---|---|
| **总条数** | **8,666** |
| sentiment=positive | 4,928 (56.9%) |
| sentiment=negative | 1,001 (11.5%) |
| sentiment=mixed | 2,728 (31.5%) |
| weekend_afternoon_intensity ≥ 0.7（强相关）| 1,297 (15.0%) |

aspect_type 分布（top 9）：

| aspect_type | 数量 | 含义 |
|---|---|---|
| environment | 3,575 | 环境氛围（嘈杂 / 安静 / 文艺 / 现代等） |
| scenario_fit | 1,689 | 场景适配（带娃 / 朋友局 / 情侣等） |
| comfort | 805 | 舒适度（拥挤 / 步行 / 通风等） |
| transport | 562 | 交通可达性（地铁 / 步行 / 停车） |
| food | 553 | 餐饮口味（辣 / 清淡 / 价格） |
| crowd | 475 | 人流密度（高峰 / 错峰） |
| budget | 397 | 预算适配 |
| booking_risk | 315 | 预约 / 排队风险 |
| queue | 295 | 等位时长 |

### 1.3 `routes` — 多模式路径（**1,892 条**）

| mode | 数量 |
|---|---|
| walking | 473 |
| bicycling | 473 |
| driving | 473 |
| transit | 473 |

覆盖 150 核心 POI × 5 nearest neighbors，构成完整 4 模式通勤矩阵。

字段：`origin_id / dest_id / mode / duration_min / distance_m / cost_yuan / steps_json`

### 1.4 `tool_calls.db` — 工具调用日志（**892 条**）

每次 tool 调用通过 `tool_call_log.timed_call(...)` 上下文管理器记录，含 `session_id / tool_name / params / response / latency_ms / status / error / timestamp`。

### 1.5 `bj_pal.db` 派生表（v2.4 + v2.7 + v3.1 新增）

| 表名 | 来源 | 字段 | 用途 |
|---|---|---|---|
| `plan_traces` | v2.4 D1 `plan_tracer.py` | `plan_id / step_id / decision / confidence / fallback_action / created_at` | 履约 trace 内核；UI trust_panel 展开；evals/L1 S1 检查 `coverage_rate(plan_id) == 1.0` |
| `user_memory` | v2.7 `user_memory.py` | `user_id / facet / value / confidence / updated_at` | stateful 跨 session 偏好；record/get/forget/infer/merge |
| `calibration_history` | v3.1 `calibration_history.py` | `window_id / ece / plan_count / computed_at` | 滑窗 ECE 演化（每 N plan 算一次） |
| `confidence_buckets` | v3.1 `calibration_history.py` | `window_id / bucket_idx / predicted_mid / actual_pass_rate / n` | 置信度直方图（10 桶 0.0-1.0） |
| `prediction_log` | v2.4 + v3.1 | `pred_id / poi_id / predicted_value / actual_value / created_at` | record_prediction / record_actual，喂 ECE 计算 |
| `group_member_profile` | v2.4 D5 | `group_id / member_id / role / weight / member_mode` | 4 成员模式（反复横跳 / 沉默 / 隐性领导 / 正常）+ broadcast 编排 |

---

## 二、静态规则 JSON（业务知识）

### 2.1 `data/heritage_brands.json` — 老字号品牌库（**20 个**, 7.2 KB）

| 类型 | 品牌 |
|---|---|
| 烤鸭 | 全聚德（1864）、便宜坊（1416） |
| 涮羊肉 | 东来顺（1903）、聚宝源（1992） |
| 炙子烤肉 | 烤肉季（1848）、烤肉宛（1686） |
| 京味小吃 | 护国寺小吃（1956）、白魁老号（1780） |
| 爆肚 | 爆肚冯（1881）、爆肚金（1885） |
| 卤煮 / 炒肝 | 北新桥卤煮老店、天兴居（1862） |
| 鲁菜 / 淮扬菜 | 丰泽园（1930）、玉华台（1921） |
| 茶庄 | 吴裕泰（1887）、张一元（1900） |
| 酱菜 | 六必居（1530） |
| 中药 | 同仁堂（1669）、鹤年堂（1405） |
| 糕点 | 稻香村（1895） |

每个品牌带 `flagship_keywords / flagship_locations / branch_min_acceptable_rating / notes / warning`，用于 [08] 真假分店识别。

### 2.2 `data/heritage_reservations.json` — 古建预约规则（**32 个**, 11.6 KB）

覆盖北京 32 个限流景点（含 29 个需预约 + 3 个免预约对照组）：
- 故宫博物院（7 天前 20:00 释票，30 秒售罄）
- 国家博物馆（7 天前 17:00，周一闭馆）
- 颐和园 / 天坛 / 雍和宫 / 八达岭 / 慕田峪 / 鸟巢 / 水立方 / 圆明园 / 北海 / 景山 / 国子监 / 潭柘寺 / 戒台寺 / 白塔寺 / 智化寺 / 鲁迅博物馆 / 宋庆龄故居 / 中山公园 / 太庙 / 玉渊潭 / 中国美术馆 / 中国电影博物馆 / 首都博物馆 / 军博 / 科技馆 / 毛主席纪念堂 / 天安门城楼 / 恭王府

每个含 `release_lead_days / release_time / sessions / weekly_close_day / release_url / notes`，用于 [10] 古建预约规则集成。

### 2.3 `data/holiday_calendar_2026.json` — 节假日日历（**7 个**, 1.8 KB）

| 节假日 | tier | 日期 |
|---|---|---|
| 元旦 | tier_2_high | 2026-01-01 → 01-03 |
| 春节 | tier_1_extreme | 2026-02-15 → 02-21 |
| 清明节 | tier_2_high | 2026-04-04 → 04-06 |
| 劳动节 | tier_1_extreme | 2026-05-01 → 05-05 |
| 端午节 | tier_2_high | 2026-06-19 → 06-21 |
| 中秋节 | tier_2_high | 2026-09-25 → 09-27 |
| 国庆节 | tier_1_extreme | 2026-10-01 → 10-08 |

附加：20 个 `famous_outdoor_pois_extreme_crowd_on_holiday` 列表 + 周一-周日 weekday baseline 系数。用于 [42] 节假日人流预测。

### 2.4 `data/area_centers_inferred.json` — 片区中心点（**323 个**, 38.7 KB）

从 amap POI 关键词聚合反推的 area_anchor 中心 (lng, lat)。覆盖 80%+ UGC area_anchor 的解析。补充 7 个硬编码 AREA_CENTERS（五道营 / 奥森 / 王府井等）。

---

## 三、派生信号（启动时从 SQLite 索引）

| 工具 | 索引规模 | 输出 | 改进编号 |
|---|---|---|---|
| `tools/ugc_bm25.py` | 8,666 文档（jieba 分词倒排）| query → top-k UGC | [28] |
| `tools/wait_predictor.py` | 490 POI（含等位分钟数）| poi → expected_min / p50 / p90 / confidence | [39] |
| `tools/facilities.py` | 5,198 POI | 5 维 facility flag (toilet/baby/wheelchair/charging/parking) | [01] |
| `tools/seasonal.py` | 5,198 POI | 4 季 + 节庆 peak/avoid 标签 | [05] |
| `tools/audience_segment.py` | 5,198 POI | (local_count, tourist_count, expert_count) | [20] |
| `tools/heritage_brand.py` | 20 品牌 | identify_brand → BrandInfo (is_flagship + branch_quality) | [08] |
| `tools/reservation.py` | 32 古建 | check_feasibility → 是否能约 | [10] |
| `tools/crowd_forecast.py` | 7 节假日 + 20 famous POI | crowd_multiplier (×0.4-×5.25) | [42] |
| `tools/weather_shelter.py` | 5,653 POI（启发式分类）| 4 档遮蔽（full_indoor/covered/subway/open）| [16] |
| `tools/parking.py` | 5,653 POI（启发式 capacity + 占用率）| (available, wait_min, fee, difficulty) | [03] |
| `tools/poi_graph.py` | 5,000 节点 + 97k 边（co_mention/same_area/geo + PageRank）| find_neighbors / find_complementary | [22] |

---

## 四、评估与实验数据

### 4.1 `data/longcat_demo_v2_results.json` — 40 场景 LongCat 跑测（289 KB）

40 个真实 query × LongCat 完整 plan-and-probe 结果，含 v1 plan + reroute events + v2 plan + IM 卡片 + tool 调用日志。**38/40 成功** (95%) — Round 6+7 工程鲁棒性改进生效后的 baseline。

### 4.2 `data/eval_v1_baseline.json` (25.7 KB) + `data/eval_v2_baseline.json` (31.4 KB)

TravelPlanner 风格评估器（[83]）的两版 baseline：

| 指标 | v1（无 [73][75][15] 改进）| v2（改进后） |
|---|---|---|
| delivery_rate | 0.700 (28/40) | 0.975 (39/40) |
| commonsense_pass | 0.200 | 0.475 |
| hard_constraint_pass | 0.450 | 0.675 |
| **final_pass** | **0.125** | **0.275** |

### 4.3 `data/smoke_robust_results.json` — 4 场景修复验证

针对 [73] partial parse 修复的 smoke test，4/4 全过（含之前 JSON 截断的 S04 / S11 / S15 + 限流连续段头 S16）。

### 4.4 备份文件

- `data/ugc/aspects.jsonl.bak-r6` — Round 6 备份（6,338 行）
- `data/ugc/aspects.jsonl.bak-r16` — Round 16 备份（6,590 行）
- 当前 `data/ugc/aspects.jsonl` — **8,666 行（7.7 MB）**

---

## 五、amap 原始数据（**20 个 jsonl，63 MB**）

### 5.1 已合并（`data/amap/merged/`）

| 文件 | 大小 | 用途 |
|---|---|---|
| `amap_beijing_pois_with_food_merged_20260503.jsonl` | 18.2 MB | **主数据集**（含 1,435 餐饮）→ loader 用 |
| `amap_beijing_pois_merged_20260503.jsonl` | 13.1 MB | 不含餐饮的合并版（备份）|

### 5.2 分类抓取（`data/amap/pois/`）

| 主题 | 大小 | 备注 |
|---|---|---|
| business_area | 0.1 MB | 商圈 POI |
| core_business_areas | 1.6 MB | 核心商圈 |
| core_food | 4.5 MB | 核心餐饮 |
| core_landmarks | 0.3 MB | 核心地标 |
| core_museums | <0.1 MB | 核心博物馆 |
| core_sports_landmarks | <0.1 MB | 核心体育地标 |
| scenic | 3.4 MB | 风景名胜（基础）|
| scenic_deeper | 9.6 MB | 风景名胜（深抓）|

### 5.3 raw_pages（高德返回原始分页，**8 个 jsonl, 11.2 MB**）

留作审计 / 重抓兜底，非运行时使用。

### 5.4 路径数据（`data/amap/routes/`）

- `expanded_v2.jsonl` (1.1 MB) — 1,892 路线持久化（4 模式 × 473 leg）
- `amap_beijing_route_planning_ugc_eval_v3_20260503.jsonl` (0.3 MB) — UGC 评估专用路线

---

## 六、ETL 脚本（**19 个**）

### 6.1 数据扩展（**11 个，~6,000 行**）

| 阶段 | 文件 | 行数 |
|---|---|---|
| Round 1 | `expand_ugc_areas.py` | 265 |
| Round 2 | `expand_ugc_areas_round2.py` | 438 |
| Round 3 | `expand_ugc_areas_round3.py` | 305 |
| Round 4 | `expand_ugc_areas_round4.py` | 329 |
| Round 5 | `expand_themes_round5.py` | 379 |
| Round 5 续 | `expand_scenarios.py` | 288 |
| Round 6 | `expand_ugc_round6_facility_audience.py` | 176 |
| Round 7-16 | `expand_ugc_round7_to_16.py` | 816 |
| Round 17-36 | `expand_ugc_round17_to_36.py` | 1,130 |
| Round 37-56 | `expand_ugc_round37_to_56.py` | 660 |
| Round 57-76 | `expand_ugc_round57_to_76.py` | 250 |
| Round 77-100 | `expand_ugc_round77_to_100.py` | 251 |

### 6.2 维护工具

| 文件 | 用途 |
|---|---|
| `text_aspect_extractor.py` | LLM 抽 aspects 核心，所有 expand 都调它 |
| `add_time_bucket_intensity.py` | 给 UGC 加 weekend_afternoon_intensity（纯规则）|
| `populate_estimated_routes.py` | haversine + detour 估算 routes（无 amap API 时）|
| `batch_amap_inference.py` | 从 amap 客观属性推 UGC（Class B）|
| `dump_ugc.py` | SQLite → jsonl 持久化 |
| `dump_routes.py` | routes 表 → jsonl |
| `dump_and_verify_after_etl.py` | 全验证（计数 + 派生信号重建）|

---

## 七、Round 6-100 ETL 日志

| Batch | 子项 | 入库 | 耗时 |
|---|---|---|---|
| Round 6 | 6 | 38 | 3.3 min |
| Round 7-16 | 50 | 252 | 19.1 min |
| Round 17-36 | 100 | 494 | 38.7 min |
| Round 37-56 | 100 | 492 | 45.8 min |
| Round 57-76 | 100 | 496 | 51.5 min |
| Round 77-100 | 120 | 594 | 57.2 min |
| **R6-R100 合计** | **476** | **+2,366** | **3 小时 35 分** |

UGC 总条数演化：6,300 → 6,338 → 6,590 → 7,084 → 7,576 → 8,072 → **8,666**（+37.6%）

日志：`data/etl_round*.log`（实时输出 + 错误捕获）

---

## 八、6 大数据维度盘点

### 8.1 地理维度

- **5,653 amap POI**（六环内）+ 经纬度 100% 覆盖
- **323 area_anchor 中心点**（推断 + 7 个手工 anchor）
- **1,892 routes**（4 模式 × 473 leg）+ POI 图 5,000 节点 / 97k 边

### 8.2 内容维度

- **8,666 UGC aspects**，9 类 aspect_type
- **20 老字号** + **32 古建预约规则** + **7 法定节假日**
- **5,198 POI 派生信号覆盖** facility / audience / seasonal 三维

### 8.3 时间维度

- **weekend_afternoon_intensity** ∈ [0,1] 100% 覆盖
- **24 节气 / 12 月份** 主题 raw_text（R87-R96）
- **10 个细分时段画像**（早 5-7 / 7-9 / 9-11 / 11-13 / 13-15 / 14-17 / 17-19 / 19-21 / 21-23 / 23-1 / 凌晨 3-5）

### 8.4 场景维度

- **50 用户画像**（学生 / 单身 / 已婚 / 中产 / 退休 / 长辈 / 商务 / 初游 / 复游 / 外宾 各 5 变体）
- **5 行业 vertical**（医生 / 教师 / 程序员 / 金融 / 体力 各 5 场景）
- **8 风险场景**（带婴儿 / 老人 / 朋友 / 雨天 / 雪后 / 雾霾 / 春节 / 国庆）

### 8.5 价格维度

- **3 价位分层**（¥0-50 穷游 / ¥150-300 家庭 / ¥500+ 高端）
- POI `avg_price` 字段覆盖率
- 老字号 `branch_min_acceptable_rating` 防分店踩雷

### 8.6 评估维度

- **40 场景 LongCat 真实结果** + 4 项指标 baseline
- 4 套件 evaluator（commonsense 6 项 + hard 4 项）
- Round 6-100 全量 ETL 日志可重放

---

## 九、可复现的全套构建命令

```bash
# 1. 装依赖
source .venv/bin/activate
uv pip install -r requirements.txt

# 2. 加载数据（所有派生信号在第一次调用时懒加载）
python3 src/loader.py

# 3. 跑 19/19 测试套件
for t in tests/test_*.py; do python3 "$t"; done

# 4. (可选) 重跑全 100 轮 ETL — 烧 LongCat 配额
BJ_PAL_LLM=longcat BJ_PAL_LLM_RPM=10 \
  python3 src/etl/expand_ugc_round6_facility_audience.py \
  python3 src/etl/expand_ugc_round7_to_16.py \
  python3 src/etl/expand_ugc_round17_to_36.py \
  python3 src/etl/expand_ugc_round37_to_56.py \
  python3 src/etl/expand_ugc_round57_to_76.py \
  python3 src/etl/expand_ugc_round77_to_100.py

# 5. dump SQLite → jsonl 持久化（防 db rebuild 丢数据）
python3 src/etl/dump_and_verify_after_etl.py

# 6. 跑 LongCat demo 看效果
BJ_PAL_LLM=longcat python3 scripts/run_longcat_demo.py

# 7. 跑 4 项评估
python3 -m evals.eval_plans --input data/longcat_demo_v2_results.json --plan-key v2

# 8. Streamlit UI
python3 -m streamlit run src/ui/app.py
```

## 十、数据资产 SUM

| 类别 | 数量 | 大小 |
|---|---|---|
| SQLite pois | 5,653 行 | - |
| SQLite ugc_aspects | **8,666 行** | - |
| SQLite routes | 1,892 行 | - |
| SQLite tool_calls | 892 行 | - |
| 静态规则 JSON | 4 个文件 | 59 KB |
| amap 原始 + 合并 | 20 jsonl | 63 MB |
| UGC jsonl | 1 主文件 | **7.7 MB** |
| 评估 / 实验 JSON | 5 个 | 0.4 MB |
| ETL 脚本 | 19 个 | ~6,000 行代码 |
| ETL log 文件 | 6 个 | <1 MB |
| **data/ 总大小** | **51 文件** | **84 MB** |
