# 生产部署

单机部署：一台 CentOS 7.9 服务器（以 `node2` 示例）同时运行 Nginx、PostgreSQL 16 和两个 Docker 容器。公网入口只有海外中转机上 `frps` 的 80/443，经 frp 隧道透传到本机 Nginx；容器端口 49153（前端）与 65534（后端）只听内网，便于内部调用与排障。

## 环境准备

| 项目 | 值                                                                           |
| --- |-----------------------------------------------------------------------------|
| 域名 | `ai.offer.us.ci`（DNS 就绪）                                                    |
| 证书 | Let's Encrypt，certbot 在 `node2` 签发，同步到 `/etc/nginx/certs/teachai`（`fullchain.pem`、`privkey.pem`） |
| 生产数据库 | `teachai_agent`（PostgreSQL 16，宿主机）                                          |
| 资产目录 | `/data/TeachAIAgent`，已执行 `chown -R 10001:10001`                             |
| 后端端口 | 容器内 `0.0.0.0:65534`                                                         |
| 前端端口 | `0.0.0.0:49153`（host 网络，不做端口映射）                                             |
| 迁移 | 容器启动时自动执行 `alembic upgrade head`                                            |
| llama.cpp | `http://47.120.68.25:65535/v1` ，模型 `Qwen3.6-35B-A3B`                        |
| 公网接入 | 海外中转机 `47.243.194.102`（`frps`）→ `frp` 隧道 → `node2`（`frpc` + Nginx），见「公网接入链路」 |

## 架构

```text
浏览器 ──HTTPS──▶ frps 47.243.194.102:443      （海外中转机，只做 TCP 转发）
                    │ frp 隧道（7000）
                    ▼
              Nginx (443, ai.offer.us.ci)       （node2，TLS 终结）
                    ├── /             → 127.0.0.1:49153  teachai-agent-web (Next.js standalone)
                    └── /api/ /docs/ /static/ → 127.0.0.1:65534  teachai-agent-api (FastAPI)
                                             │
                                             ├── PostgreSQL 16   127.0.0.1:5432 (宿主机)
                                             ├── /data/TeachAIAgent (bind mount)
                                             └── llama.cpp       http://47.120.68.25:65535/v1
```

同源部署是刻意选择：refresh cookie 为 `Path=/api/v1/auth`、CSRF cookie 为 `Path=/`，同源时不需要跨域放行，也不会被 SameSite 拦截。前端 `NEXT_PUBLIC_API_BASE_URL` 生产值为相对路径 `/api/v1`。

## 公网接入链路（frp 隧道）

域名 `ai.offer.us.ci` 解析到海外中转机 `47.243.194.102`（阿里云，只跑 `frps`，不装 Nginx），`frp` 把 80/443 原样 TCP 透传到 `node2`，TLS、HSTS、Cookie 与全部 HTTP 转发逻辑都在 `node2` 的 Nginx 上，整条链路只进行两跳。

```text
浏览器 ──HTTPS──▶ 47.243.194.102  frps（80 / 443 按 remote_port 转发，控制端口 7000）
                    │ frp 隧道（token 认证）
                    ▼
               node2  frpc ──▶ 127.0.0.1:443  Nginx（TLS 终结，证书 /etc/nginx/certs/teachai）
                          └─▶ 127.0.0.1:80   Nginx（ACME 校验 + 301 跳 https）
```

### frps（`/usr/local/frp/frps.toml`，v0.67.0）

```toml
bindPort = 7000

auth.method = "token"
auth.token = "<FRP_TOKEN>"

# 80/443 由具体 TCP 代理按 remote_port 申请，不使用 vhost 模式。
allowPorts = [{ start = 80, end = 80 }, { start = 443, end = 443 }]
```

- v0.52 起只认 TOML：`[common]` + `bind_port` 这类 ini 语法会被直接拒绝，文件后缀改成 `.toml` 也不改变解析器。
- 不要保留 `vhostHTTPPort` / `vhostHTTPSPort`：它们会先占住 80/443，TCP 代理申请 `remote_port` 时报 `port already used`。
- 不启用 dashboard。临时需要时设 `webServer.addr = "127.0.0.1"` 并配账号密码，不要把 7500 放进安全组。
- 安全组只放行 22、80、443、7000，7000 的源地址限制到 `node2` 出口 IP。

