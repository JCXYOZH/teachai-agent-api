# 注册与身份验证

## 账号标识

- 默认注册方式改为手机号（中国大陆 11 位手机号）；邮箱注册通过注册页底部的「使用邮箱注册」切换。
- 两种账号都能用「手机号或邮箱 + 密码」登录：`POST /api/v1/auth/login` 传 `identifier`，旧的 `email` 字段仍兼容。
- `users.email` 与 `users.phone` 都可为空，但必须二选一；数据库用 `CHECK` 约束 `ck_users_contact_present` 兜底。

## 手机号注册（默认）

1. 点击「发送验证码」调用 `POST /api/v1/auth/register/send-code`，请求体只传 `phone`，返回 `202`：

   ```json
   {
     "channel": "phone",
     "target": "138****8000",
     "email": null,
     "expires_in": 600,
     "resend_after": 60
   }
   ```

   此时不会创建用户，也不会保存密码。手机号统一归一化：去掉空格、`-`、`()`，去掉 `+86` / `86` 前缀，只接受 `1[3-9]` 开头的 11 位号码。

2. 用户填写短信验证码、密码和可选昵称后调用 `POST /api/v1/auth/register`：

   ```json
   {
     "phone": "13800138000",
     "password": "Teach1234",
     "display_name": "林老师",
     "code": "123456"
   }
   ```

   验证码校验通过后才会创建用户，返回 `201`，响应体与登录一致，并设置 refresh/CSRF cookie。省略 `display_name` 时默认用 `用户` 加手机号后四位。

3. 未收到短信时再次调用 `POST /api/v1/auth/register/send-code`；60 秒冷却期内返回 `429`。

## 邮箱注册（切换入口）

流程不变，`POST /api/v1/auth/register/send-code` 请求体只传 `email`，响应里 `channel` 为 `email`、`target` 为归一化后的邮箱：

```json
{
  "channel": "email",
  "target": "teacher@example.com",
  "email": "teacher@example.com",
  "expires_in": 600,
  "resend_after": 60
}
```

创建成功前不会生成 `users` 行，因此登录会返回 `401`，同一邮箱也无法被重复注册或覆盖密码。数据库中已经存在的邮箱账号不受影响，仍可直接登录。

## 互斥校验

`register` 与 `register/send-code` 都要求 `email`、`phone` 恰好传一个：同时传或都不传返回 `422 VALIDATION_ERROR`。

## 登录

`POST /api/v1/auth/login`：

```json
{
  "identifier": "13800138000",
  "password": "Teach1234"
}
```

- `identifier` 能解析为手机号时按 `users.phone` 查，否则按归一化邮箱查。
- 账号不存在与密码错误返回同一个 `401 INVALID_CREDENTIALS`，不做账号枚举。

## 数据与安全

- `users.email` 保留原有唯一约束与 `lower(email)` 唯一索引；新增 `users.phone`，唯一且可空。
- `email_verifications` 与 `phone_verifications` 结构一致：只保存待验证标识、验证码摘要、尝试次数、过期时间和发送时间。
- 同一手机号只允许一条未消费的待验证记录（部分唯一索引）。
- 验证码使用 `jwt_secret` 做 HMAC-SHA256 摘要，摘要输入为 `identifier:code`，数据库泄露时无法直接还原。
- 验证码默认 10 分钟过期；重发冷却 60 秒；连续错误 5 次后必须重发。
- 所有对外响应中的手机号一律脱敏为 `138****8000`。
- 未消费记录在服务启动时清理超过 1 天的数据（两张表一起）。

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

