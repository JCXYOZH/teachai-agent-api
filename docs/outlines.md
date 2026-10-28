# 课程大纲生成

阶段 3B 使用已配置的 Provider 生成课程大纲。每次生成和手动保存都会创建新的不可变版本，`GET` 返回最新版本。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/courses/{course_id}/outline/generate` | 同步生成大纲并创建新版本 |
| GET | `/api/v1/courses/{course_id}/outline` | 获取最新大纲 |
| PUT | `/api/v1/courses/{course_id}/outline` | 手动保存编辑结果并创建新版本 |

生成请求示例：

```json
{
  "provider_id": "llama-local",
  "model": "Qwen3.6-35B-A3B",
  "scene_count": 8,
  "instructions": "面向零基础学习者，增加一个可拖拽的互动场景。"
}
```

`provider_id` 省略时使用当前用户的默认 Provider；没有默认配置时回退到 `llama-local`。`model` 可临时覆盖 Provider 的默认模型。

## 生成行为

- 自动读取课程关联素材的解析文本，默认最多 16,000 字符；超出时截断并返回 `materials_truncated=true`。
- 模型输出会接受代码围栏、前后解释文字、camelCase 字段、缺失 ID/order 和重复要点。
- 场景 ID 和顺序会被重新规范化；未知场景类型回退为 `slide`。
- JSON 无效或没有可用场景时自动重试一次；仍失败返回 `OUTLINE_INVALID_RESPONSE`。
- 单次生成总超时默认 600 秒，可通过 `OUTLINE_GENERATION_TIMEOUT_SECONDS` 调整。
- 大纲最多 100 个场景，单个大纲 JSON 最多 512 KB。

## 编辑与版本

手动保存请求：

```json
{
  "course_title": "函数的极限",
  "language_directive": "使用简体中文教学，保留英文术语。",
  "scenes": [
    {
      "type": "slide",
      "title": "从变化率出发",
      "description": "用一个直观例子引出极限。",
      "key_points": ["平均变化率", "趋近过程"],
      "estimated_duration": 10
    }
  ]
}
```

版本号从 1 开始递增。生成记录保存 `provider_id`、`model_name` 和素材统计；手动记录保留编辑后的场景，但 `provider_id` 为空。

阶段 3B 不生成场景正文、测验答案、交互代码、图片、音频或视频。