### frpc（`node2`，`/usr/local/frp/frpc.toml`，v0.67.0）

```toml
serverAddr = "47.243.194.102"
serverPort = 7000

auth.method = "token"
auth.token = "<FRP_TOKEN>"

[[proxies]]
name = "teachai-edge-http"
type = "tcp"
localIP = "127.0.0.1"
localPort = 80
remotePort = 80

[[proxies]]
name = "teachai-edge-https"
type = "tcp"
localIP = "127.0.0.1"
localPort = 443
remotePort = 443
transport.proxyProtocolVersion = "v2"
```

- `transport.proxyProtocolVersion` 是代理级字段，写在 `[[proxies]]` 内，不是客户端级；位置写错不会报错，但也不生效。
- 只有 443 开 PROXY protocol：80 只承载 ACME 校验与 301，用不到真实 IP；两端都开也可以，但 Nginx 两个 `listen` 必须同步加 `proxy_protocol`，多一个失配点。
- 同机已有的其他项目代理不要改动，代理名不能重复。

### 为何必须开 PROXY protocol

TCP 透传后 Nginx 看到的 `$remote_addr` 恒为 `127.0.0.1`。这不只是日志问题：短信验证码按 IP 限流（`SMS_IP_LIMIT_PER_MINUTE` / `SMS_IP_LIMIT_PER_HOUR`）与聊天日配额都按 IP 计数，关掉等于全站共享一份额度。Nginx 侧对应：

```nginx
    listen 443 ssl http2 proxy_protocol;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;
```

两端必须同时生效：frpc 不发协议头而 Nginx 要求，会直接返回 400；反之取不到真实 IP。

### 上线顺序

1. `node2` 先只启动 80 代理，`curl -I http://ai.offer.us.ci` 应返回 `301` 到 https。
2. 确认证书仍在有效期（当前网站 `Feb  1 05:58:30 2027 GMT`）；临近到期先续期，见下节。
3. 再加 443 代理并给 Nginx 加 `proxy_protocol`，`nginx -t && nginx -s reload` 后 `curl -fsS https://ai.offer.us.ci/api/v1/health` 。
4. 清理历史：删除 `node2` 上残留的旧配置文件，确保 80 端口只有一份 `default_server` 声明。
5. 看 `access.log`：`$remote_addr` 已是真实公网 IP，而不是 `127.0.0.1`。

### 证书续期

证书在 `node2`，续期也由 `node2` 发起，走 80 端口的 HTTP-01：

```bash
certbot certonly --webroot -w /usr/local/nginx/html/certbot \
  --cert-name ai.offer.us.ci -d ai.offer.us.ci \
  --deploy-hook 'cp /etc/letsencrypt/live/ai.offer.us.ci/fullchain.pem /etc/nginx/certs/teachai/fullchain.pem && cp /etc/letsencrypt/live/ai.offer.us.ci/privkey.pem /etc/nginx/certs/teachai/privkey.pem && /usr/local/nginx/sbin/nginx -s reload'
```

`--deploy-hook` 负责把新证书同步到 `/etc/nginx/certs/teachai` 并 reload；漏掉这步会出现「续期成功但站点仍在用旧证书」。`certbot-renew.timer` 默认每天运行两次，确认已 `systemctl enable --now certbot-renew.timer`。

### 踩坑记录

- vhost 模式与 `remote_port` 模式互斥，换模式必须先删 vhost 配置再重启 `frps`。
- 公网入口必须保持同源：任何 `return 301` 跳到别的域名都会让 refresh/CSRF cookie 跨域失效。
- Nginx 的 80 与 443 server 块 `server_name` 都必须是 `ai.offer.us.ci`，否则请求落到 default server。
- `frp` 是纯 TCP 转发，不做任何缓冲；SSE 的缓冲控制由 Nginx 站点配置里的专用 `location` 负责。

## 服务器准备

服务器不需要 Python 环境，后端依赖全部在镜像内。

