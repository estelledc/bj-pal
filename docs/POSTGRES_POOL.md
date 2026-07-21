# PostgreSQL 连接池运行契约

BJ-Pal v6.29 为 durable planning-job store 增加同步 `psycopg_pool.ConnectionPool`。目标是限制单进程数据库连接和等待队列，在容量耗尽、连接失效与进程退出时给出可复核的失败语义；它不提供数据库 HA、自动 failover 或生产容量结论。

## 配置

选择 PostgreSQL store 后，DSN 仍只从环境读取，不写入日志、artifact 或 `repr`：

```bash
export BJ_PAL_JOB_STORE=postgres
export BJ_PAL_JOB_POSTGRES_DSN='postgresql://...'
export BJ_PAL_JOB_POSTGRES_SCHEMA=public

# 可选；以下是默认值
export BJ_PAL_JOB_POSTGRES_POOL_MIN_SIZE=1
export BJ_PAL_JOB_POSTGRES_POOL_MAX_SIZE=4
export BJ_PAL_JOB_POSTGRES_POOL_TIMEOUT_SECONDS=1
export BJ_PAL_JOB_POSTGRES_POOL_MAX_WAITING=8
```

安全范围：`min_size=0..64`、`max_size=1..64` 且 `min<=max`、获取超时 `0.05..60` 秒、等待者 `1..256`。非法或矛盾配置在连接前拒绝启动。实例总连接上限仍需按 `进程数 × max_size` 估算，并给迁移、管理连接和数据库保留容量；项目没有自动全局连接预算。

## 生命周期与失败语义

- 构造时先创建/校验 schema，再以 `open=False` 创建 pool，并用 `open(wait=True)` 验证最小连接。
- 每次借出连接前执行 `ConnectionPool.check_connection`。死连接不会交给 job transition；pool 尝试补充连接。
- transaction context 正常退出 commit，异常退出 rollback，然后把可用连接归还池。
- 等待队列已满、获取超时、pool 已关闭和连接失败分别转换为稳定 `JobStoreUnavailable`；错误不包含 DSN。
- FastAPI lifespan 在退出时关闭已创建的 job service；`run_job_worker.py` 在 `finally` 中关闭。SQLite store 实现同一 `close()` port，但不持有共享 handle。
- pool 关闭后不允许 reopen；新请求失败关闭。正在借出的连接要到归还后才真正关闭，因此部署方仍应先停止接流、等待在途请求，再结束进程。

## 受控验收

需要一套可丢弃的 PostgreSQL 17+ 实例：

```bash
BJ_PAL_TEST_POSTGRES_DSN='postgresql://...' make test-postgres-job-store PYTHON=python
BJ_PAL_TEST_POSTGRES_DSN='postgresql://...' make eval-postgres-pool PYTHON=python
```

第二条命令会创建随机 schema，执行池满 timeout、释放恢复、`pg_terminate_backend` 后换连接、64 次有限并发 readiness 查询和关闭后会话检查，随后删除该 schema。输出 `evals/results/postgres-pool-acceptance.json` 不保存 host、port、用户名、密码、DSN、schema 或 backend PID；独立 verifier 复算 SHA、raw latency、容量与关闭 gate。

2026-07-21 的 checked-in artifact 来自本机 Docker PostgreSQL 17.10：`max_size=2`，持满后排队请求 0.105 秒 timeout，额外等待者被拒绝且两类错误均未泄露 DSN；释放后恢复；被终止连接被替换；64/64 readiness 成功、并发 2、p95 2.593 ms；关闭前后应用会话均为 0。这只是一轮本机受控 acceptance，不是生产 benchmark、跨主机故障注入、数据库 failover、SLA 或真实用户证据。
