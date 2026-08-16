# AI AutoFigure 优化执行规格 V4：代表性纵切片 + 多模态 Pilot

> 状态：实施合同（2026-08-16）  
> 基线提交：`c9e120e0a298bafb601f098313d7c4aeb43ddc7b`  
> 本版本取代旧“直接用四图裁决 VLM/OCR/CV 主辅关系”的设计。旧版本由 Git 基线保留，不再作为执行依据。

## 0. 核心决定

1. **先打通代表性纵切片，再建评测基础设施。**
2. 纵切片目标使用：
   `examples/01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png`。
3. 四张现有 PNG 只用于 **Pilot / calibration**；不得据此修改生产授权、融合阈值或人审合同。
4. 项目根已建立独立 Git 仓库。金标、规则、工具和报告必须绑定独立仓库 commit；父仓状态不构成本项目证据。
5. 不再追问全局“谁是主通道”。评测问题按文字、几何、层级结构、拓扑、公式、漏检和审核队列分别回答。
6. 生产 `cross_modal_fusion.py`、OCR/CV 脚本和已进入历史证据链的 schema 在本轮保持冻结。

## 1. 纵切片参考冻结

### 1.1 参考文件

| 字段 | 冻结值 |
|---|---|
| 相对路径 | `examples/01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png` |
| 尺寸 | 1429 × 627 px |
| 文件大小 | 210794 bytes |
| SHA-256 | `792A16D4BD2C26CCA9FCA79668395A987825AB75EB2BC8A65F2D42A47C38A340` |
| 模式 | `reconstruct_1to1` |
| 目标后端 | PowerPoint 原生可编辑对象 |

参考 SHA 或尺寸变化时必须创建新 run，旧 authority、spec、preflight、render 和 audit 全部失效。

### 1.2 权威论文来源

- 论文：*ModularAgent: A Task-Aware Modular Framework for Joint Optimization of Multimodal Large Language Models and World Models*。
- 作者：Yu-Wei Zhan, Xin Wang, Pengzhe Mao, Tongtong Feng, Ren Wang, Wenwu Zhu。
- 来源：CVPR 2026 Open Access，pp. 8087–8096。
- 官方页面：
  `https://openaccess.thecvf.com/content/CVPR2026/html/Zhan_ModularAgent_A_Task-Aware_Modular_Framework_for_Joint_Optimization_of_Multimodal_CVPR_2026_paper.html`
- 目标 PNG 对应论文 Figure 2：框架通过前向语义注入与反向奖励/梯度反馈实现 MLLM 与 World Model 的双向耦合。

权威来源只授权论文明确提供的术语、符号、公式与拓扑含义；不授权从 PNG 猜测不可辨字符。

### 1.3 为什么选择该图

该图不是“小型”样例，而是代表性复杂纵切片，覆盖：

- 任务编码、MLLM encoder、WM encoder、专家分配器、模块化联合、行为学习、环境交互和联合优化；
- 多层容器、重复 expert cells、主流程、跨区连接和反向反馈；
- 实线、点线、灰色映射箭头、蓝色 forward path、粉色/黄色 backward path；
- 普通文字、上下标、希腊字母和内联公式；
- 图片级 observation 子元素与图标。

它能够同时检验感知、Figure Spec、拓扑、connector routing、原生公式、可编辑性和素材槽诚实性。

### 1.4 素材槽边界

下列照片级内容不得从参考 PNG 裁切后嵌入成品：

- `observation` 区域中的照片拼图；
- 若无法用原生矢量合理重建的照片级图标。

它们必须进入 `manual_asset_slot`。空槽存在时纵切片状态为 `CANDIDATE_WITH_SLOTS`；槽位外的所有文字、框、箭头、公式和图例仍必须原生可编辑。常规 avatar、奖杯、action、地球图标优先用原生形状或后端 stencil 重建，不默认占位。

## 2. 不可修改清单

本轮不得修改：

- `tools/paddle_ocr_manifest.py`；
- `tools/geometry_refinement.py`；
- `schemas/perception-manifest.schema.json`；
- `schemas/geometry-manifest.schema.json`；
- `examples/target_figure.fixture.json`；
- `tools/cross_modal_fusion.py`；
- `tools/run_perception_gate.ps1`。

若实现发现必须修改其中任一项，当前阶段停止，形成单独的证据失效分析，不夹带修改。

