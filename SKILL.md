---
name: ecommerce-product-image-cn
description: 根据商品原图、真实规格和卖点生成电商主图、白底图、场景图、卖点图、细节图与尺寸参照图，并进行商品保真和图片规格检查。适用于淘宝、京东、拼多多、抖音、小红书、Amazon 和独立站商品视觉生产；不用于伪造商品功能、认证、价格、Logo 或未授权品牌素材。
---

# 电商商品套图生成

把商品图生产拆成可检查的商品档案、套图计划、异步生成任务和上架前审计。默认使用算点边界 `Qx-Image`，通过最多 3 张参考图保持同一 SKU 的外观一致性。

## 边界

- 只使用商家有权处理的商品、模特、品牌和包装素材。不要仿冒品牌、删除水印或伪造授权。
- 商品名称、结构、颜色、材质、Logo、包装文字、尺寸、认证、价格、功效和促销条件必须来自用户核实的数据；模型不得自行补充事实。
- 参考图和提示词会发送到远程图片模型并产生费用。先展示目标域名、图片数量、参考图数量和预计积分，用户确认后才加入 `--execute --confirm-live-run --max-points`。
- API Key 只从环境变量读取，不写入命令、计划、日志或仓库。图片提交不自动重试；结果未知时先检查用量，避免重复扣费。
- 生图模型直接渲染中文可能出现错字。默认 `--text-mode reserve` 只留排版空间；只有用户接受文字风险时才使用 `render`。
- 自动审计只能检查尺寸、比例、文件可读性和白底边角，不能代替商品保真人工复核。

## 工作流

1. 获取商品名称、品类、真实外观说明、1-3 张同款商品参考图、已核实卖点、尺寸、目标平台、画面比例和品牌调性。参考图不足时明确降低一致性预期。
2. 直接 API 只接受模型服务能访问的 HTTPS 图片 URL。本地图片可以先在 [算点图片控制台](https://token.qixuai.com/console/images)上传创作；不要猜测未公开的上传接口。
3. 检查环境，密钥变量默认使用 `QIXUAI_API_KEY`：

   ```powershell
   python scripts/ecommerce_image_studio.py doctor
   ```

4. 先生成计划，不调用模型。图型和平台选择见 [references/image-types.md](references/image-types.md)：

   ```powershell
   python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --selling-point "保温锁温" --selling-point "单手开合" --reference-url "https://example.com/product-front.jpg" --platform "京东" --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
   ```

   重复生产可复制 `assets/product-brief.example.json` 并使用 `--brief product-brief.json`；同名 CLI 参数会覆盖档案值。

5. 先预览请求数量和预计积分：

   ```powershell
   python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output
   ```

6. 用户核对商品信息、提示词、目标域名和预算后才提交。当前接口说明见 [references/qixuai-image-api.md](references/qixuai-image-api.md)：

   ```powershell
   python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --max-points 50 --execute --confirm-live-run --wait
   ```

7. 若生成后离开或等待超时，用已有任务清单继续查询，不重新提交：

   ```powershell
   python scripts/ecommerce_image_studio.py collect --manifest output/generation_manifest.json --wait --confirm-live-run
   ```

8. 安装 Pillow 后执行自动规格检查，并逐项完成人工保真审计：

   ```powershell
   python -m pip install Pillow
   python scripts/ecommerce_image_studio.py audit --plan image_plan.json --manifest output/generation_manifest.json --output output/audit_report.json
   ```

## 输出

```text
image_plan.json: 商品档案、套图类型、逐图提示词、请求参数、预计积分
generation_manifest.json: 任务 ID、状态、结果 URL、本地文件，可断点续查
output/*: 按序号与图型命名的生成图片
audit_report.json: 比例、尺寸、白底边角检查与人工保真清单
```

失败图只针对明确的问题修改对应提示词并新建计划，不把一次失败总结成全局规则。发布前按 [references/review-checklist.md](references/review-checklist.md) 人工验收。
