# check 报告（strict） — 04-pareto-conditioned-diffusion-reference-only

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 8.1263
- changed_pixel_ratio: 14.5445%
- top_roi: {'bbox': {'x': 120, 'y': 40, 'w': 360, 'h': 160}, 'mean_abs_rgb_delta': 24.1609, 'loss_contribution_pct': 18.2496}
- ssim: 0.853
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（28 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json
- 箭头视觉物理门禁: FAIL（5 个合同）
- ArrowSpec 编译: PASS（9 个逻辑箭头）
- PowerPoint 箭头读回: PASS
- 语义图元: PASS（0 个）
- AssetSpec 资产合同: PASS（10 个逻辑资产，152 个成员读回）
- 冻结资产输入 receipt: PASS（0 项）
- 字体/图标尺度/重叠合同: FAIL（38 个冻结对象）
- PowerPoint Live 箭头创作: DISABLED / inspect-only
- 结构证据: qa/arrow-visual-report.json、qa/arrow-compile-report.json、qa/powerpoint-arrow-readback.json、qa/primitive-audit.json、qa/asset-spec-audit.json、qa/asset-contract-receipt.json、qa/visual-contracts-report.json、qa/provider-capabilities.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 0 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 8 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配

### OCR 侧未匹配
- 0
- Pareto-Conditioned
- 寸
- Objectives
- Diffusion Model
- Train
- Pareto-Conditioned
- Diffusion Model

## 箭头结构审计（arrows，advisory）

- 箭头单元 4（marker 引用 4 处，marker 定义 1 个）；头/线宽比例中位数 10.0（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 4 处 · F2 头/线宽比例失调 4 处 · F3 端点悬空 4 处 · orient 非 auto 0 处 · 手折箭羽 0 组

