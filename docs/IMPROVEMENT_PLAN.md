# BJ-Pal · 改进落地计划

> 基于 `USER_RESEARCH_FINDINGS.md`（100 条 AI 用户访谈）
> 每条改动注明：来自哪个信号 / 改哪个文件 / 估计工时
> 所有改动完成后跑 `tests/` 全套确保不回归

---

## P0 · 必做（48 小时内）

### P0.1 · "Red flags" 吐槽面板（信号 2/6 强）
**来源**：5/5 一致「必须把吐槽点出来」+ 时效是分品类的
**改动**：
- `src/tools/ugc_signals.py`
  - 返回结构加 `evidence_age_days`（UGC 几天前的）+ `evidence_source_count`（多少条独立来源）+ `conflicting_signals`（有 N 条相反评价）
  - 新增 `freshness_decay()`：餐饮 30 天衰减 50%、景点 90 天衰减 30%、文化场所 180 天衰减 20%
- `src/ui/timeline.py`
  - 每张 POI 卡片必须显示「⚠ 1 条最关键吐槽」（即使整体推荐这家），引用原文 + 日期
  - 和现有 reasons spider 并列，新增 `red_flags` 面板
- `docs/DESIGN.md` §3 ranking 公式：补一行「当 confidence < 0.5 或 evidence_age_days > 30，UI 该标灰、降权但保留可见」

**工时**：6h
**验收**：随便选 5 家有 negative aspect 的 POI，UI 必须显示吐槽原文 + 日期

---

### P0.2 · 重要场合 → 筛选模式（信号 5 强）
**来源**：5/5 一致「6 人生日饭只用它筛餐厅、不交给完全规划」
**改动**：
- `src/agents/preference_mirror.py`
  - 检测关键词「生日 / 纪念日 / 老人首次见 / 6 人 / 家宴」时，在 `clarify_preference` 输出里加 `mode: 'screening'`
- `src/agents/planner.py`
  - 接收到 `mode == 'screening'` 时不输出 5-7 步 plan，只输出 ranked 候选 + 各家适合不适合的细节
- `src/ui/app.py`
  - 顶部加模式 toggle：「轻规划（动线全套）/ 筛选模式（候选+理由，最终决策你来）」
  - 偏好镜子返回 `mode='screening'` 时默认选中筛选模式

**工时**：4h
**验收**：输入"老婆生日带娃带双方父母 6 人吃饭"，UI 自动进筛选模式

---

### P0.3 · 群投票卡片删预算数字（信号 7 强 5/5）
**来源**：5/5 一致「预算绝对不愿分享给群」
**改动**：
- `src/agents/group_harmony.py`：broadcast 时把 `budget` 字段从群可见数据中剔除
- `src/tools/mock_message.py`：`render_im_card` 在群发模式下用模糊标签（"中等档位 / 高档位"）替代具体数字
- 系统内部仍然保留预算用于 ranking

**工时**：1h
**验收**：群发卡片 mock 输出里不应出现具体金额

---

### P0.4 · reroute 按改动幅度分流（信号 9 中）
**来源**：孙倩/卜清月坚持单独通知 vs 刘晋川/秋涵倾向群里直说，按改动幅度分流是共识
**改动**：
- `src/agents/replanner.py`
  - `RerouteEvent` 加 `change_magnitude` 字段：小=同片区同类、中=换片区、大=换 category
  - 加 `change_summary_zh` 一句话给人看，例："原 14:00 国子监改为 14:00 雍和宫，因为国子监周六下午被点评爆 UGC 排队 60 分"
  - 加 `unchanged_steps` 列表（让用户看到只动了 1 步）
- `src/ui/timeline.py`：被改动那一步用红/黄边框；时间轴顶部一句话说"我只换了第 3 站，其他都保留"
- `src/tools/mock_message.py`：小幅改动直接群里发；中/大幅 → 先发起人确认 60s，超时后再群里

**工时**：3h
**验收**：模拟 4 类触发器各跑一次，change_summary_zh 必须人话可读

---

### P0.5 · 错误自承认 micro-interaction（信号 3 强 5/5）
**来源**：5/5 一致「最多 2 次机会，第二次失误就停用」+ 必须有"出错后能快速纠正"机制
**改动**：
- `src/tools/mock_message.py`：新增 `apology_card`，主动说"上次给您的排队信息不准，这次我把可信度从 0.8 调到 0.5 让您先看，请验证后再用"
- `src/tools/availability_probe.py`：每次预测后加 self-evaluation hook，下次同 POI 预测时若上次错误，UI 显示 confidence 标记和"上次预测偏差 X 分钟"

**工时**：2h
**验收**：连续两次同 POI 预测，第二次必须显示前次反馈

---

## P1 · 应做（一周内）

### P1.1 · "我的北京下午足迹"数据沉淀页（付费留存）
**来源**：Session C Q3 用户反复说"数据沉淀、迁移成本高、日常习惯"是付费留存关键
**改动**：
- `src/ui/app.py` 新页面：展示每次出行的"被改动的站 / 实际等位 / 群投票结果"
- `src/tools/tool_call_log.py` 已经在写 SQLite，直接查询聚合即可
- 形成迁移成本，让用户离不开

