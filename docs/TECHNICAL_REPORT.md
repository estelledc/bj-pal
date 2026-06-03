# BJ-Pal 技术说明：周末闲时活动规划 Agent

本文记录项目目标、实现路径、评测结果和当前限制。更细的运行方式、数据说明和评测结果见文末文件索引。

## 1. 项目想解决什么

BJ-Pal 做的是周末闲时活动规划。项目目标不是“推荐几个热门景点”，而是把半天活动排成可执行方案：什么时候出发、去哪几站、每站待多久、怎么走、哪里可能排队、临时不想去某个点时怎么替换。

典型输入大概是这类：

- “今天下午带老婆和 5 岁娃出去玩，别离家太远。”
- “周五晚上跟朋友放松一下，吃饭聊天看夜景。”
- “陪爸妈在王府井吃顿正经的，再带他们逛逛。”
- “周末一个人想看展，再吃个不油腻的饭。”

项目重点不只在 LLM 生成文案，而是在几个现实问题上做约束：

- POI 不能乱编，必须从候选池里选。
- 计划要有时间顺序和交通方式。
- 排队、天气、满位、用户否决等情况要能触发调整。
- 每一步要能解释为什么选它。
- 有测试能证明这些链路不是只在 demo 里偶然跑通。

## 2. 主链路

目前主链路是 Plan-and-Execute：

```text
用户输入
  -> text_intake / multimodal_intake / user_memory 处理偏好
  -> planner 从候选 POI 池里生成 Plan v1
  -> availability_probe 扫描每一步风险
  -> replanner 对高风险 step 做局部替换
  -> plan_tracer 记录每一步决策、置信度和 fallback
  -> Streamlit UI 展示今日安排、地图、记忆和诊断信息
```

`planner.py` 先通过 `amap_search`、`ugc_signals`、`rank_fuse` 拿候选，再让 LLM 做选择、排序和理由生成。这样可以把最终地点约束在 POI 白名单和 dataclass 结构内。

主要模块如下：

| 模块 | 做什么 |
|---|---|
| `agents/planner.py` | 单轮结构化规划，输出 Plan/Step |
| `agents/replanner.py` | 风险或用户否决后，替换单个 step |
| `tools/amap_search.py` | 从本地 POI 数据里按片区、类目、约束取候选 |
| `tools/rank_fuse.py` | 融合评分、价格、UGC、距离、时段等信号 |
| `tools/availability_probe.py` | 模拟/探测排队、闭店、天气、拒单等风险 |
| `tools/route_lookup.py` | 给相邻 POI 补交通方式、距离和耗时 |
| `agents/user_memory.py` | 保存用户主动输入的长期偏好和禁忌 |
| `agents/plan_tracer.py` | 记录每步决策，方便回放和校准 |
| `ui/` | Streamlit 页面，包括输入、记忆、地图、时间线 |

## 3. 数据和候选池

数据层主要有三类：

- AMap POI：名称、类目、坐标、评分、人均、营业时间等。
- UGC aspects：用户评论中抽出来的排队、适合亲子、环境、口味等软信号。
- routes：步行、骑行、驾车、公交四种模式的距离和耗时。

这些数据由 `src/loader.py` 灌入 SQLite。规划时不是让模型凭空想地点，而是先从 SQLite 取候选，再交给模型编排。这样做牺牲了一部分“想象空间”，但能明显降低假 POI 和路线错位的问题。

交通时间的处理也做了两层：

1. 优先查 `routes` 缓存。
2. 如果缓存没有，就用经纬度距离乘城市绕行系数，再按不同交通方式估算。

这不是实时路况级别的精度，但可以支撑半天活动规划的 demo 和离线评测。实时高德路况、天气、人流仍在后续计划里。

## 4. 规划、探测和换点

一次完整规划分三步：

1. Planner 生成初始方案。每一步包含 `kind`、`poi_id`、`poi_name`、`start_time`、`duration_min`、`mode_to_here` 和 `rationale`。
2. Probe 扫描方案。如果某个地点命中排队、闭店、天气或用户否决，就返回一个 `ProbeResult`。
3. Replanner 只替换出问题的那一步，不整条路线重算。