```bash
sudo install -d -o 10001 -g 10001 -m 750 /data/TeachAIAgent
sudo chown -R 10001:10001 /data/TeachAIAgent      # 每当迁移开发测试数据后需再执行
```

证书由 `node2` 上的 certbot 签发并续期，Nginx 读取的是同步副本：

- `/etc/nginx/certs/teachai/fullchain.pem`
- `/etc/nginx/certs/teachai/privkey.pem`

签发与自动同步见「公网接入链路」的「证书续期」。

## 后端

```bash
cd /opt/teachai/teachai-agent-api
cp .env.production.example .env.production
# 填写 DATABASE_URL 密码、JWT_SECRET、AGENTSEND_API_KEY、SPUG_URL
chmod 600 .env.production
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api
```

要点：

- 容器使用 `network_mode: host`，因为 PostgreSQL 在宿主机上；uvicorn 监听 `0.0.0.0:65534`，Nginx 通过 `127.0.0.1:65534` 回源。
- 启动顺序：`scripts/docker-entrypoint.sh` 先等待数据库（`scripts/wait_for_database.py`），再执行 `alembic upgrade head`，最后启动 uvicorn。数据库不可达时按 `DB_WAIT_TIMEOUT_SECONDS` 重试后退出，由 `restart: unless-stopped` 继续重试。
- 需要人工控制迁移时把 `RUN_MIGRATIONS` 设为 `0`，再单独执行：

```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

- 镜像内含 `HEALTHCHECK`，探测 http://127.0.0.1:65534/api/v1/health
- `APP_ENV=production` 时启动会强制校验：`JWT_SECRET` 非默认且至少 32 字符、`EMAIL_DELIVERY=agentsend`、`AGENTSEND_BASE_URL` 为 HTTPS、`SMS_DELIVERY=spug`、`SPUG_URL` 为 HTTPS、`ASSET_STORAGE_ROOT` 为绝对路径。任一不满足即启动失败，属预期行为。

- 短信注册验证码走 push.spug.cc 模板推送：`SPUG_URL` 形如 `https://push.spug.cc/send/<模板ID>`，模板 ID 属于凭证，只放 `.env.production`（`chmod 600`），不提交到 Git。修改模板或更换号码后可以先手工 `curl` 一次确认返回 200。
- 短信发送阈值可在 `.env.production` 调整：`SMS_IP_LIMIT_PER_MINUTE`（默认 3）、`SMS_IP_LIMIT_PER_HOUR`（默认 10）、`SMS_TARGET_LIMIT_PER_HOUR`（默认 5）；超限返回 `429 SMS_SEND_RATE_LIMITED`，前端会自动进入重发倒计时。
- 生产环境的 Provider Base URL 必须是 HTTPS；本地 llama.cpp 走明文时，需要在 `.env.production` 里显式放行，否则大纲/课堂生成会返回 `422 INSECURE_PROVIDER_URL`：

```text
PROVIDER_ALLOW_INSECURE_HTTP=47.120.68.25:65535
```

该项默认值为空，只放行 `host:port` 精确匹配的地址，不要填写网段或通配域名。

## 前端

```bash
cd /opt/teachai/teachai-agent-web
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f web
```

要点：

- `next.config.mjs` 使用 `output: 'standalone'`，运行镜像只带 `server.js`、`.next/static` 与 `public`。
- 字体已本地化：`app/fonts/*.woff2` 随仓库分发，`app/layout.tsx` 使用 `next/font/local`，构建过程不再访问 `fonts.googleapis.com`，服务器无需出网即可构建。
- `NEXT_PUBLIC_API_BASE_URL` 是构建期变量，通过 `docker-compose.prod.yml` 的 `build.args` 传相对路径 `/api/v1`；改动后必须重新构建镜像，重启容器无效。
- 运行镜像以非 root 用户 `10001:10001` 监听 `49153`，`HEALTHCHECK` 探测 `/login`。
- 使用 `network_mode: host`：默认服务器的 bridge 网络不可用（一般容器内无 DNS，且 `DOCKER` iptables 链缺失导致建网失败），host 网络同时省掉端口映射。

## Nginx（源码安装，前缀 `/usr/local/nginx`）

仓库提供两份文件：

