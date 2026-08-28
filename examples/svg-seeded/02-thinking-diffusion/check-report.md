# check 报告（strict） — 02-thinking-diffusion

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 11.0236
- changed_pixel_ratio: 16.7185%
- top_roi: {'bbox': {'x': 960, 'y': 120, 'w': 480, 'h': 120}, 'mean_abs_rgb_delta': 23.1025, 'loss_contribution_pct': 10.1442}
- ssim: 0.7708
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: True（18 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json
- 箭头视觉物理门禁: PASS（6 个合同）
- ArrowSpec 编译: PASS（6 个逻辑箭头）
- PowerPoint 箭头读回: PASS
- 语义图元: PASS（14 个）
- AssetSpec 资产合同: PASS（0 个逻辑资产，0 个成员读回）
- 冻结资产输入 receipt: FAIL（1 项）
- 字体/图标尺度/重叠合同: PASS（0 个冻结对象）
- PowerPoint Live 箭头创作: DISABLED / inspect-only
- 结构证据: qa/arrow-visual-report.json、qa/arrow-compile-report.json、qa/powerpoint-arrow-readback.json、qa/primitive-audit.json、qa/asset-spec-audit.json、qa/asset-contract-receipt.json、qa/visual-contracts-report.json、qa/provider-capabilities.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 0 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 22 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配

### OCR 侧未匹配
- Image
- Prompt
- Response
- Diffusion Multimodal Language Model
- arl Step
- Generate answer
- before answering
- ater Ste
- Output Token
- stove
- is
- B
- The
- stove
- is
- old
- answer
- answer
- Generate rationale
- Generate answer
- after answering
- (a) Baseline dMLLM Inference.

## 箭头结构审计（arrows，advisory）

- 箭头单元 6（marker 引用 6 处，marker 定义 1 个）；头/线宽比例中位数 4.0（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 6 处 · F2 头/线宽比例失调 0 处 · F3 端点悬空 4 处 · orient 非 auto 0 处 · 手折箭羽 0 组

### 逐条发现
- [F1] line#arrow-left-input-to-model end 端点 (487,116) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F1] line#arrow-left-model-to-early-step end 端点 (487,236) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F1] line#arrow-left-to-later-step end 端点 (487,383) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-left-to-later-step end 端点 (487,383) marker=arr-k: endpoint boundary/gap error is 16.76px (limit 6.00px)
- [F1] line#arrow-right-input-to-model end 端点 (1260,112) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-right-input-to-model end 端点 (1260,112) marker=arr-k: endpoint boundary/gap error is 10.00px (limit 6.00px)
- [F1] line#arrow-right-model-to-early-step end 端点 (1260,233) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-right-model-to-early-step end 端点 (1260,233) marker=arr-k: endpoint boundary/gap error is 6.05px (limit 6.00px)
- [F1] line#arrow-right-to-later-step end 端点 (1259,378) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-right-to-later-step end 端点 (1259,378) marker=arr-k: endpoint boundary/gap error is 17.25px (limit 6.00px)

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（strict）
- blockers: 19
- repair plan coverage: PASS
- blocker inventory: qa/blockers.json
- repair plan: qa/repair-plan.json
- QA lineage: qa/qa-lineage-manifest.json
- PowerPoint Live: REQUIRED — FAIL
- asset-contract:inventory-missing
- ocr:reference-text-unmatched
- bindings:save-reopen-not-verified
- reference-inventory:receipt-missing
- source-gate:seed:unavailable
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
- live-evidence-layout-audit-mismatch
- live-evidence-regions-mismatch
- live-evidence-math-summary-mismatch
- live-evidence-inventory-candidate-mismatch

> strict 使用关键区域、箭头/图元结构与所声明的 Live 回读共同门禁；全图均值不能覆盖局部失败。
