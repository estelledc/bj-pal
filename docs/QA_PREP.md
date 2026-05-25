# BJ-Pal · 评委 Q&A 演练手册

> 15 个最可能问的问题 + 简明答案。比答错更怕"答得软"——每个回答先 1 句结论再 2 句佐证。

## 业务 / 产品类

### Q1. 这跟 GPT 套高德 API 有啥本质区别？
**A**：三层差异。① **UGC 软信号融合** ranking——纯 GPT 拿不到大众点评 UGC 的结构化抽取。② **主动 reroute**——agent 在用户看到方案前已扫一遍风险，不是被动报错。③ **可解释 reasons**——每个 POI 选择附引用 UGC 原文的依据，纯 LLM 编不出真实评论。

### Q2. 为什么只做北京？
**A**：v1 北京试点。amap 抓取脚本城市无关，3 天能扩到一线 + 强二线；UGC 抽取链路从大众点评截图通过 GPT-4V 解析也是城市无关。Planner / Ranking / Replanner 完全没有北京特化逻辑。

### Q3. 商业价值多大？
**A**：保守估算北京周末场景日均 1 万次启动 × 30% 完成下单转化 × 平均客单价 200 元 = 60 万 GMV/天。GMV 链路短：方案 → 商家预订 → 蛋糕配送 → 微信卡片，每一节都是美团生态既有产品。

### Q4. 为什么不做多 agent debate / RL？
**A**：命题需要的是落地、把事做完，不是论文 hot 词。Plan-and-Execute 已经够用——评委可以从 Tool Call Trace 看到 ~10 次工具调用 1 个 reroute 一气完成。多 agent debate 会让响应时间从秒级跳到 30 秒+，对周六下午紧迫场景反而扣分。

### Q5. 5 岁娃 / 减脂这种约束怎么硬保证？
**A**：两层。L1 硬过滤一票否决——`avg_price > budget × 1.2` 或 `category 含酒吧/夜店且 has_child=true` 的 POI 直接 SQL where 砍掉。L2 软排序——`light_diet` 这类偏好走 ranking weight 调整。硬过滤示范：京兆尹 ¥966 在家庭 ¥120 预算下永远不出现。

## 技术 / 工程类

### Q6. Agent 思考过程能解释吗？
**A**：能。Tool Call Trace 侧栏每次调用记 (timestamp, tool, params, response, latency, status)；每个 RankedPOI 附 reasons[(factor, contrib, evidence)]。例子：雍和宫 score=0.325，分解到 amap_rating +0.343 / ugc_soft -0.065 / crowd_penalty -0.059，每条 evidence 直接引 UGC 原文。

### Q7. LLM 输出不合规怎么办？
**A**：四层防御。① pydantic-style schema 校验（实际用 dataclass + 默认值兜底，3.9 兼容）。② JSON 解析失败时 `_safe_parse_json` 容忍 ```json ``` 包裹和前后噪声文本。③ Step 字段全部带默认值（duration_min=60, mode_to_here="walking"），LLM 偶尔漏字段不崩。④ 实在不可解析时抛 RuntimeError 带前 300 字 LLM 文本，UI 层 catch 退到 mock client 保 demo 不挂。

### Q8. 余位探针怎么真接通？
**A**：三条路径。① 美团商家开放 API（POST /merchant/v1/reservation/create）。② 哗啦啦 / 客如云这类餐饮 SaaS 自带余位接口。③ 景区排队走高德 / 美团 LBS 实时拥挤度。每个 mock 接口在源码注释里都标了真实路径，签名一致，切换只需替换 `.complete()` 方法体。

### Q9. 排队时长怎么准？
**A**：三层数据源融合。① UGC 结构化抽取的 wait_min（已落 SQLite）。② 美团商家系统实时排队号（生产环境）。③ 高峰期启发式（11:30-13:30 / 17:30-20:00）。本 demo 默认用 ① + 启发式，trap POI（雍和宫 / 故宫等）用 hardcode 确保现场必触发。

### Q10. UGC 数据质量如何？（v2.2 升级版）
**A**：**1,102 条 aspect 切片 / 103 片区 / 5 类来源透明区分**：
- `manual_ugc_seed_v1` 37 条 — 89 张大众点评截图 GPT-4V 抽取
- `synthetic_from_public_summaries_v2` 732 条（Class A 479 + Class C 场景 137 + Round 5 主题 116） — 网络公开评论汇总 LongCat 结构化抽取
- `derived_from_amap_attributes_v2` 333 条 — 仅基于 amap 客观字段（评分 / 价格 / 类目）推理，**禁止编造网友评论**

每条带 `confidence ∈ [0,1]` + `weekend_afternoon_intensity ∈ [0,1]` + `dataset_version` + `extraction_status` + `privacy_status` + `raw_text_excerpt`（200 字）。
质量门槛：ranking 层 `confidence ≥ 0.6` 且 intensity 加权；reroute 触发 `confidence ≥ 0.7 + sentiment=negative`；动态 trap 用 amap rating ≥ 4.7 + UGC crowd negative 交叉。
覆盖密度：王府井-东单 20 / 五道营-雍和宫 19 / 安定门-雍和宫 16 / 三里屯 14 / 望京 14 / 798 12 / scenario 主题各 10-16。

## 隐私 / 合规类

