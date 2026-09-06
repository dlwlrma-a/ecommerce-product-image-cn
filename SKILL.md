---
name: 电商商品套图生成
description: 先检查同一 SKU 参考图的视角覆盖，再通过需求访谈确认平台、受众、语言、套图类型、逐图文案和风格，生成并审计高一致性的电商白底图、首图、场景图、卖点图、细节图与尺寸图。适用于淘宝、京东、拼多多、抖音、小红书、Amazon 和独立站；不依据未展示的商品视角猜测外观，也不伪造商品事实与授权。
slug: ecommerce-product-image-cn
displayName: 电商商品套图生成
version: 1.7.0
summary: 检查参考视角，逐图规划、精确报价并生成高一致性商品套图
license: MIT
---

# 电商商品套图生成

把商品图生产拆成素材检查、需求确认、本地计划、远程精确报价、付费生成和逐图审计。默认使用算点边界 `Qx-Image`，同一套图的每个镜头都会共享最多 16 张同一 SKU 参考图，以尽量保持产品一致。

## 对话与确认门槛

- 用户只说“做一套商品图”或只上传图片时，不得直接生成。先按 [需求访谈指南](references/intake-guide.md) 一次性收集缺失信息。
- 按 [商品一致性指南](references/fidelity-guide.md) 检查 SKU、颜色、版本和视角。不得猜测未展示的正面、背面、侧面、结构或细节；例如衣服只有背面图时必须索要正面图。
- 必须确认目标平台与用途、目标语言、套图构成、比例或尺寸、真实卖点、准确文案、受众和风格。用户说“你推荐”时可提出方案，但仍需用户确认。
- 一套图可以多次使用同一类型，例如 3 张不同卖点图。优先用商品档案的 `shots` 逐图描述用途、构图、文案、场景和证据视角；不要为了绕过重复类型限制而拆成多个计划。
- 四种授权必须分开，前一项确认不得代表后一项：`确认套图方案`、`同意浏览器授权`、`同意上传参考图并获取报价`、`确认按精确积分生成`。
- 安装 Pillow 是独立的本机环境变更，只在用户同意安装或环境已有 Pillow 时执行；不得和授权或生成命令捆绑。

## 商品保真边界

- 只处理商家有权使用的商品、模特、品牌和包装素材，不仿冒品牌、不删除水印、不伪造授权。
- 商品名称、结构、颜色、材质、Logo、包装文字、尺寸、认证、价格、功效和促销条件只能来自用户核实的数据。
- 所有参考图必须属于同一 SKU、颜色和版本，并标注 `front/back/left/right/side/top/bottom/detail/packaging/label/scale` 角色；不同颜色或款式分开建计划。
- 本地参考图上传后会得到临时 HTTPS URL。报价和生成时，每个镜头使用全部同一 SKU 参考图；镜头的 `reference_roles` 只表示该构图必须具备的证据，不用于丢弃其他视角。
- 模型支持中文等多语言文字。默认 `render`，但只渲染用户确认的商品名、卖点和尺寸；不得自行添加价格、认证、功效、折扣或承诺。生成后必须逐字检查。
- 自动审计只能检查文件、比例、尺寸和白底边角；仍需逐图核对轮廓、结构、颜色、材质、图案、Logo、标签、接缝、纽扣、拉链、接口和配件。

## 标准工作流

1. 检查素材并完成访谈，展示“套图方案确认单”：商品版本、参考视角、平台、受众、语言、规格，以及每个镜头的类型、用途、构图、文案和保真风险。此时不运行上传、报价或生图接口。

2. 用户明确确认方案后，用本地文件或服务可访问的 HTTPS URL 创建计划。本地文件支持 PNG、JPG、JPEG、WEBP、GIF，单张不超过 10 MB；参考图合计最多 16 张。

   ```powershell
   python scripts/ecommerce_image_studio.py plan --brief assets/product-brief.example.json --output image_plan.json
   ```

   简单套图也可以使用 `--types white_bg,hero,feature,feature`；重复类型会建立不同镜头。复杂套图使用档案中的 `shots`。计划阶段完全离线。

3. 运行环境检查。若 QixuAI 尚未授权，先解释将申请 `images:write files:write billing:read`，并单独询问是否打开浏览器。不得要求用户把 API Key 发到聊天中。

   ```powershell
   python scripts/ecommerce_image_studio.py doctor
   python scripts/qixuai_auth.py login
   ```

   `login` 只创建设备码、打开页面并立即退出，不会占用 Agent 60 秒等待窗口。用户在网页登录并点击允许后，再运行：

   ```powershell
   python scripts/qixuai_auth.py login --complete
   python scripts/qixuai_auth.py status --check
   ```

   若网页尚未允许，`--complete` 最多等待 45 秒后安全退出，稍后可再次执行，不需要重新登录。服务端会在登录后返回原授权页。详见 [设备授权](references/qixuai-device-auth.md)。

4. 本地预览报价动作，不产生网络请求、不创建清单、不上传图片：

   ```powershell
   python scripts/ecommerce_image_studio.py quote --plan image_plan.json --output-dir output
   ```

   向用户展示上传域名、参考图数量、生成张数，以及提示词会用于报价。只有用户明确同意上传参考图并获取报价后，才能执行：

   ```powershell
   python scripts/ecommerce_image_studio.py quote --plan image_plan.json --output-dir output --execute --confirm-remote-quote
   ```

   该步骤上传本地参考图并调用无扣费报价接口，返回精确积分、余额、价格版本和 5 分钟有效期。余额不足时展示充值链接并暂停；充值完成后重新报价。详见 [积分报价与充值](references/qixuai-billing.md)。

5. 把精确积分、张数、报价到期时间和逐图内容展示给用户。只有用户再次明确确认按该报价生成，才执行付费请求：

   ```powershell
   python scripts/ecommerce_image_studio.py generate --plan image_plan.json --output-dir output --max-points 50 --execute --confirm-live-run --wait
   ```

   `--max-points` 必须不低于精确报价。生成只使用清单中仍有效的报价和已上传参考图，不会自动重新报价或替用户延长授权。每个任务携带 `quote_id` 和持久化 `Idempotency-Key`，相同计划重试不会重复扣费。报价过期、价格或参数变化时，返回第 4 步并重新取得用户确认。

6. 等待超时或稍后回来时，只查询已有任务，不重新提交：

   ```powershell
   python scripts/ecommerce_image_studio.py collect --manifest output/generation_manifest.json --wait --confirm-live-run
   ```

7. 若 Pillow 已安装，执行自动规格检查；未安装时先单独询问用户是否安装。然后完成 [人工验收清单](references/review-checklist.md)：

   ```powershell
   python scripts/ecommerce_image_studio.py audit --plan image_plan.json --manifest output/generation_manifest.json --output output/audit_report.json
   ```

## 输出

```text
image_plan.json: 商品档案、参考图覆盖、逐镜头构图/文案/提示词及本地预估
generation_manifest.json: 上传状态、精确报价、报价指纹、幂等键、任务 ID、状态和结果文件
output/*: 按镜头顺序与图型命名的生成图片
audit_report.json: 自动规格检查和人工商品保真清单
```

失败图只针对明确的问题修改对应镜头并新建计划。任何文案、尺寸、参考图、模型或提示词变化都会使原报价失效，必须重新报价和确认。
