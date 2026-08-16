# AI AutoFigure

把用户确认的科研图 PNG 高保真重建为可编辑 PowerPoint。项目采用 **perception-first**：在第一次绘制前完成本地 OCR、参考测量、规格冻结、文字容量和场景碰撞预检；绘制后的 Reviewer 只做验收或少量对象级修正。

```text
参考冻结 → 图像分析 + OCR → Phase-1 几何观测 → 人/证据处理歧义 → Figure Spec
→ Scene Preflight → 首个可见版本 → 验收（NO_OP 或 MINOR）
```

## 已核验的本机 OCR 基线

`D:\paddle ocr` 是可直接采用的稳定 PaddleOCR 环境，不需要换成 RapidOCR：

| 项 | 本机实测 |
|---|---|
| Python | 3.11.15 |
| PaddleOCR | 3.7.0 |
| PaddleX | 3.7.2 |
| PaddlePaddle GPU | 3.2.2 |
| NumPy / Pillow / SciPy | 2.3.5 / 12.3.0 / 1.17.1 |
| OpenCV | `opencv-contrib-python` 4.10.0.84 |
| 检测 | `PP-OCRv6_medium_det` |
| 识别 | `PP-OCRv6_medium_rec` |
| 行方向 | `PP-LCNet_x1_0_textline_ori`（0°/180°） |
| 设备 | RTX 5060 Laptop GPU，CUDA 12.9 / cuDNN 9.9 |

模型、inference 文件 SHA-256 及 Paddle 解释器中实际用到的分析依赖版本均锁在 `ocr-config.json`。OCR 验收阈值和 anchors 只从 `examples/target_figure.fixture.json` 读取，config 不复制第二份。运行器只读本地模型、禁止模型下载回退，并把 Paddle/缓存/临时写入隔离到当前 `examples/generated/runs/<run_id>/`。manifest 的 `NETWORK_NOT_REQUESTED_BY_PIPELINE` 只表示本流程不发起网络请求，不冒充操作系统级断网证明。

## 已核验的宿主 CV 基线

确定性几何分析使用独立的 Conda prefix `D:\opencv\env`，不复用受用户级
`site-packages` 污染的 Anaconda base，也不修改 PaddleOCR 环境：

| 项 | 锁定值 |
|---|---|
| Python | 3.12.13 |
| OpenCV | `opencv-python` 4.13.0.92 |
| NumPy / Pillow / SciPy | 2.4.4 / 12.2.0 / 1.18.0 |
| scikit-image | 0.26.0 |
| Torch / Paddle | 不安装；传统 CV 不依赖二者 |

`host-runtime.json` 只锁解释器、隔离策略和 requirements/schema 绑定；包版本的
唯一权威仍是 `requirements.txt`。新的 CV/QA 进程始终使用绝对解释器和
`-I -B -X utf8`，不得从 `C:\Users\...\site-packages` 借包。已有实机证据
绑定的原生公式工具为保留脚本哈希暂用 `PYTHONNOUSERSITE=1` + `-s`。
`D:\paddle ocr\env` 的
`opencv-contrib-python` 只服务 OCR adapter；两个环境中的 OpenCV wheel 不得混装。

目标图 `examples/target_figure.png` 的基线为 1536×1024、3:2、SHA-256 `239e74f150e1ba224d578183eef8d7194556607bc0375a9cf1a4828ce7c6ce04`。单次 full-image 实测得到 146 个文本框，平均 OCR score 约 0.962；普通中英标签效果好，公式/上下标会碎裂，因此只能作为 `formula_candidate`，不能自动冻结为 LaTeX。

## 为什么首稿会更可靠

