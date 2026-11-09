# 课堂内教师对话

阶段 5 打通课堂播放器里的「问老师」：用户在浏览课堂时可就当前内容向单一「教师」角色提问。系统保留单 Agent 架构，不引入助教、学生或多 Agent 协作；不生成模型侧引用，只展示服务端确定的「第 N 幕 · 标题」。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/courses/{course_id}/chat` | 读取本课程对话历史与今日剩余次数 |
| POST | `/api/v1/courses/{course_id}/chat/messages` | 提问并同步返回教师回答 |
| DELETE | `/api/v1/courses/{course_id}/chat` | 清空本课程对话（软清空） |

提问请求示例：

```json
{
  "message": "为什么说间隔趋于零时得到瞬时变化率？",
  "stage_id": "3f1c...",
  "scene_id": "9a2d...",
  "include_materials": false
}
```

回答示例（节选）：

```json
{
  "course_id": "6b1e...",
  "session_id": "d3c4...",
  "question": {
    "id": "a1...",
    "role": "user",
    "content": "为什么说间隔趋于零时得到瞬时变化率？",
    "stage_index": 1,
    "stage_title": "函数的极限",
    "scene_title": "检查理解",
    "created_at": "2026-11-09T09:00:00+00:00"
  },
  "reply": {
    "id": "a2...",
    "role": "assistant",
    "content": "因为平均变化率的分母在缩短……",
    "stage_index": 1,
    "stage_title": "函数的极限",
    "provider_id": "llama-local",
    "model_name": "Qwen3.6-35B-A3B",
    "created_at": "2026-11-09T09:00:03+00:00"
  },
  "daily_limit": 200,
  "daily_used": 1,
  "remaining_today": 199
}
```

`stage_index` 是课堂版本号（第 N 幕），`stage_title` / `scene_title` 取自服务端当前课堂，客户端直接展示即可。必须先生成课堂，否则返回 `409 CHAT_CLASSROOM_REQUIRED`。

## 上下文组装

- 大纲：最新版本大纲的场景标题、描述与要点，最多 6,000 字符。
- 当前场景：按场景类型抽取可读文本（幻灯片取元素文本与脚本、测验取题干/选项/答案/解析、互动场景剥离 HTML 只保留提示与正文、PBL 取项目说明与里程碑），单场景最多 2,500 字符。互动场景的整段 `html` 不会进入 prompt。
- 对话历史：最近 12 条消息，按时间正序拼入 prompt。
- 课程素材摘录：仅当请求 `include_materials=true` 时注入，最多 6,000 字符；默认关闭。

`stage_id` / `scene_id` 必须属于该课程与当前课堂；课堂重生成后旧的 `scene_id` 不会报错，而是退化为「按大纲回答」，回答的 `scene_id` 置空。

## 模型选择与失败处理

沿用生成链路的语义：默认本地 `llama-local`；只有用户在设置页勾选「使用当前模型」（`is_default`）并保存后才改用其配置。规则：

- 空回答：同厂商重试一次；仍为空返回 `502 CHAT_EMPTY_RESPONSE`，不切换厂商。
- 主通道为 `llama-local` 且连接失败或返回非 2xx：对已保存配置单次回退，回答里的 `provider_id` 记录实际生效的厂商。
- 超时（默认 180 秒）：`504 CHAT_TIMEOUT`。
- 其他 Provider 故障：`502 CHAT_PROVIDER_ERROR`。
- 回答上限 `CHAT_MAX_TOKENS`（默认 1,500 tokens），不复用 `generation_jobs`，不会与课堂/旁白/配图任务互斥。

## 限额与持久化

- 单条消息清洗后最多 2,000 字符，空内容返回 `422 VALIDATION_ERROR`。
- 每用户每天最多 200 次提问，按东八区零点切换；超限返回 `429 CHAT_DAILY_LIMIT_REACHED`，`details` 含 `limit` 与 `used`。
- 消息与会话长期保留；课程软删除后接口按 `COURSE_NOT_FOUND` 不可访问，数据仍在库中。
- 每课程只有一条长会话（`UNIQUE(owner_id, course_id)`）。
- 「清空对话」是软清空：写入 `cleared_at`，历史不再返回，但消息保留用于限额统计，避免清空绕过每日上限。P1 不支持删除单条消息。
- 所有读写同时校验 `owner_id` 与 `course.deleted_at IS NULL`，跨用户访问一律 404。

## 前端

课堂播放器场景头部提供「问老师」入口：桌面为右侧 420px 抽屉，移动端为底部弹层。发送后输入框立即清空并显示「老师正在思考…」，失败时恢复草稿；支持 Enter（Shift+Enter 换行），展示按字符计数的 2000 字上限（超出后发送按钮禁用并提示删减）、今日剩余次数与课程素材开关；回答上方显示「第 N 幕 · 标题」与来源场景。

教师回答按安全的 Markdown 子集渲染：标题、有序/无序列表、加粗、斜体、行内代码、代码块、引用与 http(s) 链接；不渲染内嵌 HTML，链接强制 `noopener noreferrer`。

## 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHAT_MAX_MESSAGE_CHARS` | 2000 | 单条消息最大字符数 |
| `CHAT_HISTORY_MESSAGES` | 12 | 带入 prompt 的历史消息条数 |
| `CHAT_MATERIAL_CHAR_LIMIT` | 6000 | 可选素材摘录字符上限 |
| `CHAT_GENERATION_TIMEOUT_SECONDS` | 180 | 单次回答超时 |
| `CHAT_MAX_TOKENS` | 1500 | 回答 token 上限 |
| `CHAT_DAILY_MESSAGE_LIMIT` | 200 | 每用户每日提问次数 |
