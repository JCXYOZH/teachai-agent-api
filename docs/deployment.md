# 生产部署

单机部署：一台 CentOS 7.9 服务器同时运行 Nginx、PostgreSQL 16 和两个 Docker 容器。公网入口只有 Nginx 的 80/443；容器端口 49153（前端）与 65534（后端）监听 `0.0.0.0`，便于内部调用与排障。

## 已确认的环境

| 项目 | 值                                                                            |
| --- |------------------------------------------------------------------------------|
| 域名 | `ai.offer.us.ci`（DNS 已就绪）                                                    |
| 证书 | Let's Encrypt，已复制到 `/etc/nginx/certs/teachai`（`fullchain.pem`、`privkey.pem`） |
| 生产数据库 | `teachai_agent`（PostgreSQL 16，宿主机）                                           |
| 资产目录 | `/data/TeachAIAgent`，已执行 `chown -R 10001:10001`                              |
| 后端端口 | 容器内 `0.0.0.0:65534`                                                          |
| 前端端口 | `0.0.0.0:49153`（host 网络，不做端口映射）                                              |
| 迁移 | 容器启动时自动执行 `alembic upgrade head`                                             |
| llama.cpp | `http://47.120.68.25:65535/v1` ，模型 `Qwen3.6-35B-A3B`                         |

## 架构

```text
浏览器 ──HTTPS──▶ Nginx (443, ai.offer.us.ci)
                    ├── /             → 127.0.0.1:49153  teachai-agent-web (Next.js standalone)
                    └── /api/ /docs/ /static/ → 127.0.0.1:65534  teachai-agent-api (FastAPI)
                                             │
                                             ├── PostgreSQL 16   127.0.0.1:5432 (宿主机)
                                             ├── /data/TeachAIAgent (bind mount)
                                             └── llama.cpp       http://47.120.68.25:65535/v1
```

同源部署是刻意选择：refresh cookie 为 `Path=/api/v1/auth`、CSRF cookie 为 `Path=/`，同源时不需要跨域放行，也不会被 SameSite 拦截。前端 `NEXT_PUBLIC_API_BASE_URL` 生产值为相对路径 `/api/v1`。

## 服务器准备

服务器不需要 Python 环境，后端依赖全部在镜像内。

```bash
sudo install -d -o 10001 -g 10001 -m 750 /data/TeachAIAgent
sudo chown -R 10001:10001 /data/TeachAIAgent      # 每当迁移开发测试数据后需再执行
```

证书文件复制到 `/etc/nginx/certs/teachai`：

- `/etc/nginx/certs/teachai/fullchain.pem` ← `ai_offer_us_ci_fullchain.pem`
- `/etc/nginx/certs/teachai/privkey.pem` ← `ai_offer_us_ci_private.key`

## 后端

```bash
cd /opt/teachai/teachai-agent-api
cp .env.production.example .env.production
# 填写 DATABASE_URL 密码、JWT_SECRET、AGENTSEND_API_KEY
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
- `APP_ENV=production` 时启动会强制校验：`JWT_SECRET` 非默认且至少 32 字符、`EMAIL_DELIVERY=agentsend`、`AGENTSEND_BASE_URL` 为 HTTPS、`ASSET_STORAGE_ROOT` 为绝对路径。任一不满足即启动失败，属预期行为。

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

# 1. 确认 worker 账号存在（主配置里写的 user nginx）
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
## 避坑环境问题：容器网络 DNS

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

镜像可以在开发机上通过远程 Docker（`DOCKER_HOST=tcp://10.250.91.121:2375`）构建，服务器只保留 compose 文件与 `.env.production`：

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

两个容器端口都可从外部直连，用于排障与内部调用。若不允许从外部访问，请在防火墙上限制来源：

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="49153" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="65534" protocol="tcp" accept'
sudo firewall-cmd --reload
```

注意：直连 65534 会绕过 TLS，refresh cookie 带 `Secure` 不会在 http 下生效，正常用户必须走 `https://ai.offer.us.ci`。

## 验收清单

1. `curl -fsS https://ai.offer.us.ci/api/v1/health` 返回 `status=ok`。
2. `curl -fsS https://ai.offer.us.ci/api/v1/health/ready` 返回数据库与存储均为 `ok`。
3. 浏览器登录后刷新页面仍保持会话（验证 refresh cookie 的 `Secure` + `Path`）。
4. 上传一个 20 MB 以上的 PDF，确认不返回 413。
5. 打开 `https://ai.offer.us.ci/docs` 能加载本地 Swagger UI，控制台无资源加载失败。
6. 触发一次大纲生成与课堂生成，确认轮询进度正常、无 401/500。
7. 生成一张配图并导出 PPTX，确认文件落在 `/data/TeachAIAgent` 且属主为 10001。

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