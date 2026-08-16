---
name: ai-autofigure
description: "把用户确认的科研图 PNG 高保真重建为可编辑 PowerPoint。先用本地 PaddleOCR、确定性图像分析和场景预检冻结可靠规格，再生成首个可见版本；适用于科研示意图、架构图、流程图和方法图的 PNG→PPTX 复刻、文字与拓扑核验、可编辑重建、人工素材槽及科研图像合规审计。"
---

# AI AutoFigure

目标不是“先画一个大概，再让 Reviewer 重做”，而是让**首个可见版本已经接近终稿**。绘制前允许多轮廉价的感知、规格和几何预检；绘制后的 Reviewer 只验收或提出少量对象级修正。

默认交付 PowerPoint 原生可编辑对象；draw.io 仅在用户选择时使用。视觉模型负责结构理解，本机 PaddleOCR 负责文字证据，确定性脚本负责尺寸、哈希、碰撞和文字适配。任何一种证据都不能单独自证正确。

## 范围边界

- 只从用户明确确认的参考 PNG 开始；参考路径、SHA-256、像素尺寸和模式必须冻结。
- 允许本机锁定的 PaddleOCR；禁止远程 VLM/OCR API、生成式 OCR 代替事实来源，禁止下载未锁定模型。外层 Agent 原生视觉按 `agent-vision-config.json` 协议执行：任务包由管线生成、产物哈希绑定、经 `validate_agent_vision.py` 校验，永不自证。记录 `NETWORK_NOT_REQUESTED_BY_PIPELINE`，除非有进程/操作系统级阻断证据，不得声称已断网。
- OCR 是候选证据，不是语义真值。低置信、冲突、旋转文字和公式必须标为 `INCONCLUSIVE` 或交用户确认，禁止猜字。
- Phase-1 `geometry_refinement` 是确定性的像素观测层，不是原始 PowerPoint 几何真值。它只报告字形墨迹框、受限的 ink-bottom alignment（不是字体 baseline）、可靠文字对间距和框体候选及其不确定性；未经 gold fixture 与 promotion gate 验证，不得直接冻结为 spec 坐标。
- 复杂照片级子元素走 `manual_asset_slot`。默认留 `empty` 槽；仅 `RECONSTRUCT_1TO1` 可在能力审计后使用哈希绑定的 `reference_preview` 无损最小裁片，让用户先看到完整候选。preview 必须显式 `REPLACE_ME`、不计原生覆盖率、从相似度诊断遮罩、阻断 `APPROVED`；整图 wrapper、面板截图、含可重建文字/公式/连接器/轴/图例/边框/定量证据的裁片仍禁止。
- VBA 不是默认后端，仅用户明确要求时作为可选导出。

## 最小加载

始终完整读取：

- `references/01-workflow-contract.md`
- `references/02-qa-gates.md`
- `references/06-manual-asset-slots.md`
- `references/08-anti-hallucination.md`
- `references/09-backend.md`
- `references/11-agent-vision-protocol.md`

用户要求投稿规范化时再读 `publication-profiles.yaml` 和 `references/03-style-principles.md`。审核来源与取舍时读 `references/10-source-synthesis.md`。

## 状态机

```text
FROZEN_REFERENCE
  → PERCEPTION_CAPTURE → PERCEPTION_GATE
  → SPEC_FROZEN → PREFLIGHT_PASS
  → FIRST_RENDER → ACCEPTANCE_AUDIT
  → CANDIDATE | CANDIDATE_WITH_SLOTS | CANDIDATE_WITH_REFERENCE_PREVIEWS
  → USER_FILLED_SLOTS → APPROVED
```

允许且必须支持两条回退：

```text
PERCEPTION_GATE --INCONCLUSIVE--> USER_CONFIRMATION → SPEC_FROZEN
ACCEPTANCE_AUDIT --SPEC_INVALID/MAJOR--> REGION_REPLAN → PREFLIGHT_PASS
ACCEPTANCE_AUDIT --MINOR--> MINOR_PATCH → ACCEPTANCE_AUDIT
```

参考哈希、模型哈希、规格或画布尺寸改变，会使其下游证据失效。生成者不能自授 `APPROVED`。

