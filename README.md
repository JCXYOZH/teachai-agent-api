# TeachAI Agent API

TeachAIAgent 的后端服务：把一份课程素材变成可播放、可互动、可导出的 AI 课堂。

## 项目简介

TeachAIAgent 是面向教师与自学者的 AI 备课与授课工作台。用户上传课件、教案或教材片段后，系统先把素材解析成可检索的中文文本，再基于用户自己的模型配置生成课程大纲、课堂讲稿与配图，最后在沉浸式课堂里播放、答疑并导出 PPTX 或单文件 HTML。

本仓库是其中的后端，负责数据模型与鉴权、素材存储与解析、模型调用与重试、生成任务编排、课堂 DSL 合成、音视频与图片资产、导出渲染，以及生产部署模板。前端见 `teachai-agent-web`仓库，部署细节见 `docs/deployment.md`。

## 核心功能

| 模块 | 能力 |
| --- | --- |
| 认证 | 手机号或邮箱验证码注册、密码登录、access + refresh 双令牌、CSRF 双提交、按用户隔离 |
| 素材库 | 文件夹与课程管理、PDF/DOCX/PPTX/Markdown/TXT 上传解析、中文检索、鉴权下载、软删除 |
| 模型配置 | 按用户保存各 Provider 的 Base URL / Key / 默认模型，真实连接测试，Key 加密落库且只写不读 |
| 大纲 | 版本化大纲生成、手工编辑、素材截断、JSON 宽容解析与失败重试 |
| 课堂 | 基于大纲生成课堂 DSL（0.13.0，1280×720）、场景与讲稿、分幕问答、练习判分 |
| 旁白 | 按场景合成语音（Qwen / edge-tts），音频进媒体资产，鉴权播放 |
| 配图 | Qwen Image 主通道 + Pollinations 免费兜底，质量评分与低分重试、整堂补图与单页重生成 |
| 教师对话 | 单 Agent 教师角色，SSE 流式答疑，按课程保存会话历史 |
| 导出 | PPTX 保真版与单文件 HTML，共享导出任务、体积预算、鉴权下载 |
| 运维 | 进程内任务队列、结构化指标日志、健康与就绪检查、Docker + Nginx + frp 生产链路 |

## 设计特点

- **单核心 Agent**：课堂问答核心做「教师」一个角色；上下文按课程、大纲与素材裁剪。
- **模型选择可预测**：默认固定使用本地 `llama-local`（`Qwen3.6-35B-A3B`），只有用户在设置页勾选「使用当前模型」并保存后才切到自己配置的 Provider；解析失败不换厂商，同厂商重试一次；本地模型不可用时对已保存配置单次回退。
- **输入先清洗**：所有用户文本经 `app/core/text.py` 的 `normalize_user_text` 规范化（NUL、未配对代理项、超长空白），避免 `asyncpg` 直接 500。
- **生成不占请求**：大纲、课堂、旁白、配图、导出全部写入 `generation_jobs` 再由同进程 worker 池认领，无需 Redis，重启与副本崩溃可恢复。
- **资产与元数据分离**：文件落本地磁盘按 UUID 分目录，数据库只存元数据与解析文本，备份口径清晰。
- **可观性内建**：每次模型调用与任务结束都有结构化日志行，按 Provider / 任务类型聚合指标。
- **同源部署**：前端以相对路径调用 `/api/v1`，refresh 与 CSRF cookie 不跨域；公网只有海外中转机 `frps` 的 80/443 两个入口。

## 技术栈

| 组件 | 版本      |
| --- |---------|
| Python | 3.8.9   |
| FastAPI | 0.124.4 |
| Starlette | 0.41.3  |
| Pydantic | 2.9.2   |
| SQLAlchemy | 2.0.36  |
| asyncpg | 0.29.0  |
| Alembic | 1.13.3  |
| LangChain | 0.1.16  |
| langchain-openai | 0.1.3   |
| Swagger UI | 5.17.14 |
| PostgreSQL | 16.15   |

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

启动后访问：http://127.0.0.1:8000/docs <br>
开发数据库为 teachai_agent，测试数据库为 teachai_agent_test。本地开发默认 `EMAIL_DELIVERY=log`、`SMS_DELIVERY=log`，验证码直接写进日志。

## API 文档

