# examples/ — 双输入路线案例索引

案例按不可变的初始输入路线分为两个目录。目录分类回答“建案时用户提供了什么”，不是“当前正在用什么方法修复”。

- `reference-only/`：建案时只有冻结的 `reference.png`，没有外部 SVG 种子。
- `svg-seeded/`：建案历史中存在用户提供的外部 SVG 种子；后续即使回退到 PNG 重建，仍留在此目录。

每个案例根都是唯一、扁平的工作单元：输入、当前候选、交付物和合同均在根目录，机器证据统一在 `qa/`；不得再创建平行版本目录。案例历史由版本控制承担。

<!-- AUTOFIGURE_CASE_INDEX:START -->
| 输入路线 | 案例 | 当前处理模式 | 工作流 | 最近验证 |
|---|---|---|---|---|
| `reference-only` | [`01-modular-agent-reference-only/`](reference-only/01-modular-agent-reference-only/) | `png_reconstruct` | `qa_failed` | `failed` |
| `svg-seeded` | [`01-modular-agent/`](svg-seeded/01-modular-agent/) | `png_reconstruct` | `qa_failed` | `failed` |
| `svg-seeded` | [`02-thinking-diffusion/`](svg-seeded/02-thinking-diffusion/) | `svg_import` | `candidate` | `diagnostic` |
| `svg-seeded` | [`03-llmind/`](svg-seeded/03-llmind/) | `svg_import` | `candidate` | `diagnostic` |
<!-- AUTOFIGURE_CASE_INDEX:END -->

## 受控 ModularAgent A/B

同一冻结参考图的两条真实路线：

- `svg-seeded/01-modular-agent/`：历史上使用过 GPT Web SVG，当前处理模式为 `png_reconstruct`，strict 仍有 5 个 blocker。
- `reference-only/01-modular-agent-reference-only/`：只从自己的 `reference.png` 构建，禁止读取上一个案例的 SVG、PPTX、scene、bindings、assets、裁剪文件和候选坐标。

统一报告：[`route-comparison-modular-agent-route-ab.md`](route-comparison-modular-agent-route-ab.md)，机器指标见同名 JSON。

当前结论必须原样表述为：**reference-only 全链路真实跑通，但严格质量尚未验证成熟**。它生成了可编辑 PPTX、原生公式、对象绑定、PowerPoint 保存重开和实时画布审计证据；但只有 2/6 个关键区通过，不能标记 `approved`。通过的两区恰好是 observation 与 environment globe 的授权紧边界 PNG 微资产，二者 SSIM/Edge IoU 均为 1.0，证明“从本案例参考 PNG 裁剪微资产”的机制有效，而不是证明整图一比一完成。

## 历史案例状态

- `svg-seeded/01-modular-agent/`：`qa_failed`。Task-Guided 路径、六个双色圆、rollout、observation 箭头和 live evidence 仍阻断 strict；全图均值不能覆盖这些失败。
- `svg-seeded/02-thinking-diffusion/`：`candidate`。已重新生成 v3.1 可移植合同和 PowerPoint 保存重开证据；只有 standard 诊断，没有关键区定义，不能追认为 strict approved。
- `svg-seeded/03-llmind/`：`candidate`。已重新转换并注入 6 个原生公式；同样缺少关键区定义，只保留 standard 诊断状态。

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
