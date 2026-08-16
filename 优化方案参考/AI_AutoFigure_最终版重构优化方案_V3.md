# AI AutoFigure 最终版重构优化方案
## Approved PNG → 高保真、可编辑 PPTX 的多 Agent 自动复刻系统

**适用项目：** `D:\AI+科研\AI智能绘图（最终版）\AI autofigure - 副本\ai-autofigure`  
**目标执行器：** Codex + GPT-5.6 Sol 原生视觉能力  
**核心工具层：** `icebird1998/scientific-illustrator`（PowerPoint / draw.io MCP 能力直接复用）  
**文档定位：** 面向 Codex / Claude Code / GLM 等工程 Agent 的可执行重构、测试与验收规范  
**配套架构文档：** `AI_AutoFigure_最终版系统架构图_V3.md`；两份文件通过 `ARCH-01 ~ ARCH-10` 严格双向对应  
**版本定位：** Reconstruction-only；本阶段**不包含科研图重新设计、美化、配色重构、版式再设计**
**预览兼容性：** 架构文件优先显示预渲染 SVG；Mermaid 仅作为可维护源码，不要求 Markdown 阅读器原生支持 Mermaid

---

# 0. 一句话目标

把**用户已经确认满意的 reference PNG**视为唯一视觉权威，通过多 Agent 的上下文隔离、单 Writer 的 PowerPoint 写入、原生 PPT 对象重建、复杂视觉单元的 reference 原像素裁剪、确定性结构/几何审计和独立渲染审核，将 PNG 尽可能一比一还原为**高保真、可编辑、结构可靠、可继续人工修改的 PPTX**。

---

# 1. 项目边界与总原则

## 1.1 本项目只负责什么

本项目只负责：

1. 读取一张用户已经确认满意的 `reference.png`；
2. 理解图中结构、对象、关系、层级和复杂视觉区域；
3. 将可可靠重建的对象转换为 PowerPoint 原生可编辑对象；
4. 将不适合原生重建的复杂视觉元素从 reference PNG 中直接裁剪并插入 PPT；
5. 对初版 PPTX 做结构、几何、连接线、字体、裁切、重叠、渲染差异等自动审核；
6. 只修复能够被证据明确定位的问题；
7. 输出最终 `final.pptx` 与必要的 QA 报告。

## 1.2 本阶段明确不负责什么

以下能力**不得进入当前默认运行链路**：

- 重新设计科研图；
- “顶会风格化”；
- 自动换配色；
- 自动重新布局；
- 自动把斜箭头改成直角箭头；
- 自动统一用户原图中的字体风格；
- 自动减少渐变、3D、装饰元素；
- 调用额外 VLM API；
- 调用图像生成模型重新生成复杂子资产；
- 把 VBA 作为默认绘图主路径；
- 为了提高 SSIM 而牺牲语义、结构或可编辑性。

旧的 `claude设计 / codex设计 / deepseek设计 / D:\自动AI科研绘图` 中的“科研图美化”能力保留在原项目，**当前 AI autofigure 不加载这些美化规则**。

---

# 2. 不可违背的设计原则

## P0 — Reference PNG 是视觉权威

用户已经确认满意的 reference PNG 是本项目的视觉真值。

禁止 Agent 以“更规范”“更专业”“更像顶会”等理由擅自：

- 改颜色；
- 改布局；
- 改字体风格；
- 改连接线形式；
- 改模块比例；
- 改视觉层级；
- 简化或重构原图。

> **Reference fidelity 高于通用科研审美建议。**

## P1 — 多 Agent 思考，单 Writer 修改

允许多个 Agent 并行分析、裁剪和审核，但**同一时刻只有 Writer Agent 可以修改 PPT**。

原则：

> **Read Parallel / Write Serial**

禁止多个 Agent 并行修改同一 PPTX / 同一 slide。

## P2 — 角色隔离不等于角色扮演

多 Agent 的核心价值不是“多几个专家名字”，而是：

- 上下文隔离；
- 认知隔离；
- 防止历史错误持续污染；
- 避免绘图 Agent 审批自己的结果；
- 控制每个 Agent 只读取当前任务必要的信息。

应使用短生命周期 Agent，而不是让所有 Agent 从任务开始一直活到任务结束。

## P3 — Artifact-first Communication

Agent 之间禁止通过超长自然语言报告交接。

必须优先通过临时结构化 artifact：

```text
examples/generated/runs/<run_id>/
  state.json
  regions/R01_spec.json
  audits/R01_v2.json
  renders/R01_v2.png
  assets/raster_003.png
```

临时文件只服务于当前任务，任务结束后可以清理。

## P4 — 原生优先，复杂视觉采用 reference raster

所有对象只分两类：

### NATIVE
适合使用 PPT 原生对象可靠重建：

- text box；
- rectangle / rounded rectangle；
- circle / ellipse；
- basic shape；
- line；
- arrow；
- connector；
- regular table；
- regular chart；
- border；
- grid；
- legend；
- editable annotation；
- 可可靠表达的重复结构。

### REFERENCE_RASTER
不适合稳定原生重建的复杂视觉元素：

