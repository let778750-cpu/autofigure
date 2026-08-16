# 02 · QA Gates 与首稿验收

目标是把重大错误前移到感知/spec/preflight 阶段，使 `FIRST_RENDER` 成为可验收版本。严禁用单一总分或大面积背景相似度补偿文字、拓扑、碰撞和可编辑性错误。

状态值统一为 `PASS / WARNING / INCONCLUSIVE / FAIL / NOT_APPLICABLE`。必需 Gate 的 `FAIL` 或 `INCONCLUSIVE` 均不得继续到下一阶段。

## 1. 绘制前 Gates

### G0 · Reference

- 绝对路径、SHA-256、像素宽高、颜色模式、用户确认状态齐全；
- 当前文件与 manifest/spec 的 source binding 一致；
- 画布宽高比从参考实测，不从 PowerPoint 默认模板推断。

### G1 · Perception

- 图像分析未把白/近白背景整体误判为前景；
- OCR 已完成全图和重叠分块 pass，原始候选、框、score、方向和冲突均可追溯；
- 关键标题、数字、单位、箭头标签无未决冲突；
- 公式候选未被普通 OCR 自动确认为 LaTeX；
- OCR 后的 Phase-1 geometry manifest 与当前 source/run/OCR/runtime/script/schema 哈希闭合，overlay、lossless label atlas 与 ambiguity mask 均存在且哈希一致；manifest 固定 `mode=observation_only`、`policy.promotion_allowed=false`；
- 阶段只报告 ink bbox、ink-bottom alignment（不是字体 baseline）、可靠 pair gap 与 frame candidate；公式、纵排、多行、低分辨率或受框线/图形污染的观测已显式降级，不存在伪精确数值；`GEOMETRY_INCONCLUSIVE` 使 G1 保持 `INCONCLUSIVE`；
- raw manifest 的全部 candidate ID 均有 hash-bound review 决策，且 receipt 为 `PERCEPTION_REVIEW_PASS`；
- spec `disposition` 只允许 `CONFIRMED/INCONCLUSIVE/UNREADABLE/NOT_TEXT`；所有未知均以 `INCONCLUSIVE/UNREADABLE` 暴露，不以高 confidence 掩盖。

### G2 · Spec

- 每个元素 ID 唯一，父子、边端点和引用存在；
- bbox、z-index、文本/公式框参数、允许重叠白名单和来源证据完整；parent 只能指向合法容器；
- frozen text 逐条绑定 review candidate ID、值和 bbox；关键文字还绑定用户/可靠原文；
- 纯 prose 使用 `text`；混合内容使用有序 `content_runs`，每个 math run 恰好引用一条 inline formula；`IL-6`、`p53`、`α-SMA` 等实体标签不因连字符/数字/希腊字母被误判；
- 公式 canonical LaTeX/hash、inline/display、唯一所有者、`native_office_math` 和 `strict_no_raster_no_svg` 均完整；
- 每个对象映射到 `native_editable`、`manual_asset_slot` 或明确歧义；无整图 wrapper。

### G3 · Preflight

- 所有 bbox 在画布内，子对象被父容器包含；
- 无未声明 shape–shape、text/formula–shape、text–text 碰撞；非法 parent 不产生碰撞豁免；
- 字体可用且文字测量通过；MathText 只给出近似容量诊断，不作为 Office Math 可插入证明；
- 每条公式有 hash-bound、确定性重编译一致且为 PASS 的 compile-only native converter receipt；精确编译 OMML hash 与 `office-math-semantic-v2` hash 均闭合，否则阻断 Drawer；
- connector 端点、显式 via、画布边界、受保护对象净空和 connector–connector crossing 有效；
- 已创建正确比例的空白 deck，并读回 PageSetup/幻灯片数/零 shape 验证；preflight receipt 绑定当前 source/raw manifest/review receipt/spec/schema/script/canvas 哈希。

G0–G3 全部 PASS 后，才允许 `FIRST_RENDER`。任何 major finding 必须 `SPEC_INVALID → REGION_REPLAN`，不能留给 Corrector。

## 2. 首稿 Acceptance Gates

| Gate | PASS 条件 |
|---|---|
| semantic | PPT 原生文本逐字等于 frozen spec；数字、单位、公式和关系无误；canonical LaTeX hash 与公式元数据闭合 |
| topology | source/target、方向、分支、汇合、反馈和跨区域关系正确 |
| mapping | 每个 spec element 有唯一原生对象/槽/明确例外，无多余或遗漏 |
| editability | 主体模块、文字、连接线、关键公式、图例和轴是可编辑对象；每条公式是 Office Math，不是文本/图片/SVG/OLE 伪装 |
| geometry | 无裁切、越界、失真、非语义重叠；containment 成立 |
| connectors | 端点绑定边界、路径不穿受保护对象、无未声明交叉 |
| text_formula | 文本不溢出/不异常换行；readback 在合法段落位置命中 `a14:m` 且含 mode 对应的 `m:oMath`/`m:oMathPara`；canonical LaTeX hash、semantic OMML hash、唯一 formula ID 和有序 text/math runs 一致；one-shot PowerPoint finalize 现场读到的 MathZone 数量、顺序、字符范围和文本哈希一致；每条公式的独立控制图只在所属对象内产生像素差；shape 可见、不透明、在画布内、无高层图片/OLE/普通文本覆盖；公式有净空 |
| slot_integrity | 槽边界、比例、语义、层级、替换接口和状态诚实 |
| render | PowerPoint 首次 warm-up 导出丢弃；随后连续两份 fresh PNG 的尺寸与解码 RGBA 像素哈希一致，并与最终 PPTX/input/plan/injection report/工具哈希闭合；无丢字、白屏、丢对象、字体异常或修复弹窗 |
| regression | 当前修正未破坏已通过区域；证据与最新 revision/hash 绑定 |

