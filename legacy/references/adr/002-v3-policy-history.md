# ADR-002 · V3 原则的保留、修正与退役

- 状态：Accepted
- 日期：2026-08-17
- 替代：根目录 `OPTIMIZATION_DETAILS.md` 和 `优化方案参考/` 的重复正文

## 保留的决定

- 用户确认 PNG 是 `RECONSTRUCT_1TO1` 视觉权威；不擅自美化、改布局或改连线。
- 多通道分析、单 Writer 修改，fresh evidence 高于历史上下文。
- 表示先分为 `NATIVE / REFERENCE_RASTER`，复杂视觉的 cropability 和 atomicity 必须审计。
- 查看、冻结、preflight、绘制、读回、fresh render、独立审查的门禁顺序。
- 每个 finding 最多两次最小修正，无改善进入 `STALLED`。

## 已修正的偏离

- 后续规范曾全面禁止目标裁片，这与 V3 的 `REFERENCE_RASTER/cropability` 冲突，并促使 Drawer 用低质量原生近似代替复杂箭头和图标。Figure Spec v4 恢复受控 `reference_atomic_asset`，但禁止复合截图。
- `micro_asset` 同时表示容器、轨迹、图标和位图，职责过载；v4 拆为 `group/native_shape/reference_atomic_asset/manual_asset_slot`。
- “每个对象先原生，失败再裁片”没有可测失败定义；现改为分类先行，边界案例最多试绘/修正一次。
- Phase-1 geometry 不再“整体永不晋升”；原始证据仍只观察，可校准类通过独立 sidecar 晋升。
- 原生公式全量反事实审计从默认改为 `standard/strict` 两档，不削弱结构、可见性和两次 fresh render。

## 单一权威位置

- 工作流与 Figure Spec：`references/01-workflow-contract.md`
- QA：`references/02-qa-gates.md`
- 素材政策：`references/06-asset-policy.md`
- 运行策略：`policy-profiles.json`
- 入口与红线：`SKILL.md`

旧正文的特定路径、临时基线数字、过时不可修改清单和与当前 contract 冲突的段落不再是执行依据；如需追溯，使用 Git 历史。