- 复杂 3D 科研示意；
- 高细节图标；
- 显微图；
- 遥感/医学/实验图像；
- 不规则复杂纹理；
- 复杂渐变体；
- 高密度手绘/生图模型微资产；
- 无法可靠拆解的复杂复合视觉单元。

REFERENCE_RASTER 必须直接来自 approved PNG 的原像素裁剪。

禁止为了“补全细节”重新调用 image generation。

## P5 — 可以没有复杂持久化 metadata，但不能没有运行时几何状态

最终项目不需要为每个 raster 维护庞大的永久元数据体系。

但是在当前任务运行期间允许临时存在：

- coarse ROI；
- refined bbox；
- crop padding；
- cropability；
- region id；
- placement；
- audit findings。

这些信息可以任务结束后删除。

## P6 — 最终视觉一致性定义在 Render Space，而不是 Object Space

不能要求：

```text
PPT object bbox == PNG element bbox
```

真正目标是：

```text
Render(PPT) ≈ Reference PNG
```

允许 PowerPoint 内部几何参数为补偿渲染器而产生小幅偏移，只要最终导出的视觉结果更接近 reference。

## P7 — 独立证据高于 Agent 自报成功

以下内容都不能作为“已经正确”的证据：

- MCP 调用返回 success；
- Writer 说“已完成”；
- 对象成功创建；
- 文件成功保存。

真正有效证据必须来自：

1. PPTX / OOXML / MCP 结构读取；
2. 当前最新渲染 PNG；
3. reference crop；
4. deterministic audit；
5. fresh Auditor 的独立判断。

---

# 3. 总体多 Agent 架构

> 对应架构图：`ARCH-01`。

## 3.1 推荐角色

系统采用五类角色，但并非五个长期常驻 Agent。

| 角色 | 生命周期 | 可看 Reference | 可改 PPT | 主要职责 |
|---|---|---:|---:|---|
| Orchestrator | 长期 | 尽量少 | 否 | 状态机、调度、Pass/Fail、区域推进 |
| Analyst | 短期 | 是 | 否 | 视觉解析、对象分类、拓扑与粗几何 |
| Raster Specialist | 短期 | 是 | 否 | complex raster 定位、精确裁剪、裁剪 QA |
| Writer | 长期 | 必要时 | **是，唯一** | PowerPoint 原生对象绘制、raster 插入、修复 |
| Auditor | 短期 | 是 | 否 | 独立结构+渲染审核、输出 findings |

不单独保留长期 Corrector Agent。

**Auditor 输出结构化 correction target，Writer 直接执行最小修复。**

## 3.2 权限模型

必须把 Agent 权限做成工程约束，而不是仅写在 prompt 里：

```text
Orchestrator      READ state / SPAWN agent / NO PPT mutation
Analyst           READ reference / WRITE spec / NO PPT mutation
Raster Specialist READ images / WRITE crop / NO PPT mutation
Writer            READ specs+findings / EXCLUSIVE PPT mutation
Auditor           READ reference+render+PPT audit / WRITE findings / NO mutation
```

如果 Codex 子 Agent 机制无法真正限制工具权限，则必须在 orchestrator 层只把对应工具暴露给对应 Agent。

---

# 4. 架构图索引与执行对应关系

本文件**不内嵌系统流程图**。所有架构可视化统一维护在配套文件：

`AI_AutoFigure_最终版系统架构图_V3.md`

架构文档采用“双轨表示”：

1. **已渲染 SVG 预览**：普通 Markdown 预览器无需 Mermaid 插件即可直接显示；
2. **Mermaid 源码**：供 Codex / Claude Code / GLM 等 AI 读取、修改和版本审查。

两份文档通过固定编号 `ARCH-01 ~ ARCH-10` 严格双向对应：

| 架构编号 | 本方案对应章节 | 架构主题 |
|---|---|---|
| `ARCH-01` | §3 | 多 Agent 总体拓扑、权限边界、Single Writer |
| `ARCH-02` | §4、§6、§31 | Approved PNG → final.pptx 端到端主流程 |
| `ARCH-03` | §5、§30 | Context Firewall、短生命周期 Agent、Artifact-first Handoff |
| `ARCH-04` | §7–§12 | NATIVE / REFERENCE_RASTER、Residual-Guided Cropping、Raster Promotion |
| `ARCH-05` | §13–§17 | P0–P4 QA、Conflict Resolver、最小修复闭环 |
| `ARCH-06` | §18–§23 | 项目目录、Scientific Illustrator 依赖、Read Parallel / Write Serial |
| `ARCH-07` | §24–§25 | 状态机、异常状态、STALLED 防死循环 |
| `ARCH-08` | §26–§27 | Unit / Integration / Regression / Final Acceptance |
| `ARCH-09` | §30–§31 | Agent Handoff 与 Phase 1–7 工程落地顺序 |
| `ARCH-10` | §32–§35 | 最终系统总览、Definition of Done、权威优先级 |

### 4.1 文档一致性硬约束

1. 任何对角色、权限、状态机、目录、QA Gate、Raster Policy 或执行顺序的修改，都必须同步更新对应 `ARCH-*`；
2. 任何对架构图节点或边的修改，都必须反向核对本方案对应章节；
3. 两份文件冲突时，**本方案文字定义暂时作为规范真值**，但冲突本身视为文档测试失败，必须立即同步；
4. CI / 测试脚本应校验：
   - `ARCH-01 ~ ARCH-10` 在两份文件中全部存在；
   - 项目绝对路径一致；
   - Agent 名称一致；
   - 状态名与 Gate 名一致；
   - 配套文件名一致；
