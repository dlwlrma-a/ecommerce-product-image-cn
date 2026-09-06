# 算点边界 Qx-Image 接口

根据 2026-09-06 已部署接口和 V3 文档：

- Base URL：`https://token.qixuai.com/v1`
- 模型：`Qx-Image`
- 上传：`POST /images/uploads`，`multipart/form-data`，文件字段名 `file`
- 报价：`POST https://token.qixuai.com/api/billing/quotes/image-generation`
- 创建：`POST /images/generations?async=true`
- 查询：`GET /tasks/{task-id}`
- 余额：`GET https://token.qixuai.com/api/billing/balance`
- 充值：<https://token.qixuai.com/console/recharge?intent=buy>

`prompt` 必填且最多 5000 字符。`size` 支持比例或自定义宽高；`quality` 支持 `low`、`medium`、`high`；`image` 最多包含 16 个已上传的 HTTPS URL。上传支持 PNG、JPG、JPEG、WEBP、GIF，单文件最大 10 MB，响应中的 URL 通常保留 24 小时。

## 精确报价

请求使用完整任务结构：

```json
{
  "model": "Qx-Image",
  "jobs": [
    {
      "model": "Qx-Image",
      "prompt": "完整提示词",
      "size": "1:1",
      "quality": "medium",
      "image": ["https://example.com/reference.jpg"]
    }
  ]
}
```

成功响应包含 `quote_id`、`total_credits`、`balance`、`sufficient`、`expires_at` 和 `pricing_version`。报价有效期 5 分钟，不冻结、不扣除积分。匿名请求返回 HTTP 401；错误还可能为 400、403、409。

## 正式生成

生成请求必须与报价中的模型、提示词、尺寸、质量和参考图一致，并增加 `quote_id`：

```json
{
  "model": "Qx-Image",
  "prompt": "完整提示词",
  "size": "1:1",
  "quality": "medium",
  "image": ["https://example.com/reference.jpg"],
  "quote_id": "服务端报价标识"
}
```

同时发送 HTTP `Idempotency-Key`。同一任务重试必须复用原键。服务端可能返回 HTTP 402 `insufficient_credits`、409 参数或价格冲突、410 报价过期；这些情况都不能自动改价并继续。

脚本会记录本地文件的角色与 SHA-256，上传前重新核对；成功上传 URL、有效期、报价请求指纹、价格版本、幂等键和任务 ID 都会立即写入 `generation_manifest.json`。报价后参考 URL 过期或计划发生变化时必须重新报价。
