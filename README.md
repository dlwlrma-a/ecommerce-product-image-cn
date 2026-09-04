# 电商商品套图生成

面向国内与跨境电商的商品套图生成 Skill。它先询问并确认平台、受众、语言、套图类型、准确文案与视觉风格，再把商品原图和真实卖点整理成可检查计划，通过算点边界 `Qx-Image` 异步生成套图，并保存任务清单用于断点续查。

核心特点：参考图视角覆盖门槛、生成前需求访谈、两次确认、最多 16 张同一 SKU 参考图、本地图片自动上传、多语言与逐图文案、24 小时 URL 有效期处理、商品保真锁定、预算上限和逐图审计。

```powershell
python scripts/ecommerce_image_studio.py doctor
python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --selling-point "保温锁温" --reference-url "front=https://example.com/product.jpg" --reference-url "detail=https://example.com/detail.jpg" --platform "京东" --language "简体中文" --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --reference-file "front=.\product-front.jpg" --reference-file "detail=.\product-detail.png" --platform "京东" --language "简体中文" --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output
```

确认计划与预计积分后才执行：

```powershell
$env:QIXUAI_API_KEY="你的 API Key"
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --max-points 50 --execute --confirm-live-run --wait
```

接口文档：[本地图片上传](https://token.qixuai.com/docs#image-upload)、[算点边界图片生成](https://token.qixuai.com/docs#image-create)。

MIT License
