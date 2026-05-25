# BJ-Pal Mock API 说明 · 生产对接路径

> 给评委看的"如果上生产，明天就能接通"的承诺。
> 每个 mock 接口在源码里都标了真实 API 对接路径。

## 设计原则

1. **接口签名向真实 API 对齐**——参数名 / 字段顺序 / 返回结构都按公开文档来
2. **数据来源真实**——mock 调用时返回的餐厅 / 路线信息直接来自 `data/amap/merged/...jsonl`，不是凭空编
3. **故障注入有规律**——5% timeout / 10% no_availability / 8% 排队超长 / 2% 商家拒单（覆盖真实失败模式）
4. **Tool Call Log 全程暴露**——`tool_calls.db` 记录每次调用的 (timestamp, tool_name, params, response, latency_ms, status, error)，UI Trace 侧栏可展开

---

## 接口清单

### 1. `mock_book.book_restaurant`

```python
def book_restaurant(
    poi_id: str, poi_name: str,
    target_time: str, party_size: int,
    contact_name: str = "用户", note: Optional[str] = None,
) -> BookingResult
```

- **生产对接**：`POST https://api.meituan.com/merchant/v1/reservation/create`
  body: `{merchant_id, party_size, time, contact, note}`
- **替代渠道**：哗啦啦 / 客如云 SaaS（覆盖大量中小连锁餐厅）
- **故障注入**：`no_availability 10%` / `timeout 5%` / `rejected_by_merchant 2%` / `confirmed 83%`

### 2. `mock_book.book_cake_delivery`

```python
def book_cake_delivery(
    restaurant_id: str, restaurant_name: str,
    cake_spec: str, delivery_time: str, greeting_message: str,
) -> CakeDeliveryResult
```

- **生产对接**：美团秒送 / 跑腿 `POST /api/v1/instant_delivery/cake`
- **故障注入**：`out_of_stock 5%` / 其余 `scheduled` 含 ETA

### 3. `mock_message.render_im_card`

```python
def render_im_card(plan: Plan, audience: str = "spouse") -> MessageCard
```

- 纯本地拼接，不调外部 API
- 输出含 title / body / actions / raw_text；audience ∈ {spouse / friend / group} 决定开场白

### 4. `mock_message.send_via_wechat_mock`

```python
def send_via_wechat_mock(card: MessageCard, contact: str) -> SendResult
```

- **生产对接**：
    - 微信小程序：`subscribeMessage.send`
    - 微信公众号：`customerservice.sendMessage`
    - 企业微信：webhook bot
- 当前 mock 直接返回 `delivered=True` + 伪 message_id

### 5. `availability_probe.probe`

```python
def probe(poi: POI, party_size: int, target_time: str) -> ProbeResult
```

- **生产对接**：
    - 餐厅余位：上述 mock_book 的预订系统通常自带余位查询接口
    - 景区排队：高德 / 美团 LBS 实时拥挤度
    - 博物馆预约：每个馆有官网接口（故宫 / 国博 / 中科馆都有）
- mock 行为见上文 trap POI 配置

### 6. `tool_call_log.*`

不是外部接口，是项目内部 SQLite 调用日志。生产环境可换成 OpenTelemetry / 公司 trace 系统。

---

## 环境变量

```bash
# LLM 后端（默认 mock，离线可跑）
BJ_PAL_LLM=mock|longcat|anthropic

# LongCat（走 Anthropic 兼容协议 + Bearer 认证）
LONGCAT_API_KEY=sk-...
LONGCAT_BASE_URL=https://api.longcat.chat/anthropic
BJ_PAL_LONGCAT_MODEL=LongCat-2.0-Preview

# Anthropic（开发期 fallback）
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-opus-4-7

# 测试开关
BJ_PAL_TEST_LONGCAT=1   # 让 test_planner.py 同时跑 LongCat 真实调用
```

---

## 切换到生产环境的步骤

1. 把 `mock_book.py` 中两个函数的实现替换为真实 HTTP 调用，保持函数签名不变
2. 把 `mock_message.py` 中 `send_via_wechat_mock` 替换为真实微信 API
3. `availability_probe.py` 中 `probe()` 改为调真实余位 API；保留 hardcoded TRAP_POIS 作为"已知热门点提前 warning"逻辑；动态 trap 评分（`compute_dynamic_trap_score`）保留并接入实时人流数据
4. `tool_call_log.py` 切换到公司 trace 系统（保留 SQLite 作 dev fallback）
5. agent / planner / replanner / preference_mirror 层 **不需要任何改动**——这是抽象层的胜利

