# v2 提示词输出合同（VLM → SVG）

本文件是 `autofigure prepare` 生成的 prompt.md 的规范来源。VLM 重绘输出必须满足本合同，否则 `convert` 拒绝或降级。

## 硬性要求

1. **画布精确**：`<svg>` 根元素必须带 `width="W" height="H" viewBox="0 0 W H"`（W/H = 原图像素尺寸），所有坐标以原图像素为基准，不得缩放。`convert` 校验 viewBox 与参考图尺寸，不一致即拒绝。
2. **文字逐字**：所有文字逐字照抄原图（大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成 `<path>`（否则文字不可编辑，直接违背项目目标）。
3. **公式表达**：变量斜体 `font-style="italic"`；上下标用 `<tspan baseline-shift="sub|super" font-size="较小值">`。
4. **照片/写实图标占位**：照片、真实场景截图、复杂写实图标**不要重绘**，放占位矩形：
   ```xml
   <rect id="atomic:observation-photo" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>
   ```
   `convert` 会自动从参考图裁剪该 bbox 嵌入为位图。除此之外**禁止 `<image>`**（会被跳过并记 warning）。
5. **结构特性**：渐变用 `<linearGradient>`；箭头用 `<marker>`（`orient="auto"`，`markerUnits="userSpaceOnUse"`）；虚线用 `stroke-dasharray`。
6. **只输出 SVG 代码**，不要解释文字。

## 风格要求

- 颜色、字号、粗细、对齐、间距尽量贴近原图；先整体布局后局部细节。
- 文字定位用 `text-anchor`（start/middle/end），x 为锚点、y 为基线。
- 竖排标签用 `transform="rotate(90 cx cy)"`（旋转中心即文字锚点）。

## 当前实现边界（convert 如实告知，不要在合同里赌运气）

- `<g>` 分组：当前按拍平处理（样式与变换会正确继承，但不产生原生 group 对象）。
- `radialGradient`、`marker-mid`：暂不支持，遇到记 warning 降级。
- `style="k:v"` 内联样式与 presentation 属性都支持；不支持 CSS 类/外部样式表。

## ModularAgent 实例（2026-08-18 实测）

GPT-5 按本合同对 1429×627 架构图直出的 SVG：66 个 `<text>`、公式 tspan 上下标完整（含 `ƒ_map`）、渐变楔形与中段箭头一次到位。经 `convert` 转为 255 个原生对象后，PowerPoint 渲染对参考图 mean_abs_rgb_delta=17.40（诊断口径），优于旧重型管线 30 轮迭代的 19.9987。

## 常见翻车点

- 把照片画成卡通 → 用 `atomic:` 占位符。
- 文字逐字错误（尤其公式符号、希腊字母）→ `check` 的文本比对报告会列出，必须人工逐条消除。
- viewBox 用了 1024×768 等默认值 → `convert` 直接拒绝，重画。
- 把多条文字合并进一个 `<text>` 并用 dy 换行 → 每行一个 `<text>` 更稳。