## 工作流

### 1. Designer：感知门禁与规格冻结

1. Codex 必须自行调用项目根的 canonical 入口 `autofigure.cmd -InputPath <reference.png> -Device auto`；不得要求用户手动输入 Python/PowerShell 命令，也不得绕过入口直调 `analyze_target.py`、`segment_panels.py` 或 OCR adapter。入口负责选择锁定的 Host CV/PaddleOCR 解释器、校验 runtime receipt，并为本次任务创建隔离的 `examples/generated/runs/<run_id>/`。不得复用旧 `_analysis` 文件判断当前参考图。`examples/` 根部只放稳定输入/fixture；一次性单元测试临时文件必须使用操作系统临时目录，不得写进项目或示例运行目录。
   调用 Draw.io/PowerPoint MCP 保存或导出时，先解析当前 run 的绝对路径并传给工具；MCP 工作目录在本项目之外，禁止使用相对输出路径。
2. 脚本读取参考图真实尺寸、颜色模式、背景候选和 SHA-256，禁止视觉模型估算画布。
3. 运行本地 PaddleOCR：全图首扫 + 重叠分块复扫；保留每个框、文本、置信度、原始方向、冲突候选、模型、实际分析依赖版本和脚本哈希。90°/270°文字走旋转 crop 复核。目标 fixture 的 OCR 阈值与 anchors 只能从 `examples/target_figure.fixture.json` 读取。
4. OCR manifest 完成后，canonical runner 必须用锁定的 Host CV 解释器执行 `geometry_refinement`，将 `geometry-manifest.json`、overlay、lossless label atlas 与 ambiguity mask 写入同一 run 的 `geometry/`。该阶段只观测逐文字 ink bbox、满足严格条件的单行水平 ink-bottom alignment（不是字体 baseline）、可靠同排文字对的 signed/minimum gap，以及矩形/圆角框候选；公式、纵排、多行或受框线/图形污染的区域必须降级为 `INCONCLUSIVE`，不得填造数值。manifest 必须绑定 source、run ID、OCR manifest、host runtime receipt、脚本与 schema 哈希。Phase-1 结果不得作为几何真值，不得绕过 gold fixture、promotion gate、视觉/来源交叉核验或人工确认；箭头与连接器精测属于 Phase-2。
5. 外层 Agent 原生视觉按 `references/11-agent-vision-protocol.md` 协议执行，不使用任何远程 VLM/OCR API。canonical runner 在 geometry 之后自动生成 `agent-vision/` 任务包（裁剪图 + 版本化提示词 + 应答骨架，全部哈希绑定）。Agent 会话内**亲自看图**填写 `agent-vision-response.json`：Q1 结构盘点独立看图（任务包不含 OCR 文本，两轮不得互相复制）；Q2 冲突仲裁只准从 selections 中选择或 REJECT_ALL，禁止书写新文本；Q3 公式转写独立采样 3 次，自一致由 `validate_agent_vision.py` 工具端计算；Q4 漏检巡查只判断是否含文字。应答必须过 `validate_agent_vision.py`（查询全覆盖、坐标画布内、绑定校验，fail-closed），再由 `cross_modal_fusion.py` 产出一致性分层与审核队列。融合只改变审核优先级：TRIPLE 一致也不豁免人审；视觉坐标仅 advisory，锚定后一律采用 CV 实测 bbox；公式提议永远是 `PROPOSAL_ONLY_NOT_AUTHORITATIVE`，权威 LaTeX 仍只能来自用户/原文。视觉文档缺失时融合退化为 OCR+CV 双通道，管线不硬依赖视觉。
6. 从 raw perception manifest 生成全候选决策模板；有融合产物时用 `finalize_perception_review.py --init --fusion-manifest <…/fusion-manifest.json>` 按分歧优先级排序并预填可追溯 `review_note`（含 LaTeX 提议原文，仅供人工比对，`authoritative_latex` 仍必须由权威证据填写）。用用户确认或可靠原文逐项完成，再生成哈希绑定的 perception review receipt。候选覆盖不完整、非终态或公式无权威证据时 receipt 必须 `INCONCLUSIVE`。
7. 仅从当前 raw manifest + `PERCEPTION_REVIEW_PASS` receipt 生成 `figure-spec.json`：稳定 ID、合法容器父子关系、bbox、z-index、`text_style`/`formula_style`、`allowed_overlap` 白名单、独立 `edges` 的端点/via/净空、结构化候选引用、来源证据与不确定性。纯 prose 使用 `text`；包含数学 span 的文字必须改为有序 `content_runs`，math run 只引用唯一 `formula_id`。spec 的 `disposition` 只使用 `CONFIRMED/INCONCLUSIVE/UNREADABLE/NOT_TEXT`。
8. 每条公式冻结 `canonical_latex`/UTF-8 SHA-256、`inline|display`、`native_office_math` 与 `strict_no_raster_no_svg`。用 `tools/powerpoint_native_math.py` 做 compile-only LaTeX→MathML→OMML 转换并绑定 PASS converter receipt；receipt 同时保留精确 OMML hash 与版本化 `office-math-semantic-v2` hash。MathText 不能充当此证明。
9. 关键语义无未决项后冻结 spec。未决项不能被 Drawer 自行解释。