局部替换是当前版本的取舍。整条重算延迟更高，也更难解释改动范围。现在的方案是：如果第 3 站排队，就在同片区、同类型、未使用过的候选里找替代点，其余步骤尽量不动。

最近修过的一个问题是“换一个”会回到之前已经换掉的地点。现在 `replan_step` 支持 `excluded_poi_names`，会排除当前方案里已有的点和本轮会话里换过/看过的点，避免用户连续点“换一个”时反复出现同一个地点。

## 5. 用户偏好和记忆

左侧记忆面板只保留长期偏好/禁忌，不再把每次 planner 或 reroute 的中间状态自动写进去。原因是自动写入很容易污染记忆，比如用户只是点击“换一个”，系统却把某些临时推断沉淀成长期偏好。

现在的策略是：

- 用户在右侧手动输入偏好/禁忌。
- 交给 LLM 做结构化抽取。
- 抽取结果写入 `user_memory`。
- planner 只读取已有记忆，不在规划过程中自己写新记忆。

这里也特意去掉了纯规则抽取。规则很容易漏掉“乳糖不耐受”“寻麻疹”“喜欢吃西瓜”“自助餐”这类开放表达，也容易把“寻麻疹”误推成“不吃海鲜”。目前的实现是 LLM 抽取为主，测试里覆盖了这几个例子。

相关文件：

- `src/agents/user_memory.py`
- `src/agents/text_intake.py`
- `tests/test_user_memory_llm_intake.py`

## 6. 评测怎么做

项目没有只靠手动点 UI 验证，评测分三层：

| 层级 | 用途 | 大致规模 | 后端 |
|---|---|---:|---|
| L1 | 提交级冒烟，检查核心链路有没有断 | 少量 anchor cases | mock |
| L2 | 模块行为回归，比如文本抽取、时间段、记忆、群体偏好 | 分模块 case | mock + 抽样真实 API |
| L3 | 全量分布评估 | 100 场景 | LongCat / 真实后端 |

评测指标主要参考 TravelPlanner 的思路：

- delivery：是否端到端成功返回。
- commonsense：步数、时间顺序、POI 白名单、交通方式、重复地点等常识检查。
- hard_constraint：预算、步行半径、总时长、饮食约束等硬约束。
- final_pass：上述条件同时通过。

另外还有 5 个行为信号检查：

| 信号 | 检查目标 |
|---|---|
| S1 责任承担 | 每步决策可追踪，有 fallback |
| S2 红旗可见 | 推荐不只报好处，也暴露负向证据 |
| S3 道歉容忍 | 多次失败后能给出合适的 apology |
| S4 周末聚焦 | 工作日/非周末语境能触发澄清 |
| S5 重要场合 | 生日饭、家宴等场景进入筛选模式 |

根据 `docs/eval-100-results.md`，v3 在 100 场景上达到：

| 指标 | v1 baseline | v2 | v3 |
|---|---:|---:|---:|
| delivery | 0.975 | 0.975 | 1.000 |
| commonsense | 0.475 | 0.575 | 0.810 |
| hard_constraint | 0.675 | 0.650 | 0.610 |
| final_pass | 0.275 | 0.275 | 0.470 |

v3 的 final_pass 相对 v2 有提升，但 hard_constraint 还不够高。现在系统已经能稳定出方案，但对预算、步行半径、饮食约束的细粒度控制还需要继续优化。

## 7. 这次新增的展示案例

为准备展示材料，额外跑了一批真实 API 测试。脚本是 `scripts/select_showcase_cases.py`，它从已有 100 场景池里取前 40 条候选，逐条跑规划、探测和 reroute，再按展示价值筛出 8 条。

本轮结果：

- 后端：`dpsk` 真实 API。
- 有效候选数：39。
- 成功数：40。
- 入选数：8。
- 原始结果：`data/showcase_candidates_dpsk.json`。
- 入选 JSON：`docs/showcase_test_cases.json`。
- 可直接阅读的 Markdown：`docs/showcase_test_cases.md`。

