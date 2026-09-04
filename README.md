# ecommerce-product-image-cn

面向国内与跨境电商的商品套图生成 Skill。它把商品原图和真实卖点整理成可检查计划，通过算点边界 `Qx-Image` 异步生成白底主图、氛围首图、场景图、卖点图、细节图和尺寸参照图，并保存任务清单用于断点续查。

核心特点：最多 3 张商品参考图、商品保真锁定段、默认无文字底图、积分预算上限、提交不自动重试、规格和白底检测。

```powershell
python scripts/ecommerce_image_studio.py doctor
python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --selling-point "保温锁温" --reference-url "https://example.com/product.jpg" --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output
```

确认计划与预计积分后才执行：

```powershell
$env:QIXUAI_API_KEY="你的 API Key"
python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --max-points 50 --execute --confirm-live-run --wait
```

接口文档：[算点边界图片生成](https://token.qixuai.com/docs#image-create)。

MIT License
