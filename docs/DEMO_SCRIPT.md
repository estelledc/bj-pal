# BJ-Pal · Demo 脚本（90 秒 pitch + 5 分钟现场）

> 路演时背下来照念。所有时间标记基于第一次播放点击。

## 90 秒 Pitch（评委初筛 / 海选阶段）

### 0:00-0:15 痛点镜头（不开屏，先口播）
> "周六下午，小明把手机递给老婆问'你看这样行不'。30 秒后老婆说：'换一个'。
>
> 这是真实的痛点——不是搜索推荐能解决的。"

### 0:15-0:20 输入（开屏 BJ-Pal Web UI）
> 用户输入框打字（已预填）：
>
> "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。"
>
> 点击「🚀 生成方案」

### 0:20-0:35 v1 方案飘入
> 时间轴上飘入 5 步方案（实测真实输出）：
>
> - 14:00 雍和宫 (citywalk)
> - 15:00 金鼎轩(地坛店) ¥88 (meal)
> - 16:15 地坛公园 (culture)
> - 17:45 雍和炸鸡烧饼 ¥18 (rest)
> - 18:30 返程
>
> 旁白："Planner 用 5,656 北京 POI + UGC 软信号挑出了这套方案。但是——"

### 0:35-0:50 ⭐ Reroute Wow 时刻
> 第 1 步「五道营胡同」时间槽变黄 → 红
>
> 弹窗：
> > ⚠️ 检测到风险：**五道营胡同** 排队/拥堵
> > • UGC[crowd]: 多条评价指出周末 14-18 点为 citywalk 黄金时段，南锣鼓巷、五道营等商业化胡同人流量大、拥挤明显
> > • UGC[transport]: 评价明确提到五道营胡同不太好停车，不建议自驾前往
> > → 已切换到 **国子监**
>
> 时间轴上第 1 步动画替换为「国子监 🔄」，地图 polyline 重画
>
> 旁白："这就是 agent 的异常处理——**1102 条 UGC 软信号**（5 类来源透明区分）告诉它周末爆棚 + 停车不便，自动切换到 7 分钟步行的国子监，同样是片区代表性文化点。**evidence 字段一键展开 raw_text_excerpt 可溯源每条来源**。"

### 0:50-1:10 一键发朋友闭环
> 切到下方话术化卡片：
>
> > 搞定了！下午这样安排你看行不
> >
> > 14:00 → 国子监 🔄
> > 15:00 → 金鼎轩(地坛店)
> > 16:15 → 地坛公园
> > 17:45 → 雍和炸鸡烧饼
> >
> > 五道营-雍和宫片区 下午 14:00 起，family 画像
> > 回我一个 OK 我就预订~
>
> 点击「📱 一键发送」→ 显示"✅ 已发送给 老婆" → 1 秒后"📩 老婆：OK 就这么定吧"

### 1:10-1:30 收尾
> 切到「🎯 行动」面板：
>
> 点「✅ 确认下单（mock）」→ 显示 "✅ 已锁定 金鼎轩 15:00 3 人座位 / booking_id=BK..."
>
> 旁白：
> > "**5,656 个北京真实 POI + 1,102 条结构化 UGC（103 片区 5 类来源透明）+ 1,892 条多模态路线** + 主动 reroute Agent + 一键发朋友闭环 = BJ-Pal。
> > 美团生态闭环：方案 → 美团下单 → 高德导航 → 微信卡片。
> > 这是北京试点，下一步上海杭州一键扩展。"

---

## 5 分钟现场 Demo（决赛 / 复试阶段）

### 0:00-0:30 老用户回顾（5min 现场新开场）
> 切到「📋 我的北京下午足迹」面板（P1.1）：
>
> > 上周六下午 14:00-18:30
> > 4 个人在三里屯
> > ❌ 第 1 站京 X 烤肉排队 40 分钟
> > 🔄 改计划又花 20 分钟
> > 实际 19:30 才吃上
>
> 旁白："这是孙倩访谈里的真话——'集体决策没人愿承担选错的风险'。"
>
> 切回输入框：
>
> > "今天周六下午，4 个人，2 男 2 女，三里屯，2 小时定方案。"
>
> 点「🚀 生成方案」
>
> "现在你只需 30 秒——而且 BJ-Pal 替你扛下选错的责任。"

### 0:30-1:30 重复 90s pitch

