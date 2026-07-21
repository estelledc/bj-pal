# BJ-Pal Legacy Mock API 说明

> 本文记录黑客松时期的直接 mock helper，不是当前生产对接承诺。当前公开 demo profile 是 deterministic synthetic，文中的供应商 URL、故障比例和“真实数据”说法没有随 v6.2 获得可复查的授权或 acceptance 证据，不能作为简历事实。
>
> v6.2 的正确副作用入口是 `POST /v1/operations` → 非请求者审批 → 独立 sandbox worker → receipt/uncertain → provider-bound 只读 reconciliation；`mock_book.*` 只保留为历史 helper/回归，不再是默认 CLI/Streamlit 预订入口，也不能直接替换成真实 HTTP。

## 设计原则

1. helper 保留历史函数签名，便于复现旧 demo；不声称已与当前供应商契约对齐。
2. 公开默认数据是 synthetic；本机可选 cache 也不等于实时、可预订或获得商业授权。
3. 旧随机故障注入只用于历史演示，不是生产失败分布证据；v6.2 operation execute/lookup 测试改用确定性 outcome。
4. 新写入采用 `tool_call_audit_v2` 有界隐私投影、稳定错误码和 session SHA chain，并默认落到独立 `runtime/tool_audit.db`；它仍只是本地调试账本，不是生产审计或 side-effect receipt。历史 `tool_calls.db` 不自动复制或擦除，新 SQLite 也未加密或远端不可变。

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

不能直接替换 mock helper。真实副作用必须按以下顺序推进：

1. 取得供应商 API、测试环境、数据/商业授权和可回滚策略；
2. 用 typed adapter 获得 quote/reference/validity/currency/amount/terms，并保存 provider provenance；
3. 复用 v6.2 `SideEffectOperationRepository`，让 action + quote 绑定 approval SHA，且请求者不能自批；
4. 以 operation ID 作为供应商幂等引用，保存可验证 receipt；调用后结果不明时先进入 `uncertain`，禁止盲重试；
5. 将真实订单查询接入现有 provider-bound reconciliation 并保存 acceptance；把补偿实现为重新 quote/审批/留 receipt 的独立写 operation，再补客服 handoff、PII redaction、secret manager、retention 和审计治理；
6. 用测试环境 acceptance artifact 证明成功、拒绝、超时、不确定与重复请求语义，再考虑开放真实入口。

`mock_message.send_via_wechat_mock` 也属于外部副作用，不能绕过同类 approval/operation/receipt 边界。

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

### M1 真实信号源接入路径

旧文档引用的本地调研文件未随当前仓库保留，因此不再用它作为实现依据。当前接入顺序以 [ROADMAP.md](ROADMAP.md) P1 和 [DESIGN.md](DESIGN.md) 的 provenance 契约为准：先取得合法 provider 与 acceptance sample，再实现 typed adapter、timeout/限流分类、TTL/stale、provider version、reference 和有效期。真实 API 返回 200 不等于实时人流，也不等于可预订。