5. 架构 SVG 由架构源码生成，**不得人工单独修改 SVG 而不更新 Mermaid / 架构定义**。

---
# 5. 上下文隔离与 Agent 生命周期

> 对应架构图：`ARCH-03`。

## 5.1 Orchestrator

Orchestrator 不负责细节绘制。

它只维护：

```json
{
  "task": "reference_to_pptx",
  "reference": "reference.png",
  "regions": {
    "R01": "PASS",
    "R02": "REPAIR",
    "R03": "PENDING"
  },
  "current_region": "R02",
  "whole_figure_gate": "PENDING"
}
```

禁止把以下内容长期灌入 Orchestrator 上下文：

- 所有对象 bbox；
- 所有字体参数；
- 所有历史 screenshot；
- 所有 MCP 日志；
- 所有旧 findings 全文。

## 5.2 Analyst

Analyst 必须：

- read-only；
- 不调用 PowerPoint mutation；
- 不知道 Writer 最终决定；
- 不评价“我画得怎么样”。

### Global Analyst

只负责：

- canvas/aspect ratio；
- panel / region；
- reading order；
- global alignment anchors；
- 全局视觉层级；
- major raster candidates。

### Region Analyst

每次只分析一个 region。

推荐一个 region 一个短生命周期上下文。

输出最小 `Rxx_spec.json`：

```json
{
  "region_id": "R03",
  "bounds_norm": [0.31, 0.12, 0.28, 0.41],
  "objects": [
    {
      "id": "E31",
      "class": "native_text",
      "text": "Cross Attention"
    },
    {
      "id": "E32",
      "class": "native_shape",
      "shape": "rounded_rectangle"
    },
    {
      "id": "E33",
      "class": "native_connector",
      "source": "E31",
      "target": "E32"
    },
    {
      "id": "E34",
      "class": "reference_raster_candidate"
    }
  ]
}
```

不要生成几十 KB 的自然语言分析报告。

## 5.3 Writer

Writer 是唯一拥有 PPT 修改权限的 Agent。

职责：

1. 初始化/连接 PowerPoint；
2. 建立 slide；
3. 按 region 创建 native objects；
4. 使用 stable semantic names；
5. 插入已验证 raster crop；
6. 执行 Auditor 指定的最小对象级修复；
7. 每轮修复后重新导出当前 render；
8. 最终保存 PPTX。

### Writer 禁止

- 自己批准自己的区域；
- 擅自美化；
- 擅自改变 reference；
- 因为一个局部有问题而截图整个 panel；
- 用 broad raster 覆盖本来可以编辑的文字/箭头；
- 同时和其他 Agent 修改 PPT。

## 5.4 Auditor

每轮审核应尽量使用 fresh context。

推荐：

```text
R03 Writer v1
→ Auditor-R03-v1
→ findings
→ Auditor 销毁

Writer repair
→ Auditor-R03-v2
→ findings/pass
→ Auditor 销毁
```

Auditor 每次只读取：

- reference crop；
- current render；
- current PPT structure audit；
- hard reconstruction rules。

不要给它完整修改历史。

---

# 6. Region-by-Region 构建策略

> 对应架构图：`ARCH-02`、`ARCH-03`。

禁止一次性把整张复杂图全部画完再审核。

默认：

```text
R01
Build → Audit → Repair → PASS
↓
R02
Build → Audit → Repair → PASS
↓
...
↓
Whole Figure Audit
```

原因：

- 降低上下文压力；
- 降低错误扩散；
- 提供明确 rollback boundary；
- 避免后期一次修改破坏大量已正确区域；
- 更容易精确定位 raster residual。

区域通过后进入 **Frozen** 状态。

已 Frozen 的 region 只能在：

- global alignment 明确要求；
- Whole-Figure Audit 明确给出 evidence；

时解冻。

---

# 7. NATIVE / REFERENCE_RASTER 决策协议

> 对应架构图：`ARCH-04`。

## 7.1 默认优先级

```text
Can reliably reconstruct natively?
    ├─ YES → NATIVE
    └─ NO  → REFERENCE_RASTER
```

不要为了追求“100% 可编辑”把不适合的复杂图硬拆成大量低质量形状。

## 7.2 NATIVE 的判定

如果目标主要由以下 grammar 组成，应原生重建：

- rules-based geometry；
- text；
- standard arrows；
- standard connectors；
- borders；
- common symbols；
- repeated grids；
- tables；
- normal charts；
- simple diagrams。

## 7.3 REFERENCE_RASTER 的判定

以下情况优先 raster：

- VLM 无法稳定拆解；
- 需要大量自由曲线才能逼近；
- 细节密度明显高于 PPT 原生图形表达能力；
- 复杂 3D；
- 生物/化学/医学/卫星等高细节微资产；
- 自然图像；
- 强纹理；
- 内部视觉复杂度远大于其在整图中的语义作用。

---

# 8. 精确裁剪：核心技术方案

> 对应架构图：`ARCH-04`。

精确裁剪不得依赖 VLM 直接输出精确像素 bbox。

