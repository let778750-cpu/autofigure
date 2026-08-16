# AI AutoFigure 最终版系统架构图 V3
## 与《AI AutoFigure 最终版重构优化方案 V3》严格一一对应

**适用项目：** `D:\AI+科研\AI智能绘图（最终版）\AI autofigure - 副本\ai-autofigure`  
**配套方案文件：** `AI_AutoFigure_最终版重构优化方案_V3.md`  
**架构编号：** `ARCH-01 ~ ARCH-10`  
**显示策略：** **SVG 预览优先 + Mermaid 源码保留**。即使 Markdown 阅读器不支持 Mermaid，仍应能直接看到 SVG 架构图。  
**核心原则：** Approved PNG = Visual Authority；Multi-Agent Cognition；Single Writer Mutation；Read Parallel / Write Serial；Fresh Evidence > Historical Context。

---

# 0. 架构追踪矩阵

| 编号 | 架构主题 | 严格对应重构方案 |
|---|---|---|
| `ARCH-01` | 多 Agent 总体拓扑与权限边界 | §3 |
| `ARCH-02` | Approved PNG → final.pptx 端到端主流程 | §4、§6、§31 |
| `ARCH-03` | Context Firewall 与 Artifact-first Handoff | §5、§30 |
| `ARCH-04` | NATIVE / REFERENCE_RASTER 与精确裁剪链路 | §7–§12 |
| `ARCH-05` | P0–P4 QA、冲突裁决与最小修复闭环 | §13–§17 |
| `ARCH-06` | 项目依赖与 Read Parallel / Write Serial | §18–§23 |
| `ARCH-07` | 运行状态机、异常与 STALLED 防死循环 | §24–§25 |
| `ARCH-08` | 测试、回归与最终验收 | §26–§27 |
| `ARCH-09` | Agent Handoff 与 Phase 1–7 工程落地 | §30–§31 |
| `ARCH-10` | 最终系统总览与权威优先级 | §32–§35 |

---

# 1. 使用与预览规则

1. 每个 `ARCH-*` 先引用 `architecture/ARCH-XX.svg`，这是**跨 Markdown 阅读器的正式预览**；
2. SVG 下方保留 Mermaid 源码，主要供 AI、版本控制与后续架构修改使用；
3. 当前编辑器支持 Mermaid 时可额外渲染源码；不支持 Mermaid 时也不影响 SVG 预览；
4. `architecture/*.svg` 是架构的渲染产物，不是独立真值；
5. 修改流程逻辑后必须同步更新 Mermaid/架构定义并重新生成 SVG；
6. 本文件与 `AI_AutoFigure_最终版重构优化方案_V3.md` 任何一处发生角色、状态、Gate、目录或执行顺序不一致，均视为文档测试失败。

---

# ARCH-01 — 多 Agent 总体拓扑与权限边界
**严格对应方案：§3**