---

## v2.2 ETL pipeline（数据扩展可重跑工具链）

### 设计原则

- **5 类来源透明区分** — 每条 ugc_aspects 记录都标 `dataset_version` + `source_urls` + `extraction_status` + `privacy_status` + `raw_text_excerpt`
- **可重跑可扩展** — 所有脚本都支持 `--dry-run` 预览 + idempotent 入库（`INSERT OR REPLACE`）
- **持久化** — `dump_ugc.py` / `dump_routes.py` 把 SQLite 数据 dump 成 jsonl，loader 多源加载防 db rebuild 丢数据

### 脚本清单（`src/etl/`）

| 脚本 | 类型 | 输出 | 数据源 |
|---|---|---|---|
| `text_aspect_extractor.py` | 核心库 | UGC dict | LongCat（公开评论汇总 → 9 维 aspect schema） |
| `expand_ugc_areas.py` | round 1 | 99 条 / 5 片区 | 三里屯 / 王府井 / 南锣 / 798 / 奥森 |
| `expand_ugc_areas_round2.py` | round 2 | 184 条 / 15 片区 | 安定门 / 西单 / 望京 等 10 新片区 + 5 老片区深化 |
| `expand_ugc_areas_round3.py` | round 3 | 110 条 / 10 片区 | 亮马桥 / 中关村 / 工体 / 玉渊潭 / 古北水镇 / 环球影城 等 |
| `expand_ugc_areas_round4.py` | round 4 | 122 条 / 12 片区 | 朝阳公园 / 紫竹院 / 北海 / 地坛 / 白塔寺 / 国家大剧院 等 |
| `expand_themes_round5.py` | round 5 | 116 条 / 11 主题 | 咖啡 / 烤鸭 / 涮肉 / 胡同 / 红叶 / 长城 等跨片区主题 |
| `expand_scenarios.py` | Class C | 137 条 / 12 场景 | 亲子 / 雨天 / 避暑 / 夜宵 / 情侣 / 朋友 / 老人 / 推车 等 |
| `batch_amap_inference.py` | Class B | 333 条 / 45 area | amap 客观字段（评分 / 价格 / 类目）推理；支持 `--offset` 续跑 |
| `add_time_bucket_intensity.py` | Task 1.2 | weekend_afternoon_intensity 列 | 纯规则填充 1102 条 |
| `populate_estimated_routes.py` | Task 1.4 | 1840 条 estimated_v2 | 150 POI × 5 nearest，haversine × 1.3 detour |
| `dump_ugc.py` | 持久化 | `data/ugc/expanded_v2.jsonl` | SQLite → jsonl |
| `dump_routes.py` | 持久化 | `data/amap/routes/expanded_v2.jsonl` | SQLite → jsonl |

### 与生产对接路径

| ETL 类型 | mock 阶段 | 生产对接 |
|---|---|---|
| Class A 公开评论汇总 | LongCat 抽 aspect schema（"普遍反映"客观语气） | 美团商家系统 / 大众点评内部全量评论 API → LongCat 真实结构化抽取 |
| Class B amap 属性推理 | LongCat 仅基于客观字段推理 | 美团到店人流 + 客单价 + 营业时间真实数据 |
| Class C 场景主题 | 12 个主题 raw_text + LongCat | 用户行为聚类（场景标签自动学习，不再硬编码） |
| Task 1.2 时段画像 | 纯规则 + 关键词 | 真实订单时段分布（每 POI 累积 7 桶 visit_intensity） |
| Task 1.4 routes estimated | haversine × 1.3 + 标准速度 | 高德 navigation API 真实路径（含路况 traffic_index） |

### M1 实时信号源接入路径

详见 `explorations/ideas/bj-pal-amap-heat-research.md`。短版：

```python
# tools/amap_live.py（M1 第 1 周新增）
def get_live_heat(poi_id: str, key: str) -> dict:
    # 组合 4 个 amap API：POI 详情 + 路况 + 天气 + 周边密度
    return {
        "live_heat_score": 0.0-1.0,  # 推导值，非真"人流热度"
        "age_min": 0,                # 数据新鲜度
        "components": {...},         # 可解释来源拆分
    }
```

ranking 公式新增 `live_heat: 0.10` 分量，`rating` 从 0.35 降到 0.30。MVP 工作量约 10.5 小时（1 周）。
