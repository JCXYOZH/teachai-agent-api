# 课堂生成

阶段 4A 把已确认的课程大纲编译成可播放的课堂数据。生成结果按版本保存，每个版本包含有序场景；`GET` 返回最新版本。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/courses/{course_id}/classroom/generate` | 创建课堂生成任务，立即返回排队中的任务 |
| GET | `/api/v1/courses/{course_id}/classroom` | 获取最新课堂版本与全部场景 |
| GET | `/api/v1/generation/jobs/{job_id}` | 查询大纲或课堂生成任务的状态与进度 |
| PUT | `/api/v1/courses/{course_id}/classroom/scenes/{scene_id}/responses` | 提交或覆盖作答，返回判分结果 |
| GET | `/api/v1/courses/{course_id}/classroom/responses` | 获取当前课堂版本已保存的作答 |
| POST | `/api/v1/courses/{course_id}/classroom/narration` | 为最新课堂的全部场景生成旁白音频任务 |
| POST | `/api/v1/courses/{course_id}/classroom/scenes/{scene_id}/narration` | 重新生成单个场景的旁白 |
| POST | `/api/v1/courses/{course_id}/classroom/illustrations` | 为尚未配图的幻灯片补图，整堂最多 6 张 |
| POST | `/api/v1/courses/{course_id}/classroom/scenes/{scene_id}/illustration` | 生成或重生成单页配图 |
| GET | `/api/v1/media/{media_id}` | 查看音频或图片元数据 |
| GET | `/api/v1/media/{media_id}/content` | 鉴权下载媒体文件 |

生成请求示例：

```json
{
  "provider_id": "llama-local",
  "model": "Qwen3.6-35B-A3B",
  "instructions": "面向零基础学习者，多举生活例子。"
}
```

`provider_id` 省略时使用当前用户默认 Provider；没有默认配置时回退到 `llama-local`。`model` 可临时覆盖默认模型。生成前必须已有课程大纲，否则返回 `OUTLINE_NOT_FOUND`。

接口不会等待模型返回：写入 `generation_jobs` 后立即返回 `201` 与 `status=queued` 的任务，生成在后台执行。客户端轮询该任务直到 `completed` 或 `failed`；完成时 `stage_id` 指向新课堂版本，`scene_count` 为场景数。

## 生成流程

1. 请求阶段校验课程、最新大纲与 Provider，写入 `kind=classroom` 的 `queued` 任务后立即返回。
2. 后台任务把状态置为 `running`（10%），读取素材解析文本（默认最多 12,000 字符）。
3. 调用 Provider 生成课堂 JSON（40%），宽容接受代码围栏、前后解释和缺失字段。
4. 编译为 DSL `0.12.0` 场景，写入 `course_stages` 与 `course_scenes`。
5. 任务更新为 `completed`、进度 100，并关联 `stage_id` 与新版本。

解析失败会自动重试一次；仍失败时返回 `CLASSROOM_INVALID_RESPONSE`，同时把任务标记为 `failed` 并记录 `error_code`。Provider 超时返回 `CLASSROOM_GENERATION_TIMEOUT`，其他 Provider 故障返回 `CLASSROOM_PROVIDER_ERROR`。

同一课程同类型的生成任务只允许一个。已有任务在排队或运行时，新的 POST 返回 `409 GENERATION_IN_PROGRESS`，`details.job_id` 指向进行中的任务，客户端应改为继续轮询。服务重启时遗留的 `queued`/`running` 任务会在启动阶段标记为 `failed`，错误码为 `GENERATION_INTERRUPTED`。

后台执行使用进程内任务，启动恢复也按单进程假设设计。若改为 `uvicorn --workers N`，需要换成共享队列（如 ARQ/Celery）并去掉启动时的全局恢复，否则其他 worker 正在执行的任务会被误判为中断。

## 场景格式

DSL `0.13.0` 支持四类场景，其中幻灯片与测验是主体，互动与项目式各最多一个：

- `slide`：`content.type = "slide"`，包含 1280×720 深色画布，标题、逐条要点的独立文本元素与页脚。
- `quiz`：`content.type = "quiz"`，包含 1-3 道单选题、多选题或简答题，答案与解析一并保存。
- `interactive`：`content.type = "interactive"`，包含 `html`、`widgetType`、`prompt` 与 1280×720 的 `frame`。`html` 是模型生成的单文件组件，前端放进 `sandbox="allow-scripts"` 的 iframe 并注入禁止联网的 CSP，因此可以运行内联脚本但无法触达宿主页面或后端。
- `pbl`：`content.type = "pbl"`，包含 `project`，下含 `learningObjective`、`finalDeliverable` 与 1-3 个 `milestones`；每个里程碑带 2-4 个 `microtasks`，微任务有稳定 `id`、完成标准与提示。

动作按 `actions` 数组顺序播放：

- `speech`：教师旁白文本，供后续 TTS 与播放器使用。
- `reveal`：逐条显示幻灯片要点，`elementId` 指向该要点的画布元素。
- `highlight`：突出互动场景画布，`elementId` 以 `-stage` 结尾，由播放器映射到场景容器。

模型输出缺少 `html` 或可用里程碑时，该场景自动降级为 `slide`，保证课堂始终可播放。

幻灯片画布固定参数：

```json
{
  "viewportSize": 1280,
  "viewportRatio": 0.5625,
  "background": {"type": "solid", "color": "#0B1714"}
}
```

课堂目标为 4-10 个场景，单次生成最多接受 24 个场景，其中 `interactive` 最多 4 个、`pbl` 最多 2 个。课堂生成阶段不合成媒体；旁白音频在课堂生成后按需生成。

## 旁白（TTS）

课堂生成完成后，前端可以手动触发旁白合成。默认使用 `TTS_PROVIDER_ID`（默认 `qwen`）、`qwen3-tts-flash` 与 `Cherry` 女声；也可以在请求中临时覆盖 Provider、模型和音色。设置 `TTS_PROVIDER_ID=edge-tts` 可使用无 Key 的微软中文语音后备。

```json
{
  "provider_id": "qwen",
  "model": "qwen3-tts-flash",
  "voice": "Cherry"
}
```

两个接口都在校验课堂与 Provider 后立即返回 `202` 与 `kind=narration` 的任务，客户端继续轮询 `GET /api/v1/generation/jobs/{job_id}`。整堂生成按场景顺序合成 `speech` 动作；单场景接口只处理指定场景。同一课程同一时间只允许一个旁白任务，重复请求返回 `409 GENERATION_IN_PROGRESS`。

每个 `speech` 动作成功后写回三个字段：

```json
{
  "audioId": "媒体资产 UUID",
  "audioFormat": "mp3",
  "audioDurationMs": 5230
}
```

缓存键由 `provider + model + voice + speed + text` 计算 SHA-256。文本完全相同的场景只合成一次，其余场景复用已落盘音频；文本、音色或 Provider 变化时才重新调用模型。音频保存在 `ASSET_STORAGE_ROOT/{owner_id}/media/{media_id}/audio.<format>`，元数据写入 `media_assets`，其他用户访问返回 404。

常见错误码：

- `TTS_NOT_SUPPORTED`：所选 Provider 没有 TTS 能力。
- `TTS_ENDPOINT_NOT_SUPPORTED`：TTS Base URL 未暴露 `/audio/speech`，需要改为支持语音合成的网关。
- `TTS_AUTH_FAILED`：密钥无权访问 TTS 模型。
- `TTS_TIMEOUT` / `TTS_AUDIO_TOO_LARGE`：合成超时或音频超过 `TTS_MAX_AUDIO_BYTES`。

Qwen 的 TTS Base URL 指向 DashScope 官方根地址时使用原生 `multimodal-generation` 接口；阿里云 MaaS 工作区的 `/compatible-mode/v1` 会自动派生为同主机 `/api/v1`，同样走原生接口；其他兼容网关使用 OpenAI 兼容的 `/audio/speech`。两者都按 Provider 已保存的 API Key 鉴权。

## 课堂配图

配图只处理最新课堂版本中的 `slide` 场景。两个图片接口都立即返回 `202` 与 `kind=image` 任务，客户端继续轮询同一个 `/api/v1/generation/jobs/{job_id}`。整堂接口按场景顺序跳过已有配图的幻灯片，只填充剩余额度（上限 `IMAGE_MAX_PER_CLASSROOM`，默认 6 张）；额度用尽返回 `IMAGE_LIMIT_REACHED`，所有幻灯片都已配图返回 `IMAGE_NO_SCENES`。单页接口只处理指定场景，非 `slide` 场景返回 `IMAGE_SCENE_NOT_FOUND`；新增配图同样受整堂上限约束。

```json
{
  "provider_id": "qwen",
  "instructions": "保持人物与场景写实，左侧留出文字安全区。",
  "regenerate": false
}
```

默认主通道是 Qwen `qwen-image-3.0`。请求失败、没有可用 Key 或输出无法解码时，按 `IMAGE_FALLBACK_CHAIN` 依次降级；默认链只有无 Key 的 Pollinations `flux`，配置凭据后可追加 `huggingface` 与 `cloudflare`。降级只影响本次任务，不改变用户的 Provider 默认设置，日志以 `image_provider_failed` 记录每次失败。

图片会立即下载并经过以下处理：

- 拒绝私网、保留地址、带账号密码或片段的下载 URL，关闭重定向。
- 限制下载体积，使用 Pillow 完整解码，不能只相信供应商的 `Content-Type`。
- 移除 EXIF 和其他元数据，等比缩放到最大 1600×900，转为 WebP（默认质量 82）。
- 保存到 `ASSET_STORAGE_ROOT/{owner_id}/media/{media_id}/illustration.webp`，元数据写入 `media_assets`。
- 供应商临时 URL 不落库，浏览器只通过鉴权 `/api/v1/media/{id}/content` 读取。

成功后 `content.illustration` 写回场景：

```json
{
  "mediaId": "媒体资产 UUID",
  "format": "webp",
  "mimeType": "image/webp",
  "width": 1600,
  "height": 900,
  "providerId": "qwen",
  "model": "qwen-image-3.0",
  "sha256": "...",
  "updatedAt": "2026-10-30T12:00:00+00:00",
  "alt": "场景标题"
}
```

缓存键由 `provider + model + prompt + 目标尺寸 + webp` 计算。相同内容重复请求会复用已落盘图片；传 `regenerate: true` 会覆盖原资产并保持同一个 `mediaId`。每页最多 1 张，由 `media_assets` 唯一约束兜底。

## 作答与判分

作答与项目任务记录在 `classroom_responses`，按 `owner_id + scene_id + target_id` 唯一，重复提交会覆盖而不是追加。判分规则：

- 单选题与多选题按选项值集合精确匹配，答对得 1 分；题目没有参考答案时不判分，`max_score` 记为 0。
- 简答题只保存原文，不自动打分，返回参考答案与解析供学习者自评。
- 项目微任务以 `{"completed": true|false}` 记录完成状态，不计入测验分数。
- `score`/`max_score` 按场景汇总，只统计已作答且可判分的题目。

生成新课堂版本后，旧版本场景的作答仍保留在库中，但查询只返回最新版本；场景不属于当前用户或课程时返回 404 `CLASSROOM_SCENE_NOT_FOUND`，题目或任务 id 不存在时返回 400 `CLASSROOM_TARGET_NOT_FOUND`。

## 版本与数据

- 每次成功生成都会创建新的 `course_stages` 版本，版本号从 1 递增。
- `course_scenes` 按 `order_index` 排序，并把 `stage_id`、所有者与场景内容绑定。
- `course_stages.dsl_version` 固定为 `0.13.0`；历史版本保留自己的版本号，读取逻辑不按版本裁剪字段。
- 生成记录保存 `outline_id`、`provider_id`、`model_name` 与场景数量。
- 任务与课堂查询都按当前用户隔离，其他用户访问返回 404。

## 配置

```text
CLASSROOM_MATERIAL_CHAR_LIMIT=12000
CLASSROOM_GENERATION_TIMEOUT_SECONDS=900
TTS_PROVIDER_ID=qwen
TTS_GENERATION_TIMEOUT_SECONDS=120
TTS_MAX_TEXT_CHARS=6000
TTS_MAX_AUDIO_BYTES=20971520
IMAGE_PROVIDER_ID=qwen
IMAGE_MODEL=qwen-image-3.0
IMAGE_GENERATION_TIMEOUT_SECONDS=180
IMAGE_MAX_DOWNLOAD_BYTES=26214400
IMAGE_MAX_WIDTH=1600
IMAGE_MAX_HEIGHT=900
IMAGE_WEBP_QUALITY=82
IMAGE_MAX_PER_CLASSROOM=6
IMAGE_TARGET_WIDTH=1024
IMAGE_TARGET_HEIGHT=576
IMAGE_POLLINATIONS_BASE_URL=https://image.pollinations.ai/prompt
IMAGE_POLLINATIONS_MODEL=flux
```

## 真实模型诊断

Qwen Omni 只支持文本输出，课堂调用会自动附加 `modalities: ["text"]`。可用脚本确认真实生成链路：

```powershell
.\.venv\Scripts\python.exe scripts\check_classroom.py `
  --base-url https://<workspace-host>/compatible-mode/v1 `
  --model qwen3.8-omni-flash
```

脚本读取 `DASHSCOPE_API_KEY`，只输出场景统计，不打印密钥或完整模型响应。

阶段 4B 负责前端课堂渲染器，4C 补齐互动、项目式、作答提交与事件流播放；阶段 6A 增加旁白合成、音频资产与播放器音频优先播放；阶段 6B 增加课堂配图、Qwen Image 主通道与 Pollinations 兜底。