- `deploy/nginx/nginx.conf`：主配置，安装为 `/usr/local/nginx/conf/nginx.conf`
- `deploy/nginx/teachai-agent.conf`：站点配置，安装为 `/usr/local/nginx/conf/conf.d/teachai-agent.conf`

```bash
# 0. 确认二进制带 SSL 模块
/usr/local/nginx/sbin/nginx -V 2>&1 | tr ' ' '\n' | grep -E 'http_ssl_module|http_v2_module'

# 1. 确认 worker 账号存在（主配置里用的 user nginx）
id nginx || sudo useradd -r -s /sbin/nologin nginx

# 2. 安装配置
sudo mkdir -p /usr/local/nginx/conf/conf.d /usr/local/nginx/html/certbot
sudo cp deploy/nginx/nginx.conf /usr/local/nginx/conf/nginx.conf
sudo cp deploy/nginx/teachai-agent.conf /usr/local/nginx/conf/conf.d/teachai-agent.conf
sudo sed -i 's|__CERT_DIR__|/etc/nginx/certs/teachai|g' /usr/local/nginx/conf/conf.d/teachai-agent.conf

# 3. 校验并平滑重启
sudo /usr/local/nginx/sbin/nginx -t
sudo /usr/local/nginx/sbin/nginx -s reload     # 首次启动用：sudo /usr/local/nginx/sbin/nginx
```

要点：

- 主配置里 `$connection_upgrade` 的 `map` 定义在 `http` 块，站点文件直接复用，不要重复定义。
- `client_max_body_size` 主配置给 16m，站点块提到 64m 以覆盖 50 MB 素材上传。
- `proxy_read_timeout 900s` 覆盖课堂生成与导出下载。
- 不要在 Nginx 再加 `X-Frame-Options`，前端由 Next 统一设置；HSTS 只在站点块的 443 server 里设置。
- 日志在 `/usr/local/nginx/logs/access.log`、`error.log`，不是 `/var/log/nginx`。
- 证书续期后执行 `sudo /usr/local/nginx/sbin/nginx -s reload`。
- 若 80 端口要留给 ACME 校验，`/.well-known/acme-challenge/` 走 `/usr/local/nginx/html/certbot`，用 Certbot `--webroot -w /usr/local/nginx/html/certbot` 签发。
- 443 server 块带 `proxy_protocol`（对应 frp 的 PROXY protocol v2）；换成直连或其他入口时必须同步去掉，否则 TLS 握手失败返回 400。
- 教师对话 SSE 走专用 `location`：`proxy_buffering off`、`proxy_cache off`、`gzip off`，其余 `/api/` 保持默认缓冲。
- 素材上传走 `location = /api/v1/assets`：`proxy_request_buffering off` 让 Nginx 边收边转给 API，`client_body_timeout` 提到 300s；`client_max_body_size`（64m）必须大于 `ASSET_MAX_BYTES`（50 MB），否则 Nginx 会先用自己的 413。
## 环境避坑：容器网络 DNS

服务器 Docker 默认 bridge 网络无法解析外部域名（`Temporary failure in name resolution`），但宿主网络正常。表现是镜像构建在 `pip install` / `npm ci` 阶段失败。

- 处理：两个 `docker-compose.prod.yml` 的 `build.network` 都设为 `host`，构建阶段直接用宿主 DNS。
- 运行期同样不受影响：后端与前端容器都使用 `network_mode: host`，不创建 bridge 网络，也不写入 `DOCKER` iptables 链。
- 若在机器上加回任何 bridge 网络，会再次遇到 `Failed to Setup IP tables: Unable to enable SKIP DNAT rule`，修复方式见下。
- 若要彻底修复（避免以后任何容器内出网都失败），在服务器上配置 Docker DNS：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{ "dns": ["223.5.5.5", "114.114.114.114"] }
JSON
sudo systemctl restart docker
```

改完可用 `docker run --rm python:3.8-slim python -c "import socket;print(socket.gethostbyname('pypi.org'))"` 验证。

### iptables 链缺失

报错形如 `failed to create network teachai-agent-web_default: Unable to enable SKIP DNAT rule: iptables: No chain/target/match by that name`。原因是 firewalld 重启或 reload 清空了 Docker 管理的 `DOCKER` 链，守护进程没有重建。修复：

```bash
sudo systemctl restart firewalld   # 若启用了 firewalld
sudo systemctl restart docker      # 重建 DOCKER / DOCKER-USER 链
sudo iptables -t nat -L DOCKER -n | head
```

重启 docker 会让运行中的容器按 `restart: unless-stopped` 重新拉起。
## 用已构建镜像启动

镜像可以在开发机上通过远程 Docker（`DOCKER_HOST=tcp://10.121.124.121:2375`）构建，服务器只保留 compose 文件与 `.env.production`：