### 2. Preflight：首稿前的确定性拦截

在打开 PowerPoint 绘制前，必须通过：

- 参考哈希/尺寸与 spec 一致；
- raw perception manifest 和 `PERCEPTION_REVIEW_PASS` receipt 的文件哈希、source binding、候选集与 spec 一致，未决项为 0；
- 所有 ID、父子引用、边端点和 z-index 有效；
- bbox 在画布内，子元素被父容器包含；
- 无未声明的 shape–shape、text/formula–shape、text–text 碰撞；父子关系不能被滥用为碰撞豁免；
- connector 的锚点、显式 via、画布边界、节点/文字净空和 connector–connector 交叉通过；
- 文字按选定字体、字号、边距和换行策略可容纳；普通 `text` 中无 LaTeX/上下标/等式等数学冒充，`IL-6`、`p53`、`α-SMA` 等科研实体不因连字符/数字/希腊字母被误判；
- 公式 canonical LaTeX/hash、唯一 inline/display 引用、render/fallback policy 和 compile-only converter receipt 全部闭合；MathText 只作近似容量诊断，不证明 Office Math 可插入；
- frozen 文字逐候选绑定 raw manifest 与 review receipt；文字值和候选 bbox 必须一致。关键标题、数字、单位和箭头标签还必须显式带 `user_confirmed` 或 `source_text`；
- 画布比例与参考一致，并预先生成该尺寸的空白 PPTX；preflight 读回页面尺寸和空白性，将 deck 路径与 SHA-256 写入 receipt。

任一 major finding → `SPEC_INVALID`，回到 `REGION_REPLAN`；不得“先画出来再看”。Drawer 必须消费同时绑定当前 source/raw manifest/review receipt/全部公式 converter receipt/spec/schema/script/canvas 哈希的 `PASS` preflight receipt，并只打开 receipt 指向的 deck；旧 receipt 不可复用。

### 3. Drawer：执行冻结规格

1. `powerpoint_status` → `powerpoint_get_capabilities`，打开预先按参考比例创建的隔离 deck；不要依赖默认 16:9。
2. Drawer 只执行冻结 spec，不重新读图猜文字或改变拓扑。
3. 按区域和 z-index 从背景到前景构建原生 textbox/shape/table/chart；connector 只从冻结的 `edges` 集合构建，对象名必须绑定 element/edge ID。
4. 当前 scientific-illustrator 的 formula→textbox 是 capability gap，禁止调用它交付公式。先为每条公式建立稳定 ID placeholder；保存并关闭 deck 后，由 `tools/powerpoint_native_math.py` 在 run 内候选副本上事务注入原生 `a14:m` + OMML。injection plan 的每个 math run 必须绑定 `formula_id + receipt_path + receipt_sha256`。最终只能调用同一工具的 one-shot `finalize`：由当前进程生成随机 challenge 并直接启动固定 PowerShell 子进程，经 PowerPoint 保存、关闭、只读重开，现场取得 MathZones、可见性/遮挡扫描和连续两份稳定 fresh render；每个 math run 还必须从同一文件独立重开、只隐藏其对应 MathZone 并取得对象内像素差为正且对象外像素差为零的控制图。禁止修改正被 PowerPoint 打开的 PPTX，也禁止用脱离当前事务的 receipt 授权通过。
5. `manual_asset_slot` 按四态执行：`empty/reference_preview/user_filled/backfilled_verified`。`reference_preview` 只能由 `tools/materialize_reference_preview.py` 从当前 SHA 绑定参考生成 exact-pixel PNG，并在 PowerPoint 中与原生可见标签 `REFERENCE PREVIEW — REPLACE ME` 组成可替换组；不得把它当最终素材、证据或原生对象。
6. 区域构建后取结构读回和 fresh render；重大偏差立即回到 `REGION_REPLAN`，不把坏布局继续扩散。