核心原则：

> **VLM 求 Recall，传统算法求 Precision。**

## 8.1 Stage A — Semantic Coarse ROI

Analyst 只给近似区域：

```text
x ≈ 43%–65%
y ≈ 20%–56%
```

允许故意扩大。

目标是：

- 100% 包含目标；
- 不要求边界精确。

## 8.2 Stage B — Native-first Reconstruction

在精裁 raster 前，Writer 先绘制当前 region 中可识别的 NATIVE：

- labels；
- border；
- connector；
- arrow；
- title；
- legend；
- geometry。

导出：

```text
R03_native_only.png
```

## 8.3 Stage C — Residual-guided Crop

计算：

```text
Reference Region
vs
Native-only Render
```

不是直接把 pixel diff 当最终 mask，而是利用它帮助锁定：

> 仍未被 native object 解释的高复杂区域。

## 8.4 Stage D — Deterministic Refinement

推荐轻量工具：

- OpenCV；
- color distance；
- edge / gradient；
- local variance；
- threshold；
- morphology；
- connected components；
- contour；
- optional GrabCut，仅用于帮助估计 foreground bbox。

不引入额外大型 segmentation model 作为主依赖。

## 8.5 GrabCut 使用约束

允许 GrabCut 帮助推断 foreground。

但：

**禁止用 segmentation mask 重新生成资产像素。**

最终保存的必须仍然是：

```python
crop = reference[y1:y2, x1:x2]
```

即原始 reference 像素。

原因：

- 防止 halo；
- 防止 mask 毛边；
- 防止误删细节；
- 防止内部白色区域消失。

---

# 9. Cropability Gate

> 对应架构图：`ARCH-04`。

Raster candidate 分三类：

## ISOLATED

独立矩形视觉区域。

处理：

```text
coarse ROI
→ deterministic tighten
→ crop
```

## SEPARABLE

外轮廓复杂，但与其他对象没有强粘连。

处理：

```text
coarse ROI
→ CV refinement
→ border safety
→ crop
```

## ENTANGLED

复杂资产与：

- 内部文字；
- 内部箭头；
- 内部 annotation；
- 复杂背景；

已经像素级融合。

禁止尝试高风险 inpainting / image generation。

应使用：

## Raster Promotion

扩大 raster 边界，直到得到：

> 最小视觉自洽 raster block。

例如：

```text
不要：
只裁卫星主体

改为：
卫星 + 内嵌箭头 + 内嵌 annotation
```

外围仍能原生化的内容继续 NATIVE。

---

# 10. Border Safety Test

> 对应架构图：`ARCH-04`。

精裁后的 bbox 不能立即接受。

需要检查 crop 四周的内边缘 ring。

若 ring 上仍存在明显：

- strong edge；
- foreground likelihood；
- texture；
- non-background components；

说明目标碰到了边界。

执行：

```text
bbox
→ expand N pixels
→ test
→ expand
→ test
→ PASS
```

避免：

- 天线被截；
- 阴影被截；
- 箭头末端被截；
- 3D 结构被切边。

---

# 11. Render-back Verification

> 对应架构图：`ARCH-04`。

裁出 `asset.png` 后：

1. 以原始 scale；
2. 放回 reference 原位置；
3. 比较 asset 内部和 bbox 周围 ring；
4. 验证：
   - offset；
   - scaling；
   - crop loss；
   - edge discontinuity。

这一步用于发现错误 crop。

---

# 12. Raster 清晰度策略

> 对应架构图：`ARCH-04`。

本项目不主动调用 image generation 提高清晰度。

统一规则：

```text
只要是 approved reference 中的视觉资产：
→ 使用原图 crop
```

即使分辨率一般，也先插入。

用户以后可直接：

```text
PowerPoint → Replace Picture
```

替换高分辨率版本。

因此无需额外设计 `USER_ASSET_SLOT` 数据类型。

---

# 13. QA 架构：不能只用 SSIM

> 对应架构图：`ARCH-05`。

## 13.1 禁止单一总分

禁止：

```text
Score =
0.4 SSIM +
0.3 Layout +
0.3 Editability
```

因为严重语义错误可能被大面积背景像素抵消。

## 13.2 使用 Lexicographic Gates

按优先级逐层通过：

### P0 — Scientific / Semantic Correctness

检查：

- readable text；
- arrow direction；
- topology；
- source/target；
- object completeness；
- panel semantics。

P0 失败：

> 立即 FAIL，不进入像素追分。

### P1 — Structural Integrity / Editability

检查：

- reconstructable objects 是否 native；
- PPT object 是否存在；
- z-order；
- group；
- clipping；
- unsupported flattening；
- document integrity。

### P2 — Reference Geometry

检查：

- bbox；
- relative placement；
- width/height；
- scale；
- rotation；
- margins；
- repeated alignment；
- spacing；
- connector endpoints；
- route geometry。

### P3 — Local Renderer Fidelity

检查：

- font size；
- text wrapping；
- fill；
- stroke；
- opacity；
- shadow；
- arrowhead；
- corner radius；
- local color difference。

### P4 — Global Visual Fidelity

允许：

- SSIM；
- local SSIM；
- edge F-score；
- edge distance；
- color difference；
- region pixel difference。

