# TeachAI Agent API

TeachAIAgent 的后端服务。阶段 1 已完成用户认证、数据库基线与运行时健康检查；阶段 2 已实现课程、文件夹、本地素材存储、文档解析与中文检索；阶段 3 已实现用户级 Provider 配置、真实连接测试与版本化课程大纲生成；阶段 4A 已实现基于大纲的课堂 DSL 生成与任务状态查询。

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
| POST | `/api/v1/auth/register` | 注册并建立会话 |
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

## 课堂生成

- 课堂 DSL 版本固定为 `0.12.0`，幻灯片画布为 1280×720 深色主题。
- 每次生成创建新的课堂版本，场景与旁白动作一起保存。
- 生成失败会把任务标记为 `failed` 并记录错误码，重试后仍失败返回 `CLASSROOM_INVALID_RESPONSE`。
- 生成流程、场景格式与真实模型诊断见 `docs/classroom.md`。

## 认证设计

- access token 通过 `Authorization: Bearer` 传递，默认 15 分钟。
- refresh token 仅保存在 HttpOnly cookie 中，数据库只保存 SHA-256 摘要，默认 30 天。
- 每次刷新都会撤销旧 token 并轮换 refresh token 与 CSRF token。
- refresh/logout 需要 `X-CSRF-Token`，其值与 `teachai_csrf` cookie 及数据库会话绑定。
- 所有课程、素材、会话和设置查询后续都必须按当前用户隔离。

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

1. 课堂渲染器与深色沉浸式播放界面（4B）。
2. 互动场景、课堂动作与事件流（4C）。
3. TTS、图片生成、导出与生产部署。
