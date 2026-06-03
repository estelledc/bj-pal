# BJ-Pal 评审提交包

本目录为评审提交材料。

## 文件清单

| 文件 | 用途 |
|---|---|
| `TECHNICAL_REPORT.md` | 技术说明：项目目标、主链路、数据、评测、展示案例、限制和后续计划 |
| `FLOWCHART.md` | 流程图：主运行链路、换点链路、评测与案例筛选链路 |
| `LAUNCH.md` | 启动说明：依赖、环境变量、运行命令、常见问题 |
| `launch.sh` | 一键启动脚本，默认启动 Streamlit Web UI |
| `env.example` | `.env` 模板：LongCat / DPSK / Anthropic / mock |
| `showcase_test_cases.md` | 8 条展示案例的文档版 |
| `showcase_test_cases.json` | 8 条展示案例的结构化 JSON |

## 启动

在项目根目录执行：

```bash
cp docs/submission/env.example .env
# 填好 .env 里的 API key 后：
bash docs/submission/launch.sh
```

默认启动地址：

```text
http://localhost:8501
```

端口被占用时：

```bash
PORT=8502 bash docs/submission/launch.sh
```