这些是质量信号，不是语义 Hard Gate。

---

# 14. 科研作图规范吸收策略

> 对应架构图：`ARCH-05`。

旧 skills 中科研作图规范要分类吸收。

## 14.1 进入 Hard Gate 的规范

可确定性检测并且不会改变 reference 风格的规则：

- 文本不能无意裁切；
- 对象不能越界；
- 不允许无语义重叠；
- connector source/target 必须正确；
- arrow direction 必须正确；
- 箭头不能无意穿透模块；
- connector 不应穿过不相关文字；
- 关键对象需保持编辑性；
- final PPTX 保存后必须重新读取；
- 修复后必须 regression check；
- 图片不能覆盖本可以原生化的重要文字/连接线。

## 14.2 只进入 Advisory、当前不自动执行的规范

以下旧规则**暂不进入自动修改**：

- 顶会配色；
- house style；
- 推荐字体；
- 推荐直角箭头；
- 减少渐变；
- 减少 3D；
- 自动重新分栏；
- 自动“提升高级感”；
- 自动统一视觉语言。

本阶段这些规则最多用于人工报告，不得触发 Writer 修改 reference。

---

# 15. Reference Fidelity 与科研规范冲突裁决

> 对应架构图：`ARCH-05`。

建立三层 authority：

```text
Level A — Invariant Hard Constraints
Level B — Reference Fidelity
Level C — Scientific Style Advice
```

优先关系：

```text
A > B > C
```

## 15.1 Reference Override

例：

科研建议：

```text
connector 应优先直角
```

reference：

```text
明确为斜箭头
```

决策：

```text
REFERENCE_OVERRIDE
PRESERVE
```

不得修。

## 15.2 Renderer Compensation

若 reference 中视觉位置是 `x=500`，而 PPT 内部对象必须设置到 `x=496.8` 才能在导出后视觉上落到 `x≈500`：

允许。

因为最终目标是 Render Space。

## 15.3 Semantic Ambiguity

如果 reference 看起来可能：

```text
A → B
```

也可能：

```text
A → C
```

禁止 Writer 擅自猜测后“规范化”。

返回：

```text
AMBIGUOUS
```

由 Orchestrator 决定是否请求更精确局部分析或用户确认。

---

# 16. Audit Finding 标准格式

> 对应架构图：`ARCH-05`。

Auditor 输出必须短、结构化、可执行。

```json
{
  "finding_id": "F-R03-017",
  "region": "R03",
  "objects": ["E31", "E33"],
  "severity": "hard",
  "category": "connector_endpoint",
  "expected": "E33 terminates at E31 right boundary",
  "actual": "endpoint is 9 px-equivalent inside E31",
  "evidence": "render + object geometry",
  "repair_target": "move endpoint to boundary with visual clearance",
  "acceptance": "no overlap in fresh render"
}
```

避免：

> “箭头看起来稍微不自然，建议优化一下。”

这种结果不得进入自动修复。

---

# 17. 最小修复原则

> 对应架构图：`ARCH-05`。

Writer 接到 finding 后：

1. 只修改负责该问题的对象；
2. 不重画整个 region；
3. 不截图覆盖；
4. 不修改已通过对象；
5. 修完必须：
   - fresh render；
   - fresh audit；
   - regression check。

---

# 18. 推荐项目目录

> 对应架构图：`ARCH-06`。

最终运行时建议：

```text
AI autofigure/
│
├─ README.md
├─ SKILL.md
│
├─ agents/
│   ├─ orchestrator.md
│   ├─ analyst.md
│   ├─ raster-specialist.md
│   ├─ writer.md
│   └─ auditor.md
│
├─ contracts/
│   ├─ reconstruction-contract.md
│   ├─ qa-gates.md
│   ├─ raster-policy.md
│   └─ agent-handoff-contract.md
│
├─ scripts/
│   ├─ compare_reference.py
│   ├─ refine_raster_crop.py
│   ├─ audit_pptx.py
│   ├─ geometry_checks.py
│   └─ cleanup_workdir.py
│
├─ schemas/
│   ├─ region-spec.schema.json
│   ├─ audit-finding.schema.json
│   └─ state.schema.json
│
├─ tests/
│   ├─ unit/
│   ├─ integration/
│   └─ fixtures/
│
└─ examples/
    ├─ target_figure.png
    ├─ target_figure.fixture.json
    └─ generated/
        └─ runs/<run_id>/
```

---

# 19. 不应该复制进入项目的内容

> 对应架构图：`ARCH-06`。

不要把整个 `scientific-illustrator` 源码复制进 AI autofigure。

AI autofigure 应把它视为**工具层依赖**。

复用：

- PowerPoint live MCP；
- draw.io MCP（仅可选）；
- PowerPoint object primitives；
- render export；
- structure audit；
- align/distribute；
- connector；
- z-order；
- save/open/inspect。

AI autofigure 负责：

- orchestration；
- context isolation；
- region protocol；
- raster policy；
- deterministic QA；
- reference-fidelity policy。

---

# 20. 从 xjb-skill-image-to-vba 吸收什么

> 对应架构图：`ARCH-06`。

保留思想：

