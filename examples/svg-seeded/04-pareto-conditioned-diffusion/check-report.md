# check 报告（strict） — 04-pareto-conditioned-diffusion

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 9.5003
- changed_pixel_ratio: 18.8563%
- top_roi: {'bbox': {'x': 120, 'y': 40, 'w': 360, 'h': 160}, 'mean_abs_rgb_delta': 28.3573, 'loss_contribution_pct': 19.8869}
- ssim: 0.8342
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（38 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json
- 箭头视觉物理门禁: PASS（5 个合同）
- ArrowSpec 编译: PASS（5 个逻辑箭头）
- PowerPoint 箭头读回: PASS
- 语义图元: PASS（0 个）
- AssetSpec 资产合同: PASS（8 个逻辑资产，94 个成员读回）
- 冻结资产输入 receipt: PASS（0 项）
- 字体/图标尺度/重叠合同: FAIL（38 个冻结对象）
- PowerPoint Live 箭头创作: DISABLED / inspect-only
- 结构证据: qa/arrow-visual-report.json、qa/arrow-compile-report.json、qa/powerpoint-arrow-readback.json、qa/primitive-audit.json、qa/asset-spec-audit.json、qa/asset-contract-receipt.json、qa/visual-contracts-report.json、qa/provider-capabilities.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 0 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 6 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配

### OCR 侧未匹配
- 0
- Pareto-Conditioned
- 寸
- Train
- Pareto-Conditioned
- Diffusion Model

## 箭头结构审计（arrows，advisory）

- 箭头单元 0（marker 引用 0 处，marker 定义 0 个）；头/线宽比例中位数 None（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 0 处 · F2 头/线宽比例失调 0 处 · F3 端点悬空 0 处 · orient 非 auto 0 处 · 手折箭羽 7 组

### 逐条发现
- [feather] line#19 end 端点 (42,43): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#20 start 端点 (56,18): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#21 end 端点 (67,35): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#22 end 端点 (29,43): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#43 end 端点 (42,43): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#44 start 端点 (56,18): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#45 end 端点 (67,35): 手折箭羽 2 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（strict）
- blockers: 54
- repair plan coverage: PASS
- blocker inventory: qa/blockers.json
- repair plan: qa/repair-plan.json
- QA lineage: qa/qa-lineage-manifest.json
- PowerPoint Live: REQUIRED — FAIL
- region:region-offline-title
- region:region-sampling-title
- region:region-static-dataset-label
- region:region-reweighted-label
- region:region-train-label
- region:region-left-model-title
- region:region-left-model-subtitle
- region:region-target-label
- region:region-objectives-label
- region:region-condition-label
- region:region-right-model-title
- region:region-right-model-subtitle
- region:region-noise-label
- region:region-sample-label
- region:region-designs-label
- region:region-dataset-formula
- region:region-reweighted-formula
- region:region-blue-arrow-1
- region:region-blue-arrow-2
- region:region-condition-arrow
- region:region-noise-arrow
- region:region-sample-arrow
- region:region-molecule-left
- region:region-dna-left
- region:region-balance
- region:region-molecule-right
- region:region-dna-right
- region:region-candlestick-left
- region:region-noise-curve
- region:region-candlestick-right
- region:region-left-panel
- region:region-right-panel
- region:region-dataset-cylinder
- region:region-reweighted-cylinder
- region:region-left-model-box
- region:region-target-box
- region:region-right-model-box
- visual-contract:V5:molecule-left
- visual-contract:V7:molecule-left
- visual-contract:V5:dna-left
- visual-contract:V7:dna-left
- visual-contract:V5:molecule-right
- visual-contract:V7:molecule-right
- visual-contract:V5:dna-right
- visual-contract:V7:dna-right
- visual-contract:V5:candlestick-left-top
- visual-contract:V7:candlestick-left-top
- visual-contract:V5:candlestick-right
- visual-contract:V7:candlestick-right
- visual-contract:V7:dataset-cylinder
- visual-contract:V7:reweighted-cylinder
- ocr:reference-text-unmatched
- bindings:save-reopen-not-verified
- live-evidence-missing

> strict 使用关键区域、箭头/图元结构与所声明的 Live 回读共同门禁；全图均值不能覆盖局部失败。
