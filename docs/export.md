# 课堂导出

课堂导出把已生成的课堂版本渲染成可离线分发的单文件，供下载、归档或投放到其它设备播放。导出走异步任务，与大纲、课堂、旁白、配图共用 `/api/v1/generation/jobs/{id}` 轮询。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/courses/{id}/classroom/export` | 创建导出任务，返回 202 与 `queued` 任务 |
| GET | `/api/v1/courses/{id}/classroom/exports` | 列出当前课堂版本已生成的导出文件 |
| GET | `/api/v1/media/{media_id}/content` | 鉴权下载，带 `Content-Disposition: attachment` |

请求体（全部可选，默认 `pptx` + 含图 + 含旁白）：

```json
{"format": "pptx", "include_images": true, "include_audio": true}
```

`format` 只接受 `pptx` 与 `html`，其它取值返回 `422`。

`include_audio=false` 时 PPTX 不写旁白备注，HTML 保留旁白文字但不内联音轨；quiz 参考答案仍会导出。

列表响应：

```json
{
  "stage_id": "…",
  "exports": [
    {
      "format": "pptx",
      "media_id": "…",
      "filename": "监督学习入门-v1.pptx",
      "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "size_bytes": 2401783,
      "scene_count": 8,
      "include_images": true,
      "include_audio": true,
      "warnings": [],
      "content_url": "/api/v1/media/…/content",
      "created_at": "2026-11-02T08:00:00+00:00",
      "updated_at": "2026-11-02T08:00:00+00:00"
    }
  ]
}
```

## 任务阶段

`progress` 依次为 10（Preparing export）、30（Collecting media）、60（Rendering）、85（Saving export）、100。

错误码：

| 错误码 | 状态 | 触发场景 |
| --- | --- | --- |
| `CLASSROOM_NOT_FOUND` | 404 | 课程还没有课堂版本 |
| `EXPORT_EMPTY_CLASSROOM` | 422 | 课堂没有任何场景 |
| `EXPORT_FORMAT_UNSUPPORTED` | 422 | 传入未知格式 |
| `EXPORT_TOO_LARGE` | 413 | 渲染结果超过 `EXPORT_MAX_BYTES` |

## 存储

- 复用 `media_assets`，`kind='export'`、`scene_id` 为空、`action_id='export:<format>'`。
- 每个课堂版本每种格式只保留一行，重新导出覆盖同一 `media_id` 与磁盘文件。
- 文件名规则：`{课堂名 slug}-v{版本}.{pptx|html}`，slug 保留中英文字符，其余替换为 `-`。
- 字节写入资产根目录（默认 `E:\Backups\Data\TeachAIAgent`，生产挂载 `/data/TeachAIAgent`），下载必须经过鉴权 API。

## PPTX 内容

- 16:9，`1280×720` 画布按 1px = 9525 EMU 精确映射，元素位置与尺寸与播放器一致。
- 保留字号、颜色、加粗、斜体、对齐、行距与字距；中文字体同时写入 `a:latin` 与 `a:ea`。
- 配图转 JPEG 并按 cover 裁切铺满页面，叠加四段渐变暗化，保证浅色图片上文字仍可读。
- 勾选「含旁白」时旁白写入备注页；quiz 的参考答案与解析始终写入备注页，正文末尾追加「参考答案」页。
- 元素超出单页会按高度自动分页；quiz / pbl / interactive 生成为独立静态页。
- `slide` 场景按 reveal 动作注入 PowerPoint 原生「单击出现」动画，每个要点单独一次点击；WPS / Keynote / 转 PDF 可能忽略动画但保留最终版式。
- quiz 选项统一输出为 `A. 选项文本`，同一题的题干与全部选项固定在同一页；参考答案显示为 `B（选项文本）`。

## HTML 内容

- 单文件：样式、脚本、配图（`data:image/…;base64,`）与旁白音轨（`data:audio/…;base64,`）全部内联，双击即可离线播放。
- 深色沉浸式布局：左侧场景导航、中间 1280×720 画布等比缩放、底部进度条与上下步按钮。
- 支持键盘 ←/→/Home/End、`#scene-N` 锚点、切换场景自动暂停其它音轨；`@media print` 下每页分页打印。
- 幻灯片元素 HTML 经白名单清洗：丢弃 `script/iframe/object/embed/svg/math/style` 子树，剥离事件属性，`style` 只保留安全声明并拦截 `url(`、`expression(`、`javascript:`。
- 交互场景的组件 HTML 以 `sandbox="allow-scripts"` 的内联 iframe 嵌入，双击导出的 HTML 即可离线操作；组件无法访问宿主页、网络请求会被 CSP 拦截，刷新后交互状态重置。
- quiz 选项输出为 `A. 选项文本`，参考答案输出为 `B（选项文本）`。

## 体积与告警

- `EXPORT_MAX_BYTES` 默认 `41943040`（40 MB），最终文件超过上限直接以 `EXPORT_TOO_LARGE` 失败。
- 旁白音频预算为上限的 60%；复制到 `ExportDocument` 时按场景顺序累加，超出后剩余场景丢弃音频，并在 `warnings` 记录一条提示。
- HTML 渲染再按 60% 复核一次音频总量，超限则整体不内联音频并追加告警。
- 配图或旁白资产缺失、读取失败只会写入 `warnings`，不阻断导出。

## 前端

`components/classroom/export-panel.tsx` 提供格式切换（PPTX / HTML）、含配图与含旁白开关、生成按钮、进度提示与已生成文件列表；`lib/api/exports.ts` 封装创建任务、列表查询与 Blob 下载。
