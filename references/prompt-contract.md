# v2 提示词输出合同（VLM → SVG）

本文件是 `autofigure prepare` 生成的 prompt.md 的规范来源。VLM（GPT / Kimi / Claude 等多模态大模型）重绘输出必须满足本合同，否则 `convert` 拒绝或降级。

## 硬性要求

1. **画布精确**：`<svg>` 根元素必须带 `width="W" height="H" viewBox="0 0 W H"`（W/H = 原图像素尺寸），所有坐标以原图像素为基准，不得缩放。`convert` 校验 viewBox 与参考图尺寸，不一致即拒绝。
2. **文字逐字**：所有文字逐字照抄原图（大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成 `<path>`（否则文字不可编辑，直接违背项目目标）。
3. **公式表达**：变量斜体 `font-style="italic"`；上下标用 `<tspan baseline-shift="sub|super" font-size="较小值">`。
4. **写实元素占位**：照片、真实场景截图、复杂写实图标，以及任何**不含文字**且无法高质量矢量还原的写实/纹理装饰元素：**不要重绘**，放占位矩形：
   ```xml
   <rect id="atomic:observation-photo" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>
   ```
   `convert` 会自动从参考图裁剪该 bbox 嵌入为位图（唯一允许的位图来源）。**含文字或公式的内容、以及一切几何图形/线条/箭头，禁止占位**——文字公式必须原生可编辑，几何元素必须矢量履约。直接内嵌 `<image>`（如 base64 裁切）会被 `convert` 按 bbox 从参考图裁剪替代并记 warning；单张位图覆盖画布 ≥50% 直接拒绝（防整图截图冒充矢量）。
5. **箭头以原图为准**：箭头的粗细、头部样式、尺寸、弯折位置与连接关系一律复刻原图实际形态，**不得套用固定风格**（一律细线开口或一律实心大三角都是违约）。表达机制：普通线箭头使用可归一化 marker 并编译为单一 PowerPoint 原生线端；块状/楔形箭头使用一个闭合整体轮廓，`data-*-head-type="custom"` 只保存已嵌入轮廓的语义，不再生成独立三角形。任何需要“杆身＋独立头部＋分组”才能表达的候选必须 strict fail。开放折线头用描边 marker（`orient="auto"`，`markerUnits="userSpaceOnUse"`）。**几何可验证子句**（`autofigure arrows` 与 ArrowSpec 物理门禁）：marker 的 `refX/refY` 必须等于尖端局部坐标；头长和横向头宽以**原图实测为第一判据**，短而宽的块箭头不得因轴向长度较短而被错误放大；箭头线端点距目标形状边缘 ≤6px。禁止用一束短 `<line>` 手折"箭羽"拼箭头，也禁止静默 fallback。
6. **版面纪律**：文字与文字、文字与图形不得重叠；箭头与连接线的端点必须落在形状边缘或间隙，不得穿越或压盖文字；连接线不得与沿途文字相交；元素间距与留白以原图为准。
7. **布局关系显式化**：容器内文字/公式必须有稳定 `id`，并声明 `data-layout-container="<容器id>"`；可用 `data-layout-padding` 指定内边距。重复圆、节点或图标必须逐个有稳定 `id`，并声明相同的 `data-repeat-group`、一致的 `data-repeat-axis="vertical|horizontal"` 和唯一的 `data-repeat-order`。默认硬门：尺寸差与横轴/纵轴中心漂移各不超过 0.25 px，连续中心距的最大差不超过 1 px（允许参考图整数像素量化）。工具同时核对 SVG 源坐标和保存重开的 PPT shape，并以包级 OOXML 检查所有绑定对象是否超出画布；不能用“转换成功”或全图均值掩盖源布局错误、转换漂移或 OMML 越界。
8. **结构特性**：渐变用 `<linearGradient>`；虚线用 `stroke-dasharray`。
9. **只输出 SVG 代码**，不要解释文字。

## 风格要求

- 颜色、字号、粗细、对齐、间距尽量贴近原图；先整体布局后局部细节。
- 文字定位用 `text-anchor`（start/middle/end），x 为锚点、y 为基线。
- 竖排标签用 `transform="rotate(90 cx cy)"`（旋转中心即文字锚点）。

## 当前实现边界（convert 如实告知，不要在合同里赌运气）

- `<g>` 分组：当前按拍平处理（样式与变换会正确继承，但不产生原生 group 对象）。
- `radialGradient`、`marker-mid`：暂不支持，遇到记 warning 降级。
- `style="k:v"` 内联样式与 presentation 属性都支持；不支持 CSS 类/外部样式表。
