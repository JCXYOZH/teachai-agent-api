# TeachAI Agent API

TeachAIAgent 的后端服务。阶段 1 已完成用户认证、数据库基线与运行时健康检查；阶段 2 已实现课程、文件夹、本地素材存储、文档解析与中文检索；阶段 3 已实现用户级 Provider 配置、真实连接测试与版本化课程大纲生成；阶段 4 已实现基于大纲的课堂 DSL 生成、异步任务轮询与邮箱验证码注册；阶段 6A 已加入按场景的旁白合成、音频资产与鉴权播放；阶段 6B 已加入课堂配图、Qwen Image 主通道与 Pollinations 无 Key 兜底。

## 技术栈

| 组件 | 版本 |
| --- | --- |
| Python | 3.8.9 |
| FastAPI | 0.124.4 |
| Starlette | 0.41.3 |
| Pydantic | 2.9.2 |
| SQLAlchemy | 2.0.36 |
| asyncpg | 0.29.0 |
| Alembic | 1.13.3 |
| LangChain | 0.1.16 |
| langchain-openai | 0.1.3 |
| Swagger UI | 5.17.14（本地静态资源） |
| PostgreSQL | 16 |


## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

启动后访问：http://127.0.0.1:8000/docs <br>
开发数据库为 teachai_agent，测试数据库为 teachai_agent_test 。

## API 文档

`/docs` 使用自托管的 Swagger UI，不依赖 jsDelivr 等外部 CDN。静态资源位于 `app/static/swagger-ui`，来自 `swagger-ui-dist@5.17.14`。

## 资产存储

上传资产使用本地磁盘，默认根目录为 `E:\Backups\Data\TeachAIAgent`，可通过 `ASSET_STORAGE_ROOT` 覆盖。PostgreSQL 保存资产元数据、所有权与解析文本，原始文件字节不进入数据库。目录不会通过 `/static` 暴露，下载必须经过鉴权 API。

详细目录布局、备份要求和安全约束见 `docs/asset-storage.md`。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/auth/register/send-code` | 发送注册邮箱验证码，返回 202 |
| POST | `/api/v1/auth/register` | 校验验证码并创建用户，建立会话 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/refresh` | 轮换 refresh token |
| POST | `/api/v1/auth/logout` | 撤销当前会话 |
| GET | `/api/v1/auth/me` | 当前用户 |
| GET | `/api/v1/health` | 存活检查 |
| GET | `/api/v1/health/ready` | 数据库就绪检查 |
| GET/POST | `/api/v1/folders` | 文件夹列表与创建 |
| PATCH/DELETE | `/api/v1/folders/{id}` | 文件夹更新与删除 |
| GET/POST | `/api/v1/courses` | 课程列表与创建 |
| GET/PATCH/DELETE | `/api/v1/courses/{id}` | 课程详情、更新与软删除 |
| GET/POST | `/api/v1/assets` | 素材列表与上传解析 |
| GET/PATCH/DELETE | `/api/v1/assets/{id}` | 素材详情、编辑与软删除 |
| GET | `/api/v1/assets/{id}/download` | 鉴权下载原文件 |
| GET | `/api/v1/search` | 课程与素材中文检索 |
| GET | `/api/v1/providers` | Provider 列表与当前用户配置状态 |
| GET | `/api/v1/providers/{id}` | 单个 Provider 详情 |
| PUT/DELETE | `/api/v1/providers/{id}/config` | 保存或删除 Provider 配置 |
| POST | `/api/v1/providers/{id}/test` | 真实生成请求测试，支持临时覆盖值 |
| GET/PUT | `/api/v1/courses/{id}/outline` | 读取或手动保存课程大纲 |
| POST | `/api/v1/courses/{id}/outline/generate` | 创建大纲生成任务，返回排队中的任务 |
| POST | `/api/v1/courses/{id}/classroom/generate` | 创建课堂生成任务，返回排队中的任务 |
| GET | `/api/v1/courses/{id}/classroom` | 获取最新课堂版本与场景 |
| POST | `/api/v1/courses/{id}/classroom/illustrations` | 为前 6 个幻灯片场景生成配图 |
| POST | `/api/v1/courses/{id}/classroom/scenes/{scene_id}/illustration` | 生成或重生成单页配图 |
| GET | `/api/v1/generation/jobs/{id}` | 查询大纲或课堂生成任务的状态与进度 |