- 近白背景不再硬编码 `#F0F0F0`；目标图实测为 `#FEFEFE`，避免把空白当前景。
- OCR 使用全图 + 重叠分块复扫，保留候选、冲突、方向、模型/脚本哈希，而不是只留一串“最终答案”。
- OCR 后由 Host CV 自动生成逐文字 ink bbox、受限的 ink-bottom alignment（不是字体 baseline）、可靠文字对间距和框体候选，并逐项保留像素误差与降级原因；公式、纵排、多行和框线污染区域不会被强行量化。
- 画布从 PNG 实测并预生成同纵横比 PPTX，绕开后端没有 slide-size setter 的限制。
- Figure Spec 在绘制前检查 source/hash-bound OCR review、合法容器、z-order、普通 shape/text/formula 碰撞、connector 路径净空、字体与公式容量，以及实际空白 PPTX 的 PageSetup/hash。
- 公式不再由普通文本框或 PNG/SVG 冒充：权威 LaTeX 经受限解析、MathML 和 Office `MML2OMML.XSL` 编译为 PowerPoint 原生 `a14:m/m:oMath`，保存重开后仍可用 Equation Tools 编辑。
- 不可合理原生化的照片级局部可进入 `manual_asset_slot`。为先展示完整候选，`reconstruct_1to1` 可使用 source SHA+bbox 绑定的 exact-pixel `reference_preview`；它必须显式待替换、不计原生覆盖率、从像素相似度诊断遮罩，并阻断 `APPROVED`。
- major finding 回 `REGION_REPLAN`；Corrector 只处理 minor patch，不能掩盖错误规格。

## 目录

```text
AI autofigure/
  autofigure.cmd                    # Codex/用户唯一公开的感知启动入口
  SKILL.md                         # 首稿优先工作流与状态机
  ocr-config.json                  # 本机 OCR 版本、模型和哈希锁
  schemas/                         # perception 与 figure-spec 合同
  references/                      # 权威边界、QA、反幻觉、后端合同
  tools/
    analyze_target.py              # 背景/结构确定性观测
    segment_panels.py              # 色块区域候选（不冒充真实 panel）
    paddle_ocr_manifest.py         # 离线 OCR、分块/旋转复核、去重与冲突保留
    geometry_refinement.py        # Phase-1 字形/对齐/间距/框体观测（非几何真值）
    finalize_perception_review.py  # 独立审核 raw OCR 并生成 hash-bound receipt
    run_perception_gate.ps1        # run_id 隔离的一键感知入口
    output_policy.py               # 项目内输出只能进入 examples/generated
    check_project_hygiene.py       # 阻止 work、根缓存和未知根级产物回归
    create_canvas_pptx.py          # 同比例空白 PowerPoint
    materialize_reference_preview.py # 生成仅限候选的无损局部预览及哈希收据
    compile_figure_spec.py         # 冻结 authority/review/canvas/math 与 Designer scene
    preflight_scene.py             # 绘制前场景、文字/公式、连线与真实画布硬门
    powerpoint_native_math.py      # LaTeX→OMML、原生公式注入与结构读回
    powerpoint_native_math_roundtrip.ps1 # PowerPoint 保存/重开与 MathZones 收据
    figure_lint.py                 # 保存后像素诊断（非语义硬门）
  tests/                           # 单元、契约与目标图 fixture
  examples/
    target_figure.png              # 稳定参考输入
    target_figure.fixture.json     # 稳定验收合同
    generated/
      runs/<run_id>/               # OCR/分析/渲染运行证据；不进入版本库
      native-math-poc/             # 经实机重建并保留的集成样例
```

这里刻意没有把“所有测试文件”都塞进 `examples`：可复现且值得审阅的样例证据进入 `examples/generated/`；pytest 用例副本、Python 字节码和工具缓存使用操作系统临时目录或 `examples/generated/.cache/`，不作为项目内容保留。`tools/`、`schemas/`、`references/` 等是执行逻辑与合同，不是测试结果，必须留在项目根的清晰职责目录中。运行 `& $HostPython -I -B tools/check_project_hygiene.py --pretty` 可检查旧 `work/`、根缓存、`__pycache__` 和未分类的 examples 子目录是否重新出现。由于 `mcp.json` 的服务工作目录位于兄弟项目，所有 Draw.io/PowerPoint MCP save/export 都必须使用当前 run 内的绝对路径；相对路径可能把产物写进插件目录。

## 使用

### 1. 维护者校验宿主依赖与 OCR 安装