### Q11. 用户隐私怎么处理？
**A**：三道闸。① UGC `privacy_status=identity_removed`——大众点评截图已脱敏。② 用户对话不持久化——session_id 哈希前 8 位，会话结束 SQLite 滚动。③ Tool Call Log 不存原文 prompt，只存 params 摘要。生产接入按公司隐私基线走。

### Q12. 蛋糕配送 / 微信发送都是真的吗？
**A**：demo 是 mock。每个 mock 接口在 `MOCK_API_README.md` 标了真实对接路径——美团秒送 / 微信小程序 subscribeMessage 等。生产路径上 agent 层不需要任何改动，这是抽象层的设计胜利。

## 数据 / 扩展类

### Q13. 数据扩展路径？
**A**：amap 抓取脚本（`scripts/fetch_amap_pois.py`）城市无关，改 city 参数即可；UGC 抽取脚本（`scripts/extract_ugc_aspects.py`）从大众点评截图通过 GPT-4V 解析，城市无关。3 天能扩到上海 / 杭州，1 周覆盖一线 + 强二线。

### Q14. 模型可替换吗？
**A**：可以。`agents/llm_client.py` 抽象层支持 `mock / longcat / anthropic` 三种后端，切换只改环境变量 `BJ_PAL_LLM`。LongCat 走 Anthropic 兼容协议 + Bearer 认证，Claude / DeepSeek 都能在 30 分钟内接通。Mock 后端规则化生成，离线开发期不耗 token。

### Q15. 为什么 LongCat 不是 GPT？
**A**：合规优先。LongCat 是美团自研，黑客松场景无第三方 LLM 合规风险。从 activity-planner 项目复用接入方式，0 成本切换。

## 数据扩展专属（v2.2 加问 5 题）

### Q16. 1,102 条 UGC 是不是都是真的？
**A**：**坦白讲，不全是 — 但每条来源都透明可查**。manual_v1 37 条来自真实大众点评截图 GPT-4V 抽取，是真 UGC。其余 1,065 条是合成数据，**但合成方法学诚实**：Class A 用网络公开评论汇总让 LongCat 抽 aspect schema（不伪造具体引用、用"普遍反映"客观语气）；Class B 仅基于 amap 客观字段（评分 / 价格 / 类目）推理（禁止编造网友评论）。每条 raw_json 字段标 `dataset_version` + `extraction_status` + `source_urls` + `raw_text_excerpt`，评委想看哪条溯源都能查。

### Q17. 动态 trap POI 评分怎么定的？
**A**：`compute_dynamic_trap_score` 三维加权：① amap 评分 ≥ 4.8 +0.4 / 4.7 +0.3 / 4.5 +0.15（高分店才可能 trap）② UGC negative crowd/queue/booking_risk +0.2 每条（最多 +0.5）③ 名字含"全聚德/便宜坊/海底捞/胡大/老字号/故宫/雍和宫"等关键词 +0.15（demo 直觉）。score ≥ 0.5 即触发 reroute；普通店 rating < 4.5 直接返回 0 不误伤。实测：全聚德前门店 score=0.75（amap 4.8 + UGC queue + 老字号关键词三层全中），路边小馆 score=0.0。

### Q18. weekend_afternoon_intensity 怎么算的？
**A**：**纯规则可解释**，不依赖 LLM：① `time_bucket` 直接命中 `weekend_afternoon` → 1.0；② `general` + 含"周末/下午/citywalk/14:00-17:00"等关键词 → 0.7；③ `general` 默认 0.5；④ 含"工作日/早上/深夜/晚餐/夜场"等负向词 → -0.2 衰减；⑤ `evening / weekday_dinner` 桶 → 0.1-0.2。1,102 条全填，HIGH 215 / MID 764 / LOW 123。db rebuild 时 loader 自动 fallback compute，不用每次重跑 ETL。

### Q19. routes 1,892 条都是高德路网吗？
**A**：**52 条 amap 真实路径 + 1,840 条 estimated_v2** — 后者透明标注。estimated_v2 用 haversine × 1.3 detour（北京城市内典型绕行系数）+ 4 模式标准速度（步行 5km/h / 骑行 15 / 驾车 25 市区 / 公交 18 + 5min 等车）。覆盖 150 个核心 POI × 5 nearest 配对 ≈ 473 unique leg × 4 模式。源码注释 + raw_json 字段 `method=haversine_x_detour_with_mode_speed` 都标了。MVP 后切高德 navigation API 真实路径只需替换 `_find_cached_leg` 实现。

### Q20. clone 后能直接跑通吗？
**A**：可以。`expanded_v2.jsonl`（UGC + routes 共 2.4MB）已进 git；clone 后 `python3 src/loader.py` 自动加载 → 1065 UGC + 1892 routes（不依赖 manual_ugc_seed.jsonl，loader graceful 跳过缺失文件）。原始 amap POI（86MB）和大众点评截图仍 gitignore。test_data_coverage.py 4 章节 16 断言全过即可证明数据完整。

---

## 演练 checklist

- [ ] 90s pitch 背诵 3 遍，时间控制在 85-95s
- [ ] 5 分钟现场 demo 全跑 1 遍，本地无报错
- [ ] Q1-Q20 每题用一句话回答（避免长篇大论）
- [ ] Trace 侧栏能现场展开（带网线 / 不带都能跑）
- [ ] 准备一份预录视频 mp4 作为兜底（如现场 streamlit 挂掉）
- [ ] 提前部署到云端 demo URL（fly.io / huggingface space），现场万一本机出问题切线上
