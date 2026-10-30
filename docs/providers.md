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
  "is_default": true,
  "tts_base_url": null,
  "tts_model": "qwen3-tts-flash",
  "tts_voice": "Cherry"
}
```

## 旁白（TTS）配置

`ProviderView` 在原有字段之外返回：

- `supports_tts`：是否支持语音合成。当前 OpenAI、Qwen 与 GLM 为 `true`。
- `tts_models` / `tts_voices`：建议模型与音色列表。
- `tts_base_url` / `tts_model` / `tts_voice`：当前用户保存的覆盖值，未保存时为 `null`。
- `tts_effective_base_url` / `tts_effective_model` / `tts_effective_voice`：最终生效值（含内置默认值）。

默认组合：Qwen `qwen3-tts-flash` + `Cherry`，OpenAI `gpt-4o-mini-tts` + `coral`，GLM `glm-tts` + `tongtong`。更新配置时传 `clear_tts: true` 可清空三项覆盖值；`clear_tts` 不能与三个 TTS 字段同时提交。

Qwen 的 TTS Base URL 留空时：Provider Base URL 仍是官方默认地址则使用 DashScope 原生接口；如果 Provider Base URL 是阿里云 MaaS 工作区的 `/compatible-mode/v1`，会自动派生同主机的 `/api/v1` 并调用原生语音接口；其他自建兼容网关仍使用 `/audio/speech`。也可以显式填写 `tts_base_url`。生产环境所有非回环地址必须使用 HTTPS。

### Edge TTS 无 Key 后备

设置 `TTS_PROVIDER_ID=edge-tts`，或在旁白请求中传 `"provider_id": "edge-tts"`，即可使用微软 Edge 在线语音，默认音色为 `zh-CN-XiaoxiaoNeural`。该方式不需要 API Key，也不会出现在普通 LLM Provider 列表中；Qwen 仍是默认 TTS。

```json
{
  "provider_id": "edge-tts",
  "voice": "zh-CN-XiaoxiaoNeural"
}
```

可用中文音色包括 `zh-CN-XiaoxiaoNeural`、`zh-CN-XiaoyiNeural`、`zh-CN-YunjianNeural`、`zh-CN-YunxiNeural`、`zh-CN-YunxiaNeural`、`zh-CN-YunyangNeural` 及两个方言音色。该客户端依赖微软在线服务，服务器必须能访问 `speech.platform.bing.com`；它没有官方 SLA，商业部署前应确认服务条款，关键业务建议保留 Qwen 或其他正式 TTS 作为主通道。

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

验证 `qwen3-tts-flash` 的真实语音合成：

```powershell
$env:DASHSCOPE_API_KEY="sk-..."
.\.venv\Scripts\python.exe scripts\check_tts.py `
  --base-url https://<workspace-host>/api/v1 `
  --model qwen3-tts-flash `
  --voice Cherry
```

脚本只输出格式、字节数、时长和错误信息，不打印 API Key。

## 课堂配图

配图复用 Qwen Provider 已保存的 API Key 与 Base URL，默认模型为 `qwen-image-3.0`。如果 Base URL 是 MaaS 的 `/compatible-mode/v1`，后端会派生同主机的 `/api/v1/services/aigc/multimodal-generation/generation` 原生图片接口。

Qwen 请求失败、超时、没有可用 Key 或返回内容无法解码时，任务按 `IMAGE_FALLBACK_CHAIN` 的顺序继续尝试。默认链只有 Pollinations `flux`：不需要 Key，适合开发与兜底，但没有官方 SLA；生产环境应监控 `image_provider_failed` 日志并确认服务条款。

可选兜底通道，未配置凭据时会被跳过并返回 `IMAGE_PROVIDER_NOT_CONFIGURED`：

| 通道 | 免费额度 | 所需凭据 | 说明 |
| --- | --- | --- | --- |
| `pollinations` | 公开服务，无需 Key | 无 | 默认兜底，`flux` 可用；并发与稳定性无保证 |
| `huggingface` | 免费账号额度 | `IMAGE_HF_TOKEN` | 走 Inference API，`IMAGE_HF_MODEL` 可换模型；额度耗尽返回 429/503 |
| `cloudflare` | Workers AI 免费神经元额度 | `IMAGE_CLOUDFLARE_ACCOUNT_ID` + `IMAGE_CLOUDFLARE_API_TOKEN` | 默认 `@cf/black-forest-labs/flux-1-schnell`，输出比例由模型决定 |
| `qwen` | 取决于账号 | Qwen Provider 配置 | 主通道，可要求 16:9 横版 |

示例：`IMAGE_FALLBACK_CHAIN=pollinations,cloudflare,huggingface`。链中没有可用通道时任务以 `IMAGE_PROVIDER_ERROR` 失败，错误信息列出每个通道的错误码。

所有图片都会先下载到内存、限制大小、用 Pillow 解码校验、移除 EXIF、转为 WebP，再写入 `ASSET_STORAGE_ROOT`。临时供应商 URL 不会写入数据库，也不会直接暴露给浏览器。整堂配图只填充尚未配图的幻灯片，达到 `IMAGE_MAX_PER_CLASSROOM` 后返回 `422 IMAGE_LIMIT_REACHED`。

相关配置：

```text
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
IMAGE_FALLBACK_CHAIN=pollinations
IMAGE_HF_ENDPOINT=https://api-inference.huggingface.co/models
IMAGE_HF_MODEL=black-forest-labs/FLUX.1-schnell
IMAGE_HF_TOKEN=
IMAGE_CLOUDFLARE_ACCOUNT_ID=
IMAGE_CLOUDFLARE_API_TOKEN=
IMAGE_CLOUDFLARE_MODEL=@cf/black-forest-labs/flux-1-schnell
```

真实探测（脚本只输出 provider、尺寸、字节数与错误码，不打印任何 Key）：

```powershell
$env:DASHSCOPE_API_KEY="sk-..."
.\.venv\Scripts\python.exe scripts\check_image.py --provider all --out-dir .\image-smoke
```
