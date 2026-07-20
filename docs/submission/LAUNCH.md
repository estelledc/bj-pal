# BJ-Pal 启动说明

以下命令均在项目根目录执行。

## 1. 环境要求

- Python 3.11+
- macOS / Linux shell
- 根目录 `.env`

依赖安装：

```bash
make setup PYTHON=python3.11
```

首次运行或数据索引需要刷新时：

```bash
make bootstrap-demo PYTHON=.venv/bin/python
```

## 2. 配置 LLM API

项目读取根目录 `.env`，与 `README.md`、`requirements.txt` 同级。不要放在 `docs/submission/`。

复制模板：

```bash
cp docs/submission/env.example .env
```

LongCat：

```dotenv
BJ_PAL_LLM=longcat
LONGCAT_API_KEY=你的_api_key
LONGCAT_BASE_URL=https://api.longcat.chat/anthropic
BJ_PAL_LONGCAT_MODEL=LongCat-2.0-Preview
```

DPSK / DeepSeek：

```dotenv
BJ_PAL_LLM=dpsk
DPSK_API_KEY=你的_api_key
DPSK_BASE_URL=你的_base_url
DPSK_MODEL=你的_model
DPSK_MAX_TOKENS=8192
```

`DPSK_MODEL` 必须显式配置，应用不会静默选择模型档位。2026-07-20 的同场景单样本中，`deepseek-v4-pro` 首轮通过 strict contract，`deepseek-v4-flash` 两轮后仍被拒绝；当前只将 pro 作为下一轮有界试验的优先项，不把 1:1 样本写成成功率、延迟分布或成本结论。

Anthropic：

```dotenv
BJ_PAL_LLM=anthropic
ANTHROPIC_API_KEY=你的_api_key
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

mock 离线模式，不需要 API key：

```bash
BJ_PAL_LLM=mock bash docs/submission/launch.sh
```

检查配置，不启动 Streamlit：

```bash
DRY_RUN=1 bash docs/submission/launch.sh
```

期望输出：

```text
backend: longcat
llm api: LONGCAT_API_KEY configured
dry run: launch script configuration is valid
```

脚本只检查变量是否存在，不输出密钥值。

## 3. 一键启动 Web UI

```bash
bash docs/submission/launch.sh
```

脚本流程：

1. 进入项目根目录。
2. 如存在 `.env`，加载环境变量。
3. 检查当前 LLM 后端需要的 API key。
4. 运行 `python3 src/loader.py`，然后启动 Streamlit。

默认打开：

```text
http://localhost:8501
```

指定端口：

```bash
PORT=8502 bash docs/submission/launch.sh
```

跳过数据索引刷新：

```bash
SKIP_INDEX=1 bash docs/submission/launch.sh
```

只检查启动脚本配置，不真正启动 Streamlit：

```bash
DRY_RUN=1 bash docs/submission/launch.sh
```

## 4. 手动启动命令

```bash
python3 src/loader.py
python3 -m streamlit run src/ui/app.py
```

使用真实 LongCat：

```bash
BJ_PAL_LLM=longcat python3 -m streamlit run src/ui/app.py
```

使用 mock：

```bash
BJ_PAL_LLM=mock python3 -m streamlit run src/ui/app.py
```

## 5. 演示流程

1. 打开 Web UI。
2. 在右侧输入活动片区，例如 `五道营-雍和宫片区` 或 `三里屯片区V3`。
3. 输入偏好/禁忌，例如 `乳糖不耐受，喜欢安静，带 5 岁娃，不吃辣`。
4. 点击生成今日安排。
5. 在时间线里点击 `换一个`，检查 reroute 是否避开已换过的地点。
6. 查看地图上的顺序标记和路线。
7. 打开左侧记忆面板，看偏好是否沉淀。

## 6. 常见问题

### 端口被占用

```bash
PORT=8502 bash docs/submission/launch.sh
```

### 出现 NotOpenSSLWarning

macOS 系统 Python 可能出现 urllib3 的 LibreSSL warning。这个 warning 不影响本地 Streamlit 启动；如果要消除，可以换成 pyenv/conda 的 OpenSSL Python。

### 真实 API 慢或限流

临时切 mock：

```bash
BJ_PAL_LLM=mock bash docs/submission/launch.sh
```

### 提示缺少 API key

错误示例：

```text
missing required env: LONGCAT_API_KEY
```

检查根目录 `.env` 和当前路径：

```bash
pwd
ls -la .env
DRY_RUN=1 bash docs/submission/launch.sh
```

### 需要重新跑展示案例筛选

```bash
python3 scripts/select_showcase_cases.py \
  --backend longcat \
  --limit 40 \
  --select 8
```