这一节仅用于安装、升级或诊断 runtime。普通用户不需要输入这些 Python 命令；Codex 执行 PNG 重绘时也不得把它们转交给用户。

```powershell
$HostPython = 'D:\opencv\env\python.exe'
$PaddlePython = 'D:\paddle ocr\env\python.exe'
$env:PYTHONNOUSERSITE = '1'
& $HostPython -I -m pip install -r requirements.txt
& $HostPython -I -B -X utf8 tools\validate_host_runtime.py --config host-runtime.json --output examples\generated\runs\<run_id>\runtime\host-runtime-receipt.json --run-id <run_id> --source-sha256 <sha256>
& $PaddlePython -I -B -X utf8 tools\paddle_ocr_manifest.py --validate-only
```

### 2. 运行感知门禁

用户不需要激活 Conda，也不需要直接运行 Python 或 `.ps1`。Codex 必须自行调用根目录公开入口；薄启动器只为当前进程绕开 PowerShell execution policy，底层仍由唯一的 `run_perception_gate.ps1` 选择 Host CV/PaddleOCR 解释器并执行全部校验，不修改系统策略。

一键入口会创建新的 run 目录，依次运行结构分析、区域候选、PP-OCRv6、Host CV Phase-1 `geometry_refinement`，以及 agent-vision 任务包生成，并输出 OCR `perception-manifest.json`、`text_review.md`、OCR overlay、`geometry/geometry-manifest.json`、`geometry/geometry-overlay.png`、lossless label atlas/ambiguity mask、`agent-vision/task-package.json`（含裁剪图、提示词、应答骨架）及所有上游哈希。几何阶段 exit 3 只用于保留并核验 `GEOMETRY_INCONCLUSIVE` 证据，公开入口最终仍以 exit 3 fail closed；其他非零退出直接失败。agent-vision 阶段是增强层，其 `INCONCLUSIVE` 不降低感知门状态（可用 `-SkipAgentVisionPkg` 跳过）。其 manifest 只允许 `GEOMETRY_OBSERVATIONS_READY` 或 `GEOMETRY_INCONCLUSIVE`，并固定 `mode=observation_only`、`policy.promotion_allowed=false`。Paddle 运行缓存仅服务当前阶段，成功生成证据后由入口在核验 owned run 边界后删除，不保留空缓存树。可复现调用如下（通常由 Codex 执行）：

```bat
.\autofigure.cmd -InputPath .\examples\target_figure.png -Device auto
```

维护者需要查看底层参数时可运行 `Get-Help .\tools\run_perception_gate.ps1 -Detailed`，但不得建立第二套 Python 编排入口。

OCR 表格中的高分仍是模型自报。识图三模融合按 `references/11-agent-vision-protocol.md` 执行：入口生成任务包后，外层 Agent 亲自看图填写应答，再经校验、融合、排序审核：

```powershell
# 2.5 协议化 Agent 视觉（任务包已由入口生成于 agent-vision/）
#   a. Agent 按 agent-vision\INSTRUCTIONS.md 逐 crop 填写 agent-vision-response.json（Q1 独立看图/Q2 只选不写/Q3 三次独立采样/Q4 漏检巡查）
& $HostPython -I -B -X utf8 tools\validate_agent_vision.py --task-package examples\generated\runs\<run_id>\agent-vision\task-package.json --response examples\generated\runs\<run_id>\agent-vision\agent-vision-response.json --output examples\generated\runs\<run_id>\agent-vision\agent-vision-document.json
& $HostPython -I -B -X utf8 tools\cross_modal_fusion.py --ocr-manifest examples\generated\runs\<run_id>\ocr\perception-manifest.json --geometry-manifest examples\generated\runs\<run_id>\geometry\geometry-manifest.json --task-package examples\generated\runs\<run_id>\agent-vision\task-package.json --vision-document examples\generated\runs\<run_id>\agent-vision\agent-vision-document.json --segment-dir examples\generated\runs\<run_id>\segmentation --output-dir examples\generated\runs\<run_id>\fusion
```

先生成与 raw manifest 哈希绑定的决策模板（有融合产物时按分歧优先级排序并预填 `review_note`），再用用户确认或可靠原文逐项完成：