`/docs` 使用自托管的 Swagger UI，不依赖 jsDelivr 等外部 CDN。静态资源位于 `app/static/swagger-ui`，来自 `swagger-ui-dist@5.17.14`。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/auth/register/send-code` | 发送注册验证码（邮箱或手机号），返回 202 |
| POST | `/api/v1/auth/register` | 校验验证码并创建用户，建立会话 |
| POST | `/api/v1/auth/login` | 账号密码登录（`identifier` 兼容旧 `email`） |
| POST | `/api/v1/auth/refresh` | 轮换 refresh token |
| POST | `/api/v1/auth/logout` | 撤销当前会话 |
| GET | `/api/v1/auth/me` | 当前用户 |
| GET | `/api/v1/health` | 存活检查 |
| GET | `/api/v1/health/ready` | 数据库与存储就绪检查 |
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
| POST | `/api/v1/courses/{id}/classroom/narration` | 为全部场景合成旁白 |
| POST | `/api/v1/courses/{id}/classroom/scenes/{scene_id}/narration` | 单场景旁白合成 |
| POST | `/api/v1/courses/{id}/classroom/illustrations` | 为前 6 个幻灯片场景生成配图 |
| POST | `/api/v1/courses/{id}/classroom/scenes/{scene_id}/illustration` | 生成或重生成单页配图 |
| PUT | `/api/v1/courses/{id}/classroom/scenes/{scene_id}/responses` | 保存单个场景的练习作答 |
| GET | `/api/v1/courses/{id}/classroom/responses` | 读取当前课堂的作答汇总 |
| POST | `/api/v1/courses/{id}/classroom/export` | 创建课堂导出任务（pptx / html），返回 202 |
| GET | `/api/v1/courses/{id}/classroom/exports` | 列出当前课堂版本的导出文件 |
| GET | `/api/v1/courses/{id}/chat` | 读取教师对话会话与历史 |
| POST | `/api/v1/courses/{id}/chat/messages` | 发送提问并返回完整回答 |
| POST | `/api/v1/courses/{id}/chat/messages/stream` | SSE 流式问答（`delta` / `done` / `error`） |
| DELETE | `/api/v1/courses/{id}/chat` | 清空当前课程的对话 |
| GET | `/api/v1/media/{id}` | 媒体资产元数据 |
| GET | `/api/v1/media/{id}/content` | 鉴权读取媒体内容（音频、图片、导出文件） |
| GET | `/api/v1/generation/jobs/{id}` | 查询生成任务状态与进度 |
| POST | `/api/v1/generation/jobs/{id}/retry` | 重试失败任务，复制原 payload 重新入队 |

错误统一返回：

```json
{"error": {"code": "ERROR_CODE", "message": "Human readable message", "details": null}}
```

## 资产存储

上传资产使用本地磁盘，默认根目录为 `E:\Backups\Data\TeachAIAgent`，可通过 `ASSET_STORAGE_ROOT` 覆盖；生产为 `/data/TeachAIAgent`。PostgreSQL 保存资产元数据、所有权与解析文本，原始文件字节不进数据库。目录不会通过 `/static` 暴露，下载必须经过鉴权 API。

超过 `ASSET_MAX_BYTES` 的上传由 `UploadSizeGuardMiddleware` 按 `Content-Length` 在读请求体之前拒绝（`413 FILE_TOO_LARGE`），不会先把整份文件写进临时目录。

目录布局、软删除清理、备份要求与安全约束见 `docs/asset-storage.md`。

## Provider 与大纲

- Provider 配置、API Key 加密、URL 防护和连接测试规则见 `docs/providers.md`。
- 大纲版本、素材截断、JSON 宽容解析与重试策略见 `docs/outlines.md`。
- 连接测试会真实请求 `/chat/completions` 或 Anthropic `/messages`；`/models` 仅用于补充模型列表。
- Qwen 优先读取 `QWEN_API_KEY`，为空时回退到 `DASHSCOPE_API_KEY`。可用 `scripts/check_qwen.py` 验证 `qwen3.8-omni-flash`。
- 旁白可用 `scripts/check_tts.py` 验证；阿里云 MaaS 工作区应传入 `--base-url https://<workspace-host>/api/v1`。
- 无 Key 场景可设置 `TTS_PROVIDER_ID=edge-tts`，用 `scripts/check_edge_tts.py` 验证微软中文音色。
- 配图默认使用 Qwen `qwen-image-3.0`，失败时按 `IMAGE_FALLBACK_CHAIN` 降级；默认只有无需 Key 的 Pollinations `flux`，可选 `huggingface`、`cloudflare`。
- 配图可用 `scripts/check_image.py --provider all` 真实探测每个通道，脚本不打印任何 Key。
- 生产环境的 Provider Base URL 必须是 HTTPS；本地 llama.cpp 走明文时需用 `PROVIDER_ALLOW_INSECURE_HTTP=47.120.68.25:65535` 精确放行。

