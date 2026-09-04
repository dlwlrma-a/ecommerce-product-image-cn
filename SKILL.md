---
name: 电商商品套图生成
description: 先通过需求访谈确认商品、平台、受众、语言、套图类型、文案和视觉风格，再根据商品参考图生成并审计电商白底图、首图、场景图、卖点图、细节图与尺寸图。适用于淘宝、京东、拼多多、抖音、小红书、Amazon 和独立站；不用于未经确认就直接生图，或伪造商品事实与授权。
slug: ecommerce-product-image-cn
displayName: 电商商品套图生成
version: 1.3.0
summary: 先确认语言、图型与文案方案，再生成和审计电商商品套图
license: MIT
---

# 电商商品套图生成

把商品图生产拆成可检查的商品档案、套图计划、异步生成任务和上架前审计。默认使用算点边界 `Qx-Image`，通过最多 3 张参考图保持同一 SKU 的外观一致性。

## 对话门槛

- 不要在用户只说“做一套商品图”或只上传参考图时直接生成。先使用 [需求访谈指南](references/intake-guide.md) 收集缺失信息。
- 复用用户已经提供的内容，不重复询问。缺少关键信息时，用一条合并消息询问，优先给出可选项，避免逐题盘问。
- `目标平台与用途`、`目标语言`、`套图类型` 和 `画面比例/尺寸` 没有静默默认值，必须由用户明确选择。用户说“你推荐”时，可以给出带理由的推荐方案，但仍要等用户确认。
- 先展示“套图方案确认单”：商品与版本、平台、受众、语言、画面规格、逐图用途、准确文案、场景和保真风险。此阶段不调用上传或生图接口。
- 只有用户明确确认方案后才能创建计划；随后展示上传张数、生成张数、端点和预计积分，再次获得执行确认后才能实际上传和生成。

## 边界

- 只使用商家有权处理的商品、模特、品牌和包装素材。不要仿冒品牌、删除水印或伪造授权。
- 商品名称、结构、颜色、材质、Logo、包装文字、尺寸、认证、价格、功效和促销条件必须来自用户核实的数据；模型不得自行补充事实。
- 本地参考图会先上传到算点边界，上传后得到的 HTTPS URL 和提示词会继续发送给远程图片模型。先展示上传域名、上传张数、生成张数和预计积分，用户确认后才加入 `--execute --confirm-live-run --max-points`。
- API Key 只从环境变量读取，不写入命令、计划、日志或仓库。图片提交不自动重试；结果未知时先检查用量，避免重复扣费。
- 模型支持中文等多语言文字渲染。默认 `--text-mode render`，但只渲染用户提供或确认过的商品名、卖点与尺寸；不得自行添加价格、功效、认证和促销承诺。生成后逐字检查并重做有错字的图片。
- 自动审计只能检查尺寸、比例、文件可读性和白底边角，不能代替商品保真人工复核。

## 工作流

1. 按 [需求访谈指南](references/intake-guide.md) 获取商品名称与版本、品类、真实外观说明、1-3 张同款商品参考图、已核实卖点与尺寸、目标平台、受众、使用场景、目标语言、套图类型、画面规格和品牌调性。需要翻译时确认目标语言，并要求用户复核译文含义。
2. 参考图可使用本地文件或模型服务能访问的 HTTPS URL。本地文件支持 PNG、JPG、JPEG、WEBP、GIF，单张不超过 10 MB；工具会按 [官方上传接口](https://token.qixuai.com/docs#image-upload)先上传，再把返回的 HTTPS URL 交给生图接口。本地文件和远程 URL 合计最多 3 张。
3. 检查环境，密钥变量默认使用 `QIXUAI_API_KEY`：

   ```powershell
   python scripts/ecommerce_image_studio.py doctor
   ```

4. 将用户确认的方案写入商品档案，再生成计划；这一步不调用模型。图型选择见 [references/image-types.md](references/image-types.md)：

   ```powershell
   python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --selling-point "保温锁温" --selling-point "单手开合" --reference-url "https://example.com/product-front.jpg" --platform "京东" --audience "城市通勤人群" --scene "早晨办公桌" --language "简体中文" --text-mode render --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
   ```

   本地参考图使用 `--reference-file`，可重复传入；也可与 `--reference-url` 混用：

   ```powershell
   python scripts/ecommerce_image_studio.py plan --product-name "便携咖啡杯" --category "饮具" --description "磨砂黑色杯身，不锈钢内胆，黑色旋盖" --reference-file ".\product-front.jpg" --reference-file ".\product-side.png" --language "简体中文" --text-mode render --types white_bg,hero,lifestyle,feature,detail --size 1:1 --output image_plan.json
   ```

   重复生产可复制 `assets/product-brief.example.json` 并使用 `--brief product-brief.json`；同名 CLI 参数会覆盖档案值。

5. 先预览请求数量和预计积分：

   ```powershell
   python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output
   ```

6. 展示计划中的逐图用途、文案、商品状态、目标域名和预算。用户再次明确同意实际上传与扣费后才提交。当前接口说明见 [references/qixuai-image-api.md](references/qixuai-image-api.md)：

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
image_plan.json: 商品档案、目标语言、逐图文案、套图类型、提示词、请求参数、预计积分
generation_manifest.json: 参考图上传状态与 URL、任务 ID、生成状态、结果 URL、本地文件，可断点续查
output/*: 按序号与图型命名的生成图片
audit_report.json: 比例、尺寸、白底边角检查与人工保真清单
```

失败图只针对明确的问题修改对应提示词并新建计划，不把一次失败总结成全局规则。发布前按 [references/review-checklist.md](references/review-checklist.md) 人工验收。
