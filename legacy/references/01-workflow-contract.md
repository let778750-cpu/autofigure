# 01 · 工作流与 Figure Spec 合同

本合同把参考图感知、可编辑场景和最终对象绑定到同一 source hash。目的是在第一次绘制前暴露错误，而不是让 Reviewer 猜测 Drawer 当时看到了什么。

## 0. 工件边界

- `examples/` 根部只保存稳定参考输入和验收 fixture。
- 项目内所有生成输出必须先通过 `tools/output_policy.py`，并解析到 `examples/generated/**`；`--overwrite` 不能绕过该边界。
- 项目外的明确交付只接受显式绝对路径。pytest 用例副本在每次调用唯一的操作系统临时目录中运行并在会话结束清理。
- MCP server 的工作目录在兄弟插件项目中；save/export 必须传当前 run 内的绝对路径，禁止依赖相对路径。
- PNG 重绘的感知阶段只能由 Codex 调用项目根 `autofigure.cmd` 启动，不得要求用户手输 Python/PowerShell 命令或绕过 canonical runner。薄 `.cmd` 只解决 Windows 启动/当前进程 execution policy；确定性分析与 OCR 后的 Phase-1 `geometry_refinement` 仍只由 `D:\opencv\env\python.exe -I -B -X utf8` 执行，PaddleOCR 仍只由 `D:\paddle ocr\env\python.exe` 执行。每个 run 必须保存并绑定 host runtime receipt，禁止依赖 PATH、用户 site 或激活状态。

## 1. 状态与回退

```text
REFERENCE_FROZEN → PERCEPTION_COMPLETE → REVIEWED
→ SPEC_DRAFT → SPEC_FROZEN → PREFLIGHT_PASS
→ RENDERED → MECHANICAL_PASS → INDEPENDENT_REVIEW_PASS
→ RELEASE_CANDIDATE → APPROVED
```

`run-state.json` 是唯一当前状态，`run-events.jsonl` 只追加阶段历史；两者必须哈希闭合。

- `PERCEPTION_GATE` 有未决关键项：停在 `INCONCLUSIVE`，获取用户/原文证据后再冻结 spec。
- `ACCEPTANCE_AUDIT` 发现 minor：`MINOR_PATCH` 后重审。
- 发现错误画布、错误文字清单、错误拓扑或需整区重排：`SPEC_INVALID → REGION_REPLAN → PREFLIGHT_PASS`。
- 参考、模型、runtime/config、requirements、spec 或脚本哈希改变，会使所有下游工件失效。

## 2. Figure spec 最小结构

```jsonc
{
  "schema_version": "4.0",
  "policy_profile": "standard",
  "mode": "reconstruct_1to1",
  "source": {
    "path": "<absolute reference path>",
    "sha256": "<64 hex>",
    "width_px": 0,
    "height_px": 0,
    "pixel_format": "RGB",
    "user_confirmed": true
  },
  "perception": {
    "manifest_path": "examples/generated/runs/<run_id>/ocr/perception-manifest.json",
    "manifest_sha256": "<64 hex>",
    "review_receipt_path": "examples/generated/runs/<run_id>/perception-review-receipt.json",
    "review_receipt_sha256": "<64 hex>"
  },
  "coordinate_system": {
    "origin": "top-left",
    "unit": "source_pixel",
    "bbox_order": ["x", "y", "w", "h"]
  },
  "measurement_dpi": 96,
  "canvas": {
    "width_px": 0,
    "height_px": 0,
    "background": "#FEFEFE",
    "background_evidence": "measured_reference",
    "pptx_path": "examples/generated/runs/<run_id>/canvas.pptx",
    "pptx_sha256": "<64 hex>",
    "slide_width_emu": 12191999,
    "slide_height_emu": 8127999
  },
  "elements": [],
  "edges": [],
  "formulas": [],
  "uncertainties": []
}
```

示例数值只能来自当前参考的脚本测量，不能沿用 16:9、4:3 或 `#FFFFFF` 默认值。

## 3. elements[]

每个可见对象一条，稳定 ID 同时用于 spec、PPT shape name、finding 和回归：

