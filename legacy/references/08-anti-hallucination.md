# 08 · 反幻觉与感知门禁

视觉幻觉不能靠“提醒模型认真看”消除。这里用多源证据、显式未知和绘制前硬门，把错误挡在首个可见版本之前。

## 1. 必做证据

1. **确定性源信息**：脚本实测参考图 SHA-256、像素尺寸、颜色模式和背景候选；禁止视觉模型估计。
2. **本地 OCR 双尺度**：PP-OCRv6 medium 全图首扫，再对重叠 tiles 复扫；结果映射回原图坐标并去重。
3. **局部方向复核**：普通方向模型只覆盖 0°/180°；疑似 90°/270°标签须旋转 crop 重跑。
4. **视觉结构盘点**：视觉模型识别面板、对象类型、父子、层级和拓扑，但不能把估计坐标当测量真值。
5. **确定性 preflight**：在绘制前检查 bbox、containment、z-index、普通碰撞、文字适配、端点和画布比例。
6. **运行时分权**：OpenCV/SciPy/scikit-image 分析必须绑定独立 host runtime receipt；Paddle runtime 只负责 OCR。两类解释器、依赖和输出以文件/hash 交接，不共享 Python 对象或用户 site。
7. **Phase-1 几何观测**：OCR 后由 Host CV 从冻结 PNG 与 hash-bound OCR manifest 提取 ink bbox、受限 ink-bottom alignment、可靠 pair gap 和 frame candidate；输出必须绑定 run/source/runtime/script/schema，并保留不确定性与降级原因。

## 2. OCR 证据纪律

- 只用 `D:\paddle ocr` 中已锁定的本地模型；禁止自动下载或调用远程 OCR/VLM。
- manifest 必须记录参考、模型、配置和脚本哈希。缺任一项时不可复用旧结果。
- 保存原始候选、bbox、score、pass/scale/orientation 和冲突 alternatives；不能只留最终字符串。
- 高置信不等于真值。标题、数字、单位、希腊字符和连线标签需与视觉/原文交叉核对。
- 对 `ν/v`、`ρ/p`、`l/1`、`O/0`、`×/x`、负号/连字符等高风险字符主动降级。
- 公式 crop 不得由普通 OCR 自动确认。当前未安装公式模型时，标 `formula_candidate` + `INCONCLUSIVE`，需要可靠 LaTeX/原文或用户确认。

## 3. Geometry refinement 证据纪律

- `GEOMETRY_OBSERVATIONS_READY` 表示可复现观测已生成，不表示恢复了原始 PPTX 的文本框、字体 hinting、排印 baseline 或亚像素矢量坐标；manifest 始终是 `mode=observation_only`、`policy.promotion_allowed=false`。
- 所谓 alignment 是光栅字形的 ink-bottom alignment，不是字体 baseline。只对满足严格前置条件的水平单行非公式文字报告 alignment/pair gap；公式、纵排、多行、低像素或与框线/图形污染相交的区域必须为 `INCONCLUSIVE`。
- 每个距离必须明确坐标系、参与候选、测量定义和 `uncertainty_px`；缺任一项不得伪造单值。
- 在独立 gold fixture 和 promotion gate 通过前，任何 Phase-1 数值都不能自动冻结为 Figure Spec、覆盖 OCR bbox 或绕过视觉/来源/用户交叉核验。
- 箭头、箭杆、端点和连接器拓扑属于 Phase-2；Phase-1 不得用 LSD/Hough 片段猜测方向。

## 4. Disposition

每项感知结果只能落入：

- `CONFIRMED`：来源一致且满足该类对象的证据要求；
- `INCONCLUSIVE`：候选冲突、低置信或证据不足；
- `UNREADABLE`：像素本身不足以可靠识别；
- `NOT_TEXT`：确认是非文字视觉对象。

禁止用统一 0.9 confidence 掩盖未知。关键项为 `INCONCLUSIVE/UNREADABLE` 时，状态必须停在 `PERCEPTION_GATE`。

## 5. 首稿前失败信号

- panel/node/edge 数量在不同读法中不一致；
- 背景误判导致大面积空白被当成前景；
- OCR 与视觉标题或箭头标签不一致；
- 公式被拆成无意义的普通文字串；
- 元素无父级、bbox 越界、子元素不被父级包含；
- 未声明的 shape–shape、text–shape 或 text–text 相交；
- 字体缺失、文本测量异常或预计溢出；
- 画布比例来自默认模板而非参考图。
- geometry manifest 缺失/哈希不闭合，或 observation-only 结果被当成几何真值。

命中这些信号时应修正感知/spec 或 `REGION_REPLAN`，不能寄希望于 Reviewer 事后“看出来”。

## 6. 首稿后的独立验收

Reviewer 应使用 fresh render 和只读结构证据，不复用 Drawer 的结论。它核对的是 frozen spec 是否被正确实现，而不是再次自由解释目标图。结论为 `PASS/NO_OP`、`MINOR`、`SPEC_INVALID/MAJOR` 或 `INCONCLUSIVE`；重大前提错误必须回退重规划。

## 7. 能力边界

本流程显著降低漏字、错字、错误画布、明显碰撞和坏初稿扩散，但不宣称消除所有视觉幻觉。细线拓扑、遮挡关系、复杂公式、微小希腊字符和照片级微资产仍可能需要用户证据。诚实保留未知比高置信编造更接近高质量首稿。
