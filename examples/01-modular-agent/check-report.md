# check 报告（advisory，非门禁） — 01-modular-agent

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 16.6037
- changed_pixel_ratio: 38.2667%
- top_roi: {'bbox': {'x': 1000, 'y': 480, 'w': 400, 'h': 147}, 'mean_abs_rgb_delta': 29.5891, 'loss_contribution_pct': 9.6869}
- ssim: 0.7333
- diff 图: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\01-modular-agent\qa\diff.png
- 对照预览: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\01-modular-agent\preview.png

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

- 箭头单元 41（marker 引用 42 处，marker 定义 8 个）；头/线宽比例中位数 3.78（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 0 处 · F2 头/线宽比例失调 6 处 · F3 端点悬空 6 处 · orient 非 auto 0 处 · 手折箭羽 0 组

### 逐条发现
- [F3] line#0 end 端点 (422,55) marker=arr-gray: 端点距最近形状边缘 12.0px（> 6，应落在形状边缘/间隙）
- [F3] line#1 end 端点 (589,55) marker=arr-gray: 端点距最近形状边缘 8.0px（> 6，应落在形状边缘/间隙）
- [F3] line#2 end 端点 (758,55) marker=arr-gray: 端点距最近形状边缘 11.0px（> 6，应落在形状边缘/间隙）
- [F3] line#4 end 端点 (661,125) marker=arr-blue-sm: 端点距最近形状边缘 8.0px（> 6，应落在形状边缘/间隙）
- [F2] line#25 end 端点 (1033,407) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F2] line#26 end 端点 (1131,407) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F2] line#27 end 端点 (1230,407) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F2] line#34 end 端点 (1032,268) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F2] line#35 end 端点 (1129,268) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F2] line#36 end 端点 (1227,268) marker=arr-gold: 头长 6.8 / 线宽 1.6 = 4.2（合理带 [1.5, 4]，建议头长 ≤6.4）
- [F3] line#40 start 端点 (1018,572) marker=arr-gray-start: 端点距最近形状边缘 17.0px（> 6，应落在形状边缘/间隙）
- [F3] line#40 end 端点 (1173,572) marker=arr-gray: 端点距最近形状边缘 17.0px（> 6，应落在形状边缘/间隙）

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，改后需重跑 convert/math/check。

> OCR 对公式/上下标本身不可靠，逐条人工判断，不以本报告自动放行或拦截。