### 逐条发现
- [F1] line#chart-offline-axis-y end 端点 (355,121) marker=axis-arrow: arrowhead tip/ref mismatch (+1.80, +0.00) px
- [F2] line#chart-offline-axis-y end 端点 (355,121) marker=axis-arrow: head/stroke ratio 10.00 is outside [1.5, 4]
- [F3] line#chart-offline-axis-y end 端点 (355,121) marker=axis-arrow: endpoint boundary/gap error is 17.46px (limit 6.00px)
- [F1] line#chart-offline-axis-x end 端点 (427,176) marker=axis-arrow: arrowhead tip/ref mismatch (+1.80, +0.00) px
- [F2] line#chart-offline-axis-x end 端点 (427,176) marker=axis-arrow: head/stroke ratio 10.00 is outside [1.5, 4]
- [F3] line#chart-offline-axis-x end 端点 (427,176) marker=axis-arrow: endpoint boundary/gap error is 31.32px (limit 6.00px)
- [F1] line#chart-target-axis-y end 端点 (653,381) marker=axis-arrow: arrowhead tip/ref mismatch (+1.80, +0.00) px
- [F2] line#chart-target-axis-y end 端点 (653,381) marker=axis-arrow: head/stroke ratio 10.00 is outside [1.5, 4]
- [F3] line#chart-target-axis-y end 端点 (653,381) marker=axis-arrow: endpoint boundary/gap error is 11.00px (limit 6.00px)
- [F1] line#chart-target-axis-x end 端点 (725,436) marker=axis-arrow: arrowhead tip/ref mismatch (+1.80, +0.00) px
- [F2] line#chart-target-axis-x end 端点 (725,436) marker=axis-arrow: head/stroke ratio 10.00 is outside [1.5, 4]
- [F3] line#chart-target-axis-x end 端点 (725,436) marker=axis-arrow: endpoint boundary/gap error is 31.32px (limit 6.00px)
- [F9] line#chart-offline-axis-y path 端点 (355,176): arrow path crosses 'chart-offline-axis-x'
- [F9] line#chart-target-axis-y path 端点 (653,436): arrow path crosses 'chart-target-axis-x'

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（strict）
- blockers: 136
- repair plan coverage: PASS
- blocker inventory: qa/blockers.json
- repair plan: qa/repair-plan.json
- QA lineage: qa/qa-lineage-manifest.json
- PowerPoint Live: REQUIRED — FAIL
- region:whole-canvas
- region:left-panel-frame
- region:offline-heading
- region:dataset-assets
- region:reweight-stage
- region:train-flow
- region:left-model
- region:right-panel-frame
- region:sampling-heading-distribution
- region:noise-label
- region:target-condition
- region:right-model
- region:sample-output
- region:ink-molecule-offline
- region:ink-molecule-output
- region:ink-dna-offline
- region:ink-dna-output
- region:ink-scale-reweight
- region:ink-model-left-watermark
- region:ink-model-right-watermark
- region:ink-chart-offline
- region:ink-chart-target
- region:ink-gaussian-noise
- region:arrow-region-raw-to-reweighted
- region:arrow-region-reweighted-to-model
- region:arrow-region-noise-to-model
- region:arrow-region-target-to-model
- region:arrow-region-model-to-designs
- region-contract:dataset-assets:exhaustive-relation-missing:chart-offline-axis-x
- region-contract:dataset-assets:exhaustive-relation-missing:chart-offline-axis-y
- region-contract:target-condition:exhaustive-relation-missing:chart-target-axis-x
- region-contract:target-condition:exhaustive-relation-missing:chart-target-axis-y
- region-contract:ink-chart-offline:exhaustive-relation-missing:chart-offline-axis-x
- region-contract:ink-chart-offline:exhaustive-relation-missing:chart-offline-axis-y
- region-contract:ink-chart-target:exhaustive-relation-missing:chart-target-axis-x
- region-contract:ink-chart-target:exhaustive-relation-missing:chart-target-axis-y
- arrow-visual:arrow-raw-to-reweighted:silhouette-bbox
- visual-contract:V5:panel-sampling
- visual-contract:V7:panel-sampling
- visual-contract:V5:dataset-raw
- visual-contract:V7:dataset-raw
- visual-contract:V5:dataset-reweighted
- visual-contract:V7:dataset-reweighted
- visual-contract:V5:model-left-box
- visual-contract:V7:model-left-box
- visual-contract:V5:model-right-box
- visual-contract:V7:model-right-box
- visual-contract:V31:designs-box
- visual-contract:V32:designs-box
- visual-contract:V34:designs-box
- visual-contract:V5:molecule-offline
- visual-contract:V7:molecule-offline
- visual-contract:V5:molecule-output
- visual-contract:V7:molecule-output
- visual-contract:V5:dna-offline
- visual-contract:V7:dna-offline
- visual-contract:V34:dna-offline
- visual-contract:V5:dna-output
- visual-contract:V7:dna-output
- visual-contract:V34:dna-output
- visual-contract:V5:scale-reweight
- visual-contract:V7:scale-reweight
- visual-contract:V34:scale-reweight
- visual-contract:V5:model-left-watermark
- visual-contract:V7:model-left-watermark
- visual-contract:V5:model-right-watermark
- visual-contract:V7:model-right-watermark
- visual-contract:V5:chart-offline
- visual-contract:V7:chart-offline
- visual-contract:V34:chart-offline
- visual-contract:V5:chart-target
- visual-contract:V7:chart-target
- visual-contract:V34:chart-target
- visual-contract:V34:gaussian-noise
- visual-contract:V4:text-offline-title
- visual-contract:V4:text-offline-subtitle
- visual-contract:V4:text-dataset-raw-symbol
- visual-contract:V4:text-reweighted
- visual-contract:V4:text-dataset-reweighted-symbol
- visual-contract:V4:text-train
- visual-contract:V4:text-model-left
- visual-contract:V4:text-sampling-title
- visual-contract:V4:text-noise
- visual-contract:V4:text-target-objectives
- visual-contract:V4:text-condition
- visual-contract:V4:text-model-right
- visual-contract:V4:text-sample
- visual-contract:V4:text-designs
- visual-contract:V20:arrow-to-train-label
- visual-contract:V20:noise-arrow-to-label
- visual-contract:V20:condition-label-to-arrow
- visual-contract:V20:sample-arrow-to-label
- visual-contract:V20:designs-to-dna
- ocr:reference-text-unmatched
- bindings:save-reopen-not-verified
- live-render-finalizer-unverified
- live-root-save-reopen-missing
- live-candidate-hash-mismatch
- live-reopened-hash-mismatch
- live-binding-evidence-hash-mismatch
- live-evidence-bindings-mismatch
- live-evidence-scene-mismatch
- live-evidence-arrow-readback-mismatch
- live-evidence-arrow-compile-mismatch
- live-evidence-primitive-audit-mismatch
- live-evidence-regions-mismatch
- live-evidence-math-summary-mismatch
- live-evidence-inventory-candidate-mismatch
- live-region:whole-canvas
- live-region:left-panel-frame
- live-region:offline-heading
- live-region:dataset-assets
- live-region:reweight-stage
- live-region:train-flow
- live-region:left-model
- live-region:right-panel-frame
- live-region:sampling-heading-distribution
- live-region:noise-label
- live-region:target-condition
- live-region:right-model
- live-region:sample-output
- live-region:ink-molecule-offline
- live-region:ink-molecule-output
- live-region:ink-dna-offline
- live-region:ink-dna-output
- live-region:ink-scale-reweight
- live-region:ink-model-left-watermark
- live-region:ink-model-right-watermark
- live-region:ink-chart-offline
- live-region:ink-chart-target
- live-region:ink-gaussian-noise
- live-region:arrow-region-raw-to-reweighted
- live-region:arrow-region-reweighted-to-model
- live-region:arrow-region-noise-to-model
- live-region:arrow-region-target-to-model
- live-region:arrow-region-model-to-designs

> strict 使用关键区域、箭头/图元结构与所声明的 Live 回读共同门禁；全图均值不能覆盖局部失败。
