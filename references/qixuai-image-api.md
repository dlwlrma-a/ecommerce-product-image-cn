# 算点边界 Qx-Image 接口

根据 2026-09-06 已部署接口和 V3 文档：

- Base URL：`https://token.qixuai.com/v1`
- 模型：`Qx-Image`
- 上传：`POST /images/uploads`，`multipart/form-data`，文件字段名 `file`
- 创建：`POST /images/generations?async=true`
- 查询：`GET /tasks/{task-id}`
- 充值：<https://token.qixuai.com/console/recharge?intent=buy>

`prompt` 必填且最多 5000 字符。`size` 支持比例或自定义宽高；`quality` 支持 `low`、`medium`、`high`；`image` 最多包含 16 个已上传的 HTTPS URL。上传支持 PNG、JPG、JPEG、WEBP、GIF，单文件最大 10 MB，响应中的 URL 通常保留 24 小时。

## 直接生成

用户确认套图方案后，脚本直接上传本地参考图并逐张调用创建接口，不调用报价接口。每张请求包含完整模型、提示词、尺寸、质量和全部同一 SKU 参考图，并发送 HTTP `Idempotency-Key`。同一任务重试必须复用原键。

服务端余额充足时直接扣积分并创建异步任务；积分不足时返回 HTTP 402 `insufficient_credits`，其中包含当前余额、所需积分和充值链接。该响应表示任务没有创建、没有扣费，可以充值后继续。

脚本会记录本地文件的角色与 SHA-256，上传前重新核对；成功上传 URL、有效期、幂等键和任务 ID 会立即写入 `generation_manifest.json`。上传结果未知时停止检查；生成结果未知时保留同一幂等键重试。
