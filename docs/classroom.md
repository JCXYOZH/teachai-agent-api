# 课堂生成

阶段 4A 把已确认的课程大纲编译成可播放的课堂数据。生成结果按版本保存，每个版本包含有序场景；`GET` 返回最新版本。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/courses/{course_id}/classroom/generate` | 基于最新大纲同步生成课堂并返回任务结果 |
| GET | `/api/v1/courses/{course_id}/classroom` | 获取最新课堂版本与全部场景 |
| GET | `/api/v1/classroom/jobs/{job_id}` | 查询生成任务状态 |

生成请求示例：

```json
{
  "provider_id": "llama-local",
  "model": "Qwen3.6-35B-A3B",
  "instructions": "面向零基础学习者，多举生活例子。"
}
```

`provider_id` 省略时使用当前用户默认 Provider；没有默认配置时回退到 `llama-local`。`model` 可临时覆盖默认模型。生成前必须已有课程大纲，否则返回 `OUTLINE_NOT_FOUND`。

## 生成流程

1. 读取课程、最新大纲与素材解析文本（默认最多 12,000 字符）。
2. 创建 `generation_jobs` 记录，状态从 `queued` 进入 `running`。
3. 调用 Provider 生成课堂 JSON，宽容接受代码围栏、前后解释和缺失字段。
4. 编译为 DSL `0.12.0` 场景，写入 `course_stages` 与 `course_scenes`。
5. 任务更新为 `completed`、进度 100，并关联新版本。

解析失败会自动重试一次；仍失败时返回 `CLASSROOM_INVALID_RESPONSE`，同时把任务标记为 `failed` 并记录 `error_code`。Provider 超时返回 `CLASSROOM_GENERATION_TIMEOUT`，其他 Provider 故障返回 `CLASSROOM_PROVIDER_ERROR`。

## 场景格式

当前阶段只生成两类场景：

- `slide`：`content.type = "slide"`，包含 1280×720 深色画布、标题/正文/页脚文本元素。
- `quiz`：`content.type = "quiz"`，包含 1-3 道单选题、多选题或简答题，答案与解析一并保存。

每个场景都带一条 `speech` 动作，保存教师旁白文本，供后续 TTS 与播放器使用。

幻灯片画布固定参数：

```json
{
  "viewportSize": 1280,
  "viewportRatio": 0.5625,
  "background": {"type": "solid", "color": "#0B1714"}
}
```

课堂目标为 4-10 个场景，单次生成最多接受 24 个场景。当前不生成 `interactive`、`pbl`、图片、音频或视频。

## 版本与数据

- 每次成功生成都会创建新的 `course_stages` 版本，版本号从 1 递增。
- `course_scenes` 按 `order_index` 排序，并把 `stage_id`、所有者与场景内容绑定。
- `course_stages.dsl_version` 固定为 `0.12.0`，与 OpenMAIC DSL 版本对齐。
- 生成记录保存 `outline_id`、`provider_id`、`model_name` 与场景数量。
- 任务与课堂查询都按当前用户隔离，其他用户访问返回 404。

## 配置

```text
CLASSROOM_MATERIAL_CHAR_LIMIT=12000
CLASSROOM_GENERATION_TIMEOUT_SECONDS=600
```

## 真实模型诊断

Qwen Omni 只支持文本输出，课堂调用会自动附加 `modalities: ["text"]`。可用脚本确认真实生成链路：

```powershell
.\.venv\Scripts\python.exe scripts\check_classroom.py `
  --base-url https://<workspace-host>/compatible-mode/v1 `
  --model qwen3.8-omni-flash
```

脚本读取 `DASHSCOPE_API_KEY`，只输出场景统计，不打印密钥或完整模型响应。

阶段 4B 负责前端课堂渲染器，阶段 4C 负责互动与课堂动作。
