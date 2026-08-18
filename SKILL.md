---
name: ai-autofigure
description: "将用户确认的科研图 PNG 高保真重构为原生可编辑 PowerPoint；适用于单图复刻、文字/公式/拓扑核验、来源绑定原子素材、区域审查与可恢复交付。"
---

# AI AutoFigure

以当前参考的结构、语义和视觉保真为目标，不把“全原生”当作高于保真的目的。默认后端为 PowerPoint；每次执行必须建立 `examples/generated/runs/<run_id>/`，所有证据绑定当前 source、schema、脚本和产物 SHA。

## 必读与按需路由

开始前完整读取：

- `references/01-workflow-contract.md`
- `references/02-qa-gates.md`
- `references/06-asset-policy.md`
- `references/08-anti-hallucination.md`
- `references/09-backend.md`
- `references/11-agent-vision-protocol.md`

投稿规范化时再读 `publication-profiles.yaml`、`references/03-style-principles.md` 与 `references/04-publication-journal-standards.md`；多来源取舍时读 `references/10-source-synthesis.md`。

## 单一状态顺序

```text
REFERENCE_FROZEN → PERCEPTION_COMPLETE → REVIEWED
→ SPEC_DRAFT → SPEC_FROZEN → PREFLIGHT_PASS
→ RENDERED → MECHANICAL_PASS → INDEPENDENT_REVIEW_PASS
→ RELEASE_CANDIDATE → APPROVED
```

`run-state.json` 是唯一当前状态，`run-events.jsonl` 只追加历史。生成器不得写 `APPROVED`；同一 finding 两次修正无改善则写 `STALLED`。

## 核心执行规则

1. Codex 自行调用 `autofigure.cmd -InputPath <png> -Device auto -PolicyProfile standard`；不得要求用户手动激活环境或绕过 canonical runner。`strict` 只用于高风险投稿或回归。
2. 本地 PaddleOCR、Agent 视觉和 CV 都是候选证据。公式、数字、单位、标题、轴/图例/连接器标签、冲突与低置信项必须由用户或原文授权。普通文字只有通过版本化 fixture 校准、跨通道一致与 source-SHA 固定抽样，才可标记 `consensus_auto`，绝不写成 `user_confirmed`。
3. Phase-1 `geometry_refinement` 原始 manifest 永远是 `observation_only`；其中对齐观测是 ink-bottom alignment，不是字体 baseline。只有 gold fixture 每类不少于 30 个实例且 median≤1 px、P95≤2 px、高风险误晋升为 0 的类别，才能通过独立 promotion receipt 进入草案 spec；公式、纵排、多行、污染区与箭头拓扑不得晋升。
4. 冻结 Figure Spec v4 前先分类：`native_required`、`native_preferred`、`reference_atomic_asset`、`manual_asset_slot`。边界案例最多试绘/修正一次；不允许无限原生近似。
5. 文字、公式、数据标签、坐标轴、图例文字与普通结构箭头必须原生。含 `via` 的路径必须保留为可编辑线段链，只在首末段放对应箭头。
6. 来源绑定原子素材仅允许单一视觉对象，且不得含可重建文字、公式、轴/图例、面板边框、定量证据或未拆分语义。允许平移、等比显示与 PowerPoint 旋转；禁止拉伸、生成式补绘和内容修补。复杂视觉箭头仍须在 `edges[]` 保留端点、方向与语义。
7. `manual_asset_slot` 和复合 `reference_preview` 都是未完成状态，对象名必须带 `_REPLACE_ME`，画面显示 `REPLACE ME` 并降低发布上限；`reference_atomic_asset` 是声明清楚的最终来源位图，不显示待替换标签，也不降低批准上限。
8. Drawer 只消费 PASS preflight 与冻结 spec，不得通过对象 ID 触发案例专用绘制。每个区域执行 Designer→Drawer→Reviewer→Corrector，所有 PowerPoint/draw.io MCP save/export 使用当前 run 的绝对路径。
9. 原生公式必须是 Office Math，并经过转换、注入、关闭重开、MathZone/语义读回、可见性与整 deck 两次 fresh render。`standard` 对干净公式不长期保留逐公式隐藏图；`strict` 保留全部反事实控制图。
10. 最终审查以对象结构、拓扑、fresh render 与区域 finding 为准；全图相似度只作诊断，不能掩盖任何 major finding。

## 不可突破红线

- 禁止整图或面板截图冒充重构、位图/SVG/普通 textbox 公式、静默丢弃 `via`、未声明位图、生成式重绘、远程 OCR、编造科研事实或案例专用共享渲染逻辑。
- 不复用 SHA 不一致的旧感知、spec、preflight、render 或审计证据；旧 run 只读，迁移写入新 run。
- 自动化最高只能到 `RELEASE_CANDIDATE`；发布判断必须由独立 Reviewer，最终 `APPROVED` 只能由用户事件产生。

## 交付

交付可编辑 `.pptx`、fresh 预览、run ID、来源/规格/脚本哈希、状态、原生公式数、对象/连接器读回、来源绑定位图清单、槽位/preview 清单、区域审查结果与剩余不确定性。
