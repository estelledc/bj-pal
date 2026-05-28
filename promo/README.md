# BJ-Pal 宣传物料

> 美团黑客松 2026 / deadline 6/06
> 用 Open Design 本机 Claude Code 自动生成
> 完成时间：2026-05-26

## 物料清单

| 文件 | 用途 | 尺寸 | 大小 |
|---|---|---|---|
| `pitch-deck.html` | 决赛路演幻灯片（10 张横屏） | 1920×1080 ×10 | 32KB |
| `pitch-deck.pdf` | PDF 备份 / U 盘 | A4 横向 | — |
| `landing-page.html` | 项目主页（GitHub Pages 部署用） | 1920× 长滚动 | 33KB |
| `xhs-carousel.html` | 小红书图文（9 张） | 1080×1440 ×9 | 24KB |
| `one-pager.html` | A4 单页评委简介 | 1240×1754 | 23KB |
| `one-pager.pdf` | A4 PDF（打印 / 邮件附件） | A4 纵向 | — |
| `readme-hero.html` | GitHub README 顶部 banner | 1280×640 | 9KB |
| `architecture.md` | 系统架构图（mermaid 源） | text | — |

PNG 截图目录：

| 目录 | 数量 | 用途 |
|---|---|---|
| `xhs-png/card-1.png ~ card-9.png` | 9 | 小红书图文上传 |
| `pitch-png/01.png ~ 10.png` | 10 | 路演备用截图 / 朋友圈分享单页 |
| `hero-png/01.png` | 1 | GitHub README 顶部图 / Twitter / 公众号封面 |

## 各物料使用场景

### 决赛路演当天

- **主屏幕**：`pitch-deck.html` 用 Chrome 全屏（F11），方向键翻页
- **PDF 备份**：`pitch-deck.pdf` 拷 U 盘，应付现场设备故障
- **桌摆 A4**：`one-pager.pdf` 打印一份摆桌上给评委
- **答辩补料**：`pitch-deck.html` 第 7-8 页（算法 + Demo）随时回跳
- **架构问答**：`architecture.md` 在 GitHub 渲染 mermaid 图，手机上随手打开

### 引流期（决赛前 1-2 周）

- **小红书图文笔记**：用 `xhs-png/` 9 张 PNG，文案见 `intern-journal/explorations/content/xiaohongshu/2026-05-26-bjpal-hackathon.md`
- **GitHub README**：把 `hero-png/01.png` commit 到 bj-pal 仓库 `assets/hero.png`，README 顶部 `![](assets/hero.png)`
- **Landing Page 部署**：`landing-page.html` deploy 到 GitHub Pages（见下文）
- **朋友圈**：landing page 截图 + 链接 + 一句话钩子

### 决赛后

- **回顾文章**：`one-pager.pdf` 作为附件
- **简历**：`pitch-deck.pdf` + landing page 链接

## 部署 Landing Page 到 GitHub Pages

```bash
# 在 bj-pal 仓库根目录
mkdir -p docs
cp promo/landing-page.html docs/index.html
git add docs/index.html
git commit -m "添加 landing page"
git push
# Settings → Pages → Source: docs/ → Save
# 访问: https://estelledc.github.io/bj-pal/
```

## 重新生成 / 修改

每个 HTML 都是单文件可直接编辑。如果需要批量更新数据：

```bash
cd ~/intern-journal/explorations/open-design
# 编辑 prompts/bj-pal/<slug>.txt
# 串行重跑（避免并发失败）
bash run-bjpal.sh
# 重新截图
node screenshot-cards.mjs ./.od/projects/bjpal-03-xhs-carousel/index.html \
  ~/intern-journal/explorations/mini-apps/bj-pal/promo/xhs-png/
node screenshot-flex.mjs ./.od/projects/bjpal-01-pitch-deck/index.html \
  ~/intern-journal/explorations/mini-apps/bj-pal/promo/pitch-png 1920 1080 ".card, .slide, section"
node pdf-export.mjs promo/pitch-deck.html promo/pitch-deck.pdf A4 landscape
node pdf-export.mjs promo/one-pager.html promo/one-pager.pdf A4
```

## 元数据回填（决赛后）

| 物料 | 用了几次 | 评委反馈 | 复用价值 |
|---|---|---|---|
| pitch-deck | | | |
| landing-page | | | |
| xhs-carousel | | | |
| one-pager | | | |
| readme-hero | | | |

## 生成代价 + 教训

- **失败率**：3 并发 ~50%，串行 + 简化 skill ~10%
- **关键 fix**：避开复杂 skill (如 magazine-web-ppt 313 行 SKILL.md + 5 reference)，改用 article-magazine / card-xiaohongshu
- **关键 fix**：prompt 末尾明确"直接 Write index.html，不要等模板探索"
- **总耗时**：~80 分钟（含一次 daemon 重启 + 多次重试）