![ARCH-01 — 多 Agent 总体拓扑与权限边界](architecture/ARCH-01.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TB
    REF[Approved Reference PNG] --> O[Orchestrator]
    O --> A[Analyst / short-lived / read-only]
    O --> R[Raster Specialist / short-lived / read-only]
    O --> W[Writer / only PPT mutation authority]
    O --> AU[Auditor / fresh read-only]
    A --> ART[(examples/generated/runs artifacts)]
    R --> ART
    AU --> ART
    ART --> O
    ART --> W
    W --> SI[Scientific Illustrator / PowerPoint MCP]
    SI --> PPT[(Current PPTX)]
    PPT --> AU
```

</details>

---

# ARCH-02 — Approved PNG → final.pptx 端到端主流程
**严格对应方案：§4、§6、§31**

![ARCH-02 — Approved PNG → final.pptx 端到端主流程](architecture/ARCH-02.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TD
    PNG[Approved PNG] --> INIT[Orchestrator INIT]
    INIT --> GA[Global Analyst]
    GA --> REG[Region Skeleton]
    REG --> PAR[Parallel Region Analysts]
    PAR --> SPEC[Region Specs]
    SPEC --> Q[Region Queue]
    Q --> N[Writer: NATIVE Build]
    N --> NR[Native-only Render]
    NR --> RC{Raster Candidate?}
    RC -- Yes --> RS[Raster Specialist]
    RS --> C[Exact Reference Crop]
    C --> I[Writer inserts REFERENCE_RASTER]
    RC -- No --> AUD[Fresh Auditor]
    I --> AUD
    AUD --> G{Region Gate}
    G -- Repair --> N
    G -- Pass --> F[Freeze Region]
    F --> M{More Regions?}
    M -- Yes --> Q
    M -- No --> WFA[Whole-Figure Audit]
    WFA --> OUT[final.pptx]
```

</details>

---

# ARCH-03 — Context Firewall 与 Artifact-first Handoff
**严格对应方案：§5、§30**

![ARCH-03 — Context Firewall 与 Artifact-first Handoff](architecture/ARCH-03.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart LR
    O[Orchestrator Context] -->|minimal context| A[Fresh Analyst]
    O -->|minimal context| R[Fresh Raster Specialist]
    O -->|fresh review| AU[Fresh Auditor]
    A --> WORK[(examples/generated/runs/)]
    R --> WORK
    AU --> WORK
    WORK --> O
    WORK --> W[Writer Context]
```

</details>

---

# ARCH-04 — NATIVE / REFERENCE_RASTER 与精确裁剪链路
**严格对应方案：§7–§12**

![ARCH-04 — NATIVE / REFERENCE_RASTER 与精确裁剪链路](architecture/ARCH-04.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TD
    E[Reference Element] --> D{Reliable Native PPT?}
    D -- Yes --> N[NATIVE]
    D -- No --> RC[REFERENCE_RASTER Candidate]
    RC --> ROI[Semantic Coarse ROI]
    ROI --> NB[Build surrounding NATIVE]
    NB --> NR[Native-only Render]
    NR --> RES[Residual-Guided Localization]
    RES --> CV[Deterministic Refinement]
    CV --> G{Cropability Gate}
    G -- Isolated/Separable --> T[Tight BBox]
    G -- Entangled --> RP[Raster Promotion]
    RP --> T
    T --> BS[Border Safety]
    BS --> S{Safe?}
    S -- No --> X[Expand BBox]
    X --> BS
    S -- Yes --> RAW[Raw Reference Pixel Crop]
    RAW --> RB[Render-back Verification]
    RB --> INS[Writer Insert]
```

</details>

---

# ARCH-05 — P0–P4 QA、冲突裁决与最小修复闭环
**严格对应方案：§13–§17**

![ARCH-05 — P0–P4 QA、冲突裁决与最小修复闭环](architecture/ARCH-05.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TD
    CUR[Current Region] --> S[Structure Evidence]
    CUR --> R[Fresh Render]
    CUR --> D[Deterministic QA]
    REF[Reference Crop] --> R
    S --> P0[P0 Semantic]
    R --> P0
    D --> P0
    P0 --> P1[P1 Structure/Editability]
    P1 --> P2[P2 Geometry]
    P2 --> P3[P3 Local Renderer]
    P3 --> P4[P4 Global Visual]
    P4 --> PASS[PASS]
    P0 --> CR[Conflict Resolver]
    P1 --> CR
    P2 --> CR
    P3 --> CR
    P4 --> CR
    CR --> REPAIR[Writer Minimal Repair]
    CR --> PRESERVE[REFERENCE_OVERRIDE]
    CR --> AMB[AMBIGUOUS]
```

</details>

---

# ARCH-06 — 项目依赖与 Read Parallel / Write Serial
**严格对应方案：§18–§23**

![ARCH-06 — 项目依赖与 Read Parallel / Write Serial](architecture/ARCH-06.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart LR
    A1[Analyst R01] --> Q[(Artifact Queue)]
    A2[Analyst R02] --> Q
    R1[Raster E11] --> Q
    AU[Read-only Audit] --> Q
    Q --> LOCK{Single Writer Lock}
    LOCK --> W[Writer]
    W --> PPT[(One PPTX)]
    SI[Scientific Illustrator] --> W
```

</details>

---

# ARCH-07 — 运行状态机、异常与 STALLED 防死循环
**严格对应方案：§24–§25**

![ARCH-07 — 运行状态机、异常与 STALLED 防死循环](architecture/ARCH-07.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
stateDiagram-v2
    [*] --> INIT
    INIT --> GLOBAL_ANALYSIS
    GLOBAL_ANALYSIS --> REGION_ANALYSIS
    REGION_ANALYSIS --> REGION_NATIVE_BUILD
    REGION_NATIVE_BUILD --> RASTER_EXTRACTION
    RASTER_EXTRACTION --> REGION_AUDIT
    REGION_NATIVE_BUILD --> REGION_AUDIT: no raster
    REGION_AUDIT --> REGION_REPAIR: repair
    REGION_REPAIR --> REGION_AUDIT
    REGION_AUDIT --> REGION_PASS: pass
    REGION_PASS --> REGION_ANALYSIS: next
    REGION_PASS --> WHOLE_FIGURE_AUDIT: all done
    WHOLE_FIGURE_AUDIT --> GLOBAL_REPAIR: repair
    GLOBAL_REPAIR --> WHOLE_FIGURE_AUDIT
    WHOLE_FIGURE_AUDIT --> FINAL_PASS: pass
    FINAL_PASS --> DELIVER
    REGION_REPAIR --> STALLED: repeated no improvement
```

</details>

---

# ARCH-08 — 测试、回归与最终验收
**严格对应方案：§26–§27**

![ARCH-08 — 测试、回归与最终验收](architecture/ARCH-08.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TD
    CODE[ai-autofigure] --> U[Unit Tests]
    CODE --> I[Integration Tests]
    CODE --> R[Regression Corpus]
    U --> G[Final Acceptance Gates]
    I --> G
    R --> G
    G --> H[Hard Pass]
    G --> GE[Geometry Pass]
    G --> V[Visual Pass]
    H --> D{All Pass?}
    GE --> D
    V --> D
    D -- Yes --> REL[Release]
    D -- No --> FAIL[Do not declare done]
```

</details>

---

# ARCH-09 — Agent Handoff 与 Phase 1–7 工程落地
**严格对应方案：§30–§31**

![ARCH-09 — Agent Handoff 与 Phase 1–7 工程落地](architecture/ARCH-09.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart TD
    P1[Phase 1 Inventory] --> P2[Phase 2 Minimal Native Loop]
    P2 --> P3[Phase 3 Raster Pipeline]
    P3 --> P4[Phase 4 Deterministic QA]
    P4 --> P5[Phase 5 Context Isolation]
    P5 --> P6[Phase 6 Regression]
    P6 --> P7[Phase 7 Cleanup]
    P7 --> DOD[Definition of Done]
```

</details>

---

# ARCH-10 — 最终系统总览与权威优先级
**严格对应方案：§32–§35**

![ARCH-10 — 最终系统总览与权威优先级](architecture/ARCH-10.svg)

<details>
<summary>Mermaid 源码（用于 AI/版本控制；普通阅读器无需渲染）</summary>

```mermaid
flowchart LR
    REF[Approved PNG] --> MA[Isolated Multi-Agent Cognition]
    MA --> AN[Analyst]
    MA --> RS[Raster Specialist]
    AN --> SW[Single Writer]
    RS --> SW
    TOOL[Scientific Illustrator] --> SW
    SW --> PPT[Editable Draft PPTX]
    PPT --> DET[Deterministic Measurement]
    PPT --> FA[Fresh Auditor]
    REF --> FA
    DET --> CR[Conflict Resolver]
    FA --> CR
    CR -->|minimal repair| SW
    CR -->|pass| FINAL[final.pptx]
```

</details>

---

# 12. 权威优先级与执行不变量

```text
Invariant Hard Constraints
        >
Approved Reference Fidelity
        >
Fresh Render / Fresh Structure Evidence
        >
Deterministic Geometry & QA Evidence
        >
Scientific Style Advice（当前 Reconstruction-only 阶段不自动执行）
```

必须始终满足：

- Writer 是唯一 PowerPoint mutation authority；
- Analyst / Raster Specialist / Auditor 均不可写 PPT；
- Auditor 不能依据 Writer 的“成功声明”通过；
- Raster 最终像素必须直接来自 approved reference；
- ENTANGLED raster 使用 Raster Promotion，而不是引入生成式补图；
- SSIM / pixel similarity 不得越级覆盖 P0/P1/P2；
- 通过区域先 Freeze，除非 fresh whole-figure evidence 明确要求解冻；
- 同一 finding 连续两次无改善或震荡时进入 `STALLED`；
- 当前版本禁止 beautification / redesign / restyle / scientific normalization。

---

# 13. 一致性检查清单

```text
[ ] 已读取 AI_AutoFigure_最终版重构优化方案_V3.md
[ ] 已读取 AI_AutoFigure_最终版系统架构图_V3.md
[ ] ARCH-01 ~ ARCH-10 全部存在
[ ] architecture/ARCH-01.svg ~ ARCH-10.svg 全部存在
[ ] 角色名称完全一致
[ ] Single Writer 权限一致
[ ] NATIVE / REFERENCE_RASTER 定义一致
[ ] Cropability Gate 与 Raster Promotion 一致
[ ] P0–P4 QA 一致
[ ] 状态机与异常状态一致
[ ] Phase 1–7 一致
[ ] Definition of Done 一致
[ ] 项目路径一致
```

**唯一合法项目路径：**

```text
D:\AI+科研\AI智能绘图（最终版）\AI autofigure - 副本\ai-autofigure
```

两份 Markdown 与 `architecture/` SVG 目录应作为一个不可拆分的文档规范包使用。