- Element Manifest；
- stable object id；
- pixel → PPT coordinate mapping；
- semantic anchor；
- connector endpoint；
- z-order；
- render → verify；
- preservation geometry contract。

不继续作为主路径：

- VBA-first；
- 两遍宏 Skeleton → Styled；
- 大量宏注入；
- 单纯 SSIM 追分；
- 固定多轮 VBA repair。

VBA 只允许作为极端 fallback，不作为默认执行路径。

---

# 21. 旧项目迁移策略

> 对应架构图：`ARCH-06`。

## 21.1 KEEP / EXTRACT

从旧项目提取：

- PPT 几何规则；
- 字体裁切检测；
- connector geometry；
- OOXML readback；
- overlap；
- z-order；
- regression；
- target-size 检查；
- anti-raster-overuse 规则；
- 可靠的测试 fixtures。

## 21.2 ARCHIVE

归档但不进入运行时：

- beautification skills；
- top-conference visual redesign；
- 自动科研配色；
- image generation；
- GLM/Claude vision API；
- 老的 VLM parser；
- 历史实验 report；
- staging；
- work cache。

## 21.3 DELETE FROM DISTRIBUTION

最终压缩包不得包含：

- `.env`；
- API key；
- 历史 `.git` 嵌套仓库；
- node_modules；
- Python cache；
- 大量旧 screenshot；
- 临时 render；
- 测试失败遗留文件；
- 无用模型权重。

---

# 22. Scientific Illustrator 的使用方式

> 对应架构图：`ARCH-06`。

默认 backend：

```text
PowerPoint
```

draw.io 不作为默认必经步骤。

只有：

- 用户明确要 `.drawio`；
- PPT 无法表达且 draw.io 明显有优势；

才调用 draw.io。

禁止默认：

```text
reference
→ draw.io
→ PPT
```

因为会增加：

- coordinate drift；
- font difference；
- connector difference；
- 双重维护成本。

---

# 23. 并行与串行策略

> 对应架构图：`ARCH-06`。

## 可以并行

- Region Analyst；
- 不同 raster candidate 的离线 crop analysis；
- 不同 frozen region 的 read-only audit；
- deterministic Python checks。

## 必须串行

- 对同一 PPTX 的 mutation；
- Writer operations；
- z-order 修改；
- grouping；
- slide-level save；
- global correction。

---

# 24. 状态机

> 对应架构图：`ARCH-07`。

推荐：

```text
INIT
↓
GLOBAL_ANALYSIS
↓
REGION_ANALYSIS
↓
REGION_NATIVE_BUILD
↓
RASTER_EXTRACTION
↓
REGION_AUDIT
↓
REGION_REPAIR
↺
REGION_PASS
↓
NEXT_REGION
↓
WHOLE_FIGURE_AUDIT
↓
GLOBAL_REPAIR
↺
FINAL_PASS
↓
DELIVER
```

异常状态：

```text
AMBIGUOUS_REFERENCE
CROP_UNSAFE
TOOL_ERROR
PPT_BACKEND_ERROR
AUDIT_CONFLICT
STALLED
```

异常不能被 Writer 静默“自己理解一下”后继续。

---

# 25. 防止死循环

> 对应架构图：`ARCH-07`。

每个 finding 维护：

```text
finding_id
attempt_count
previous_score
last_operation
```

若同一 finding：

- 连续 2 次修复没有改善；
- 或出现往返震荡；

停止自动修改，升级为：

```text
STALLED
```

禁止：

```text
为了 SSIM 改 → 结构变差 → 再改回来 → SSIM 再变差
```

---

# 26. 测试体系

> 对应架构图：`ARCH-08`。

必须建立三层测试。

## 26.1 Unit Tests

至少覆盖：

- coordinate transform；
- bbox normalization；
- crop expansion；
- border safety；
- connected component；
- overlap；
- connector endpoint；
- object bounds；
- text clipping；
- schema validation。

## 26.2 Integration Tests

至少测试：

### Case A — 简单流程图
全部 NATIVE。

### Case B — NATIVE + 单个独立 raster
测试精裁和 placement。

### Case C — 多 raster
测试并行 crop + 单 Writer。

### Case D — ENTANGLED asset
测试 Raster Promotion。

### Case E — 多 panel 复杂科研图
测试 region 生命周期。

### Case F — 极端长任务
验证上下文隔离是否有效。

### Case G — Reference vs 科研规范冲突
确保 `REFERENCE_OVERRIDE` 生效，Writer 不擅自美化。

### Case H — 高全局 SSIM、局部严重错误
确保局部 Hard Gate 能阻止误通过。

## 26.3 Regression Corpus

从过去 Claude/Codex/DeepSeek 项目中选典型失败案例：

- 箭头穿框；
- 箭头方向错；
- 字体溢出；
- bbox 偏移；
- raster 裁太宽；
- raster 裁掉边缘；
- 图片覆盖原生文字；
- z-order 错；
- 全局 SSIM 高但局部严重错误；
- VLM 坐标幻觉；
- Writer 自我确认造成误通过；
- 多 Agent 同时写 PPT 导致状态漂移。

每次重构后自动重跑。

---

# 27. 验收门槛

> 对应架构图：`ARCH-08`。

最终不能只报告：

```text
PPT created successfully
```

至少必须满足：

## Hard Pass