```bash
cd /opt/teachai/teachai-agent-api
docker compose -f docker-compose.prod.yml up -d --no-build

cd /opt/teachai/teachai-agent-web
docker compose -f docker-compose.prod.yml up -d --no-build
```

`--no-build` 保证不会在服务器上重新执行 `pip` / `npm ci`。镜像 tag 固定为 `teachai-agent-api:latest` 与 `teachai-agent-web:latest`，升级时重新构建同名 tag 后再次 `up -d --no-build` 即可（`docker compose` 会按镜像 ID 变化重建容器）。

开发机上的构建命令（需要保留 `--network host`，否则服务器 bridge DNS 无法解析 PyPI / npm）：

```bash
docker build --network host -t teachai-agent-api:latest ./teachai-agent-api
docker build --network host -t teachai-agent-web:latest \
  --build-arg NEXT_PUBLIC_API_BASE_URL=/api/v1 ./teachai-agent-web
```

## 端口暴露

容器端口监听在宿主机 `0.0.0.0`，仅用于排障与内网调用；公网入口只有 `frps` 的 80/443。若不允许内网之外的机器直连，请在防火墙上限制来源：

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="49153" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="65534" protocol="tcp" accept'
sudo firewall-cmd --reload
```

注意：直连 65534 会绕过 TLS，refresh cookie 带 `Secure` 不会在 http 下生效，正常用户必须走 `https://ai.offer.us.ci`。

## 生成任务队列

大纲、课堂、旁白、配图与导出的生成任务不再交给 FastAPI `BackgroundTasks`，而是先写入 `generation_jobs`
表，再由进程内的 worker 池认领执行（`app/services/generation_queue.py`，与 API 同进程）。

- 认领：`SELECT ... WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`，
  同一事务内置 `running`，因此多副本不会重复执行同一个任务，也无需 Redis。
- 重启恢复：启动时把遗留 `running` 任务重新入队并给 `payload.attempts` 加一；只有超过
  `GENERATION_JOB_MAX_ATTEMPTS` 才置 `failed`（`GENERATION_INTERRUPTED`）。
- 崩溃兜底：每 60 秒扫描一次 `running` 且 `updated_at` 超过 `GENERATION_JOB_STALE_SECONDS` 的任务，
  回队列或按尝试次数置失败的终态。
- 单副本（当前部署）：`GENERATION_JOB_STARTUP_GRACE_SECONDS=0`，重启后立即回收；多副本滚动重启时
  把它设成大于最长任务时长（例如 1800），避免抢走其他副本仍在执行的任务。
