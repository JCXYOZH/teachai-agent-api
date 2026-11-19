# 待开发路线（Roadmap）

本文件是 TeachAIAgent（后端 `teachai-agent-api` + 前端 `teachai-agent-web`）唯一的待办清单来源，每完成一项就同时在这更新状态。

## 当前状态

阶段 1–6D 与 P0/P1/P2 全部完成并已生产联调：认证（邮箱 / 手机号验证码注册）、课程与文件夹、本地素材（上传、解析、检索、鉴权下载）、用户级 Provider 配置与真实连接测试、版本化课程大纲、课堂 DSL 与沉浸式播放器、旁白合成、课堂配图（Qwen Image + 免费兜底）、课堂导出（PPTX 保真版 + 单文件 HTML）、教师对话（SSE 流式）、生成任务队列与可观性，以及 Docker + 宿主 Nginx + TLS 生产部署与 frp 公网接入。

最近一次语义调整（已实现并测试）：

- 生成默认固定使用本地 `llama-local`（`Qwen3.6-35B-A3B`），只有用户在设置页勾选「使用当前模型」并保存后才切换到该 Provider。
- 不勾选时仅保存配置信息，不做多厂商链式兜底；本地模型不可用时对已保存配置单次回退一次。
- 响应解析失败不换厂商，同厂商重试一次。

最近一轮修复（已实现并测试）：上传超限改为前端按 50 MB 预检、后端按 `Content-Length` 在读 body 前返回 `413 FILE_TOO_LARGE`，多文件上传逐份上报失败；下载 Blob 改为延迟 `revokeObjectURL`，修掉 Firefox/Safari 下载中断；Nginx 上传路径流式转发（`proxy_request_buffering off`）；前端镜像改用 BuildKit 缓存挂载缩短构建时间。

回归基线：后端 `pytest` 246 passed（默认 `-n 4` 并行，约 2 分钟）、`ruff`/`mypy` 干净；前端 `typecheck`/`lint` 通过、`vitest` 114 passed。任何新功能都必须在这条基线上不退化。

## 待办

- 暂无。P0/P1/P2 已全部完成，后续新开发需求直接再追加在本文件。