## 课堂生成与配图

- 课堂 DSL 版本固定为 `0.13.0`，幻灯片画布为 1280×720 深色主题。
- 每次生成创建新的课堂版本，场景与旁白动作一起保存。
- 生成失败会把任务标记为 `failed` 并记录错误码，重试后仍失败返回 `CLASSROOM_INVALID_RESPONSE`。
- 配图每页最多 1 张、整堂最多 6 张；整堂只补齐尚未配图的幻灯片；供应商返回的临时图片 URL 只用于即时下载，不写入数据库。
- 配图按 `IMAGE_CONCURRENCY`（默认 2）并行，每张图经本地启发式质量评分，低分同 Provider 换变体提示词重试一次；`status=low` 的资产不再缓存复用。
- 生成流程、场景格式与真实模型诊断见 `docs/classroom.md`。

## 教师对话

- 只有「教师」一个角色：不做教师 / 助教 / 学生多 Agent，也不给学生独立身份。
- 入口：`POST /chat/messages`（一次性）与 `POST /chat/messages/stream`（SSE，事件 `delta` / `done` / `error`，等首 token 时每 15 秒心跳）。
- 上下文：课程标题与描述、当前大纲分幕、素材摘要（`CHAT_MATERIAL_CHAR_LIMIT`）与最近 `CHAT_HISTORY_MESSAGES` 条历史。
- 输入上限 `CHAT_MAX_MESSAGE_CHARS=2000`，前端按 code point 计数并在超限时禁用发送，服务端同样校验。
- 模型选择沿用全局语义：默认本地 `llama-local`，用户勾选「使用当前模型」后才用其配置，失败按既定规则同厂商重试与单次回退。
- 流结束后一次性落库；每用户每天上限 `CHAT_DAILY_MESSAGE_LIMIT`。
- 接口、提示词与错误码见 `docs/chat.md`。

## 课堂导出

- `POST /classroom/export` 立即返回 `queued` 任务，渲染完成后写入 `media_assets(kind='export')`，同格式重新导出覆盖同一个文件。
- PPTX 按 1280×720 画布 1px = 9525 EMU 还原元素位置、字号与配色，配图转 JPEG 并叠渐变暗化，旁白（开启「含旁白」时）与答案进备注页，文末附「参考答案」页；slide 场景按 reveal 动作生成原生「单击出现」动画。
- HTML 输出单文件：样式、脚本、配图与旁白全部内联，带场景导航、进度条、键盘切换与打印分页，元素 HTML 经白名单清洗；交互组件以 sandbox iframe 内联，可离线操作。
- 上限由 `EXPORT_MAX_BYTES` 控制（默认 40 MB）；旁白音频按上限 60% 预算裁剪，超出部分丢弃音频并在 `warnings` 中提示。
- 接口、错误码、文件命名与内容映射见 `docs/export.md`。

## 认证设计

- 注册采用「先发送验证码、再一次性提交账号信息」的两步流程，验证码通过后才创建 `users` 行，重复注册无法覆盖已有账号。
- 手机号注册是默认入口，邮箱注册需要显式切换；两者互斥，账号字段由 `CHECK (email IS NOT NULL OR phone IS NOT NULL)` 兜底，手机号唯一索引、响应统一脱敏为 `138****8000`。
- 邮箱统一 `strip().lower()` 规范化，数据库另有 `lower(email)` 唯一索引兜底。
- 验证码为 6 位数字，默认 10 分钟有效、60 秒重发冷却、最多 5 次尝试；数据库只保存 HMAC-SHA256 摘要。
- 邮件：开发环境 `EMAIL_DELIVERY=log` 写日志，生产强制 `agentsend` 走 REST API 发信。
- 短信：`SMS_DELIVERY=memory|log|spug`，生产强制 `spug`（push.spug.cc 模板推送）；`SPUG_URL` 属凭证只放 `.env.production`。
- 短信防刷：号码 60 秒冷却之外叠加 IP 与号码双维度滑动窗口（`SMS_IP_LIMIT_PER_MINUTE` / `SMS_IP_LIMIT_PER_HOUR` / `SMS_TARGET_LIMIT_PER_HOUR`），超限返回 `429 SMS_SEND_RATE_LIMITED` 并带 `details.retry_after`。
- access token 通过 `Authorization: Bearer` 传递，默认 15 分钟；refresh token 仅存 HttpOnly cookie，数据库只存 SHA-256 摘要，默认 30 天。
- 每次刷新都会撤销旧 token 并轮换 refresh token 与 CSRF token；refresh / logout 需要 `X-CSRF-Token`。
- 所有课程、素材、会话和设置查询都按当前用户隔离。

