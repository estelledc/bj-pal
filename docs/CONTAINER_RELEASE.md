# Public container release contract

BJ-Pal 的公开容器是可复现作品集运行面，不是在线业务服务。正式 `vMAJOR.MINOR.PATCH` tag 会触发 `.github/workflows/publish-container.yml`，只有下面的前置验收全部通过才会推送到 GHCR：

1. tag 与 `pyproject.toml`、`src/version.py` 的版本精确一致；
2. 镜像只从公开仓库上下文构建，不传入 provider、control-plane 或部署凭证；
3. 容器使用固定 UID/GID `10001`，在只读根文件系统、临时 `runtime/` 与 `/tmp`、无 Linux capability 和 `no-new-privileges` 下启动；
4. `/healthz`、`/readyz`、公开 OpenAPI 精确 allowlist、固定 synthetic planning request、无 feedback capability 和 demo/security headers 全部通过；
5. 冒烟通过后才登录 GHCR，并同时推送 release tag、commit SHA tag 和 `latest`；workflow summary 保存 registry 返回的 digest。

## v6.26.0 发布证据

- Release：<https://github.com/estelledc/bj-pal/releases/tag/v6.26.0>
- Workflow：<https://github.com/estelledc/bj-pal/actions/runs/29803766469>
- Release tag：`ghcr.io/estelledc/bj-pal:v6.26.0`
- Commit tag：`ghcr.io/estelledc/bj-pal:sha-fda394414a42`
- Digest：`sha256:a4242a80e0952f10ebc03ee61014465c38b17353a690b4960e6cdcc1fb117051`

workflow 中的 tag/version gate、credential-free build、hardened public-demo smoke、三次 push 与 digest summary 全部成功。另以未登录 registry token 请求读取 `v6.26.0` manifest，得到 HTTP 200 和同一 digest；这证明 anonymous pull metadata 可达，不证明长期可用性或 HTTPS 托管。

## v6.25.0 历史发布证据

- Release：<https://github.com/estelledc/bj-pal/releases/tag/v6.25.0>
- Workflow：<https://github.com/estelledc/bj-pal/actions/runs/29801249729>
- Release tag：`ghcr.io/estelledc/bj-pal:v6.25.0`
- Commit tag：`ghcr.io/estelledc/bj-pal:sha-d8bba81d29bf`
- Digest：`sha256:7ee708a056d1a9797af4739ec876c346a17f282d902b386c171efe2e7235861d`

workflow 与匿名 manifest 验证均已通过；该 digest 是 v6.25 历史证据，不是当前 release。

## v6.24.0 历史发布证据

- Release：<https://github.com/estelledc/bj-pal/releases/tag/v6.24.0>
- Workflow：<https://github.com/estelledc/bj-pal/actions/runs/29798918826>
- Release tag：`ghcr.io/estelledc/bj-pal:v6.24.0`
- Commit tag：`ghcr.io/estelledc/bj-pal:sha-a981f7173a77`
- Digest：`sha256:b40f592eed1ea2407ebaecdb51f828dafc0d74679b5be68ab54564a308e4d235`

workflow 中的 tag/version gate、credential-free build、hardened container smoke、三次 push 与 digest summary 全部成功。另以未登录 registry token 请求读取 `v6.24.0` manifest，得到 HTTP 200 和同一 digest；这证明 anonymous pull metadata 可达，不证明长期可用性。

## 本地拉取

发布后使用固定 release tag；审计或长期引用优先使用 workflow 给出的 digest：

```bash
docker pull ghcr.io/estelledc/bj-pal:v6.26.0
docker compose -f compose.public.yaml up -d
python scripts/smoke_deployed_api.py \
  --base-url http://127.0.0.1:8000 \
  --expected-version 6.26.0
```

Compose 默认只绑定 `127.0.0.1`，使用 mock LLM、bundled synthetic data 和易失 `runtime/`。它不会读取本机 CSSwitch、DeepSeek Key 或历史 SQLite 数据；重启后运行期状态可以丢失。

从 v6.25.0 开始，容器默认命令只启动 [hosted public demo contract](PUBLIC_DEMO.md)；完整 control plane 仍保留在 `http_api.app:app`，但不能直接作为无网关公网入口。

## 明确不证明的内容

- GHCR 镜像可拉取、可启动，不等于有公网 API、TLS、反向代理或 SLA。
- 单个 GitHub-hosted runner 的容器冒烟不等于多架构、多实例或长期稳定性验证。
- `latest` 便于体验但可变；复现证据必须绑定 release tag、SHA tag或 digest。
- 镜像仍使用单进程 FastAPI + SQLite，不适合开放互联网写流量。
- OCI license 标记为 `NOASSERTION`；仓库未选择许可证前，公开可见不等于获得复用授权。