Reviewer 结论：

- `PASS/NO_OP`：理想结果；首稿直接进入 candidate。
- `MINOR`：只需少量对象级位置、尺寸、字号、颜色或 route 微调。
- `SPEC_INVALID/MAJOR`：错误感知、画布、文字清单、拓扑或整区布局，必须重规划。
- `INCONCLUSIVE`：缺 fresh render、缺字体、证据/哈希不一致或无法可靠判断。

## 3. 红线

命中任一项即 FAIL：

- final 包含 target PNG、参考裁片、panel 截图、整图 wrapper、`data:image` 或 `roi_trace_*`；感知 run 内的临时 OCR 分块不得进入 final；
- 以位图/伪文字冒充公式，以截图冒充可编辑对象；
- 以普通 textbox、PNG/JPEG、SVG/EMF、整式图片或未验证 OLE 对象冒充原生公式；公式 readback 缺少合法位置的 `a14:m`/`m:oMath`、外部 receipt 绑定、semantic OMML hash、one-shot PowerPoint 逐 MathZone/可见性/fresh-render/独立控制图证据或 canonical LaTeX 元数据；
- 以手写、重放或事后修改的 detached receipt 冒充当前 PowerPoint 执行；静态 audit 不得高于 `STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE`，机械门禁也不得自授 `APPROVED`；
- 编造用户未提供的科研事实、数值、性能指标、公式含义或隐藏拓扑；
- 静默缩放不同尺寸图后比较，或相似度脚本没有可解释失败条件；
- 用 warning-free 的自报文本代替 bbox、对象结构和 fresh render 证据；
- 把 `GEOMETRY_OBSERVATIONS_READY` 当作原始矢量几何真值，或在 gold fixture/promotion gate 前用它自动覆盖 spec；Phase-1 也不得声称完成箭头/连接器精测；
- 在 `reconstruct_1to1` 中把参考背景强改为 `#FFFFFF`、改布局或套模板；
- 普通碰撞因后端 audit 未实现而被当作“已检查”。

## 4. 保真与投稿规范双轴

- `fidelity_verdict`：是否忠实实现冻结 PNG 的几何、颜色、视觉重量和局部风格。
- `publication_verdict`：是否满足用户选择的 profile；未选择时为 `NOT_EVALUATED`。
- `reconstruct_1to1` 的根背景跟随参考实测值；仅在 `publication_normalize` 或 profile 明确要求时改为纯白，并记录 `intentional_deviation`。
- 两轴冲突时交用户选择 `preserve_fidelity / normalize_publication / return_upstream`。

## 5. Quality Signals

像素 delta、SSIM、edge score、颜色差和 ROI loss 是软诊断，不能覆盖 Core Gate。比较前必须尺寸一致；尺寸不一致本身就是 geometry failure，不能静默 resize。

起始诊断阈值：

- 常规：`mean_abs_rgb_delta ≤ 18` 且 `top_roi_loss < 5%`；
- strict：`mean_abs_rgb_delta ≤ 3` 且 `changed_pixel_ratio ≤ 3%`。

阈值需用真实样本回归标定，不能被称为跨字体/渲染器的绝对标准。

## 6. 文字与碰撞

- 绘制前用指定字体、字号、边距、wrap/autofit 测量；绘制后读 PPT TextFrame 和实际渲染双检。
- 文字内容以 frozen spec 为准；OCR 用于发现渲染丢字/错字，不取代原生对象读回。
- 普通相交采用白名单：任何两个可见 bbox 相交都产生 finding，只有 `allowed_overlap`、父子 containment、合法 connector crossing 等对象级声明可豁免。
- warning 不得被工具层的 `passed=true` 吞掉；当前工作流按 finding 语义重新判定。

## 7. 修正与 STALLED

- `MINOR` 才交 Corrector；每次只改 finding 的最小责任对象，随后 fresh render/audit/regression。
- `MAJOR/SPEC_INVALID` 直接回 Designer/Preflight，允许整区重规划。
- 同一 finding 连续两次无改善或往返震荡 → `STALLED`，停止自动修改并报告。
- 自动 render-verify 最多 6 轮、minor patch 最多 3 轮；达到上限不能自动升级为 PASS。