- PPTX 可以正常打开；
- readable text 无已知错误；
- connector semantic direction 无已知错误；
- 无关键对象缺失；
- 无严重 clipping；
- 无意外对象越界；
- 本应 native 的关键文字/箭头没有被 raster 覆盖；
- final save 后重新读取结构成功；
- zero unresolved hard findings。

## Geometry Pass

- region-level alignment 达到设定容差；
- connector endpoint 达到设定容差；
- repeated element spacing 达到设定容差；
- raster placement 达到设定容差。

## Visual Pass

- region-level render similarity 达标；
- edge correspondence 达标；
- 无明显局部差异热点；
- whole-figure visual consistency 达标。

具体数值阈值必须通过 regression corpus 标定后再固定，禁止凭空写死一个 SSIM 数字作为万能标准。

---

# 28. 推荐最终输出

```text
examples/generated/deliveries/<case_id>/
├─ final.pptx
├─ final_preview.png
└─ qa_summary.json
```

默认不要输出几十个中间报告。

详细 debug artifacts 留在：

```text
examples/generated/runs/<run_id>/
```

必要时再保留。

`qa_summary.json` 只保留：

- final gate；
- unresolved ambiguity；
- raster count；
- hard finding count；
- region pass status；
- renderer/backend；
- 是否存在用户后续建议替换的低清 raster。

---

# 29. SKILL.md 应该非常薄

最终 `SKILL.md` 不应该写成几万字百科全书。

它只负责：

1. 识别任务为 reconstruction；
2. 加载 reconstruction contract；
3. 启动 Orchestrator；
4. 指定 Scientific Illustrator 为 PPT 工具层；
5. 强制单 Writer；
6. 强制 region gate；
7. 强制 fresh audit；
8. 禁止 beautification；
9. 指向 scripts / contracts。

复杂细节放到 `contracts/`，按需加载。

这样可明显降低主上下文污染。

---

# 30. Agent Handoff 最小协议

> 对应架构图：`ARCH-03`、`ARCH-09`。

## 30.1 Orchestrator → Analyst

只传：

```text
reference path
region id
region crop / coarse bound
需要输出的 schema
```

## 30.2 Analyst → Writer

只传：

```text
Rxx_spec.json
reference crop path
```

## 30.3 Writer → Raster Specialist

只传：

```text
reference crop
native-only render
raster candidate id
coarse ROI
```

## 30.4 Writer → Auditor

只传：

```text
reference crop
current render
structure audit
region spec
hard QA rules
```

## 30.5 Auditor → Writer

只传：

```text
PASS
或
structured findings.json
```

禁止传递大段历史推理过程。

---

# 31. 建议的 Codex 执行顺序

> 对应架构图：`ARCH-02`、`ARCH-09`。

AI 在重构当前项目时必须依次执行：

## Phase 1 — Inventory

1. 扫描现有目录；
2. 建立 KEEP / EXTRACT / ARCHIVE / DELETE 表；
3. 检查是否存在重复 Scientific Illustrator 源码；
4. 检查旧 VLM/API；
5. 检查 `.env` / secrets；
6. 检查可复用 deterministic scripts；
7. 定位 `xjb-skill-image-to-vba` 中可抽取能力；
8. 定位旧科研规范 skills 中可进入 Hard Gate 的规则。

未经 inventory 不得直接大规模删除。

### Phase 1 验收

必须输出：

```text
migration_inventory.md
```

且每项有明确：

```text
KEEP / EXTRACT / ARCHIVE / DELETE / REPLACE-BY-DEPENDENCY
```

---

## Phase 2 — Minimal Architecture

先实现：

- Orchestrator；
- Writer；
- Analyst；
- Auditor；
- region artifact；
- simple NATIVE-only reconstruction。

先证明最小闭环：

```text
reference
→ native PPT
→ render
→ audit
→ repair
→ pass
```

### Phase 2 验收

简单流程图 fixture 必须通过：

- 文字；
- 形状；
- 箭头；
- connector；
- alignment；
- save/readback；
- fresh audit。

---

## Phase 3 — Raster Pipeline

加入：

- raster candidate；
- coarse ROI；
- refine crop；
- border safety；
- raster promotion；
- placement；
- render-back verification。

### Phase 3 验收

至少通过：

- ISOLATED；
- SEPARABLE；
- ENTANGLED；
- 低清 reference raster；
- crop 边界触碰；
- complex asset 与文字粘连。

---

## Phase 4 — Deterministic QA

加入：

- OOXML/PPT audit；
- geometry；
- overlap；
- connector；
- crop；
- local visual compare；
- region-wise diff；
- P0→P4 gates。

---

## Phase 5 — Context Isolation

把：

- Region Analyst；
- Raster Specialist；
- Fresh Auditor；

真正改成短生命周期 subagent。

验证 Agent 之间只通过 artifacts 交接。

必须确认 Writer 是唯一拥有 mutation 权限的角色。

---

## Phase 6 — Regression

使用旧项目典型失败样本测试。

每个历史 bug 必须对应一个可自动复现的 regression fixture 或最小测试。

---

## Phase 7 — Cleanup

删除：

- duplicated tools；
- obsolete VLM；
- old pipelines；
- unused VBA-first workflow；
- temporary data；
- secret/config residue；
- 重复 Scientific Illustrator 源码。