当确认依据来自论文、图注或其他可靠原文时，先用 `source-authority` 合同冻结参考 PNG、精确文字/LaTeX、语义关系和来源定位。校验器会复验 JSON Schema、参考 SHA/尺寸/颜色模式、bbox、公式 hash、关系端点和人工复核状态；OCR、VLM 或 PNG 像素均不能成为 authority evidence：

```powershell
& $HostPython -I -B -X utf8 tools\validate_source_authority.py --authority examples\<case>.source-authority.json --pretty
```

`DRAFT` 权威不得直接改成 `FROZEN`。先生成当前 run 独占的分色叠图和
hash-bound review manifest；绿色是原文已确认项，橙色是待人工确认候选，紫色是
`manual_asset_slot`，蓝色关系只列入右侧索引。叠图只用于审阅，不会回写 authority：

```powershell
& $HostPython -I -B -X utf8 tools\render_source_authority_review.py --authority examples\modularagent.source-authority.json --run-id <run_id> --output-dir examples\generated\runs\<run_id>\authority-review --pretty
& $HostPython -I -B -X utf8 tools\validate_source_authority_review.py --manifest examples\generated\runs\<run_id>\authority-review\review-manifest.json --pretty
```

authority 冻结后，先用确定性匹配器生成 review decisions。它只会提升“标准化文字精确一致且空间唯一”的候选，或“中心点只落入一个 CONFIRMED 公式 bbox”的候选；其余项目保持 `INCONCLUSIVE`，不得靠 OCR/VLM 自动补全。公式 authority 绑定可以把未被 OCR flags 识别为公式的候选安全提升为 `FORMULA_CONFIRMED`，最终校验器会重验 frozen authority 的路径、schema、SHA、source SHA、item disposition 与 canonical LaTeX：

```powershell
& $HostPython -I -B -X utf8 tools\prepare_authoritative_perception_review.py --manifest examples\generated\runs\<run_id>\ocr\perception-manifest.json --authority examples\<case>.source-authority.json --fusion-manifest examples\generated\runs\<run_id>\fusion\fusion-manifest.json --output examples\generated\runs\<run_id>\perception-review-decisions.authoritative.json
```

```powershell
& $HostPython -I -B -X utf8 tools\finalize_perception_review.py --manifest examples\generated\runs\<run_id>\ocr\perception-manifest.json --decisions examples\generated\runs\<run_id>\perception-review-decisions.json --init --fusion-manifest examples\generated\runs\<run_id>\fusion\fusion-manifest.json
# 编辑 decisions；公式必须提供可追溯 LaTeX/原文证据（融合预填的 LaTeX 提议仅供参考比对，永不自证）
& $HostPython -I -B -X utf8 tools\finalize_perception_review.py --manifest examples\generated\runs\<run_id>\ocr\perception-manifest.json --decisions examples\generated\runs\<run_id>\perception-review-decisions.json --output examples\generated\runs\<run_id>\perception-review-receipt.json
```

候选未全覆盖、决策非终态、公式无权威证据或 raw manifest 非 `OCR_HYPOTHESES_REVIEW_REQUIRED` 时，receipt 必须保持 `INCONCLUSIVE`。Figure spec 只能从当前 raw manifest + `PERCEPTION_REVIEW_PASS` receipt 映射，且对外 `disposition` 只使用 `CONFIRMED / INCONCLUSIVE / UNREADABLE / NOT_TEXT`。

### 3. 生成正确比例画布并预检

若 Figure Spec 把照片级局部声明为 `reference_preview`，先从当前冻结参考生成 exact-pixel 裁片。它不是最终素材；PPT 中还必须叠加原生 `REFERENCE PREVIEW — REPLACE ME` 标签：

```powershell
& $HostPython -I -B -X utf8 tools\materialize_reference_preview.py --source examples\target_figure.png --expected-source-sha256 <sha256> --bbox <x> <y> <w> <h> --asset examples\generated\runs\<run_id>\assets\<slot>.png --receipt examples\generated\runs\<run_id>\assets\<slot>.reference-preview.json --source-user-confirmed
```

