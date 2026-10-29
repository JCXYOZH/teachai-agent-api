# 注册与邮箱验证

## 流程

1. 点击“发送验证码”时调用 `POST /api/v1/auth/register/send-code`，请求体只有 `email`，返回 `202`：

   ```json
   {
     "email": "teacher@example.com",
     "expires_in": 600,
     "resend_after": 60
   }
   ```

   此时不会创建用户，也不会保存密码。

2. 用户填写邮箱验证码、密码和可选昵称后调用 `POST /api/v1/auth/register`：

   ```json
   {
     "email": "teacher@example.com",
     "password": "Teach1234",
     "display_name": "林老师",
     "code": "123456"
   }
   ```

   验证码校验通过后才会创建用户，返回 `201`，响应体与登录一致，并设置 refresh/CSRF cookie。验证码错误时不会写入用户，也不会消耗待验证记录。

3. 未收到邮件时再次调用 `POST /api/v1/auth/register/send-code`；60 秒冷却期内返回 `429`。

创建成功前不会生成 `users` 行，因此登录会返回 `401`，同一邮箱也无法被重复注册或覆盖密码。数据库中已经存在的用户不受影响，仍可直接登录。

## 数据与安全

- `users.email` 保留原有唯一约束，并新增 `lower(email)` 唯一索引。
- `email_verifications` 只保存待验证邮箱、验证码摘要、尝试次数、过期时间和发送时间；密码与昵称仅在最终创建账号时处理。
- 同一邮箱只允许一条未消费的待验证记录（部分唯一索引）。
- 验证码使用 `jwt_secret` 做 HMAC-SHA256 摘要，数据库泄露时无法直接还原。
- 验证码默认 10 分钟过期；重发冷却 60 秒；连续错误 5 次后必须重发。
- 验证码错误只返回剩余次数，不区分邮箱是否存在之外的额外信息。
- 未消费记录在服务启动时清理超过 1 天的数据。

## 邮件发送

配置项（`.env`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EMAIL_DELIVERY` | `log` | `memory`（测试）、`log`（开发）、`agentsend`（真实发送） |
| `AGENTSEND_API_KEY` | 空 | agentsend 密钥，必填于 `agentsend` 模式 |
| `AGENTSEND_INBOX_NAME` | `teachai-support` | agentsend inbox 名称 |
| `AGENTSEND_INBOX_ID` | 空 | 固定 inbox ID；为空时按名称查找或创建 |
| `AGENTSEND_BASE_URL` | `https://api.agentsend.io` | AgentSend API 地址 |
| `AGENTSEND_TIMEOUT_SECONDS` | `20` | 邮件 API 超时 |
| `VERIFICATION_CODE_TTL_SECONDS` | `600` | 验证码有效期 |
| `VERIFICATION_RESEND_SECONDS` | `60` | 重发冷却 |
| `VERIFICATION_MAX_ATTEMPTS` | `5` | 单个验证码允许的错误次数 |

开发时 `EMAIL_DELIVERY=log`，验证码会以 `email_verification_code email=... code=...` 写入 uvicorn 日志。

真实发送直接调用 AgentSend 公开 REST API：

```http
POST https://api.agentsend.io/inboxes
x-api-key: <AGENTSEND_API_KEY>
{"displayName": "teachai-support"}

POST https://api.agentsend.io/inboxes/{inbox_id}/messages
x-api-key: <AGENTSEND_API_KEY>
{"to": ["teacher@example.com"], "subject": "TeachAI 邮箱验证码", "bodyText": "..."}
```

首次发送会按名称复用已有 inbox，找不到时自动创建并在进程内缓存。发送失败时接口返回 `503 EMAIL_DELIVERY_UNAVAILABLE`，并立即释放冷却，便于重试。

生产环境必须设置 `EMAIL_DELIVERY=agentsend` 与 `AGENTSEND_API_KEY`，否则应用拒绝启动。

## 错误码

| HTTP | code | 含义 |
| --- | --- | --- |
| 409 | `EMAIL_ALREADY_REGISTERED` | 邮箱已有账号 |
| 409 | `REGISTRATION_ALREADY_PENDING` | 并发注册，已有待验证记录 |
| 400 | `EMAIL_VERIFICATION_NOT_FOUND` | 没有待验证记录或验证码已消费 |
| 400 | `VERIFICATION_CODE_EXPIRED` | 验证码过期 |
| 400 | `INVALID_VERIFICATION_CODE` | 验证码错误，`details.attempts_remaining` 为剩余次数 |
| 429 | `VERIFICATION_ATTEMPTS_EXCEEDED` | 错误次数用尽 |
| 429 | `VERIFICATION_RESEND_TOO_SOON` | 重发过于频繁，`details.retry_after` 为秒数 |
| 503 | `EMAIL_DELIVERY_UNAVAILABLE` | 邮件服务未配置或发送失败 |

## 迁移

`0007_email_verification` 创建验证表与 `lower(email)` 唯一索引。升级前会检查历史重复邮箱；若存在，迁移会中止并列出冲突邮箱，要求先人工处理，避免静默删除或合并用户数据。`0008_email_verification_code` 移除验证表中不再需要的密码和昵称列，使待验证记录只承担验证码职责。