筛选标准不是只看分数高低，还要求覆盖不同用户画像，避免最后全是亲子案例。最后入选如下：

| 排名 | 场景 | 画像 | 片区 | 步数 | reroute | 为什么适合展示 |
|---:|---|---|---|---:|---:|---|
| 1 | S01 亲子周末·五道营 | 亲子/家庭 | 五道营-雍和宫片区 | 5 | 2 | 带 5 岁娃、老婆减脂，能展示亲子和饮食偏好 |
| 2 | S32 陪父母吃馆·王府井 | 陪父母 | 王府井-东单片区 | 5 | 1 | 正餐、父母舒适度、休息点组合比较清楚 |
| 3 | S14 朋友夜生活·三里屯V3 | 朋友聚会 | 三里屯片区V3 | 5 | 2 | 周五夜晚聚餐、聊天、夜景，适合展示多人社交 |
| 4 | S23 独自逛展·798 | 独自出行 | 798-酒仙桥艺术区 | 7 | 2 | solo 看展加清淡用餐，路线更长，信息更丰富 |
| 5 | S02 亲子文化·安定门V3 | 亲子/家庭 | 安定门片区V3 | 5 | 2 | 带爸妈和娃逛胡同看古迹，强调遮阴和座位休息 |
| 6 | S04 亲子赏景·什刹海 | 亲子/家庭 | 什刹海-鼓楼片区 | 5 | 2 | 湖景拍照、老人腿脚不便，慢节奏约束明确 |
| 7 | S03 亲子科普·奥森 | 亲子/家庭 | 奥林匹克公园片区 | 5 | 1 | 3 岁娃、科普展、能跑能玩 |
| 8 | S40 陪父母老字号·大栅栏V3 | 陪父母 | 大栅栏片区V3 | 6 | 1 | 老字号、前门大栅栏、中轴线文化体验 |

这组案例主要覆盖三点：

1. 系统不是只输出静态推荐，而是完整跑了 plan -> probe -> reroute。
2. 不同 persona 的输入会走出不同类型的路线。
3. 每条案例都有原始 JSON，可以复查每一步 POI、理由和换点事件。

## 8. 迭代过程

项目大致分几轮完成。

### v1：把主链路跑通

这一版先把最小功能串起来：

- SQLite loader。
- AMap POI 检索。
- UGC 信号读取。
- Planner。
- Ranking。
- Availability Probe。
- Replanner。
- Streamlit UI。
- Tool call log。

这一阶段的目标很简单：用户输入一句话，系统能给出一条活动方案；如果某个点排队或不可用，能换一个。

### v2：补真实约束

v2 开始补本地生活里更真实的问题：

- routes 缓存和四模式交通时间。
- 截图/文本补充偏好入口。
- UGC aspect 扩展。
- time bucket 和 weekend afternoon intensity。
- red flags 面板。
- 预算隐私与重要场合筛选。
- 群发投票和 IM 卡片。

这一版之后，方案不再只是“评分排序”，而是同时考虑预算、时段、排队、天气、亲子、长辈、UGC 负面信号等因素。

### v2.4：开始重视可验证性

v2.4 主要做三件事：

- D3：L1/L2/L3 评测。
- D5：群偏好收敛。
- D1：plan trace。

这版之后，我们可以回答“为什么选这个点”“失败时会怎么处理”“这次改动有没有把核心行为改坏”。

### v3：补算法能力

v3 加了三类算法：

| 算法 | 模块 | 用途 |
|---|---|---|
| Tree of Thoughts | `agents/planner_tot.py` | 多分支生成和自评分 |
| OPTW + OR-Tools CP-SAT | `agents/optw_solver.py` | 在时间窗内求更优访问序列 |
| Kemeny + Borda | `agents/voting.py` | 聚合多人偏好 |

这些算法没有单独另起一套 UI，而是接到同一套 Plan、Probe、Reroute、Trace 下游，避免系统越来越散。

### v3.1：做置信度校准