### 4. Reviewer：首稿验收

Reviewer 使用 fresh、只读证据，不相信 Drawer 的自报。至少核对：

- PPT 对象结构与 frozen spec；
- 当前整页/区域渲染与 hash-bound 参考；
- 保存后 OCR 文字与 frozen spec 的逐字差异；
- one-shot `finalize` 后的公式 readback：每条都唯一命中合法段落内的 `a14:m` + `m:oMath|m:oMathPara`；canonical LaTeX hash 与外部 plan/receipt 一致；`office-math-semantic-v2` hash 一致；text/math run 内容和顺序一致；当前 PowerPoint 子进程读到的 MathZone 数量、顺序、字符范围和文本哈希一致；shape 可见、不透明、完整在画布内、未被高 z-order 图片/OLE/普通文本公式覆盖；两次 fresh render 的解码像素一致；每个 MathZone 的独立控制图只在其所属对象内产生足量像素变化。原始 OMML C14N 在 PowerPoint 正规化后可变化，不能错误要求其字节哈希不变。静态 audit 最多是 `STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE`；机械成功只能是 `MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW`，不得自授 `APPROVED`；
- `powerpoint_audit_figure`、场景预检与 `tools/figure_lint.py` 的证据。

结论只能是：

- `PASS/NO_OP`：无需修改；
- `MINOR`：可由少量对象级改动解决；
- `SPEC_INVALID/MAJOR`：感知、布局或拓扑前提错误，回 Designer/Preflight 重规划；
- `INCONCLUSIVE`：证据不足，不能继续自证。

### 5. Corrector：仅处理小修

只修改 finding 指向的最小责任对象，修改后 fresh render + fresh audit + regression check。禁止用 Corrector 承担整区重排、错误文字清单或错误画布的抢救；这些必须走 `REGION_REPLAN`。同一 finding 连续两次无改善或震荡则 `STALLED`。

## 硬约束

禁止：把 target crop 冒充最终素材或可编辑完成品、整图 wrapper、面板截图、`data:image`、`roi_trace_*`、位图公式、SVG/EMF 公式、普通 textbox 公式、未验证 OLE 公式、手写 SVG 冒充、装饰模板化、编造科研事实。唯一例外是受控 `reference_preview`：只服务于候选可视化，必须是最小无损裁片、哈希/bbox 绑定、显式待替换、QA 遮罩且阻断审批。感知阶段可在 run 内做哈希绑定的临时分块/旋转识别。语义维度零容差；视觉维度按冻结参考和明确阈值验收。`compare_images` 一类会静默缩放或无条件通过的相似度脚本不能作为 gate。

## 交付

保存 `.pptx` 和 fresh 预览 PNG，并报告：run ID、参考/模型/规格哈希、画布尺寸、感知门禁、preflight、公式 converter/injection/readback receipts、原生 Office Math 数、区域/全图 gate、可编辑对象数、素材槽、残余 uncertainty、Reviewer 是 `NO_OP` 还是小修。

自动化最多标记 `CANDIDATE`；有未填/未验证槽时为 `CANDIDATE_WITH_SLOTS`；存在任一参考裁片预览时只能为 `CANDIDATE_WITH_REFERENCE_PREVIEWS`。只有用户替换全部 preview、填完必要槽并通过 backfill 验证后才可 `APPROVED`。
