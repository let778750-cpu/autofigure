<p align="center"><strong>简体中文</strong> ｜ <a href="README_EN.md">English</a></p>

# AI AutoFigure · autofigure2PPT

把科研图 PNG 重建为原生可编辑 PowerPoint，并以参考哈希、对象绑定、局部指标和 PowerPoint 保存重开证据约束质量。

> 当前事实：项目已真实跑通 `reference-only` 与 `svg-seeded` 两条输入路线，但 ModularAgent 的两条路线都仍是 `qa_failed`。**管线跑通不等于一比一质量成熟，任何关键区失败都不得写成完成。**

## 两条输入路线

v3.1 把过去混在 `source_mode` 里的两个维度拆开：

| 维度 | 取值 | 语义 |
|---|---|---|
| `input_route` | `reference-only` / `svg-seeded` | 建案时的输入来源；创建后不可变 |
| `processing_mode` | `svg_import` / `svg_repair` / `png_reconstruct` | 当前处理方法；失败回退时可变 |

- `reference-only`：用户没有提供外部 SVG 种子。执行者仍可从 PNG 生成内部 SVG，作为离线转换载体；它在 provenance 中是 reconstruction candidate，不是 external seed。
- `svg-seeded`：历史上存在外部 SVG 种子，不绑定 GPT，也可以来自 Kimi、Claude、Codex 或人工。SVG 被拒绝后只改变 `processing_mode`，不会改变目录或 `input_route`。

```mermaid
flowchart LR
    A[冻结 reference.png + SHA-256] --> B{input_route}
    B -->|svg-seeded| C[摄取外部 SVG 种子]
    B -->|reference-only| D[生成区域任务]
    C --> E[svg_import / svg_repair]
    C -->|候选被拒绝| F[png_reconstruct]
    D --> F
    E --> G[原生 PPTX + bindings]
    F --> G
    G --> H[区域/箭头/布局 QA]
    H -->|失败区域| I[PowerPoint Live 混合修复]
    H -->|strict 零 blocker| J[approved]
```

## 案例结构与真实状态

```text
examples/
├─ reference-only/
│  └─ 01-modular-agent-reference-only/
└─ svg-seeded/
   ├─ 01-modular-agent/
   ├─ 02-thinking-diffusion/
   └─ 03-llmind/
```

- [`svg-seeded/01-modular-agent`](examples/svg-seeded/01-modular-agent/)：`png_reconstruct + qa_failed`，保留 5 个 strict blocker。
- [`reference-only/01-modular-agent-reference-only`](examples/reference-only/01-modular-agent-reference-only/)：只从 PNG 独立构建，188 个绑定对象、45 个可编辑文本、22 个原生公式；2/6 关键区通过，仍为 `qa_failed`。
- [`svg-seeded/02-thinking-diffusion`](examples/svg-seeded/02-thinking-diffusion/) 与 [`03-llmind`](examples/svg-seeded/03-llmind/)：只有 standard 诊断，均为 `candidate`；缺少关键区定义，不能追认为 strict approved。

受控 A/B 报告：[`route-comparison-modular-agent-route-ab.md`](examples/route-comparison-modular-agent-route-ab.md)。

![reference-only ModularAgent 对照](examples/reference-only/01-modular-agent-reference-only/preview.png)

## 能做什么，不能做什么

- 文字、公式、规则节点和箭头转为原生可编辑对象；公式可升级为 Office Math。
- 普通直线箭头优先使用 PowerPoint connector；曲线保留 freeform 路径；无法原生表达的 marker 必须显式回退并分组。
- 经用户授权且不可约的 observation、照片、小地球等微资产可从**本案例自己的** `reference.png` 紧边界裁剪，标记 `editable=false`。禁止整图贴图或用位图冒充文字、公式、节点和拓扑。
- PowerPoint Live 可以打开可见托管画布、检查对象、审计、保存重开和回读。它不会自动证明视觉区域通过，也没有自行放行权限。
- PNG-only 入口已经通过真实科研图全链路，但当前结果表明视觉重建质量仍依赖视觉执行者与迭代修复，不能宣传成“无模型自动一比一”。

## 快速开始

要求：Windows、Microsoft PowerPoint、Python 3.12。第三方 PowerPoint 插件不是核心依赖。

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements-v2.txt
```

### 路线 A：用户提供外部 SVG 种子

```bat
autofigure prepare reference.png --case my-case --input-route svg-seeded
autofigure ingest my-case candidate.svg --kind svg --candidate-role external-seed --candidate-origin web-vlm
autofigure convert my-case
autofigure math my-case
autofigure check my-case --profile standard
```

如果 SVG 被拒绝：

```bat
autofigure ingest my-case --rejected --fallback png_reconstruct
```

案例仍位于 `examples/svg-seeded/my-case/`，仅 `processing_mode` 改为 `png_reconstruct`。

### 路线 B：只有 PNG

```bat
autofigure prepare reference.png --case my-direct-case --input-route reference-only
```

这会生成 `qa/region-tasks.json`。由 Codex、其他 VLM 或人工仅依据该案例的 `reference.png` 生成候选，再摄取：

```bat
autofigure ingest my-direct-case candidate.svg --kind svg --candidate-role reconstruction-candidate --candidate-origin codex
autofigure convert my-direct-case
autofigure math my-direct-case
autofigure check my-direct-case --profile strict --require-live
```

未提供 `--input-route` 会直接报错。旧 `--source-mode` 只保留一版弃用兼容，不能代替路线，也不得据此推断历史来源。

## 严格质量门禁

- 状态：`prepared → candidate → qa_failed/repairing → approved`。
- `approved` 只能由 strict 零 blocker 写入。
- 默认关键区：SSIM ≥ 0.85、Edge IoU ≥ 0.75；授权位图微资产 SSIM ≥ 0.95；颜色探针使用 ΔE00。
- 全图 mean/SSIM/changed 只作报告，不得覆盖任一局部失败。
- strict 没有关键区时添加 `regions:no-critical-regions`，禁止“零关键区自动通过”。
- hybrid 任务缺少真实 live 区域证据时保留 `live-evidence-missing`。

```bat
autofigure repair my-case
autofigure check my-case --profile strict --require-live
```

## 合同与可移植性

每个案例有 `run.json`、`provenance.json`、`scene.json`、`assets.json`、`regions.json`、`bindings.json`。正式身份使用案例内相对路径和 SHA-256，不以某台电脑的 `source_abspath` 为权威。

```bat
autofigure cases --write-index
autofigure cases --check
autofigure compare 01-modular-agent 01-modular-agent-reference-only
```

## PowerPoint 与第三方插件

默认只要求 Microsoft PowerPoint、`powerpoint-live` MCP 和 Autofigure 自身转换/QA。OneKeyTools10 仅保留为隔离环境试点候选；iSlide 是可选人工素材源；ThreeD Tools 只在明确三维案例后评估；其余美化、动画类插件不进入自动化主流程。禁止使用 Ribbon 坐标点击、SendKeys 或图像识别点击作为生产 MCP。

## 开发验证

```bat
.venv\Scripts\python -m pytest tests\v2 -q
.venv\Scripts\python -m ruff check tools\v2 tests\v2
.venv\Scripts\python -m compileall -q tools\v2 tests\v2
autofigure cases --check
```

更多细节见 [`PROJECT_ARCHITECTURE.md`](PROJECT_ARCHITECTURE.md)、[`HIGH_FIDELITY_V3.md`](HIGH_FIDELITY_V3.md)、[`SKILL.md`](SKILL.md) 和 [`examples/README.md`](examples/README.md)。

## License

[MIT](LICENSE) © 2026 let778750-cpu