**工时**：6h

---

### P1.2 · README + DEMO_SCRIPT 主台词重构（定位重构）
**来源**：信号 1（责任）+ 信号 4（聚焦周末）综合
**改动**：
- `README.md` 主标题 / 第一行
  - 旧：「美团黑客松 2026 短时活动规划 Agent」
  - 新：「**周六下午的决策解药——一句话替你扛下选错的责任**」
- `docs/DEMO_SCRIPT.md` 5min 现场 demo 调整开头
  - 旧：第一屏是 plan v1
  - 新：第一屏是「老用户回顾」——"上周六 4 个人在三里屯排队 40 分钟、改计划又花 20 分钟"，然后切 BJ-Pal——"现在你只需 30 秒"
  - 这条直接来自孙倩访谈原话

**工时**：1h

---

### P1.3 · 老年简化版投票卡片（信号 - 李慧珍场景）
**来源**：李慧珍：「群里投票卡片我真看不懂，怕操作乱套」
**改动**：
- `src/tools/mock_message.py` 的 `render_im_card` 加 `style: 'elderly_friendly'` 模式
  - 去掉 emoji 矩阵
  - 放大字号
  - 把"4 人投票"压缩成"全家同意了，去不去？是 / 否"两按钮
- `src/agents/preference_mirror.py`：抽到画像里有"老人参与"时，自动切到这个简化模式

**工时**：3h

---

### P1.4 · mock 下单显式标"演示"（刘晋川焦虑）
**来源**：刘晋川：「特别想确认那个一键下单是真下单还是占位，因为这关系到我钱包」
**改动**：
- `src/tools/mock_book.py`：返回 payload 里加 `is_mock: true` + `simulated_at` 时间戳
- `src/ui/app.py`：mock 状态时按钮文案从"一键下单"改成"模拟下单（演示）"，旁挂一个 i 提示"接入真实餐厅预订前显示"

**工时**：1h

---

## P2 · 设计要点（不一定 P0 实现，但需要写进决策记录）

### P2.1 · 三档定价（来自付费矩阵）
- 基础免费（5 次/月，避排队 + 单站改动）
- **9.9/月 个人版**（无限次 + 群投票 + AddOn）
- **29/月 家庭/团体版**（5 个家庭账号 + 季节性周末提醒 + 优先客服 + 历史沉淀）

写到 `docs/DESIGN.md` 新增章节「定价策略」

### P2.2 · 季节性续费提醒（信号 - Session C Q4）
- 续费窗口 = 10 月中下旬（Q4 + Q1 是高频期）
- "秋天是你高频用 BJ-Pal 的季节，要不要续？"

### P2.3 · 全国化的本地化深度（信号 - Session D Q2）
- 用户希望同 App 跟着走，但要本地化内容
- 当前 amap + UGC 数据只有北京。建议先在 README 标明「Beta：北京专属」，避免出差用户期望落空

### P2.4 · "责任盾牌"叙事（信号 1）
- `agents/group_harmony.py` 输出加 `responsibility_shield` 字段："本方案由 AI 综合 4 人偏好生成，**有问题找 BJ-Pal**"
- IM 卡片底部一行小字："谁选的？AI 选的。"
- 这是产品定位级表达，不只是 UI 文案

---

## P3 · 不能踩的雷（写进 `docs/DESIGN.md` "反模式"章节）

1. **越推越窄的"美团模式"** — 推过的店反复推；信息茧房
2. **重复推荐已消费商品** — `tool_call_log.py` 已记录历史，每次推荐前自动降权或加 badge「你上周来过」
3. **假装懂用户其实是广告** — reasons 必须诚实标注「这条是用户真实评价 / 这条是商家自填资料」，分两类显示

---

## 测试覆盖

每个 P0 完成后必须跑：
```bash
for t in tests/smoke_test.py tests/test_tools.py tests/test_planner.py \
         tests/test_ranking.py tests/test_reroute.py tests/test_preference_mirror.py \
         tests/test_route_lookup.py tests/test_v2_mock_reroute_addon.py \
         tests/test_v2_broadcast.py tests/test_v2_vision.py tests/test_v2_group_harmony.py \
         tests/test_data_coverage.py; do
    python3 "$t" || { echo "FAIL: $t"; break; }
done
```

新增测试建议：
- `test_red_flags_panel.py`（P0.1）
- `test_screening_mode.py`（P0.2）
- `test_budget_privacy.py`（P0.3）
- `test_reroute_magnitude.py`（P0.4）

---

## 落地顺序建议

```
Day 1 (上午):  P0.3 (1h) → P0.5 (2h) → P0.4 (3h)
Day 1 (下午):  P0.1 (6h)
Day 2 (上午):  P0.2 (4h)
Day 2 (下午):  P1.2 (1h) → P1.4 (1h) → P1.3 (3h)
后续:         P1.1 (6h) → P2.x 决策记录
```

完成所有 P0+P1 约 27h，刚好 2 天饱和。