| 字段 | 要求 |
|---|---|
| `id` | 当前 spec 内唯一，稳定且有语义前缀 |
| `type` | `background/panel/group/text/native_shape/formula/icon/plot/legend/reference_atomic_asset/manual_asset_slot`；connector 只在 `edges[]` 中定义 |
| `parent_id` | 根对象为 `null`；其余引用已存在容器 |
| `bbox` | `{x,y,w,h}`，source pixel，有限且非负 |
| `z_index` | 整数；同父级绘制顺序明确 |
| `visual_signature` | fill/stroke/line width/font/size/opacity 等可复刻属性 |
| `semantic_role` | 科研角色，不用泛化的“框1/元素2”代替 |
| `text` | 纯普通文字的 frozen text；一旦含数学 span，不得再用本字段承载整句 |
| `content_runs` | 混合内容专用；按阅读顺序交替保存 `{kind:"text",text}` 与 `{kind:"math",formula_id}`，与 `text` 二选一 |
| `text_style` | font family/path、size、margin、wrap、rotation、line spacing |
| `criticality` | 文字必填；`critical/ordinary` |
| `perception_candidate_ids` | 文字必填；OCR 派生文字逐条绑定 receipt candidate ID，否则为空数组 |
| `formula_style` | 公式必填；字号、margin、rotation，供首稿前容量测量 |
| `source_evidence` | `target_visual/local_ocr/user_confirmed/source_text/latex/manual_measurement` 的一项或多项 |
| `disposition` | `CONFIRMED/INCONCLUSIVE/UNREADABLE/NOT_TEXT` |
| `confidence` | 感知可靠性 0..1，不是 QA 分数 |
| `uncertainty_px` | 边界不清时的诚实误差范围 |
| `render_strategy` | `native_required/native_preferred/reference_atomic_asset/manual_asset_slot/source_ambiguity` |
| `geometry_source` | `designer_authored/target_visual/manual_measurement/calibrated_phase1`；晋升项必须绑定 promotion 与 calibration receipt |
| `review_risk` | `critical/ordinary` |
| `allowed_overlap` | 仅列语义上允许相交的对象 ID；默认空数组 |
| `status` | `pending/mapped/verified/intentional_deviation/blocked` |

`reference_atomic_asset` 必须包含 `asset_binding`，完整绑定 source bbox、输出/mask SHA、尺寸、原子性、无失真变换和权利依据。它只能承载单一视觉对象，正式文字/公式/轴图例/边框/定量证据必须拆为原生对象。

`type=manual_asset_slot` 还必须包含 `slot_contract`。槽位四态为
`empty/reference_preview/user_filled/backfilled_verified`：

- `reference_preview` 只允许 `reconstruct_1to1`，且必须引用由
  `tools/materialize_reference_preview.py` 生成的 hash-bound receipt；
- 裁片 bbox 与 element/slot bbox 必须逐值一致，零 padding、零 resampling、PNG lossless；
- preview 只准承载不可合理原生化的最小照片级视觉场，禁止文字、公式、connector、轴/图例、panel border 和定量证据；
- slot 必须记录 PowerPoint/draw.io 能力审计；shape 数量大本身不构成降级理由；
- preview 在成品中必须有原生可见 `REFERENCE PREVIEW — REPLACE ME` 标签，且不计 native coverage、从相似度诊断中遮罩、阻断 `APPROVED`；
- `manual_asset_slot` 与任一已完成表示互斥，禁止同一 element 同时用原生对象和位图重复覆盖。

详细准入、裁片和回填约束只在 `06-asset-policy.md` 维护。

`text_style` 必须足以在绘制前做文字测量；缺字体、无法测量或预计溢出时 preflight 不能 PASS。

## 4. edges[]

每条边至少包含 `id`、`from`、`to`、`representation`、`arrow_class`、首尾箭头、线宽、虚线、端帽、连接方式和必要的 `via`。普通连接使用 PowerPoint connector；有 `via` 的路径使用可编辑线段链，只在首末段应用对应箭头。

- `from/to` 必须引用现有元素，禁止靠近但不绑定的“视觉箭头”。
- 箭头端点触达边界，不进入受保护对象内部、不压文字。
- 语义必需的交叉必须在 `allowed_overlap`/route exception 中按对象声明，不能全局关闭碰撞检查。