## 短信发送

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SMS_DELIVERY` | `log` | `memory`（测试）、`log`（开发，验证码写日志）、`spug`（真实短信） |
| `SPUG_URL` | 空 | push.spug.cc 推送模板地址，`spug` 模式必填；生产必须 HTTPS |
| `SPUG_TIMEOUT_SECONDS` | `10` | 短信 API 超时 |
| `SMS_IP_LIMIT_PER_MINUTE` | `3` | 同一 IP 每分钟最多发码次数 |
| `SMS_IP_LIMIT_PER_HOUR` | `10` | 同一 IP 每小时最多发码次数 |
| `SMS_TARGET_LIMIT_PER_HOUR` | `5` | 同一手机号每小时最多发码次数 |

开发时 `SMS_DELIVERY=log`，验证码以 `sms_verification_code phone=138****8000 code=... ttl_seconds=600` 写入 uvicorn 日志（号码已脱敏）。

真实发送是一次模板推送：

```http
POST <SPUG_URL>
{"name": "尊敬的用户", "code": "123456", "targets": "13800138000"}
```

返回 200 视为成功，其余状态码与网络异常都抛 `SmsDeliveryError`，接口返回 `503 SMS_DELIVERY_UNAVAILABLE` 并立即释放冷却。`SPUG_URL` 含模板 ID，属凭证，只放 `.env.production`，不要提交到 Git。

### 发送限流（防刷）

手机号渠道在 60 秒重发冷却之外，还有三层滑动窗口限流，命中任意一层返回 `429 SMS_SEND_RATE_LIMITED`，`details.retry_after` 为建议等待秒数：

- 同一 IP：每分钟最多 `SMS_IP_LIMIT_PER_MINUTE` 次、每小时最多 `SMS_IP_LIMIT_PER_HOUR` 次。
- 同一手机号：每小时最多 `SMS_TARGET_LIMIT_PER_HOUR` 次。

计数落在 `verification_send_logs`（`channel`、`target`、`ip_address`、`created_at`），每次进入发送流程前写一行；服务启动时清理 1 天前的记录。邮箱渠道不受这组限流约束，仍只受 60 秒冷却与尝试次数限制。IP 取自 `get_client_ip`，生产在反向代理后读取 `X-Forwarded-For` 首段。

`APP_ENV=production` 时强制 `SMS_DELIVERY=spug`、`EMAIL_DELIVERY=agentsend`，且 `SPUG_URL`、`AGENTSEND_BASE_URL` 必须是 HTTPS，否则应用拒绝启动。

## 错误码

| HTTP | code | 含义 |
| --- | --- | --- |
| 409 | `EMAIL_ALREADY_REGISTERED` | 邮箱已有账号 |
| 409 | `PHONE_ALREADY_REGISTERED` | 手机号已有账号 |
| 409 | `REGISTRATION_ALREADY_PENDING` | 并发注册，已有待验证记录 |
| 400 | `EMAIL_VERIFICATION_NOT_FOUND` | 没有待验证的邮箱验证码记录 |
| 400 | `PHONE_VERIFICATION_NOT_FOUND` | 没有待验证的手机验证码记录 |
| 400 | `VERIFICATION_CODE_EXPIRED` | 验证码过期 |
| 400 | `INVALID_VERIFICATION_CODE` | 验证码错误，`details.attempts_remaining` 为剩余次数 |
| 429 | `VERIFICATION_ATTEMPTS_EXCEEDED` | 错误次数用尽 |
| 429 | `VERIFICATION_RESEND_TOO_SOON` | 重发过于频繁，`details.retry_after` 为秒数 |
| 429 | `SMS_SEND_RATE_LIMITED` | 短信发送超过 IP/号码限额，`details.retry_after` 为秒数 |
| 503 | `EMAIL_DELIVERY_UNAVAILABLE` | 邮件服务未配置或发送失败 |
| 503 | `SMS_DELIVERY_UNAVAILABLE` | 短信服务未配置或发送失败 |

## 迁移

`0007_email_verification` 创建验证表与 `lower(email)` 唯一索引。升级前会检查历史重复邮箱；若存在，迁移会中止并列出冲突邮箱，要求先人工处理，避免静默删除或合并用户数据。`0008_email_verification_code` 移除验证表中不再需要的密码和昵称列，使待验证记录只承担验证码职责。

`0015_phone_auth` 把 `users.email` 改为可空，新增 `users.phone`（唯一、可空）与 `CHECK (email IS NOT NULL OR phone IS NOT NULL)`，并新建 `phone_verifications`。升级前已有的邮箱账号无需改动；降级会删除手机号列与手机验证表，需先确认没有仅用手机号注册的账号。

`0016_sms_send_logs` 新建 `verification_send_logs` 及两个组合索引（`ip_address, created_at` 与 `channel, target, created_at`），只追加表、不改动既有数据；降级会删表并清空防刷计数。