```powershell
& $HostPython -I -B -X utf8 tools\create_canvas_pptx.py examples\target_figure.png examples\generated\runs\<run_id>\canvas.pptx --pretty
& $HostPython -I -B -X utf8 tools\compile_figure_spec.py --scene examples\generated\runs\<run_id>\scene-declaration.json --output examples\generated\runs\<run_id>\figure-spec.json
& $HostPython -I -B -X utf8 tools\preflight_scene.py examples\generated\runs\<run_id>\figure-spec.json --canvas-pptx examples\generated\runs\<run_id>\canvas.pptx --output examples\generated\runs\<run_id>\preflight.json --pretty
```

`scene-declaration.json` 是 Designer 的局部层级声明；编译器会验证 FROZEN authority、PASS review、空白画布、全部 CONFIRMED 公式及各自 converter receipt，并把父容器内的局部 `z_index` 编译为全局层级，同时保留 `scene_z_index` 供审计。authority 标为 `inline` 的公式必须进入仅含一个 `math` run 的原生文本容器；不能伪装成 display formula 对象。

只有同时绑定当前 source、raw perception manifest、perception review receipt、spec、schema、script 和空白 canvas PPTX 哈希，且 `authorized_for_drawer=true` 的 `PASS` preflight receipt 才允许 Drawer 打开 PowerPoint 绘制；Drawer 只能打开 receipt 指向的那一份 deck。OCR 派生文字还必须逐条绑定 review candidate ID，文字值与候选位置都一致；标签字符串本身不能自证。

### 4. 原生可编辑公式

PowerPoint 内的交付对象必须是 Office Math，而不是“保存了 LaTeX 源的图片”。独立公式使用 `display` math；中文/英文说明中的数学片段使用结构化 `text`/`math` runs，例如“其中”与 `\alpha_i` 分属普通文字和行内 Office Math。工作流先在关闭的 deck 中按稳定 shape name 原位替换占位对象，再打开、保存、重开并审计 `a14:m/m:oMath`、OMML hash 和 canonical LaTeX hash。

本机主路径使用已安装的 `latex2mathml` 和 Office 自带的 `C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL`。不支持或有危险的 LaTeX 命令必须阻断，不得静默退回普通文本、PNG、SVG 或旧 Equation OLE。MathText 只能作几何近似诊断，不能证明公式可被 PowerPoint 原生编辑。

每个 injection plan 的 math run 必须同时绑定 `formula_id`、`receipt_path` 和该 receipt 的 `receipt_sha256`。`inject` 成功只返回 `INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP`；脱离当前进程的 receipt 只是日志，静态 `audit` 最多返回 `STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE`，不能授权最终门禁。唯一授权入口是 one-shot `finalize`：它在当前进程生成随机 challenge，直接启动固定 PowerShell 子进程，完成 PowerPoint 保存/关闭/只读重开、MathZones/可见性/遮挡扫描、一次 warm-up 与两次稳定 fresh render；随后对每个 math run 分别从同一 PPTX 只读重开，只隐藏该 MathZone 并导出控制图，要求仅公式区域产生像素变化。父进程立即闭包验证 input/output/plan/injection report/script/finalizer/render 与全部控制图哈希：

```powershell
& $HostPython -s -B -X utf8 tools\powerpoint_native_math.py inject --input examples\generated\runs\<run_id>\candidate.closed.pptx --plan examples\generated\runs\<run_id>\math\injection-plan.json --output examples\generated\runs\<run_id>\math\injected.pptx --report examples\generated\runs\<run_id>\math\injection.json --pretty

& $HostPython -s -B -X utf8 tools\powerpoint_native_math.py finalize --input examples\generated\runs\<run_id>\math\injected.pptx --plan examples\generated\runs\<run_id>\math\injection-plan.json --injection-report examples\generated\runs\<run_id>\math\injection.json --output-pptx examples\generated\runs\<run_id>\math\roundtripped.pptx --roundtrip-receipt examples\generated\runs\<run_id>\math\roundtrip-receipt.json --render-directory examples\generated\runs\<run_id>\math\renders --output examples\generated\runs\<run_id>\math\native-math-finalize.json --pretty
```