注册流程、错误码与邮件短信配置见 `docs/auth.md`。

## 生成任务队列

- 大纲、课堂、旁白、配图、导出都先写 `generation_jobs`，再由同进程 worker 池认领（`app/services/generation_queue.py`）。
- 认领语句为 `SELECT ... WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`，同一事务内置 `running`，多副本不会重复执行，也不需要 Redis。
- 重启恢复：启动时把遗留 `running` 回队列并累加 `payload.attempts`，超过 `GENERATION_JOB_MAX_ATTEMPTS` 才置 `GENERATION_INTERRUPTED`；另有每 60 秒一次的 stale 扫描兜底。
- 失败任务可通过 `POST /api/v1/generation/jobs/{id}/retry` 复制 payload 重新入队。
- `GENERATION_QUEUE_ENABLED=false` 可退回 `BackgroundTasks`，仅用于单进程调试。

## 可观测性

- 每次模型调用输出 `llm_call_completed provider=... model=... outcome=ok|empty|timeout|error duration_ms=...`。
- 每次生成任务结束输出 `generation_job_finished job_id=... kind=... outcome=... duration_ms=...`。
- 每 `METRICS_SNAPSHOT_SECONDS`（默认 300，0 关闭）输出一行 `metrics_snapshot` 聚合指标。
- 每个请求都带 `request_id`，可与 `app.core.middleware` 日志对齐；计数随进程重启清零，不做跨副本聚合。

## 测试

```powershell
# 全量回归：默认 4 个 worker 并行（pyproject 的 addopts），约 2 分钟
.\.venv\Scripts\python.exe -m pytest -q
# 想更快：8 个 worker，约 1 分钟
.\.venv\Scripts\python.exe -m pytest -q -n 8
# 单文件/单用例调试：串行执行，走 TEST_DATABASE_URL 指向的库
.\.venv\Scripts\python.exe -m pytest -q -n 0 tests\test_health.py
.\.venv\Scripts\python.exe scripts\check_migrations.py
.\.venv\Scripts\python.exe scripts\check_extensions.py
```

`-n N` 由 `pytest-xdist` 提供：每个 worker 首次运行时自动创建并迁移自己的 `teachai_agent_test_gwN` 库，之后复用；`-n 0` 关闭并行并使用 `TEST_DATABASE_URL` 指向的库。用例之间共享同一张测试库会互相清表，必须靠「一 worker 一库」才能并行。`--pdb`、`-x` 这类调试参数建议配合 `-n 0`。当前基线：全部用例通过、`ruff` 与 `mypy` 干净。

`check_migrations.py` 只允许连接名称以 `_test` 结尾的数据库。

## 本地模型

开发默认端点 `http://127.0.0.1:65535/v1` ，生产则 `LLAMA_BASE_URL=http://47.120.68.25:65535/v1` ，模型 `Qwen3.6-35B-A3B`。启动模型后运行：

```powershell
.\.venv\Scripts\python.exe scripts\check_llama.py
```

## 生产部署

生产是同源容器部署：单机跑 Nginx、PostgreSQL 16 与两个容器（`network_mode: host`，`65534` / `49153`），公网入口由海外中转机的 `frps` 经 frp 把 80/443 透传到本机 Nginx，TLS 在本机终结。

```bash
cp .env.production.example .env.production
docker compose -f docker-compose.prod.yml up -d --build
```

容器启动时先等待数据库，再执行 `alembic upgrade head`，最后启动 uvicorn；可用 `RUN_MIGRATIONS=0` 改为人工迁移。完整步骤、frp 与 Nginx 配置、验收清单、备份与回滚见 `docs/deployment.md`。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| `docs/roadmap.md` | 唯一待办清单与当前状态 |
| `docs/auth.md` | 注册、登录、令牌、短信与错误码 |
| `docs/asset-storage.md` | 素材落盘布局、备份与清理 |
| `docs/providers.md` | Provider 配置、Key 加密与 URL 防护 |
| `docs/outlines.md` | 大纲版本、截断与解析重试 |
| `docs/classroom.md` | 课堂 DSL、生成流程与配图 |
| `docs/chat.md` | 教师对话接口与提示词 |
| `docs/export.md` | 导出格式、预算与下载 |
| `docs/deployment.md` | 生产部署、frp 公网接入与运维 |
