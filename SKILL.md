---
name: 电商商品套图生成
description: 检查同一 SKU 参考图的视角覆盖，确认平台、语言、套图类型、逐图文案和风格后，直接生成并审计高一致性的电商白底图、首图、场景图、卖点图、细节图与尺寸图。适用于淘宝、京东、拼多多、抖音、小红书、Amazon 和独立站；不依据未展示的商品视角猜测外观，也不伪造商品事实与授权。
slug: ecommerce-product-image-cn
displayName: 电商商品套图生成
version: 1.8.0
summary: 确认一次套图方案，有积分直接生成，余额不足提示充值
license: MIT
---

# 电商商品套图生成

先检查商品证据，再确认一次完整套图方案，随后直接调用算点边界 `Qx-Image` 生成。同一套图的每个镜头共享最多 16 张同一 SKU 参考图，以尽量保持产品一致。

## 对话门槛

- 用户只说“做一套商品图”或只上传图片时，不得直接生成。先按 [需求访谈指南](references/intake-guide.md) 一次性收集缺失信息。
- 按 [商品一致性指南](references/fidelity-guide.md) 检查 SKU、颜色、版本和视角。不得猜测未展示的正面、背面、侧面、结构或细节；例如衣服只有背面图时必须索要正面图。
- 必须确认目标平台与用途、目标语言、套图构成、比例或尺寸、真实卖点、准确文案、受众和风格。用户说“你推荐”时可提出方案，但仍需确认。
- 一套图可以多次使用同一类型，例如 3 张不同卖点图。优先使用商品档案的 `shots` 逐图描述用途、构图、文案、场景和证据视角；不要拆成多个计划。
- 只需要一次业务确认。确认单必须提前说明：本地参考图会上传到 `token.qixuai.com`，生图会按服务端实时价格扣积分。用户回复“确认这套方案”后，直接创建计划并生成，不再增加预览、报价或二次扣费确认。
- 首次设备授权仍需用户在网页自行登录并点击允许，这是登录步骤，不是报价确认。安装 Pillow 是独立的本机环境变更，不与生成捆绑。

## 商品保真边界

- 只处理商家有权使用的商品、模特、品牌和包装素材，不仿冒品牌、不删除水印、不伪造授权。
- 商品名称、结构、颜色、材质、Logo、包装文字、尺寸、认证、价格、功效和促销条件只能来自用户核实的数据。
- 所有参考图必须属于同一 SKU、颜色和版本，并标注 `front/back/left/right/side/top/bottom/detail/packaging/label/scale`；不同颜色或款式分开建计划。
- 每个镜头使用完整参考图集合。镜头的 `reference_roles` 只表示该构图必须具备的证据，不用于丢弃其他视角。
- 模型支持中文等多语言文字。默认建议渲染用户确认的商品名、卖点和尺寸；不得自行添加价格、认证、功效、折扣或承诺。生成后必须逐字检查。
- 自动审计只能检查文件、比例、尺寸和白底边角；仍需逐图核对轮廓、结构、颜色、材质、图案、Logo、标签、接缝、纽扣、拉链、接口和配件。

## 工作流

1. 检查素材并完成访谈，展示“套图方案确认单”：商品版本、参考视角、平台、受众、语言、规格，以及每个镜头的类型、用途、构图、文案和风险。明确说明确认后将上传参考图并直接发起付费生成。此时尚未上传或扣费。

2. 用户确认后创建本地计划。本地文件支持 PNG、JPG、JPEG、WEBP、GIF，单张不超过 10 MB；参考图合计最多 16 张。

   ```powershell
   python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
   ```

   简单套图可以使用 `--types white_bg,hero,feature,feature`；复杂套图使用档案中的 `shots`。

3. 若 QixuAI 尚未授权，说明将申请生图和图片上传权限，然后启动浏览器授权。不得要求用户把 API Key 发到聊天中。

   ```powershell
   python scripts/qixuai_auth.py login
   ```

   `login` 会打开授权页面并立即退出。用户在网页登录并点击允许后运行：

   ```powershell
   python scripts/qixuai_auth.py login --complete
   ```

   若网页尚未允许，命令会安全退出，稍后再次执行即可，不重新创建设备码。详见 [设备授权](references/qixuai-device-auth.md)。

4. 授权可用后直接生成，不运行报价命令，也不展示报价确认：

   ```powershell
   python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --execute --confirm-live-run --wait
   ```

   `--confirm-live-run` 表示用户已经确认第 1 步的完整套图方案，不需要再询问一次。工具上传本地参考图并直接逐张提交。服务端有足够积分就扣费生成；余额不足返回 HTTP 402 时，工具保存 `waiting_for_recharge`，显示余额、所需积分、差额和 [充值链接](https://token.qixuai.com/console/recharge?intent=buy)，不提交该任务。充值后用户回复“继续”，使用同一命令继续，无需重新确认未变化的方案。详见 [余额不足处理](references/qixuai-billing.md)。

5. 每个任务携带持久化 `Idempotency-Key`。网络中断时用同一计划和清单重试，不生成新键，避免重复扣费。等待超时或稍后回来时只查询已有任务：

   ```powershell
   python scripts/ecommerce_image_studio.py collect --manifest output/generation_manifest.json --wait --confirm-live-run
   ```

6. 若 Pillow 已安装，执行自动规格检查；未安装时另行询问是否安装。然后完成 [人工验收清单](references/review-checklist.md)：

   ```powershell
   python scripts/ecommerce_image_studio.py audit --plan image_plan.json --manifest output/generation_manifest.json --output output/audit_report.json
   ```

## 输出

```text
image_plan.json: 商品档案、参考图覆盖、逐镜头构图、文案和提示词
generation_manifest.json: 上传状态、幂等键、余额不足状态、任务 ID、状态和结果文件
output/*: 按镜头顺序与图型命名的生成图片
audit_report.json: 自动规格检查和人工商品保真清单
```

失败图只针对明确问题修改对应镜头并新建计划。方案发生变化时重新展示确认单；方案未变化且仅充值或重试时，不重复确认。