错误统一返回：

```json
{"error": {"code": "ERROR_CODE", "message": "Human readable message", "details": null}}
```

## Provider 与大纲

- Provider 配置、API Key 加密、URL 防护和连接测试规则见 `docs/providers.md`。
- 大纲版本、素材截断、JSON 宽容解析与重试策略见 `docs/outlines.md`。
- 连接测试会真实请求 `/chat/completions` 或 Anthropic `/messages`；`/models` 仅用于补充模型列表。
- Qwen 优先读取 `QWEN_API_KEY`，为空时回退到 `DASHSCOPE_API_KEY`。可用 `scripts/check_qwen.py` 验证 `qwen3.8-omni-flash`。
- 旁白可用 `scripts/check_tts.py` 验证；阿里云 MaaS 工作区应传入 `--base-url https://<workspace-host>/api/v1`。
- 无 Key 场景可设置 `TTS_PROVIDER_ID=edge-tts`，用 `scripts/check_edge_tts.py` 验证微软中文音色。
- 配图默认使用 Qwen `qwen-image-3.0`，失败时按 `IMAGE_FALLBACK_CHAIN` 降级；默认只有无需 Key 的 Pollinations `flux`，可选 `huggingface`、`cloudflare`。
- 配图可用 `scripts/check_image.py --provider all` 真实探测每个通道，脚本不打印任何 Key。

## 课堂生成

- 课堂 DSL 版本固定为 `0.13.0`，幻灯片画布为 1280×720 深色主题。
- 每次生成创建新的课堂版本，场景与旁白动作一起保存。
- 生成失败会把任务标记为 `failed` 并记录错误码，重试后仍失败返回 `CLASSROOM_INVALID_RESPONSE`。
- 配图每页最多 1 张、整堂最多 6 张；整堂只补齐尚未配图的幻灯片；供应商返回的临时图片 URL 只用于即时下载，不写入数据库。
- 生成流程、场景格式与真实模型诊断见 `docs/classroom.md`。

## 认证设计

- 注册采用“先发送验证码、再一次性提交账号信息”的两步流程。验证码通过后才创建 `users` 行，因此重复注册无法覆盖已有账号。
- 邮箱统一 `strip().lower()` 规范化，数据库另有 `lower(email)` 唯一索引兜底。
- 验证码为 6 位数字，默认 10 分钟有效、60 秒重发冷却、最多 5 次尝试；数据库只保存 HMAC-SHA256 摘要。
- 开发环境 `EMAIL_DELIVERY=log` 会把验证码写入后端日志；配置 `EMAIL_DELIVERY=agentsend` 后通过 agentsend REST API 发送真实邮件。生产环境强制使用 `agentsend`。
- 密钥只放在 `.env` 的 `AGENTSEND_API_KEY`，不得提交到仓库；inbox 按 `AGENTSEND_INBOX_NAME` 复用，也可用 `AGENTSEND_INBOX_ID` 固定。
- access token 通过 `Authorization: Bearer` 传递，默认 15 分钟。
- refresh token 仅保存在 HttpOnly cookie 中，数据库只保存 SHA-256 摘要，默认 30 天。
- 每次刷新都会撤销旧 token 并轮换 refresh token 与 CSRF token。
- refresh/logout 需要 `X-CSRF-Token`，其值与 `teachai_csrf` cookie 及数据库会话绑定。
- 所有课程、素材、会话和设置查询后续都必须按当前用户隔离。

注册流程、错误码与邮件配置见 `docs/auth.md`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\check_migrations.py
.\.venv\Scripts\python.exe scripts\check_extensions.py
```

`check_migrations.py` 只允许连接名称以 `_test` 结尾的数据库。

## llama.cpp

本地模型端点配置为：http://127.0.0.1:65535/v1 <br>
启动模型后运行：

```powershell
.\.venv\Scripts\python.exe scripts\check_llama.py
```

## Docker

目前阶段 1 提供 API 镜像与 Compose 草案；生产 Nginx、TLS、Worker 和前端镜像在部署阶段补齐。

```powershell
docker compose build
docker compose up -d
```

## 后续阶段

1. 导出与生产部署（6C-6D）。
2. 共享任务队列、图片质量评估与可选的正式图片服务商。
