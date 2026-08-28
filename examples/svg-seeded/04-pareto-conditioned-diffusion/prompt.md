你是一名科研图表重绘专家。附件是一篇论文的模型架构图（1217×609 像素）。请将它重绘为一个 SVG 文件，严格遵守以下输出合同。

【硬性要求】
1. `<svg>` 根元素必须带 `width="1217" height="609" viewBox="0 0 1217 609"`，所有坐标以原图像素为基准，不得缩放；还必须从本案例合同逐字填写
   `data-source-schema-version="4.0.0" data-case="..." data-reference-sha256="..." data-object-inventory-sha256="..." data-stable-element-ids="true" data-relations-exhaustive="true"`。
2. 所有文字必须逐字照抄原图（含大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成路径。
3. 公式用文本表达：变量斜体 `font-style="italic"`，上下标用 `<tspan baseline-shift="sub|super">`。
4. 照片、真实场景截图、复杂写实图标，以及不含文字且无法高质量矢量还原的写实装饰元素：不要重绘，放占位矩形
   `<rect id="atomic:<语义名>" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>`，
   位置尺寸与原图该区域一致。含文字/公式的内容、几何图形、线条、箭头禁止占位。
5. 箭头的粗细、头部样式（实心/开放/块状）、尺寸、弯折位置与连接关系一律以原图为准，不得套用固定风格：
   实心头用填充 marker 或整体轮廓 path，开放折线头用描边 marker，块状/楔形/弯折箭头画单一闭合整体轮廓。必须先逐条清点
   `source → target + direction`，同时冻结参考图的可见图元数量：参考若是一根连续双端杆身或一个双端块箭头，必须只生成一个 SVG 元素、
   一个双端 ArrowSpec 和一个 PowerPoint 可见对象，禁止拆成两条共线反向单头箭头；参考若明确画出两条独立反向关系，则必须走可分辨的
   不同车道，禁止重叠中心线。共享目标的独立关系不得省略，但也不得把一个双向图元错误复制成两个对象。
6. 版面纪律：文字与文字、文字与图形不得重叠；箭头与连接线端点落在形状边缘或间隙，不得压盖文字；
   连接线不与沿途文字相交；元素间距与留白以原图为准。
7. 布局必须可审计：容器内的文字/公式须有稳定 `id`，并标注
   `data-layout-container="<容器id>" data-layout-padding="<像素>"`；重复圆/节点等同级图元须逐个有稳定 `id`，并标注相同
   `data-repeat-group`、`data-repeat-axis="vertical|horizontal"` 和唯一 `data-repeat-order`。同组图元须等尺寸、同轴，中心距差最多 1 px。
8. 细节关系必须显式声明：等尺寸但非等距的同类框使用 `data-peer-size-group`，组内每个成员必须声明相同且不超过 1 px 的
   `data-peer-size-tolerance`；位于相邻对象间隙的箭头使用
   `data-gap-source-id`、`data-gap-target-id`、`data-gap-axis` 和两端 inset，使长度随两侧边界自适应；必须可见在某对象上/下层时使用
   `data-z-above`/`data-z-below`。纵向单词须区分整词旋转 `data-text-flow="rotated-word"` 与逐字直立堆叠
   `data-text-flow="stacked-characters"`，并用 `data-text-container` 声明所属框；逐字堆叠还必须显式声明
   `data-text-stack-step`。若仅为容纳 PowerPoint 的透明文本选择框而需越出所属框，可显式声明不超过 3 px 的
   `data-text-frame-overflow-tolerance`；该容差不得用于放行可见字形溢出。禁止仅凭外观混用两种实现。
9. 渐变用 `<linearGradient>`；虚线用 `stroke-dasharray`；成组元素用 `<g>`。
10. 只输出 SVG 代码本身，不要任何解释文字。

【风格要求】
- 颜色、字号、粗细、对齐、间距尽量贴近原图。
- 先整体布局后局部细节，确保每个模块位置与原图一一对应。
