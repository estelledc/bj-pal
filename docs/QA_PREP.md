# BJ-Pal · 评委 Q&A 演练手册

> 30 个最可能问的问题 + 简明答案。比答错更怕"答得软"——每个回答先 1 句结论再 2 句佐证。
> Q1-Q15 业务/技术/隐私核心；Q16-Q20 v2.2 数据扩展；**Q21-Q30 v2.4-v3.1 升级专属**。

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

注：**v3.0 后 UGC 已扩到 8,666 条**（5/21 全天 R6-R100 共 95 轮共 +2366 条），`expanded_v2.jsonl` 同步更新；详见 `docs/100-improvements.md` "v2.4 → v3.1 行为级跃迁" 段。

## v2.4 → v3.1 升级专属（5/22 - 5/29 加问 10 题）

### Q21. 评测体系搞 L1/L2/L3 三层是不是 over-engineering？
**A**：**不是过度设计，是成本约束逼出来的**。100 case × 5 信号 × 每 commit 跑 = LongCat token 烧不起。我们的解法：L1 anchor 5 case 全 mock 30s 跑（每 commit），L2 集成 25 case 抽样 LongCat 5min 跑（每周），L3 全量 100 × 5 = 280 检查 LongCat 30min 跑（每 release）。频率 × 规模 × 信号强度三层解耦，参考 video-eval-agent 的 gstack 三阶段防火墙。详见 `docs/EVAL_FRAMEWORK.md`。

### Q22. ToT / OPTW / 普通三分支是怎么选的？
**A**：**入口决策树写在 planner.plan() 里**：query 简单 / 群人数 ≤ 2 / 时间富裕 → 普通分支；用户提了 ≥ 2 偏好维度 / 复杂约束 → ToT 分支（K=3 候选并发自评分 5 维：commonsense + hard_constraint + utility + diversity + rationale_quality）；候选池 ≥ 30 / 强时间窗约束 / 多 POI 最优访问序列 → OPTW 分支（OR-Tools CP-SAT，5s timeout）。三分支 entry 都过 plan_tracer 接到统一下游链路。`planner_tot.py` 5/5 测试，`optw_solver.py` 7/7 测试 + 端到端 4 步 POI 5s FEASIBLE。

### Q23. ECE 0.1089 真的够准吗？
**A**：**达标但不完美，我们坦诚地说**。Global ECE 0.1089（291 paired outcomes 计算），目标 ≤ 0.15 已达成。但置信度直方图显示 79.1% 的 trace 集中在 0.7-0.8 桶——说明 plan_tracer 默认值（约 0.74-0.78）占主导，LLM 自评的细粒度还没充分用上。能 pass 是因为 mean_actual_success 刚好接近 0.7-0.8。**v4.0 改进项**：让 planner_tot 的 5 维自评分真正传到 plan_tracer.confidence。详见 `docs/eval-100-results.md` §4。

### Q24. 280 信号检查 100% pass 是不是过拟合 fixture？
**A**：**有这个风险，所以我们用 deterministic 检查 + fixture/prompt 分库**。S1-S5 都不依赖 LLM judge——S1 检查 `plan_tracer.coverage_rate == 1.0`，S2 检查 `len(extract_red_flags) >= 1`，都是接到模块返回值。fixture 用通用语境 query，与 production prompt 隔离（参考 video-eval-agent 防火墙原则）。**真实风险在 L2 / L3 case 设计**：如果 case 只覆盖"已知能 pass 的"形态，新场景就会爆。所以 ROADMAP 里把"L3 case 多样性扩展"列为 v4.0 P1 项。

### Q25. v2.7 stateful 跨 session 记忆怎么处理隐私？
**A**：**三道闸**。① user_memory 表存的是 facet 抽象（cuisine_pref / dietary / physical_limit）+ confidence，不存原始 query 文本。② 每个 facet 字段独立 visibility 配置（self_only / group / trusted_only），群推荐时 `get_visible_history(group_members)` 只取并集可见的。③ 每 session 顶部"隐私模式"toggle，开启后纯靠当下输入计算，不读历史；状态在 IM 卡片显示"小李是隐私模式"避免群友误判。`forget(user_id, facet)` 和 `forget(user_id, before=ts)` 都支持。

