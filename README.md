# TrafficManager

Centralized traffic policy and monthly reset management for multiple independent 3x-ui nodes. TrafficManager talks only to each node's authenticated HTTPS REST API; it never uses SSH, opens a remote SQLite database, or runs Xray.

## Architecture

One container runs a React SPA, FastAPI REST API, in-process scheduler/job executor, and SQLite. Node operations always pass through a capability-driven Modern/Legacy `ThreeXUIAdapter` and follow **probe → read → act → verify → audit**.

```text
Browser → FastAPI + React + Scheduler → Bearer HTTPS API → 3x-ui nodes
                    ↓
              /data/app.db
```

V1 deliberately uses one Uvicorn worker and supports only one application replica. Multiple replicas would run multiple schedulers; HA requires a future distributed lock and PostgreSQL.

## Quick start

Requirements: Docker Engine 24+ with the Compose plugin.

```bash
cp .env.example .env
openssl rand -base64 32
```

Copy the generated value into `APP_MASTER_KEY` in `.env`, then run:

```bash
docker compose up -d --build
docker compose ps
```

Open `http://SERVER:2083`. The first page requires creation of an administrator; there is no default password.

## 使用方法

源码仓库 [CodeAIX/trafficmanager](https://github.com/CodeAIX/trafficmanager) 和 GHCR 镜像均已公开。克隆源码或拉取镜像都不需要 GitHub 账号、Token 或 `docker login`。

### 直接使用 GitHub Container Registry 镜像

无需认证，直接拉取镜像：

```bash
docker pull ghcr.io/codeaix/trafficmanager:latest
```

创建持久化目录并生成主密钥：

```bash
mkdir -p trafficmanager/data
cd trafficmanager
openssl rand -base64 32 > master-key.txt
chmod 600 master-key.txt
```

启动容器：

```bash
docker run -d \
  --name trafficmanager \
  -p 2083:2083 \
  -e APP_MASTER_KEY="$(cat master-key.txt)" \
  -e APP_TIMEZONE=Asia/Shanghai \
  -e LOG_LEVEL=INFO \
  -v "$PWD/data:/data" \
  --restart unless-stopped \
  ghcr.io/codeaix/trafficmanager:latest
```

容器入口只在启动时使用 root 修正专用 `/data` 挂载目录的所有权，随后立即降权为 `fleet` 用户运行数据库迁移和应用。因此由宿主机 root 创建的 `./data` 可直接挂载，不需要执行 `chmod 777`。

打开 `http://服务器IP:2083` 创建首个管理员，然后进入 **Nodes → Add node** 添加 3x-ui 节点。Base URL 必须保留 3x-ui 的 WebBasePath，例如 `https://host:2053/abcdef`。

查看状态与日志：

```bash
docker ps --filter name=trafficmanager
docker logs -f trafficmanager
curl http://127.0.0.1:2083/health
```

更新镜像：

```bash
docker pull ghcr.io/codeaix/trafficmanager:latest
docker rm -f trafficmanager
# 再次执行上面的 docker run；必须继续使用原 master-key.txt 和 data 目录
```

### 从源码使用 Compose

公开仓库可匿名克隆，不需要 GitHub Token：

```bash
git clone https://github.com/CodeAIX/trafficmanager.git
cd trafficmanager
cp .env.example .env
# 将 openssl rand -base64 32 的输出写入 .env 的 APP_MASTER_KEY
docker compose up -d --build
```

数据库位于 `./data/app.db`。恢复数据库时必须同时恢复原 `APP_MASTER_KEY`；密钥丢失后，已保存的节点 Token 无法解密。

The Docker registry/repository portion of an image reference must be lowercase. This project uses `trafficmanager` consistently for the image, Compose service, and running container.

Build without Compose:

```bash
docker build -t trafficmanager:latest .
docker run -d --name trafficmanager -p 2083:2083 \
  -e APP_MASTER_KEY='YOUR_BASE64_KEY' \
  -v trafficmanager-data:/data \
  --restart unless-stopped \
  trafficmanager:latest
```

The image has a `/health` healthcheck. A healthy response is:

```json
{"status":"ok","database":"ok","scheduler":"ok"}
```

## Environment variables

| Variable | Default | Description |
|---|---:|---|
| `APP_MASTER_KEY` | generated in `/data/master.key` | AES-256-GCM key material for node tokens. Prefer a 32-byte base64 value supplied externally. |
| `APP_TIMEZONE` | `UTC` | Default display timezone only; policy schedules use their own IANA timezone. |
| `LOG_LEVEL` | `INFO` | Application log level. |
| `SESSION_SECURE` | `false` | Set `true` when this application is served through HTTPS. |

If `APP_MASTER_KEY` is omitted, a random 0600 key is generated at `/data/master.key`. Losing that file or the configured environment value makes saved 3x-ui tokens unrecoverable. Never change the key without re-entering every node token.

## Adding a node

Open **Nodes → Add node** and enter a name, optional remark, the full base URL, bearer API token, and TLS verification preference. Keep a configured 3x-ui WebBasePath in the URL, for example `https://host:2053/abcdef`; only a final slash is removed. The application rejects non-HTTP schemes.

Before saving, TrafficManager authenticates, reads `/panel/api/openapi.json`, detects Modern/Legacy capabilities, reads server/inbound/client data, and then performs the initial sync. Newly discovered clients default to **Observe**, so adding a node cannot unexpectedly reset existing users. Disabling TLS verification is node-specific and should be used only for controlled environments.

## Policies

A policy keeps quota and reset schedule separate. Quota is an integer byte count or unlimited. A reset can be disabled independently. Assignments resolve in this order:

```text
Client > Inbound > Node > Global
```

If a client belongs to multiple inbounds with different policies and has no client override, its effective policy is `POLICY_CONFLICT` and automatic execution stops for that client. Remote 3x-ui native reset settings are not changed; the Clients page warns when both native and Fleet resets are active.

Only **Managed** clients may be changed. **Observe** clients are synchronized and displayed but never mutated; **Ignore** clients stay outside normal management.

## Scheduling, timezones, and catch-up

Every policy stores a full IANA timezone and the scheduler stores UTC instants. For missing dates such as February 31, `LAST_DAY` uses the final day of that month and `SKIP` skips it. During a spring-forward gap, execution moves to the first valid local minute; a duplicated fall-back time uses its first occurrence.

Scheduled monthly jobs use `(policy_id, YYYY-MM, action)` uniqueness, making scheduler ticks and restarts idempotent. Catch-up is enabled by default for 168 hours. An occurrence outside that window is recorded as `MISSED` instead of being executed.

## Manual reset

**Reset traffic** clears per-client upload/download counters and does not alter quota or inbound aggregate counters. **Start new cycle** applies the effective quota, resets client traffic, and applies the policy reactivation rule. Both show a target preview before execution. Successful HTTP status alone is insufficient: the executor reads the client again and marks a non-zero result `VERIFY_FAILED`.

One offline node does not stop other targets. The overall result becomes `PARTIAL`, and **Retry Failed Items** creates a retry containing only failures. Job details retain per-client before/after counters and the Audit Log records every side effect without credentials.

## Backup and restore

Use **Settings → Download database backup** or stop the container and copy `data/app.db`. Restore the database only together with the exact same `APP_MASTER_KEY` or `/data/master.key`.

```bash
docker compose stop
cp backup/app.db data/app.db
docker compose start
```

Protect backups: node tokens remain encrypted, but operational metadata and client email addresses are present.

## Upgrade

Back up the database and master key, pull the new source/image, then recreate the single container:

```bash
docker compose down
docker compose build --pull
docker compose up -d
```

Alembic migrations run automatically before Uvicorn starts. Never run two versions against the same SQLite volume.

## Development and tests

Backend:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
DATA_DIR="$PWD/data" .venv/bin/pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm run build
```

The mock 3x-ui app covers bearer authentication, OpenAPI discovery, large counters, read-modify-write preservation, reset, and verification without a real VPS.

## Security notes

- Administrator passwords use Argon2id; sessions are HttpOnly and SameSite=Strict, and state changes require a CSRF token.
- Node bearer tokens use AES-256-GCM and API responses expose only `tokenConfigured`.
- Remote UUIDs, passwords, private keys, and full client payloads are not persisted.
- Keep TLS verification enabled and terminate TrafficManager itself behind HTTPS before setting `SESSION_SECURE=true`.
- Do not expose port 2083 directly to the Internet without HTTPS and appropriate network controls.

## Troubleshooting

- **Authentication failed (401/403):** create a current 3x-ui API token and update the node; auth errors are intentionally not retried.
- **Endpoint unsupported:** inspect the node's authenticated `/panel/api/openapi.json`; capability detection, not a version comparison, selects behavior.
- **Node offline/stale:** verify DNS, port, WebBasePath, TLS trust, and outbound access from the container. Last known state is retained.
- **Saved tokens cannot decrypt:** restore the original master key. Ciphertext cannot be recovered without it.
- **Container unhealthy:** run `docker logs trafficmanager` and `curl http://127.0.0.1:2083/health`; also verify `/data` is writable by the container.
- **`sqlite3.OperationalError: unable to open database file`:** pull the latest image and recreate the container. Current images automatically repair ownership of the dedicated `/data` mount before dropping to the unprivileged `fleet` user; do not use `chmod 777`.
- **Duplicate monthly run:** inspect Jobs; the database uniqueness constraint rejects a second job for the same policy cycle.

## Known V1 limitations

Single administrator, SQLite, one process/replica, no notifications, no traffic time-series, no remote client creation/deletion, and no automatic 3x-ui upgrade. Inbound aggregate reset is intentionally separate from monthly client reset. The UI is desktop-first but responsive.

## Screenshots

Screenshots are not bundled because first-run data contains environment-specific node names and email addresses. The UI includes Dashboard, Nodes, Clients, Policies, Jobs, Audit Log, and Settings views in light and dark themes.
