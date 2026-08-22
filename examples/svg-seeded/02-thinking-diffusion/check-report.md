# check 报告（standard） — 02-thinking-diffusion

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 13.512
- changed_pixel_ratio: 17.947%
- top_roi: {'bbox': {'x': 960, 'y': 120, 'w': 480, 'h': 120}, 'mean_abs_rgb_delta': 23.3686, 'loss_contribution_pct': 8.6872}
- ssim: 0.7205
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（0 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json

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
- [F1] line#arrow-0001 end 端点 (487,118) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F1] line#arrow-0002 end 端点 (487,200) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-0002 end 端点 (487,200) marker=arr-k: endpoint boundary/gap error is 37.21px (limit 6.00px)
- [F1] line#arrow-0003 end 端点 (487,384) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-0003 end 端点 (487,384) marker=arr-k: endpoint boundary/gap error is 14.83px (limit 6.00px)
- [F1] line#arrow-0004 end 端点 (1244,118) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F1] line#arrow-0005 end 端点 (1257,198) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-0005 end 端点 (1257,198) marker=arr-k: endpoint boundary/gap error is 36.00px (limit 6.00px)
- [F1] line#arrow-0006 end 端点 (1257,380) marker=arr-k: arrowhead tip/ref mismatch (+4.00, +0.00) px
- [F3] line#arrow-0006 end 端点 (1257,380) marker=arr-k: endpoint boundary/gap error is 16.83px (limit 6.00px)

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（standard）
- blockers: 11
- regions:no-critical-regions
- arrow:F1:arrow-0001
- arrow:F1:arrow-0002
- arrow:F3:arrow-0002
- arrow:F1:arrow-0003
- arrow:F3:arrow-0003
- arrow:F1:arrow-0004
- arrow:F1:arrow-0005
- arrow:F3:arrow-0005
- arrow:F1:arrow-0006
- arrow:F3:arrow-0006

> standard 结果为诊断；只有 strict 零 blocker 才能进入 approved。
