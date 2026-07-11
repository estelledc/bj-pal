# BJ-Pal 宣传物料

> 美团黑客松 2026 / deadline 6/06
> 用 Open Design 本机 Claude Code 自动生成；2026-05-30 更新为黑客松科技风
> 当前作者栏：Jason Xun · KeepL

## 物料清单

| 文件 | 用途 | 尺寸 | 大小 |
|---|---|---|---|
| `pitch-deck.html` | 决赛路演幻灯片（10 张横屏 / 科技风） | 1920×1080 ×10 | 32KB+ |
| `pitch-deck.pdf` | PDF 备份 / U 盘 | 16:9 横向 | — |
| `landing-page.html` | 项目案例主页（GitHub Pages / 响应式编辑风） | 响应式长页 | — |
| `xhs-carousel.html` | 小红书图文（9 张 / 深色卡片） | 1080×1440 ×9 | 24KB+ |
| `one-pager.html` | A4 单页技术摘要（科技风） | 1240×1754 | 23KB+ |
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

## 当前内容口径（v3.1）

- 数据：`5,656` 北京 POI / `8,666` UGC aspects / `5,198` POI 信号网 / `1,892` routes
- 算法：`ToT` / `OPTW` / `Kemeny+Borda`
- 评测：L3 `100 case × 5 信号 = 280/280`
- 校准：Global ECE `0.1089`
- 作者：`Jason Xun · KeepL`

### 决赛路演当天

- **主屏幕**：`pitch-deck.html` 用 Chrome 全屏（F11），方向键翻页
- **PDF 备份**：`pitch-deck.pdf` 拷 U 盘，应付现场设备故障
- **桌摆 A4**：`one-pager.pdf` 用于现场快速说明
- **答辩补料**：`pitch-deck.html` 第 7-8 页（算法 + Demo）随时回跳
- **架构问答**：`architecture.md` 在 GitHub 渲染 mermaid 图，手机上随手打开

### 引流期（决赛前 1-2 周）

- **小红书图文笔记**：用 `xhs-png/` 9 张 PNG；发布文案在公开内容计划中单独维护
- **GitHub README**：把 `hero-png/01.png` commit 到 bj-pal 仓库 `assets/hero.png`，README 顶部 `![](assets/hero.png)`
- **Landing Page 部署**：`landing-page.html` deploy 到 GitHub Pages（见下文）
- **朋友圈**：landing page 截图 + 链接 + 一句话钩子

### 决赛后

- **回顾文章**：`one-pager.pdf` 作为附件
- **简历**：`pitch-deck.pdf` + landing page 链接

## 构建与部署 Landing Page

```bash
# 在 bj-pal 仓库根目录构建本地发布目录
bash scripts/build_showcase.sh

# 本地预览
python3 -m http.server 8000 --directory _site
```

合并到 `main` 后，`.github/workflows/pages.yml` 会构建 `_site/` 并部署到
`https://estelledc.github.io/bj-pal/`。首次使用时需在仓库 Settings → Pages
中将 Source 设为 **GitHub Actions**。

## 重新生成 / 修改

每个 HTML 都是单文件可直接编辑。截图生成器不随本仓库发布；无论使用哪种工具，输入和输出都应保持为仓库相对路径：

```bash
# HTML 改版后重新导出对应 PNG/PDF，避免衍生物过期
<screenshot-command> promo/xhs-carousel.html promo/xhs-png/
<screenshot-command> promo/pitch-deck.html promo/pitch-png/
<pdf-export-command> promo/pitch-deck.html promo/pitch-deck.pdf 16:9
node pdf-export.mjs promo/one-pager.html promo/one-pager.pdf A4
```

## 元数据回填（决赛后）

| 物料 | 用了几次 | 现场反馈 | 复用价值 |
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
