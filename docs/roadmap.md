# 待开发路线（Roadmap）

本文件是 TeachAIAgent（后端 `teachai-agent-api` + 前端 `teachai-agent-web`）唯一的待办清单来源，完成一项就同时在这更新状态。

## 当前状态

阶段 1–6D 全部完成并已生产联调：认证与邮箱验证码注册、课程/文件夹与本地素材（上传、解析、检索、鉴权下载）、用户级 Provider 配置与真实连接测试、版本化课程大纲、课堂 DSL 与沉浸式播放器、旁白合成、课堂配图（Qwen Image + 免费兜底）、课堂导出（PPTX 保真版 + 单文件 HTML）、Docker + 宿主 Nginx + TLS 生产部署。

最近一次语义调整（已实现并测试）：

- 生成默认固定使用本地 `llama-local`（`Qwen3.6-35B-A3B`），只有用户在设置页勾选「使用当前模型」并保存后才切换到该 Provider。
- 不勾选时仅保存配置信息，不做多厂商链式兜底；本地模型不可用时对已保存配置单次回退一次。
- 响应解析失败不换厂商，同厂商重试一次。

回归基线：后端 `pytest` 185 passed、`ruff`/`mypy` 干净；前端 `typecheck`/`lint` 通过、`vitest` 86 passed。任何新功能都必须在这条基线上不退化。

## 非目标（明确不做）

- 不保留教师 / 助教 / 学生多 Agent 体系：`teachai` 只需单 Agent 的「教师」角色，供用户对话、互动提问与解答。
- 不做学生端独立身份与班级/作业流转（当前所有资源都按 owner 隔离）。
- 不做自动挑选具体模型替用户决策：换模型只能由用户改对应厂商的配置并显式勾选。
- 不引入多租户组织/团队协作模型，除非另行拍板。

## P0：用户输入文本清洗补全（已完成）

现状：模型输出侧已经在落库前统一走 `app/core/text.py` 的 `sanitize_text` / `sanitize_json`，但用户输入的文本没有。用户提交含 `0x00` 的字符串时，`asyncpg` 抛 `CharacterNotInRepertoireError`，接口 500。

待覆盖入口：

- 建课 / 改课：`title`、`description`。
- 文件夹名称。
- 手工保存大纲（`PUT` 大纲）里的标题与场景文本。
- 对话功能新增的消息内容（实现时同步接入）。

验收：向上述每个入口提交含 `\x00`、未配对代理项、超长空白的文本，均返回 200/201 且落库内容已被清洗；现有 140 + 66 条测试不退化。

状态：已完成。清洗实现在 `app/core/text.py` 的 `normalize_user_text` / `normalize_user_text_input`，入口覆盖建课改课标题与简介、文件夹名、手工保存大纲、对话消息（`CHAT_MAX_MESSAGE_CHARS=2000`，前端按 code point 计数并禁用超限发送）。

## P1：课堂内「用户 ↔ 教师」对话问答（已完成）

状态：阶段 5 已完成并通过回归。实现见 `docs/chat.md`、`app/services/chat.py`、`app/services/chat_prompt.py`、`app/api/routes/chat.py`、`components/classroom/teacher-chat.tsx`。

已拍板并落地的决策：

1. 入口：课堂播放器场景头部「问老师」按钮；桌面右侧抽屉、移动端底部弹层。
2. 上下文：最新大纲 + 当前场景正文 + 最近 12 条对话；课程素材摘录默认关闭，开启后上限 6,000 字符。
3. 返回：普通 JSON 同步返回，P1 不做 SSE。
4. 会话：每课程一条长会话；只支持「清空对话」，不做历史会话列表。
5. 持久化：消息入库并长期保留；课程软删除后不可访问；清空为软清空（`cleared_at`），保留限额统计。
6. 教师行为：只答不追问，不做模型生成引用；界面显示服务端确定的「第 N 幕 · 标题」。
7. 模型与限额：沿用「默认本地模型 + 勾选才切换」；单条消息 ≤ 2,000 字；每用户每天 200 条；回答 ≤ 1,500 tokens。
8. 移动端：与桌面同一组件的底部弹层形态。
9. 与生成任务：独立同步接口，不复用 `generation_jobs`，避免与课堂/旁白/配图互斥。

验收结果：围绕当前课堂连续问答可用；回答与大纲/场景一致；默认走本地模型且切换逻辑与设置页一致；刷新后历史恢复；移动端可用；P0 与既有测试保持通过。

## P1.5：手机号注册（默认注册方式，已完成）

需求：账号默认使用手机号注册，邮箱注册通过注册页下方小字切换；邮箱注册的账号依旧可以登录。

已拍板并落地的后端决策（阶段 2 完成）：

1. 数据模型：`users.email` 改为可空，新增 `users.phone`（唯一、可空），`CHECK (email IS NOT NULL OR phone IS NOT NULL)` 兜底；迁移 `0015_phone_auth`。
2. 验证码：新建 `phone_verifications`，结构与 `email_verifications` 一致，共用 TTL、重发冷却与尝试次数。
3. 发送通道：`SMS_DELIVERY=memory|log|spug`；`spug` 走 push.spug.cc 模板推送，`SPUG_URL` 属凭证只放 `.env.production`；生产强制 `spug` 且必须 HTTPS。
4. 接口：`register/send-code` 与 `register` 接受 `email` 或 `phone`（互斥，违反返回 `422`）；发码响应新增 `channel` / `target`；登录统一用 `identifier`，旧的 `email` 字段仍兼容。
5. 展示：所有响应里的手机号脱敏为 `138****8000`；手机号注册默认昵称 `用户` 加手机号后四位。
6. 错误码新增 `PHONE_ALREADY_REGISTERED`、`PHONE_VERIFICATION_NOT_FOUND`、`SMS_DELIVERY_UNAVAILABLE`。

前端也已落地：注册页默认手机号、输入框下方小字「使用邮箱注册 / 使用手机号注册」切换（切换时清空格式不匹配的联系人）、验证码区标题随渠道变化；登录页单输入框「手机号或邮箱」；设置页与侧边栏改为 `email ?? phone` 展示。

验收结果（阶段 4 联调，用 15398287367 走真实链路，`SMS_DELIVERY=log` 从日志取码）：发码 `202` 且返回 `153****7367`、注册 `201`、`identifier` 登录与 `/auth/me` 均返回脱敏手机号、重复号码 `409`、双字段 `422`、错误密码 `401`；浏览器端登录注册切换、字段校验、登录回跳 `/library`、设置页与侧边栏展示脱敏号码均正常，无控制台错误。后端 185 passed、前端 86 passed。

## P2：体验与稳定性

- 任务队列：当前生成任务用 FastAPI `BackgroundTasks` 在进程内执行，进程重启会中断（已有 `recover_interrupted_jobs` 兜底标记失败）。可评估独立 worker + 队列（Redis/RQ 或 arq）以支持多副本与重试。
- 公网接入：`cpolar` 免费额度带宽与稳定性有限，长任务易断流；后续换 frp TCP 透传或内网 443 端口映射。
- 配图质量：引入图片质量评估与可选正式图片服务商（当前 Qwen Image 主通道 + 免费兜底）。
- 导出体验：确认移动端 PPTX 逐条动画与 HTML 交互组件在更多浏览器上的表现，补齐回归用例。
- 运维可观测：结构化日志已具备 `request_id`，可补任务耗时与 Provider 调用失败率的聚合。
