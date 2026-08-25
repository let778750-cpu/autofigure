# PNG-only reconstruction contract

本案例从冻结的 `reference.png` 直接开始，不要求、也不依赖 GPT Web 或其他网页端预先生成 SVG。

- 画布：1513×554 px
- 参考图 SHA-256：`3e66655ae080dc92cc04d3c011f908a3aec83ca1ad89cf0559f503c81c970b54`
- 区域任务：`qa/region-tasks.json`
- 默认保真策略：`hybrid_fidelity`

执行者可以是 Codex、其他 VLM 或人工操作员。必须逐区读取参考图并保持稳定对象 ID；文字、公式、规则形状和箭头保持原生可编辑。只有在 `assets.json` 明确授权时，复杂且不可忠实矢量化的微资产才允许使用紧边界参考图裁剪。

## SVG 作者硬性合同（与 svg-seeded 路线共用）

工具会同时审计 SVG 源坐标和保存重开的 PowerPoint shape，借此区分视觉测量错误与转换漂移。
无论执行者是谁，返回的 SVG 载体都必须满足与 svg-seeded 路线完全相同的输出合同：

【硬性要求】
1. `<svg>` 根元素必须带 `width="1513" height="554" viewBox="0 0 1513 554"`，所有坐标以原图像素为基准，不得缩放。
2. 所有文字必须逐字照抄原图（含大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成路径。
3. 公式用文本表达：变量斜体 `font-style="italic"`，上下标用 `<tspan baseline-shift="sub|super">`。
4. 照片、真实场景截图、复杂写实图标，以及不含文字且无法高质量矢量还原的写实装饰元素：不要重绘，放占位矩形
   `<rect id="atomic:<语义名>" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>`，
   位置尺寸与原图该区域一致。含文字/公式的内容、几何图形、线条、箭头禁止占位。
5. 箭头的粗细、头部样式（实心/开放/块状）、尺寸、弯折位置与连接关系一律以原图为准，不得套用固定风格：
   实心头用填充 marker 或整体轮廓 path，开放折线头用描边 marker，块状/楔形/弯折箭头画整体轮廓 path。
6. 版面纪律：文字与文字、文字与图形不得重叠；箭头与连接线端点落在形状边缘或间隙，不得压盖文字；
   连接线不与沿途文字相交；元素间距与留白以原图为准。
7. 布局必须可审计：容器内的文字/公式须有稳定 `id`，并标注
   `data-layout-container="<容器id>" data-layout-padding="<像素>"`；重复圆/节点等同级图元须逐个有稳定 `id`，并标注相同
   `data-repeat-group`、`data-repeat-axis="vertical|horizontal"` 和唯一 `data-repeat-order`。同组图元须等尺寸、同轴，中心距差最多 1 px。
8. 渐变用 `<linearGradient>`；虚线用 `stroke-dasharray`；成组元素用 `<g>`。
9. 只输出 SVG 代码本身，不要任何解释文字。

候选通过 `autofigure ingest` 返回。离线初版当前以 SVG 作为可渲染载体；完整 scene/region patch 可以用于已有载体的修复，或交给 PowerPoint Live provider。任务协议与模型品牌无关，但视觉推理仍需要模型或人工执行，不能把“入口已连通”误写成“PNG 已自动一比一重建”。