- 回退：`GENERATION_QUEUE_ENABLED=false` 退回 `BackgroundTasks`，仅用于单进程调试。
- 队列复用现 `generation_jobs` 表，不新增迁移；升级时仍需先 `alembic upgrade head`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GENERATION_QUEUE_ENABLED` | `true` | 是否启用进程内队列 |
| `GENERATION_WORKER_CONCURRENCY` | `1` | 并发任务数（1–8）；llama.cpp 单实例与图片供应商限流下不建议调高 |
| `GENERATION_QUEUE_POLL_SECONDS` | `1` | 空闲时的轮询间隔（0.1–30） |
| `GENERATION_JOB_STALE_SECONDS` | `1800` | `running` 任务判定为超时的秒数（60–86400） |
| `GENERATION_JOB_STARTUP_GRACE_SECONDS` | `0` | 启动回收的宽限秒数（0–86400） |
| `GENERATION_JOB_MAX_ATTEMPTS` | `2` | 单个任务最多执行次数（1–5） |

配图任务按 `IMAGE_CONCURRENCY`（默认 `2`，范围 1–6）并行执行；免费图片供应商按 IP 限流，调高前先确认配额，否则会集中触发 `IMAGE_PROVIDER_RATE_LIMITED`。

Pollinations 免费层默认按 `IMAGE_POLLINATIONS_MIN_INTERVAL_SECONDS`（默认 45 秒，范围 0–300）在进程内串行；配置 `IMAGE_POLLINATIONS_TOKEN` 后可设为 0。多副本部署时各进程独立计数，生产建议配置 Token 或改用 Qwen。

教师对话使用 SSE 流式返回；`frp` 是 TCP 透传不做缓冲，Nginx 侧由站点配置的专用 `location` 关闭 `proxy_buffering` / `proxy_cache` 与 `gzip`。后端同时返回 `X-Accel-Buffering: no` 作为兜底。

## 验收清单

1. `curl -fsS https://ai.offer.us.ci/api/v1/health` 返回 `status=ok`。
2. `curl -fsS https://ai.offer.us.ci/api/v1/health/ready` 返回数据库与存储均为 `ok`。
3. 浏览器登录后刷新页面仍保持会话（验证 refresh cookie 的 `Secure` + `Path`）。
4. 上传一个 20 MB 以上的 PDF，确认不返回 413。
5. 打开 `https://ai.offer.us.ci/docs` 能加载本地 Swagger UI，控制台无资源加载失败。
6. 触发一次大纲生成与课堂生成，确认轮询进度正常、无 401/500。
7. 生成一张配图并导出 PPTX，确认文件落在 `/data/TeachAIAgent` 且属主为 10001。
8. `curl -I http://ai.offer.us.ci/api/v1/health` 返回 301 到 https，说明 80 透传与站点跳转都生效。
9. `access.log` 中 `$remote_addr` 是真实公网 IP（证明 PROXY protocol 生效），此时短信按 IP 限流才会按真实来源计数。
10. 教师对话逐字输出、不在中途整段出现，说明 SSE 未被缓冲。
11. 隧道空闲 30 分钟后仍能打开首页与 `/api/v1/health`。
12. 上传 55 MB 文件：浏览器端直接提示超过上限且不发请求；用 `curl` 直连 API 时在读完请求体前就返回 `413 FILE_TOO_LARGE`。

## 升级与回滚

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

- 回滚镜像：升级前把当前镜像另打标签（`docker tag teachai-agent-api:latest teachai-agent-api:previous`），回滚时改 compose 的 `image` 或 `docker tag previous latest` 后 `up -d --no-build`。
- 回滚数据库：迁移只做向前兼容的加法时可直接回退应用；涉及删列或改类型前必须先备份，必要时 `alembic downgrade -1`。

## 备份

- 数据库：`pg_dump -Fc -h 127.0.0.1 -U postgres teachai_agent > teachai_agent-$(date +%F).dump`。
- 资产：`rsync -a --delete /data/TeachAIAgent/ /backup/TeachAIAgent/`。
- 两者必须同一时间点备份，否则会出现元数据指向缺失文件。

## 日志

- 容器：`docker compose -f docker-compose.prod.yml logs --tail 200 api`，json-file 驱动已限制为 10 MB × 5。
- Nginx：`/usr/local/nginx/logs/access.log`、`error.log`。
- 后端每个请求都有 `request_id`，可与 `app.core.middleware` 日志对应。
- 模型调用与生成任务的耗时/失败率由 `app/core/metrics.py` 在进程内计数：每次调用写一条 `llm_call_completed provider=... model=... outcome=ok|empty|timeout|error duration_ms=...`，每次任务结束写一条 `generation_job_finished job_id=... kind=... outcome=... duration_ms=...`；并按 `METRICS_SNAPSHOT_SECONDS`（默认 300 秒，0 关闭）输出聚合行 `metrics_snapshot group=llm|generation_job key=... calls=... failures=... failure_rate=... avg_ms=... max_ms=...`。计数随进程重启清零，副本之间不聚合，跨实例统计按日志行以 `key` 分组。
