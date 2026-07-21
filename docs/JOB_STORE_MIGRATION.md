# Durable job store 迁移契约

BJ-Pal v6.28 提供 SQLite → PostgreSQL 的非破坏、停写窗口迁移。它解决“已有任务怎样切到 shared store”，不声称在线双写、零停机或跨区域数据库迁移。

## 安全前提

1. 停止所有会写入 SQLite job store 的 API 与 worker；`--confirm-source-quiesced` 是 operator attestation，不是进程发现机制。
2. 等待所有 `running` job 结束；工具会再次检查并拒绝迁移 running lease。`queued` job 会保留并在切换后继续处理。
3. 对 SQLite 文件做独立备份并记录恢复位置。工具不删除、重写或截断 source，但 receipt 也不证明外部备份存在。
4. PostgreSQL schema 必须为空。已有任意 job/event/admission/scheduler 行时失败关闭，不做 merge/upsert。
5. DSN 只通过 `BJ_PAL_JOB_POSTGRES_DSN` 注入，不能写进命令行、仓库或迁移 JSON。

WAL source 会被拒绝，必须先走显式 checkpointed backup 流程。当前工具只接受仓库现行四表 schema，不在跨数据库复制时顺便升级未知 legacy schema。

## 1. Dry-run

```bash
export BJ_PAL_JOB_POSTGRES_DSN='从 secret manager 注入'
export BJ_PAL_JOB_POSTGRES_SCHEMA='bj_pal'

.venv/bin/python scripts/migrate_planning_jobs.py \
  --source runtime/planning_jobs.db
```

dry-run 不创建 PostgreSQL schema，输出只包含文件名、表级 count/digest、running job 数、目标是否为空与 `ready_to_apply`；不输出 DSN、request/result/event payload 或本机绝对路径。

## 2. Apply

```bash
.venv/bin/python scripts/migrate_planning_jobs.py \
  --source runtime/planning_jobs.db \
  --apply \
  --confirm-cutover planning-jobs-sqlite-to-postgres \
  --confirm-source-quiesced
```

apply 的事务边界：

1. 先做 payload-minimized preflight；
2. SQLite `BEGIN IMMEDIATE` 阻止新的 writer，并复算 source snapshot；
3. PostgreSQL 获取与正常 job 写事务相同的 advisory lock；
4. 按稳定 sequence 复制 job、event、admission 与 tenant scheduler state；
5. 重置 PostgreSQL sequence，复算目标 count/digest；
6. 只有 source/target 完全一致时，才在同一 PostgreSQL 事务写入 append-only receipt 并提交。

任一表复制、约束、hash 或 receipt 步骤失败，整个 PostgreSQL 事务回滚；SQLite 始终保留。对同一未变化 source 的不确定重试会读取 receipt 并返回 `already_applied=true`，不会重复插入。

## 3. Verify 与切换

```bash
.venv/bin/python scripts/migrate_planning_jobs.py \
  --source runtime/planning_jobs.db \
  --verify-cutover
```

只有 `receipt_valid=true`、`source_matches_migration=true`、`target_matches_migration=true` 时，才把部署配置切为：

```bash
BJ_PAL_JOB_STORE=postgres
BJ_PAL_JOB_POSTGRES_DSN='从 secret manager 注入'
BJ_PAL_JOB_POSTGRES_SCHEMA='bj_pal'
```

重启 API/worker 后，以 `/readyz` 和一次 synthetic submit/worker lifecycle 做部署验收。迁移工具本身不修改环境变量、服务配置或进程状态。

## 4. Rollback 判定

刚完成迁移且两个 store 都未变化时，verify 返回 `rollback_safe=true`，可以把配置切回仍保留的 SQLite source。

PostgreSQL 一旦产生 claim、heartbeat、submit、result 或其他新事件，target digest 会漂移，verify 返回：

```text
rollback_safe=false
rollback_reason=store_drift_requires_forward_reconciliation
```

此时直接切回 SQLite 会丢失新状态，必须停写并设计 forward reconciliation；当前版本不会自动双向合并，也不会为了“能回滚”覆盖 source。

## 当前证据与边界

- PostgreSQL 17 集成测试覆盖完整 copy、row/event sequence、重复 apply、目标污染、running lease、并发 SQLite writer、append-only receipt、故障注入整事务回滚与切换后 rollback denial。
- count/digest 与 receipt 证明某次停写快照一致，不证明零停机、外部备份、跨主机网络分区、数据库 HA、RPO/RTO 或生产容量。
- source quiescence 仍依赖 operator；生产化需要部署编排器冻结入口、drain worker、持久化 cutover state，并把恢复演练接入真实托管数据库。