## 5. OCR 与文本冻结

普通文字对象保留：

```jsonc
{
  "text": "...",
  "criticality": "ordinary",
  "perception_candidate_ids": ["T0042"],
  "source_evidence": ["local_ocr", "user_confirmed"],
  "disposition": "CONFIRMED"
}
```

候选全文、冲突和 bbox 保存在 raw manifest/review receipt，不复制成第二份真值。spec 的文字必须等于 review 的 `confirmed_text`，其 bbox 必须覆盖对应候选位置。公式、数字、单位、标题、轴/图例/连接器标签等关键项须显式带 `user_confirmed` 或 `source_text`。普通文本只有在版本化 fixture 校准通过、本地 OCR + Agent 选择 + 结构上下文一致，且 source-SHA 固定抽样未命中时才能标记 `consensus_auto`；不得伪装成 `user_confirmed`。

raw manifest 的每个 candidate ID 必须在 hash-bound perception review receipt 中恰好出现一次。Review 内部的 `CONFIRMED/CORRECTED/FORMULA_CONFIRMED` 只有在证据类型与内容合法时才可映射到 spec `CONFIRMED`；`NOT_TEXT` 映射到 `NOT_TEXT`；`PENDING/INCONCLUSIVE` 不得进入可绘制 spec。像素本身无法辨认时，spec 记 `UNREADABLE` 并保持 gate `INCONCLUSIVE`。对外 spec `disposition` 只有 `CONFIRMED/INCONCLUSIVE/UNREADABLE/NOT_TEXT` 四值。

### Phase-1 geometry observation boundary

OCR manifest 生成后，canonical runner 必须在同一 run 内调用 Host CV 的 `tools/geometry_refinement.py`，输出 `geometry/geometry-manifest.json`、overlay、lossless label atlas 与 ambiguity mask。manifest 必须绑定 frozen source、run ID、OCR manifest、host runtime receipt、脚本/schema 及三个图像产物哈希；gate summary 在外层绑定 manifest 自身哈希，避免自引用。exit 3 只允许携带并核验 `GEOMETRY_INCONCLUSIVE`，公开入口最终仍以 exit 3 fail closed；其他非零退出、缺失产物或哈希不闭合直接失败。

Phase-1 只报告四类观测：逐候选字形 ink bbox、严格筛选的水平单行 ink-bottom alignment、可靠同容器/同排候选对的 signed/minimum gap，以及矩形/圆角框候选。ink-bottom alignment 只是光栅墨迹下缘的对齐观测，不是 PNG 无法恢复的字体排印 baseline。公式、纵排、多行、低分辨率或受框线/图形污染的区域必须保留为 `INCONCLUSIVE`。原始 manifest 始终为 `mode=observation_only`、`policy.promotion_allowed=false`；晋升只通过独立 receipt，要求至少四类稳定图例，每个可晋升类不少于 30 个实例，median≤1 px、P95≤2 px、高风险误晋升为 0。箭头、箭杆、端点和 connector 拓扑精测属于 Phase-2。

普通文字与行内公式必须分离，例如：

```jsonc
{
  "id": "T-loss",
  "type": "text",
  "content_runs": [
    {"kind": "text", "text": "Loss "},
    {"kind": "math", "formula_id": "EQ-loss"}
  ]
}
```

preflight 以保守规则拦截普通 `text` 中的 LaTeX 定界符/命令、明确上下标语法、等式/积分/求和等数学运算符和 Unicode 上下标。仅有连字符、数字或希腊字母不构成自动判定，因此 `IL-6`、`p53`、`α-SMA` 等科研实体不被误判；语义角色已声明为 equation/formula/variable 时仍必须拆成 math run。触发器只是防漏网，不替代 Designer 对数学语义的显式建模。

## 6. formulas[]

