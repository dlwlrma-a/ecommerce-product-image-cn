# 电商商品套图生成

面向国内与跨境电商的高一致性商品套图 Skill。它检查同一 SKU 的参考视角，确认平台、语言、逐图构图和准确文案后，通过算点边界 `Qx-Image` 直接生成并审计结果。

v1.8 简化了生成体验：

- 用户只确认一次完整套图方案，确认后直接生成，不再调用或展示报价环节。
- 服务端余额充足就实时扣积分生成；HTTP 402 时暂停并显示充值链接。
- 充值后使用原命令继续，无需重新确认未变化的方案。
- 最多 16 张同一 SKU 参考图，所有镜头共享完整参考集。
- `shots` 支持同类型多镜头，各自设置构图、场景、文案、尺寸和证据视角。
- 浏览器设备授权分为启动与完成两步，不阻塞 Agent 的执行窗口。
- 每个任务使用持久化 `Idempotency-Key`，网络重试不会重复扣费。

```powershell
python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --execute --confirm-live-run --wait
```

首次使用时启动设备授权，用户在网页点击允许后完成兑换，再继续原生成命令：

```powershell
python scripts/qixuai_auth.py login
python scripts/qixuai_auth.py login --complete
```

余额不足时前往 [充值积分](https://token.qixuai.com/console/recharge?intent=buy)，充值后继续即可。已有 `QIXUAI_API_KEY` 环境变量仍可显式覆盖设备凭据；不要把 API Key 贴进聊天。

接口文档：[设备授权](https://token.qixuai.com/device)、[本地图片上传](https://token.qixuai.com/docs#image-upload)、[算点边界图片生成](https://token.qixuai.com/docs#image-create)。

MIT License
