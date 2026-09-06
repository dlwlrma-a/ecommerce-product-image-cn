# 电商商品套图生成

面向国内与跨境电商的高一致性商品套图 Skill。它先检查同一 SKU 的参考视角，再确认平台、受众、语言、逐图构图和准确文案，随后通过算点边界 `Qx-Image` 精确报价、异步生成并审计结果。

v1.7 的主要能力：

- 最多 16 张同一 SKU 参考图，所有镜头共享完整参考集。
- `shots` 支持同类型多镜头，各自设置构图、场景、文案、尺寸和证据视角。
- 浏览器设备授权分为启动与完成两步，不会阻塞 Agent 的 60 秒执行窗口。
- `plan -> quote -> generate` 三段式确认；报价不扣费，付费生成必须再次确认。
- 精确报价包含余额、总积分、价格版本和有效期；余额不足时暂停并提供充值入口。
- 正式请求携带 `quote_id` 与持久化 `Idempotency-Key`，相同任务重试不会重复扣费。

```powershell
python scripts/ecommerce_image_studio.py doctor
python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
python scripts/ecommerce_image_studio.py quote --plan image_plan.json --output-dir output
```

需要授权时先启动设备授权，用户在网页点击允许后再完成兑换：

```powershell
python scripts/qixuai_auth.py login
python scripts/qixuai_auth.py login --complete
python scripts/qixuai_auth.py status --check
```

用户同意上传参考图并获取报价后：

```powershell
python scripts/ecommerce_image_studio.py quote --plan image_plan.json --output-dir output --execute --confirm-remote-quote
```

展示精确报价，并再次获得用户的付费生成确认后：

```powershell
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --max-points 50 --execute --confirm-live-run --wait
```

充值入口：<https://token.qixuai.com/console/recharge?intent=buy>。已有 `QIXUAI_API_KEY` 环境变量仍可显式覆盖设备凭据；不要把 API Key 贴进聊天。

接口文档：[设备授权](https://token.qixuai.com/device)、[本地图片上传](https://token.qixuai.com/docs#image-upload)、[算点边界图片生成](https://token.qixuai.com/docs#image-create)。

MIT License
