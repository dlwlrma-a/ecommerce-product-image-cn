# QixuAI 精确报价、余额与充值续跑

## 接口

- 余额：`GET https://token.qixuai.com/api/billing/balance`
- 图片报价：`POST https://token.qixuai.com/api/billing/quotes/image-generation`
- 权限：`billing:read`
- 充值：<https://token.qixuai.com/console/recharge?intent=buy>

报价最多包含 16 个完整 `jobs`，有效期 5 分钟，不冻结也不扣除积分。响应包含 `quote_id`、`total_credits`、`balance`、`sufficient`、`expires_at` 和 `pricing_version`。

## 强制流程

1. 本地计划中的 `estimated_points` 只是展示用粗略值，不能作为付费确认依据。
2. 用户确认套图方案后，先运行 `quote` 的默认预览。此时不联网、不创建清单、不上传。
3. 明确告知本地参考图和提示词将发送到 `token.qixuai.com`。用户单独同意后，才运行 `quote --execute --confirm-remote-quote`。
4. 报价命令先上传已确认的本地参考图，再把每个完整生成请求发送给报价接口。它把精确积分、余额、价格版本、到期时间、任务列表和请求指纹写入清单。
5. `sufficient=false` 时停止并展示余额、所需、差额和充值链接，不提交生图。充值后重新报价，不能复用旧报价。
6. `sufficient=true` 时展示精确积分和有效期。只有用户再次确认付费生成，才能运行 `generate --execute --confirm-live-run --max-points N`。
7. 正式生成携带服务端 `quote_id`。报价过期，或模型、质量、尺寸、提示词、参考图和任务列表发生变化时，工具拒绝生成并要求重新报价、重新确认。
8. 每个任务使用写入清单的 `Idempotency-Key`。网络中断后使用同一计划和清单重试，不能新建幂等键。

余额不足时建议提示：

```text
当前余额：100 积分
本次精确报价：300 积分
还差：200 积分

尚未提交任何生图任务。
请前往充值：https://token.qixuai.com/console/recharge?intent=buy
充值完成后回复“继续”，我会用原方案重新取得报价，再请你确认生成。
```

## 错误处理

- HTTP 402 `insufficient_credits` 是确定性拒绝，任务未提交；保存 `waiting_for_recharge`。
- HTTP 409 通常表示价格版本或报价参数不匹配；重新报价并再次确认。
- HTTP 410 表示报价过期；重新报价并再次确认。
- 提交超时或响应无法解析时保存 `submit_unknown`。因为幂等键已持久化，可以使用原计划和原清单重试，不会重复扣费。
- 不轮询充值页面，不代替用户支付，不在日志或聊天中输出设备令牌、API Key 或完整 `quote_id`。
