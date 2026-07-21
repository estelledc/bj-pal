# Hosted public demo contract

BJ-Pal 的 hosted demo 是一个面向面试官和代码审阅者的有界、mock-only HTTPS 入口。它不是生产服务，也不调用 DeepSeek、天气或预订供应商。

## 公开能力

容器默认启动 `http_api.public_server`，OpenAPI 只保留：

- `GET /healthz`
- `GET /readyz`
- `POST /v1/plans`

同时保留 `/openapi.json` 和 `/docs` 作为可读契约。durable jobs、operation、trial、feedback summary、feedback capability 和 clarification continuation 都不会注册到公网 app；未知路径返回 404。公网 planning 不接受 `user_id` 或 trial capability；成功计划只使用 bundled `demo/synthetic` 数据，不生成 feedback capability。需要澄清的输入返回 409 和结构化问题，但不落 continuation 数据。

## 启动时失败关闭

公网入口只有同时满足以下条件才会启动：

1. `BJ_PAL_LLM=mock`；
2. 环境中不存在 DeepSeek、LongCat、Anthropic、Open-Meteo 或 BJ-Pal control-plane 凭证；
3. 数据 manifest 和只读 SQLite 通过一致性检查，且精确标记为 `demo`、`synthetic`、`public_reproducible=true`。

因此本机 CSSwitch / DeepSeek Key 不属于部署输入，也不能通过误设环境变量切换成真实 provider。

## 滥用与费用边界

`POST /v1/plans` 在 schema validation 和 planner 之前执行以下进程级门禁：

| 变量 | 默认值 | 作用 |
|---|---:|---|
| `BJ_PAL_PUBLIC_DEMO_REQUESTS_PER_WINDOW` | 20 | 一个进程在窗口内接受的原始 plan attempt 数 |
| `BJ_PAL_PUBLIC_DEMO_WINDOW_SECONDS` | 60 | 固定窗口秒数 |
| `BJ_PAL_PUBLIC_DEMO_MAX_CONCURRENT_PLANS` | 2 | 同时执行的 plan 上限 |
| `BJ_PAL_PUBLIC_DEMO_MAX_BODY_BYTES` | 8192 | 请求体硬上限 |

限流是 aggregate/process-local，故意不信任 `X-Forwarded-For`，避免调用方伪造身份。它适合单实例、零 provider 费用的作品集 demo；多实例部署仍需由可信网关或共享存储实现全局配额。429/503 带 `Retry-After`，所有响应带 `X-BJ-Pal-Demo-Mode: synthetic-mock`、`X-Request-ID`、`Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`。

## 数据与持久性

- 计划读取镜像内的 synthetic SQLite 数据。
- 公网 app 不签发或写入 feedback、trial、job、operation、clarification continuation。
- 工具诊断若启用只能写到容器的临时 `runtime/`；默认 trace 为 off。
- 推荐运行时使用 read-only root filesystem，并把 `/app/runtime` 与 `/tmp` 挂成有界 tmpfs；`compose.public.yaml` 和 OCI workflow 已固定该约束。

## 托管平台契约

平台只需要运行公开 OCI 镜像、把外部 HTTPS 转发到容器的 `PORT`，并对 `/readyz` 做 readiness probe。launcher 与镜像 healthcheck 使用同一个 `PORT`，接受 `1..65535`，且不信任 forwarded headers。平台不得注入 provider/control credential，也不得覆盖默认命令为 `http_api.app:app`。

部署后执行：

```bash
.venv/bin/python scripts/smoke_deployed_api.py \
  --base-url https://你的域名 \
  --expected-version 6.29.0
```

smoke 会核对 health、readiness、精确 OpenAPI allowlist、synthetic plan、无 feedback capability、request ID 与 demo/security headers。它不证明长期 availability、SLA、跨实例限流、真实 provider 或真实用户结果。

## 当前证据状态

截至 2026-07-21，`v6.29.0` 的 [PR #23](https://github.com/estelledc/bj-pal/pull/23)、[main Core](https://github.com/estelledc/bj-pal/actions/runs/29814189389)、[Pages](https://github.com/estelledc/bj-pal/actions/runs/29814189487) 与 [OCI workflow](https://github.com/estelledc/bj-pal/actions/runs/29814435853) 全部成功；hardened container 在 registry 登录前通过精确 public contract smoke。release/SHA/latest 与公开 manifest 共同绑定 digest `sha256:1e3d07cdb4a77e36ec1c29096f8e32ba73bafc9ae31a19d6bbff970e42c432d6`。

仓库仍没有可用托管平台 CLI、平台环境变量或 GitHub deployment secrets。公开镜像可以独立验收，但在产生长期 HTTPS URL 与外部 smoke receipt 前，不能声称已经正式 hosted。短期 tunnel 只能证明某次外部可达，不能替代长期托管身份；本轮 Cloudflare Quick Tunnel 在生成 URL 前被本机终端策略以 SIGKILL 终止。