`finalize` 拒绝覆盖已有输出，必须使用新的 run ID；成功状态是 `MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW`，不是 `APPROVED`。保存时 PowerPoint 会补字体属性、拆分 run，并把普通变量正规化为数学字母 Unicode，因此原始 OMML 字节哈希预期会变化。审计保留编译期精确哈希，同时使用版本化 `office-math-semantic-v2` token AST 只容忍这些已验证的等价变化；粗体、花体、双线体、上下标结构、分子/分母、数字或符号改变仍会失败。项目内实机样例的唯一权威索引是 `examples/generated/native-math-poc/case-manifest.json`；当前闭环指向 `roundtripped-final.pptx`、`renders-final/slide-1.png`、`roundtrip-receipt-final.json` 和 `audit-final.json`，manifest 逐文件记录字节数与 SHA-256。历史或失败收据保留用于回归，但不得冒充当前 PASS。

### 5. 测试

```powershell
& $HostPython -I -B -m pytest -q
& $HostPython -I -m ruff check --no-cache tools tests
& $HostPython -I -B tools\check_project_hygiene.py --pretty
```

## 能力边界

- Phase-1 几何输出是对冻结 PNG 的确定性像素观测，不是原始 PPTX 文本框、字体基线或矢量坐标的绝对还原。ink bbox、ink-bottom alignment、可靠 pair gap 和 frame candidate 在 gold fixture 与 promotion gate 完成前不能直接冻结为 Figure Spec；公式、纵排、多行、抗锯齿/框线污染区域会诚实降级。箭头、箭杆、端点与连接器拓扑精测属于 Phase-2。
- 本机没有 PP-FormulaNet；复杂公式必须使用可靠 LaTeX/原文或人工确认。未来加入公式模型时也必须“识别→语法检查→回渲染对比”，不能直接自证。
- PowerPoint 原生公式是 OMML，不是完整 TeX 引擎；canonical LaTeX 永久保留在 spec/对象元数据中。Office 不支持或会有损处理的命令必须显式失败或由用户改写，不能拿“看起来像”作为通过证据。
- 可编辑性机械证据不是 shape 类型或备注，而是 one-shot finalizer 对同一 hash-bound 候选现场执行 PowerPoint 保存、关闭、重开，确认有效 OMML、结构化 text/math runs、逐 MathZone 的 COM readback、可见性/遮挡扫描、两份像素一致的 fresh render，以及每条公式单独隐藏后只在本对象内出现的像素差。持久 JSON 在同一权限域内不是密码学证明，最终仍需独立 Reviewer；如需可转交的不可抵赖证明，必须另接受保护的外部签名服务。
- 公式 finalizer 是“该 authority set 中的公式确为原生、可编辑且可见”的机械子门，不单独证明整图与 frozen figure spec 一致。当前 injection plan 不重复保存整图 bbox/字号合同；非重叠且中性命名的伪装对象、多块微小遮挡等仍由 hash-bound 全图 artifact set 与独立 Reviewer 审核，所以该工具永远不能返回 `APPROVED`。
- OCR 降低文字幻觉，不负责 panel 语义、科研拓扑或照片内容；这些仍需视觉结构盘点和来源确认。外层 Agent 原生视觉已按 `references/11-agent-vision-protocol.md` 协议化接入：结构提议、冲突仲裁（只选不写）、公式 LaTeX 提议（多采样自一致、恒为 PROPOSAL_ONLY_NOT_AUTHORITATIVE）与漏检巡查四类查询经任务包/校验/融合成为可审计证据；视觉坐标仅 advisory（锚定后采用 CV 实测 bbox），TRIPLE 一致不豁免人审，视觉缺失时融合退化为 OCR+CV 双通道。
- `scientific-illustrator` v1.5.4 继续负责 PowerPoint/draw.io 原生对象操作和真实渲染；本项目补上其缺失的感知门、通用碰撞、文字容量、正确画布与 major replan。
- 自动化最多产生 `CANDIDATE`；只有用户可以授予 `APPROVED`。