### Q26. Kemeny-Young O(K!N) 真的能跑起来？
**A**：**两段式优化**。第一轮 Borda O(NK) 粗排 top-7（4 人对 50 候选 < 10ms），第二轮 Kemeny-Young 用 ILP（`pulp` 库）求 top-7 的最小 Kendall tau 共识，K=7 阶乘只 5040 种排列，可枚举求最优 < 100ms。互补：Borda 快、Kemeny 准。`agents/voting.py` 11/11 测试。

### Q27. promo 8 件套是 AI 自动生成还是手设计的？
**A**：**100% AI 自动生成**——用本机 Open Design（Claude Code 调）跑了 4 个项目（pitch / landing / xhs / one-pager），总耗时 ~80 分钟（含一次 daemon 重启 + 多次重试）。失败率：3 并发 ~50%，串行 + 简化 skill ~10%。关键 fix 是避开复杂 skill（如 magazine-web-ppt）改用 article-magazine / card-xiaohongshu，prompt 末尾明确"直接 Write index.html，不要等模板探索"。详见 `promo/README.md` "生成代价 + 教训" 段。

### Q28. LongCat 限流 / 改协议 / 挂了怎么办？
**A**：**llm_client.py 多模型 fallback chain 已落地**。环境变量 `BJ_PAL_LLM=mock|longcat|anthropic` 三档，默认 longcat。limit 错误（429）走 RPM 令牌桶 + 指数退避（base=2s, jitter±0.5, max=60s, max_attempts=4），见 `agents/llm_robust.py`。极端情况 mock 全程兜底（规则化生成）。**真实演练过**：5/21 跑 100 场景 LongCat 时遇到限流连续段头 S16，[73] partial parse + [75] RPM 限流后 8/40 限流 case 压到 0。

### Q29. v4.0 最关心哪个改进？
**A**：**三个，按优先级**：① **plan_tracer.confidence 真实化**（解决 Q23 的桶集中问题，让 ToT 自评分真传到下游）；② **真实 amap 实时数据接入**（详见 `bj-pal-amap-heat-research.md`：高德 POI 详情 + 路况 + 天气组合 API 1 周 MVP，ranking 加 `live_heat_score` 分量）；③ **L2 evals 归档化**（当前只 stdout，改为写 JSON 到 `evals/results/L2_<sha>_<ts>.json` 跟 L1/L3 一致），详见 `docs/ROADMAP.md`。

### Q30. 你们用 22 个 agent 模块会不会反而成本太高？
**A**：**不会，因为 agent 不全在同一 query 跑**。一次 plan 链路只调 4-6 个 agent（preference_mirror / planner / replanner / plan_tracer，群投票场景加 voting / group_convergence），其他 agent 是 evals 或 demo 模式按需启动。Tool Call Trace 实测一次 plan ~10 次工具调用、~6 次 agent 调用、总 latency 1.5-3s（mock）/ 3-8s（LongCat）。22 个 agent 是**能力总和**，不是**单次成本**。

---

## 演练 checklist

- [ ] 90s pitch 背诵 3 遍，时间控制在 85-95s
- [ ] 5 分钟现场 demo 全跑 1 遍，本地无报错
- [ ] Q1-Q30 每题用一句话回答（避免长篇大论）
- [ ] **Q21-Q30 重点演练**——这是 v2.4-v3.1 升级后评委最可能挑刺的部分
- [ ] Trace 侧栏能现场展开（带网线 / 不带都能跑）
- [ ] **校准面板能现场展开**（`ui/calibration_panel.py`）展示 ECE 0.1089 + 置信度直方图
- [ ] 准备一份预录视频 mp4 作为兜底（如现场 streamlit 挂掉）
- [ ] 提前部署到云端 demo URL（GitHub Pages 用 `promo/landing-page.html`），现场万一本机出问题切线上