## 3. 小步实施顺序

### G0 · 独立基线（已完成）

- 独立 Git 根位于本项目目录；
- 基线提交为 `c9e120e...`；
- `235 passed, 1 skipped`；
- ruff PASS；
- hygiene PASS；
- 三张论文图按精确文件名归类为 stable benchmark source，保持现有路径不移动。

### G1 · Authority Contract

新增 `source-authority` 合同，逐项记录：

- authority item ID；
- exact text 或 canonical LaTeX；
- 类型：普通文字、内联公式、display 公式、语义关系；
- 权威来源：论文正文、Figure 2 caption、用户确认；
- 来源定位与 SHA；
- 参考 bbox；
- criticality；
- 状态：`CONFIRMED / INCONCLUSIVE / NOT_APPLICABLE`。

只有 `CONFIRMED` authority 可以生成 review decision。PNG 像素、OCR 和 VLM 均不能自授 authority。

门禁：schema、source binding、重复 ID、bbox、LaTeX hash 和证据类型测试全部通过。

### G2 · Fresh Perception Run

Codex 调用 canonical 入口：

```bat
.\autofigure.cmd -InputPath ".\examples\01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png" -Device auto
```

- 必须创建新 `examples/generated/runs/<run_id>/`；
- 不复用或覆盖历史 run；
- OCR、geometry 和 agent-vision task package 哈希闭合；
- exit 3 保留为真实 `INCONCLUSIVE`，不得为了纵切片强行降为 PASS。

### G3 · Authoritative Review

新增 review 准备工具，输入：

- 当前 OCR manifest；
- 当前 source-authority；
- authority schema。

匹配规则：

- exact normalized text + 空间唯一匹配；
- 公式必须由 candidate/bbox 与 authority formula 唯一关联；
- 多候选、一候选跨多 authority、bbox 不一致或文本分段无法闭合时输出 `INCONCLUSIVE`；
- 未匹配候选不得静默标 `NOT_TEXT`。

工具只生成 decisions；最终 receipt 仍由 `finalize_perception_review.py` 生成并校验。

门禁：全部 OCR candidates 恰好一条决策；关键文字与公式均有权威证据；receipt 必须为 `PERCEPTION_REVIEW_PASS` 才能继续。

### G4 · Scene Declaration 与 Figure Spec Compiler

新增 `scene-declaration` 合同，承载 Designer 明确冻结的：

- 层级 region/node；
- 原生 text/shape/icon/manual slot；
- bbox、z-index、visual signature；
- connector edge、方向、锚点、via、净空和 crossing exception；
- text candidate/authority 引用；
- formula owner 与 converter receipt 引用。

新增 Figure Spec compiler，消费：

- source-authority；
- PASS review receipt；
- scene declaration；
- canvas；
- native-math converter receipts。

compiler 只做确定性组装和绑定，不重新看图、不猜拓扑、不把 Phase-1 geometry 自动提升为 spec 真值。

门禁：schema、source/review/authority/canvas/formula 哈希全部闭合，且 spec 无未决关键项。

### G5 · Canvas 与 Preflight

- 创建 1429:627 同比例空白 PPTX；
- 运行现有 `preflight_scene.py`；
- 检查层级 containment、文字容量、普通碰撞、connector 净空、公式 receipt、canvas PageSetup 和全部绑定；
- 任何 MAJOR 返回 scene declaration 修正，不进入 Drawer。

### G6 · Drawer 与原生公式

- Drawer 只消费 PASS preflight 指向的 deck 与 frozen spec；
- 按 z-index 自后向前构建原生对象；
- 照片区只放诚实 manual asset slot；
- 内联/显示公式使用 stable placeholder，关闭 PPTX 后再走 native Office Math transaction；
- 所有 save/export 使用当前 run 的绝对路径。

### G7 · Independent Acceptance

Reviewer 使用 fresh readback/render 核对：

- 文字、符号、公式与 authority/spec 一致；
- 主要节点、层级和双向拓扑无遗漏；
- connector 方向、分支、反馈和交叉正确；
- native object mapping 一一对应；
- 公式通过 PowerPoint finalize 与独立控制图；
- 不含参考裁片、整图 wrapper、位图公式或伪可编辑对象；
- manual slots 的位置、比例、层级和替换接口正确。