---

# 32. Definition of Done

> 对应架构图：`ARCH-10`。

只有以下条件全部满足，才能宣布项目重构完成：

1. `AI autofigure` 能独立通过 Codex 启动；
2. 不依赖额外视觉 API；
3. 不依赖 image-generation API；
4. Scientific Illustrator 工具层不重复造轮子；
5. PowerPoint 是默认 backend；
6. 只有一个 Writer 拥有 mutation 权限；
7. Region Analyst 使用短生命周期上下文；
8. Auditor 为独立 read-only context；
9. raster 从 approved PNG 原像素裁剪；
10. crop 有 deterministic refinement 和 border safety；
11. ENTANGLED 情况使用 Raster Promotion；
12. QA 不依赖单一 SSIM；
13. 采用 P0→P4 分层 gate；
14. Reference 与科研美化规则冲突时 reference 优先；
15. final PPTX 保存后重新读回检查；
16. regression corpus 通过；
17. 项目无 API key / `.env` 泄露；
18. 项目目录轻量、无历史大缓存；
19. README 明确写明 Reconstruction-only；
20. 输出 PPTX 可编辑、视觉完整且没有未解决 Hard Finding；
21. `examples/generated/runs/<run_id>/` 可在证据晋升或交付后删除，而不影响稳定 fixture 与最终交付文件；
22. 多 Agent handoff 不依赖长自然语言历史；
23. 发生重复修复震荡时能够进入 STALLED，而不是无限循环；
24. Writer 不得通过自己的主观判断跳过 fresh audit。

---

# 33. 最终技术路线摘要

最终技术路线的完整可视化见配套架构文档：

- `ARCH-02`：端到端执行闭环；
- `ARCH-04`：复杂子图裁剪与 Raster Promotion；
- `ARCH-05`：QA 与修复闭环；
- `ARCH-10`：最终系统总览。

文字化摘要如下：

1. `Approved PNG` 是唯一视觉权威；
2. Orchestrator 控制任务状态但不直接绘图；
3. 短生命周期 Analyst 负责区域级视觉解析；
4. Writer 是唯一拥有 PowerPoint mutation 权限的 Agent；
5. 可可靠表达的对象走 `NATIVE`；
6. 复杂视觉走 `REFERENCE_RASTER`，直接裁取 reference 原像素；
7. Raster Specialist 使用粗定位 + 确定性 CV + Border Safety + Render-back 完成裁剪；
8. Fresh Auditor 使用结构证据、渲染证据与 deterministic QA 独立审核；
9. Writer 仅依据结构化 finding 做最小修复；
10. Region 逐个通过并冻结，最终执行 Whole-Figure Audit；
11. 通过 Definition of Done 后输出 `final.pptx`、`final_preview.png` 与精简 QA summary。

---
# 34. 重构时的最高优先级

> 对应架构图：`ARCH-10`。

若工程 Agent 在实现过程中必须在“增加更多功能”和“保持轻量稳定”之间选择：

**优先保持轻量、可解释、可测试。**

若必须在“100% 编辑性”和“reference 视觉保真”之间选择：

- 可稳定原生重建内容：优先可编辑；
- 不可稳定原生重建的复杂视觉：优先 reference raster 保真。

若“通用科研美学规则”和“approved PNG”发生冲突：

**approved PNG 胜。**

若“LLM 判断”和“确定性结构证据”发生冲突：

**确定性证据优先。**

若“历史上下文”和“fresh render / fresh audit”发生冲突：

**fresh evidence 优先。**

若“增加一个新 Agent”和“通过确定性脚本解决”都可行：

**优先确定性脚本。**

若“增加持久化 metadata”和“运行时临时状态即可解决”都可行：

**优先运行时临时状态。**

---

# 35. 对执行 AI 的最终约束

> 对应架构图：`ARCH-10`。

执行本方案时，禁止把本项目再次重构成：

```text
VLM pipeline
+ image generation
+ VBA pipeline
+ draw.io pipeline
+ PPT pipeline
+ 多套 reviewer
+ 大量 metadata database
+ 大量中间 markdown
```

如果实现开始出现这种趋势，应立即回到本方案的核心：

```text
原生 VLM 理解
+ 多 Agent 上下文隔离
+ Single Writer
+ Scientific Illustrator MCP
+ reference raster crop
+ deterministic QA
```

**本方案的最终目标不是构建功能最多的 AI 绘图平台，而是构建一个足够轻、足够稳、上下文可控、复刻精度高、可持续测试的 `Reference PNG → Editable PPTX` 专用编译与校验系统。**


---

# 附录 A. 配套架构文件一致性要求

本方案必须与 `AI_AutoFigure_最终版系统架构图_V3.md` 同时交付。

**适用项目绝对路径：**

```text
D:\AI+科研\AI智能绘图（最终版）\AI autofigure - 副本\ai-autofigure
```

任何执行 AI 在开始重构前必须：

1. 读取本方案；
2. 读取配套架构图；
3. 校验 `ARCH-01 ~ ARCH-10` 全部存在；
4. 校验角色名、状态名、目录名、Gate 名称与本方案一致；
5. 若发现不一致，先修正文档，不得带着冲突直接实施代码重构。
