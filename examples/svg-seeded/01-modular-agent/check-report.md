# check 报告（strict） — 01-modular-agent

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 14.7877
- changed_pixel_ratio: 37.1845%
- top_roi: {'bbox': {'x': 1000, 'y': 480, 'w': 400, 'h': 80}, 'mean_abs_rgb_delta': 31.3577, 'loss_contribution_pct': 7.1025}
- ssim: 0.7762
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（6 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 1 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 24 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配
- zτt+2

### OCR 侧未匹配
- Task 2: Close top drawer ...
- Task-Aware Modular Joint
- task-conditioned imagination
- Task-Guided Expert Allocator
- Zzt+2
- Şëm
- Sem
- Sěm
- Task 1
- Sém
- Encoder
- Zt
- à
- Dyn
- io
- Task 2
- u
- π
- π
- Encoder
- Task-Aware Behavior Learning
- MLLM-WM Joint Optimization
- dense reward
- action

## 箭头结构审计（arrows，advisory）

- 箭头单元 41（marker 引用 42 处，marker 定义 8 个）；头/线宽比例中位数 5.0（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 0 处 · F2 头/线宽比例失调 0 处 · F3 端点悬空 0 处 · orient 非 auto 0 处 · 手折箭羽 0 组

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（strict）
- blockers: 5
- region:task-guided-allocator-topology
- region:six-bicolor-state-circles
- region:rollout-arrow-topology
- region:observation-arrows
- live-evidence-missing

> strict 使用关键区域、箭头结构与可选 live 回读共同门禁；全图均值不能覆盖局部失败。