v3.1 加了 `calibration_history`，用 ECE 看预测置信度和实际成功率之间的偏差。`docs/eval-100-results.md` 里记录 Global ECE = 0.1089，达到了我们设的 `ECE <= 0.15` 目标。

但这里也暴露出一个问题：trace confidence 大量集中在 0.7-0.8 桶。后续需要把 ToT 自评分和 ranking 分数更真实地传进 `plan_tracer`，而不是用偏固定的启发式分数。

## 9. 主要工程取舍

### 9.1 为什么用 Plan-and-Execute

本地生活规划需要结构稳定、能画地图、能解释每一步。Plan-and-Execute 比多 agent debate 更容易控制延迟和输出结构。后者适合研究，但这个项目更需要一个能稳定演示的主链路。

### 9.2 为什么限制 POI 候选池

如果让 LLM 自由生成地点，很容易出现不存在的店、坐标错位、地图画不准。我们宁愿让模型只在候选池里选，再用 POI 白名单和 dataclass 校验。这样创造性少一点，但可靠性高很多。

### 9.3 为什么局部重规划

用户点“换一个”时，通常不是要推翻全部计划，而是不喜欢当前这站。局部换点更快，也更容易解释。现在的 `replanner.py` 会尽量保持原方案其他步骤不动。

### 9.4 为什么不用 LLM judge 做唯一评测

LLM judge 适合做补充，但不适合作为唯一回归标准。这个项目里很多错误可以确定性判断，比如 POI 是否重复、时间是否倒流、是否超预算、是否超步行半径。所以核心评测还是 deterministic checks。

### 9.5 哪些还是 mock

当前下单、微信发送、部分实时余位仍然是 mock。相关接口边界写在 `docs/MOCK_API_README.md`，后续生产替换主要集中在工具层，不需要大改 planner。

## 10. 还没做完的事

现在比较明确的限制：

- L2 evals 还主要是 stdout，应该归档成 JSON。
- confidence 分布过集中，校准还不够细。
- 高德实时路况、实时天气、人流、POI 详情还没有完全接入。
- 美团商家预订和微信发送仍然是 mock。
- 真实用户满意度还需要更多线下/线上反馈，而不是只看离线指标。

下一步优先级：

1. 把 `planner_tot` 和 ranking 分数接进 `plan_tracer.confidence`。
2. L2 评测结果落 JSON，方便长期对比。
3. 接高德实时数据和天气。
4. 做用户反馈学习闭环。
5. 把 `plan_tracer`、`replanner`、`voting` 等模块抽成更独立的 agent SDK。

## 11. 证据和文件索引

本文依据以下文件整理：

- `README.md`
- `docs/DESIGN.md`
- `docs/ROADMAP.md`
- `docs/EVAL_FRAMEWORK.md`
- `docs/eval-100-results.md`
- `docs/USER_RESEARCH_FINDINGS.md`
- `docs/100-improvements.md`
- `docs/MOCK_API_README.md`
- `docs/showcase_test_cases.json`
- `docs/showcase_test_cases.md`
- `data/showcase_candidates_dpsk.json`
- `scripts/select_showcase_cases.py`
- `tests/test_showcase_selector.py`
- `tests/test_user_memory_llm_intake.py`
- `src/agents/planner.py`
- `src/agents/replanner.py`
- `src/agents/user_memory.py`
- `src/agents/text_intake.py`
- `src/agents/planner_tot.py`
- `src/agents/optw_solver.py`
- `src/agents/plan_tracer.py`
- `src/agents/calibration_history.py`
- `src/tools/route_lookup.py`
- `src/tools/rank_fuse.py`
- `evals/behavioral/`
- `tests/`

## 12. 总结

BJ-Pal 现在还不是一个可以直接上线的完整本地生活产品，但它已经把一个活动规划 Agent 的关键链路跑起来了：候选池约束、结构化规划、风险探测、局部重规划、用户记忆、路线展示、trace、评测和案例归档。

项目重点不是让大模型写一段推荐文案，而是把周末活动规划拆成可执行、可验证、可回放的流程，并用测试和真实 API 案例记录实际运行结果。