- `canonical_latex` 仅来自用户确认或可靠原文；它是唯一公式真值，使用 UTF-8 原字节计算 `latex_sha256`。不得从渲染图片或保存后的 OMML 反推并覆盖它。
- PP-OCRv6 普通文字结果只能触发 `formula_candidate`，不能自动生成“可信 LaTeX”。
- 当前无公式模型时，二维数学结构默认 `INCONCLUSIVE`；若用户/原文已经给出权威 LaTeX，可在 receipt 中确认后继续。
- 每条记录必须声明 `mode=inline|display`、`render_kind=native_office_math` 与 `fallback_policy=strict_no_raster_no_svg`。display 公式恰好由一个 `type=formula` 元素引用；inline 公式恰好由一个 text `content_runs[].formula_id` 引用；`element_id` 必须等于该唯一所有者。
- 冻结 spec 前先用 `tools/powerpoint_native_math.py` 做无 PPTX 变更的 LaTeX→MathML→OMML 编译。每条公式绑定一个 hash-bound `NATIVE_OFFICE_MATH_CONVERTER_RECEIPT`，至少含 canonical LaTeX/hash、formula ID、mode、MathML hash、精确编译 OMML hash、`office-math-semantic-v2` profile/hash、转换器版本、MML2OMML.XSL hash 和 `native_target={kind:office_math,wrapper:a14:m,omml_root:m:oMath|m:oMathPara}`。validator 必须用当前固定转换器和可信 XSL 重编译，不接受仅内部哈希自洽的自签 JSON。receipt 缺失、非 PASS 或绑定不一致均阻断 Drawer。
- MathText 仅做近似容量诊断；其解析成功**不证明** PowerPoint 能插入原生公式，其解析失败也不能被 PNG/SVG fallback 掩盖。最终容量仍以保存/重开后的 Office Math 结构与 fresh render 为准。
- 公式编号、`\tag`、`\label`、`\ref` 及 Office 不支持的排版不得静默丢失；编号另建可编辑文本，转换器出现语义降级时停在 `INCONCLUSIVE`。
- 注入 plan 的每个 math run 必须绑定 `formula_id + converter receipt 路径 + receipt SHA-256`；混合说明必须保留有序 text/math run。注入后状态只能是 `INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP`。one-shot `finalize` 必须现场生成 challenge，绑定当前事务，并执行保存、关闭、只读重开、MathZone/可见性读回与连续两份 fresh render。`standard` 对无风险通过项不长期保留逐 MathZone 控制图；`strict` 保留全部独立反事实图。机械成功状态只能为 `MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW`。

最小记录：

```jsonc
{
  "id": "EQ-loss",
  "element_id": "T-loss",
  "canonical_latex": "L=\\sum_i (y_i-\\hat{y}_i)^2",
  "latex_sha256": "<64 hex>",
  "mode": "inline",
  "render_kind": "native_office_math",
  "fallback_policy": "strict_no_raster_no_svg",
  "converter_receipt_path": "examples/generated/runs/<run_id>/math/EQ-loss.converter.json",
  "converter_receipt_sha256": "<64 hex>",
  "source_evidence": ["source_text"],
  "disposition": "CONFIRMED"
}
```

## 7. Review → Spec → Canvas-bound Preflight

在冻结 spec 后，先从 source 尺寸创建空白 canvas PPTX，再运行 canvas-bound preflight。preflight 必须读回 deck 的页面尺寸、幻灯片数与空白性，验证全部公式 converter receipt，并在 `PASS` receipt 中绑定 source、raw perception manifest、perception review receipt、全部公式 receipt、spec、schema、script 和 canvas PPTX 的当前哈希。Drawer 只能打开该 receipt 指向的 deck。

## 8. 精度边界

| 类别 | 要求 |
|---|---|
| 必须精确 | 源哈希/尺寸、全部 frozen 文字、节点数量和归属、拓扑与箭头方向、画布比例、对象映射 |
| 阈值容差 | 字体度量、抗锯齿、纯色采样、非语义曲线控制点、细小装饰 |
| 观察而非真值 | Phase-1 ink bbox、ink-bottom alignment、可靠 pair gap、frame candidate；必须携带不确定性并经过后续 promotion |
| 来源绑定例外 | 合规 `reference_atomic_asset` 可作终稿位图；整图、整 panel、复合 preview 仍禁止冒充完成品 |

像素差、SSIM 和 ROI 只作诊断；不得用背景面积平均掉文字、拓扑或重叠错误，也不得先静默缩放不同尺寸的图再宣称通过。
