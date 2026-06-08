# BJ-Pal 系统架构图

> 文本源真相，GitHub / Notion 等 markdown 渲染器都支持 mermaid。
> 用作 README、Wiki、文档插图。

## 整体流水线（Plan-and-Execute + Probe + Replan）

```mermaid
flowchart TD
    User["👤 用户一句话<br/>+ 偏好"] --> Mirror["🪞 PreferenceMirror<br/>反问澄清"]
    Mirror --> Planner["🧠 Planner LLM<br/>Normal / ToT / OPTW"]
    Planner -->|候选池| Search["🔍 amap_search"]
    Planner -->|UGC| UGC["📝 ugc_signals"]
    Planner -->|融合排序| Rank["⚖️ rank_fuse<br/>L1 硬过滤 + L2 加权"]
    Rank --> Plan1["📋 Plan v1<br/>(5-7 步 JSON)"]
    Plan1 --> Probe["🚨 AvailabilityProbe<br/>扫每一步"]
    Probe -->|风险触发| Replanner["🔄 Replanner<br/>局部替换 failed step"]
    Replanner --> Plan2["📋 Plan v2<br/>+ RerouteEvent"]
    Probe -->|无风险| Plan2
    Plan2 --> Book["💳 mock_book<br/>餐厅预订 + 蛋糕配送"]
    Book --> Message["💬 mock_message<br/>IM 话术化卡片"]
    Message --> Vote["🗳️ Kemeny+Borda<br/>群投票共识"]
    Vote --> Send["📱 微信发送 (mock)"]

    Probe -.全程留痕.-> Log[("🗄️ tool_call_log<br/>SQLite")]
    Search -.-> Log
    UGC -.-> Log
    Rank -.-> Log
    Replanner -.-> Log
    Book -.-> Log

    classDef agent fill:#9c2a25,color:#fff,stroke:#000
    classDef tool fill:#fbf3e2,color:#1a1611,stroke:#9c2a25
    classDef storage fill:#1a1611,color:#fbf3e2

    class Planner,Replanner,Mirror,Vote agent
    class Search,UGC,Rank,Probe,Book,Message tool
    class Log storage
```

## L2 Ranking 公式

```
score = 0.35 · amap_rating
      + 0.30 · ugc_soft⁺
      + 0.15 · budget_fit
      + 0.10 · distance
      + 0.10 · crowd_penalty

ugc_soft⁺ = Σ (sign · confidence · 2 · weekend_afternoon_intensity)
```

每条候选附 `reasons[(factor, contrib, evidence)]`，evidence 直接引 UGC 原文。

## 异常处理三层

```mermaid
flowchart LR
    A["✅ 普通 POI<br/>rating + 高峰期<br/>启发式 wait_min"]
    B["⚠️ UGC negative<br/>conf >= 0.7<br/>软触发"]
    C["🔥 动态 trap<br/>amap >= 4.7<br/>+ UGC negative<br/>+ 老字号关键词"]
    D["🚨 hardcoded trap<br/>(demo 兜底)"]

    Input["输入 POI"] --> Check{"trap 评分<br/>>= 0.5?"}
    Check -->|是| C
    Check -->|否| B
    B -->|conf < 0.7| A
    A -->|rating 低| Skip["放行"]
```

## 数据资产

| 资产 | 数量 | 来源 |
|---|---|---|
| 北京 POI | 5,656 | 高德地图 |
| UGC aspects | 8,666 | 大众点评 + 小红书 + 公开摘要结构化 |
| POI 信号网 | 5,198 | parking / seasonal / crowd / facility |
| AI 用户访谈 | 100 | 自建 ai-user-research-platform |
| 预爬 routes | 1,892 | 高德路径规划 + estimated_v2 |

## 评测金字塔

```mermaid
flowchart BT
    L1["L1 anchor<br/>5 case / 每 commit"] --> L2["L2 integration<br/>5 模块 × 5 case"]
    L2 --> L3["L3 full<br/>100 case × 5 信号 = 280/280"]
    L3 --> ECE["v3.1 calibration<br/>Global ECE 0.1089"]
    ECE --> Goal["真实场景泛化<br/>(v4.0)"]
```

## v3.1 黑客松展示点

- **ToT / OPTW / Kemeny+Borda**：复杂约束、全局最优路线、群体共识三条算法线。
- **plan_tracer + OTel trace**：每一步 `(decision, confidence, fallback)` 可回放。
- **ECE 0.1089**：不是只说“可信”，而是用校准指标量化可信度。