### 1:30-2:30 偏好镜子（agent-native 创新点）
> 切回输入框，新输入：
>
> > "下午带 5 岁娃出去玩。老婆减脂。"
>
> 点击「Clarify」按钮（Preference Mirror）→ 弹出对话框：
>
> > 老婆减脂是低糖优先（少甜品/含糖饮料）还是低油优先（少炸物/红烧）？
> > [低糖优先] [低油优先] [都要严格]
>
> 旁白："这是 form 表单做不到的——agent 用一句反问把模糊偏好变成可执行约束。"
>
> 选「低糖优先」→ ranking 实时变化：原本第 2 名的某甜品店被替换；新方案出来了

### 2:30-3:30 画像切换（同片区天差地别）
> 顶部 sidebar：从「家庭」切到「朋友」
>
> 重新生成 → 完全不同方案：
>
> - 14:30 五道营胡同
> - 15:30 国子监
> - 16:30 雍和宫
> - 17:30 京兆尹(雍和宫店) ¥220 / 悦真雅院 ¥220（朋友画像 ¥250 预算下解锁）
> - 19:00 Cafe Zarah(鼓楼东大街店)
>
> 旁白："同一片区，家庭画像 ¥120 vs 朋友画像 ¥250，方案完全不一样。京兆尹（人均 ¥966）家庭场景被预算 hard filter，朋友场景下虽然超预算也被砍掉，悦真雅院 ¥220 反而成为顶部。"

### 3:30-4:30 Trace 侧栏（评委 Q&A 用）
> 展开「🔍 Tool Call Trace」：
>
> 显示 ~30 行 tool_calls 表格：
>
> | time | tool | latency | params |
> |---|---|---|---|
> | 14:32:01 | amap.search_pois | 14ms | area=五道营... category=food |
> | 14:32:01 | ugc_signals.summarize_area | 8ms | ... |
> | 14:32:01 | rank_fuse.fuse_and_rank | 21ms | candidates=12 ... |
> | 14:32:02 | availability_probe | 6ms | poi=雍和宫 ... |
> | 14:32:02 | replanner.replan_step | 18ms | failed_idx=0 ... |
> | 14:32:02 | mock_book.book_restaurant | 0ms | poi_id=B000A7KVRU ... |
> | ... |
>
> 旁白："评委可以问任何'为什么选 X 不选 Y'——每个决策都有 tool call 留痕，每个 POI 都有 reasons 引用 UGC 原文。"
>
> 点开任意一个 RankedPOI 的 reasons：展开 3 条
>
> - amap_rating: +0.336 ｜ 高德评分 4.8/5.0
> - ugc_soft_score: -0.065 ｜ [queue] 周末高峰排队 85 分钟（UGC conf=0.86）
> - budget_fit: +0.150 ｜ 人均 ¥88 远低于预算 ¥120

### 4:00-4:30 数据厚度展示（v2.2 新增 / **v3.0 后扩到 8,666 条**）
> 切到 sidebar "UGC 数据厚度" 标签 / 或展开 trace 表的 dataset_version 字段：
>
> 旁白：
> > "数据扩展是这次最大的工程投入——从最初 37 条扩到 **8,666 条 / 6300+ POI 派生信号网 / 5 类透明来源**：
> > - manual_v1 截图抽取 37 条
> > - Class A 公开评论汇总 479 条 + R6-R100 跨片区主题 +2366 条（10+ 北京片区 LongCat 抽 aspect schema）
> > - Class B amap 属性推理 333 条（仅基于客观字段，禁止编造）
> > - Class C 场景主题 137 条（亲子 / 雨天 / 避暑 / 老人友好等 12 个主题）
> > - 跨片区主题 116 条（咖啡 / 烤鸭 / 胡同 / 红叶等 11 类）
> >
> > 加 **weekend_afternoon_intensity 列 100% 覆盖**——'周六下午'画像有真证据，不是 prompt 写死。
> > 加 **routes 1,892 条**（52 amap cache + 1,840 估算）覆盖 150 核心 POI 任意 1-hop 替代点。
> > 加 **动态 trap 评分**——全聚德等老字号自动识别，不再硬编码 4 个 demo POI。
> > 加 **v3.0 9 个北京特色派生信号**：facilities / audience_segment / seasonal / heritage_brand / reservation / weather_shelter / crowd_forecast / poi_graph / parking。"

### 4:30-5:00 商业价值收尾
> 旁白：
> > "美团生态接通：方案确认 → 商家预订（美团商家 / 哗啦啦）→ 蛋糕配送（美团秒送）→ 微信通知。
> >
> > 假设北京周末场景日均 1 万次启动 × 30% 完成下单转化 × 平均客单价 200 元 = 60 万 GMV/天。
> >
> > 数据扩展：amap 抓取脚本城市无关，3 天扩到一线 + 强二线；UGC 抽取双链路通用（vision_extractor 截图入口 + text_aspect_extractor 文本入口）。
> >
> > 实时性路径：M1 第 1 周即可接高德组合 API 上线（详见 bj-pal-amap-heat-research.md）。
> >
> > 隐私 / 合规：UGC `privacy_status` 三种（identity_removed / public_review_aggregation_no_pii / amap_objective_no_pii）；用户对话不持久化；mock 接口标注了所有真实 API 对接路径。
> >
> > BJ-Pal 北京下午管家——不是搜索推荐，是把事做完。"

