# examples/ — 双输入路线案例索引

案例按不可变的初始输入路线分为两个目录。目录分类回答“建案时用户提供了什么”，不是“当前正在用什么方法修复”。

- `reference-only/`：建案时只有冻结的 `reference.png`，没有外部 SVG 种子。
- `svg-seeded/`：建案历史中存在用户提供的外部 SVG 种子；后续即使回退到 PNG 重建，仍留在此目录。

每个案例根都是唯一、扁平的工作单元：输入、当前候选、交付物和合同均在根目录，机器证据统一在 `qa/`；不得再创建平行版本目录。案例历史由版本控制承担。

<!-- AUTOFIGURE_CASE_INDEX:START -->
| 输入路线 | 案例 | 当前处理模式 | 工作流 | 最近验证 | 关键区通过 | blocker 去重数 |
|---|---|---|---|---|---|---|
| `reference-only` | [`01-modular-agent-reference-only/`](reference-only/01-modular-agent-reference-only/) | `png_reconstruct` | `qa_failed` | `failed` | 2/10 | 279 |
| `reference-only` | [`02-thinking-diffusion-reference-only/`](reference-only/02-thinking-diffusion-reference-only/) | `png_reconstruct` | `qa_failed` | `failed` | 4/8 | — |
| `reference-only` | [`04-pareto-conditioned-diffusion-reference-only/`](reference-only/04-pareto-conditioned-diffusion-reference-only/) | `png_reconstruct` | `qa_failed` | `failed` | 0/28 | — |
| `reference-only` | [`05-sting-autophagy-reference-only/`](reference-only/05-sting-autophagy-reference-only/) | `png_reconstruct` | `ready` | `not_run` | — | — |
| `svg-seeded` | [`01-modular-agent/`](svg-seeded/01-modular-agent/) | `png_reconstruct` | `qa_failed` | `failed` | 5/12 | — |
| `svg-seeded` | [`02-thinking-diffusion/`](svg-seeded/02-thinking-diffusion/) | `png_reconstruct` | `qa_failed` | `failed` | 18/18 | — |
| `svg-seeded` | [`03-llmind/`](svg-seeded/03-llmind/) | `png_reconstruct` | `qa_failed` | `diagnostic` | 0/0 | — |
| `svg-seeded` | [`04-pareto-conditioned-diffusion/`](svg-seeded/04-pareto-conditioned-diffusion/) | `svg_repair` | `qa_failed` | `failed` | 1/38 | — |
| `svg-seeded` | [`05-sting-autophagy/`](svg-seeded/05-sting-autophagy/) | `svg_repair` | `candidate` | `diagnostic` | 0/1 | 1117 |
<!-- AUTOFIGURE_CASE_INDEX:END -->

## 受控 ModularAgent A/B

同一冻结参考图的两条真实路线：

- `svg-seeded/01-modular-agent/`：历史上使用过 GPT Web SVG，当前处理模式为 `png_reconstruct`；关键区与 blocker 数见上方生成索引，blocker 集中在箭头视觉物理门禁、保存重开绑定和 Live evidence。
- `reference-only/01-modular-agent-reference-only/`：只从自己的 `reference.png` 构建，禁止读取上一个案例的 SVG、PPTX、scene、bindings、assets、裁剪文件和候选坐标；关键区与 blocker 去重数见上方生成索引。

统一报告：[`route-comparison-modular-agent-route-ab.md`](route-comparison-modular-agent-route-ab.md)，机器指标见同名 JSON。

当前结论必须原样表述为：**reference-only 已生成可编辑 PPTX、原生公式和对象绑定，但当前根候选尚未闭合 PowerPoint 保存重开证据，严格质量也未达标**。关键区通过数以生成索引为准，未全通过不能标记 `approved`。通过区中 observation 与 environment globe 为授权紧边界 PNG 微资产，二者 SSIM/Edge IoU 均为 1.0，证明“从本案例参考 PNG 裁剪微资产”的机制有效，而不是证明整图一比一完成。

## 历史案例状态

- `svg-seeded/01-modular-agent/`：`qa_failed`。箭头视觉物理门禁、Task-Guided 路径、六个双色圆、rollout、保存重开绑定和 live evidence 仍阻断 strict；全图均值不能覆盖这些失败。
- `svg-seeded/02-thinking-diffusion/`：`qa_failed`。schema 4.0 下全部关键区与箭头物理门禁已通过，剩余 blocker 集中在保存重开与 Live 证据链未闭合，不能追认为 approved；具体数量见生成索引。
- `svg-seeded/03-llmind/`：`qa_failed`（diagnostic）。已重新转换并注入 6 个原生公式；仍缺少关键区定义，只保留 standard 诊断状态。

> 本文件中所有案例数字均由 `autofigure cases --write-index` 从各案例 `qa/` 机器证据生成；六维 `qa-status.json` 目前仅在部分案例上执行，未执行处显示 `—`。

## 案例文件约定

| 文件 | 说明 |
|---|---|
| `run.json` | v3.1 清单：不可变 `input_route`、可变 `processing_mode`、状态机和最近验证摘要 |
| `provenance.json` | 参考图、外部 SVG 种子、候选生成者、哈希、时间与 A/B 分组 |
| `reference.png` | 冻结视觉基准；权威身份为相对路径 + SHA-256 |
| `redraw.svg` | 当前离线可渲染候选；reference-only 中它是内部载体，不是外部种子 |
| `redraw.pptx` | 当前原生可编辑候选/交付物 |
| `scene.json` | 对象 ID、角色、几何、层级与连接拓扑 |
| `assets.json` | 微资产来源、授权、bbox 与可编辑性 |
| `regions.json` | 关键区域、颜色探针与验收阈值 |
| `bindings.json` | 场景对象到保存重开后的 PowerPoint shape 绑定 |
| `qa/` | 区域、箭头、布局、OCR、像素、公式和 PowerPoint Live 证据 |

## 一致性检查

```bat
autofigure cases --write-index
autofigure cases --check
```

检查覆盖目录/路线一致性、全局 case ID 唯一性、合同完整性、参考哈希、索引状态和过期绝对路径。旧扁平路径只做带警告的兼容解析，不创建副本或符号链接。
