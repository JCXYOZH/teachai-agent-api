# TeachAI Agent API

TeachAIAgent 的后端服务。阶段 1 已完成用户认证、数据库基线与运行时健康检查；阶段 2 已实现课程、文件夹、本地素材存储、文档解析与中文检索。

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

错误统一返回：

```json
{"error": {"code": "ERROR_CODE", "message": "Human readable message", "details": null}}
```

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

1. Provider 抽象、模型配置与课程大纲生成。
2. 场景 DSL、生成 Worker、事件流与课堂渲染。
3. TTS、图片生成、导出与生产部署。
