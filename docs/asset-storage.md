# 资产存储

课程素材（用户上传的原文件）落本地磁盘，PostgreSQL 只保存元数据、所有权与解析文本，不存文件字节。这样数据库体积可控、备份口径清晰，也便于以后整体替换成对象存储。

## 存储位置

默认根目录为 `E:\Backups\Data\TeachAIAgent`，用 `ASSET_STORAGE_ROOT` 覆盖；生产固定为 `/data/TeachAIAgent`，以 bind mount 挂进容器。

- API 进程必须对根目录有读写权限。容器内以 `10001:10001` 运行，宿主机目录需 `sudo chown -R 10001:10001 /data/TeachAIAgent`。
- 根目录必须是绝对路径，启动时校验，不满足直接失败。
- 根目录不能通过 `/static` 暴露，静态托管只能指向别处。
- Docker 部署必须挂持久卷，并把 `ASSET_STORAGE_ROOT` 指到挂载点。
- 备份必须同时覆盖 PostgreSQL 与该目录，否则元数据会指向不存在的文件。

## 目录布局

```text
<root>/
  <owner_id>/
    <asset_id>/
      original<extension>
```

路径全部使用生成的 UUID，用户提供的文件名只作为元数据保存，因此天然免疫路径穿越，也不会出现同名覆盖。

## 数据库元数据

`assets` 表记录：

| 字段 | 说明 |
| --- | --- |
| `id`、`owner_id`、`course_id` | 素材主键、所有者与可选归属课程 |
| 原始文件名 | 仅用于展示与下载命名 |
| 媒体类型、字节数 | 服务端探测结果，不信任客户端声明 |
| SHA-256 | 内容摘要，用于去重与完整性校验 |
| 相对存储 key、解析状态 | 相对 `ASSET_STORAGE_ROOT` 的路径 |
| 创建、更新、删除时间 | 软删除用 `deleted_at` 标记 |

解析出的文本与分段保存在 `asset_extractions`，不写旁挂文件，因此备份只需「数据库 + 根目录」两份。

## 读写与权限

- 所有读写按 `owner_id` 作用域过滤，越权访问返回 404。
- 下载走鉴权接口流式返回，存储根目录永不对外。
- 写入先落临时文件，校验通过后原子 rename，避免留下半截文件。
- 删除是数据库层软删除，误删课程不会立刻销毁原始素材。
- 上传限制：体积上限（`ASSET_MAX_BYTES`，默认 50 MB）、扩展名白名单、PDF 头签名、Office 压缩包结构、加密文档检测、解析超时（`ASSET_PARSE_TIMEOUT_SECONDS`）。
- 解析粒度：PDF 按页切分；DOCX 取段落与表格；PPTX 取幻灯片与备注；文本文件按行切段。
- 文本编码支持 UTF-8、GB18030 与带 BOM 的 UTF-16。
- 中文检索用 PostgreSQL `ILIKE` 与 `strpos`；`pg_trgm` 可选，**不使用 `pgvector`**。

## 文件删除

软删除素材依旧会继续占用磁盘，确认用户无误删运维可定时显式清理。清理脚本默认 dry run：

```powershell
.\.venv\Scripts\python.exe scripts\purge_deleted_assets.py --older-than-days 30
```

确认列出的目录无误后加 `--apply` 执行。脚本只删存储目录，数据库记录保留以便审计。

## 运维自检

`GET /api/v1/health/ready` 会检查：

- 根目录是否存在、是否可写
- 可用磁盘空间
- 一次写入探针（写完立即删除）

文本检索完全基于 PostgreSQL，本存储设计不依赖任何向量扩展。
