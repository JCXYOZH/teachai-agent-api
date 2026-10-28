# Provider 配置

阶段 3A 提供用户级模型 Provider 配置。支持内置 Provider：OpenAI、Anthropic、DeepSeek、Qwen/DashScope、Kimi/Moonshot、GLM/Zhipu 和本地 llama.cpp。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/providers` | 列出内置 Provider 及当前用户的配置状态 |
| GET | `/api/v1/providers/{id}` | 查看单个 Provider |
| PUT | `/api/v1/providers/{id}/config` | 新建或更新配置 |
| DELETE | `/api/v1/providers/{id}/config` | 删除用户配置，回退到环境变量或内置默认值 |
| POST | `/api/v1/providers/{id}/test` | 测试已保存配置；可传临时覆盖值 |

更新配置示例：

```json
{
  "api_key": "sk-...",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "default_model": "qwen-plus",
  "is_default": true
}
```

自定义 Qwen 兼容网关只需替换 `base_url` 和 `default_model`。例如阿里云 MaaS 地址
`https://<workspace-host>/compatible-mode/v1` 可直接保存，前提是域名解析到公网地址。
`qwen3.8-omni-flash` 已作为 Qwen 建议模型列出；该模型只输出文本，连接测试会发送
`modalities: ["text"]`。

## 连接测试

`POST /api/v1/providers/{id}/test` 会真实执行一次最小生成请求，而不是只看模型列表：

- OpenAI 兼容 Provider 调用 `POST /chat/completions`，`max_tokens=8`，并返回实际响应中的模型名。
- Anthropic 调用原生 `POST /messages`。
- `/models` 只用于补充模型列表，即使它返回 200 也不会单独判定连接成功。
- 测试可以使用已保存配置，也可以携带未保存的 `api_key`、`base_url`、`default_model` 覆盖值。
- 响应只包含状态、延迟、模型名和模型列表，不返回 API Key 或完整模型输出。

## 安全约束

- API Key 使用 Fernet 加密后保存到 PostgreSQL，接口只返回是否已配置和末四位掩码。
- `PROVIDER_KEY_ENCRYPTION_SECRET` 可单独设置；未设置时从 `JWT_SECRET` 派生。生产环境建议单独设置且不要轮换。
- 自定义 Base URL 拒绝 URL 内嵌账号密码、查询参数、片段、私网/保留地址和重定向。
- 本地 `localhost`/`127.0.0.1` 仅在开发/测试环境开放；`llama-local` 始终允许本地地址。
- 每个用户最多只有一个默认 Provider，由数据库部分唯一索引保证。

## 环境变量兜底

用户未保存对应配置时，会依次使用环境变量和内置默认值。已支持：

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
DEEPSEEK_API_KEY
QWEN_API_KEY
DASHSCOPE_API_KEY
GLM_API_KEY
KIMI_API_KEY
LLAMA_BASE_URL
LLAMA_API_KEY
LLAMA_MODEL
```

`PROVIDER_TEST_TIMEOUT_SECONDS` 控制连接测试超时，默认 20 秒。
`OUTLINE_GENERATION_TIMEOUT_SECONDS` 控制大纲生成总超时，默认 600 秒。

Qwen 会优先读取 `QWEN_API_KEY`，为空时回退到 `DASHSCOPE_API_KEY`。可以用诊断脚本确认真实调用：

```powershell
.\.venv\Scripts\python.exe scripts\check_qwen.py `
  --base-url https://<workspace-host>/compatible-mode/v1 `
  --model qwen3.8-omni-flash
```