允许结论：`NO_OP / MINOR / SPEC_INVALID / INCONCLUSIVE`。有空槽时最高状态为 `CANDIDATE_WITH_SLOTS`。

## 4. 四图 Pilot 的重新定位

纵切片通过后才实现 Pilot。现有四图只回答工具和任务是否可评，不回答全局主辅关系。

### 4.1 任务级维度

1. OCR 文字候选；
2. OCR 冲突仲裁；
3. CV candidate coverage；
4. 实际 anchor selection；
5. 层级 region；
6. parent/child；
7. connector topology 与方向；
8. 公式检测；
9. 公式转写；
10. miss scan；
11. review queue utility。

### 4.2 金标合同

金标使用层级 regions，不设每图最少 3 个 panel：

```text
regions[]: regionId / parentRef / depth / kind / bbox / readingOrder
relations[]: from / to / direction / relationKind
textLabels[]: text / bbox / coverageMode
formulas[]: bbox / canonicalLatex / authority
graphicsOnlyRegions[]
exclusions[]
```

- `coverageMode=EXHAUSTIVE` 才允许计算 OCR precision/false-positive；
- `SAMPLED_RECALL_ONLY` 只计算 recall；
- 金标先看原图和权威来源，不看 run 输出；
- overlay 经人工复核后才能 `FROZEN`。

### 4.3 指标修正

CV 必须分开报告：

- oracle candidate recall；
- 实际选中 anchor 的 IoU；
- anchor precision；
- false-anchor count；
- IoU 分布。

队列必须报告：

- precision@k；
- recall@k；
- error-coverage@k；
- 达到 95% 真错误覆盖所需审核量；
- review-all 与 focus-early-stop 的 coverage/risk 曲线。

`focus_true_conflict_coverage` 正确定义为：

```text
|focus ∩ true_error_or_disagreement| / |true_error_or_disagreement|
```

不得用 focus 总数作为分子。

### 4.4 禁止事项

- 不把 `human_burden=focus_item_count` 冒充当前生产合同；
- 不把候选 recall 冒充坐标准确率；
- 不在同一四图上拟合权重后再评价权重策略；
- 不把 qwen/glm 的历史失败当 GPT-5.6 Sol 能力错误；
- 不把基础设施失败计为内容识别错误；
- 不把异质任务混成一个总体 accuracy；
- 不因四图结果修改生产 fusion 或授权边界。

Pilot 所有报告必须带：

```text
PILOT_ONLY_NO_PRODUCTION_CHANGE
```

## 5. 输出与版本纪律

- perception、spec、preflight、PPTX、render、audit 和 Pilot evaluation 全部写入各自 run；
- writer 拒绝覆盖已有 output；
- 每份新 manifest 绑定 source、upstream、schema、script、runtime 与 Git commit；
- 工具/schema/authority/参考任一 SHA 改变，旧下游证据失效；
- 金标和 Pilot rules 必须在视觉应答和评测前提交；
- 若 Pilot 期间修正 gold/schema/阈值，版本号递增并使此前四图 evaluation 全部失效重跑。

## 6. 测试与提交节奏

每个小步执行：目标测试 → ruff → hygiene → 必要时全量 pytest → 独立 conventional commit。

建议提交序列：

1. `docs: freeze v4 optimization contract`；
2. `feat: add source authority contract`；
3. `feat: prepare authoritative perception review`；
4. `feat: compile frozen figure specs`；
5. `test: validate modularagent vertical slice`；
6. `feat: add hierarchical pilot gold contract`；
7. `feat: evaluate perception pilot`；
8. `docs: publish pilot findings`。

任一门禁失败时只修当前阶段；不得把后续实现混入当前提交。

## 7. 本轮完成定义

本轮不是以“写完 benchmark 工具”为完成，而是同时满足：

1. ModularAgent 新 run 的 authority、review、spec、preflight 全部闭合；
2. PowerPoint 候选的非照片主体原生可编辑；
3. 公式为原生 Office Math；
4. 双向拓扑和 connector 方向通过 Reviewer；
5. 照片素材槽诚实标记，整体为 `CANDIDATE_WITH_SLOTS`；
6. 四图 Pilot 工具通过测试并输出不越权的描述性报告；
7. 全量 pytest、ruff、hygiene PASS；
8. 独立 Git worktree clean，所有证据可追溯到提交哈希。