---

## 评委 Q&A 准备（见 docs/QA_PREP.md 的 20 题）

## 录像清单（W2 D6 用户操作）

1. **场景 A 完整流（90s）**：用 OBS / Quicktime 录屏 → 实际跑 `streamlit run` + 输入 family 场景 → 60 帧/秒，1080p
2. **场景 B 偏好镜子（30s）**：单独录这一段
3. **场景 C 画像切换（30s）**：单独录
4. **场景 D Trace 侧栏（30s）**：单独录

合成方案：用 Final Cut / DaVinci Resolve 合成 5 分钟主版本，并裁出 90s 版本。

录屏工具推荐：macOS 自带 `cmd+shift+5`（最简单）或 OBS（多镜头切换）。

---

## v3.x 演化升级段（决赛版加塞用）

如果时间富裕（5min 现场超时还能续 1-2min），加这两段提评测严谨度 + 算法跃迁。

### A. 三分支 Planner 选择（30s · 算法亮点）

切回输入框，输 **复杂约束 query**：

> "今天周六下午，4 个朋友 + 1 老人 + 2 娃，15:00 起 4 小时，雨天，预算人均 ¥150-300，老人腿脚不便，娃要文化场所。"

Planner 自动选 **OPTW 分支**（候选 ≥ 30 + 强时间窗 + 多硬约束）：

> 旁白："看顶部 trace badge——`planner.plan_optw` → OR-Tools CP-SAT 5s 出 FEASIBLE 解。
> 7 步访问序列全局最优，避免局部贪心。"

切到 demo 模式 toggle，强制选 **ToT 分支**（branch_hint="balanced"）：

> 同 query，K=3 候选并发 + 自评分（commonsense + hard_constraint + utility + diversity + rationale_quality） → 选最优分支。
>
> "三个分支并行思考，AI 自己挑了均衡组，不是单条 prompt 拍出来的。"

### B. ECE 校准面板（30s · 信任度亮点）⭐

切到 sidebar 的 **「📊 校准面板」**（`ui/calibration_panel.py`）：

显示：

- **Global ECE: 0.1089**（目标 ≤ 0.15，达标）
- 滑窗 ECE 演化曲线（14 窗，0.34 → 0.06-0.25）
- 置信度直方图（10 桶，主集中在 0.7-0.8）

旁白：

> "我们跑了 799 个 plan、3,885 步 trace、291 个真实 outcome 对照——AI 自评的 confidence 和真实成功率，平均偏差 11 个百分点。
>
> 坦诚说，置信度还过于集中在 0.7-0.8，说明 LLM 自评的细粒度还没充分用上——这是 v4.0 改进点。
>
> 但我们不藏拙：每周都跑滑窗 ECE 校准自己有多准。这是真正的'AI 替你扛责任'。"

---

## 路演物料速查（promo/）

详见 `promo/README.md`。决赛当天用法：

| 时刻 | 物料 | 怎么用 |
|---|---|---|
| 入场前 30 min | `promo/pitch-deck.pdf` | U 盘备份，应付现场设备故障 |
| 评委桌摆 | `promo/one-pager.pdf` | 打印 A4 一份，每位评委一张 |
| 主屏幕 | `promo/pitch-deck.html` | Chrome 全屏 F11，方向键翻页（10 张横屏 1920×1080） |
| 答辩补料 | `promo/architecture.md` | 手机打开 GitHub 渲染 mermaid 图 |
| 现场 demo 切换 | Streamlit + pitch-deck 双窗口 | 答评委问题时随时回跳 deck 第 7-8 页（算法 + Demo） |

### 路演前 1-2 周引流

- **小红书图文**：用 `promo/xhs-png/` 9 张卡片；发布文案在公开内容计划中单独维护
- **GitHub Pages**：`promo/landing-page.html` 部署到 docs/index.html → `https://estelledc.github.io/bj-pal/`
- **GitHub README banner**：`promo/hero-png/01.png` commit 到 `assets/hero.png`

### 决赛后

- 简历附 `promo/pitch-deck.pdf` + landing page 链接
- 回顾文章附 `promo/one-pager.pdf`
