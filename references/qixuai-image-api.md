# 算点边界 Qx-Image 接口

根据 2026-09-04 的控制台和公开 API 文档：

- Base URL：`https://token.qixuai.com/v1`
- 模型：`Qx-Image`
- 上传：`POST /images/uploads`，`multipart/form-data` 的文件字段名为 `file`
- 创建：`POST /images/generations?async=true`
- 查询：`GET /tasks/{task-id}`
- `prompt` 必填，最多 5000 字符
- `size` 支持比例或自定义宽高；控制台提供 `1:1`、`16:9`、`9:16`、`3:2`、`2:3`、`4:3`、`3:4`
- `quality` 支持 `low`、`medium`、`high`
- `image` 可选，最多 3 个图片 URL
- 上传支持 PNG、JPG、JPEG、WEBP、GIF，单文件最大 10 MB；成功响应顶层 `url` 是后续生图请求使用的 HTTPS 地址
- 任务状态可能为 `pending`、`running`、`succeeded` 或 `error`

控制台当时显示固定价格 10 积分/张，实际价格可能调整。每次执行前以控制台为准，并通过 `--points-per-image` 更新计划估算。

脚本在计划阶段记录本地文件的 SHA-256，上传前重新核对，避免计划审核后文件被替换。成功上传的 URL 和任务 ID 都立即写入 `generation_manifest.json`，恢复执行时不重复上传或提交。

上传或任务 POST 超时、响应缺少 URL/任务 ID 时分别标记 `upload_unknown` 或 `submit_unknown` 并停止，不自动重试。先检查上传记录或用量明细，再决定是否用已知 URL 新建计划，避免重复上传或扣费。
