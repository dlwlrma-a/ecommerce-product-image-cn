# 算点边界 Qx-Image 接口

根据 2026-09-04 的控制台和公开 API 文档：

- Base URL：`https://token.qixuai.com/v1`
- 模型：`Qx-Image`
- 创建：`POST /images/generations?async=true`
- 查询：`GET /tasks/{task-id}`
- `prompt` 必填，最多 5000 字符
- `size` 支持比例或自定义宽高；控制台提供 `1:1`、`16:9`、`9:16`、`3:2`、`2:3`、`4:3`、`3:4`
- `quality` 支持 `low`、`medium`、`high`
- `image` 可选，最多 3 个图片 URL
- 任务状态可能为 `pending`、`running`、`succeeded` 或 `error`

控制台当时显示固定价格 10 积分/张，实际价格可能调整。每次执行前以控制台为准，并通过 `--points-per-image` 更新计划估算。

脚本将任务 ID 立即写入 `generation_manifest.json`。POST 超时或响应缺少任务 ID 时标记 `submit_unknown` 并停止，不自动重试。先到用量明细确认是否扣费，再决定是否重新建计划。

直接 API 文档只声明图片 URL，没有公开本地文件上传接口。因此脚本不自行猜测上传路径，也不把本地图片静默发送到第三方存储。需要本地上传时使用控制台。
