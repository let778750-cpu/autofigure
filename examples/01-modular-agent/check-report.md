# check 报告（advisory，非门禁） — 01-modular-agent

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 16.6261
- changed_pixel_ratio: 38.2783%
- top_roi: {'bbox': {'x': 1000, 'y': 480, 'w': 400, 'h': 147}, 'mean_abs_rgb_delta': 29.7743, 'loss_contribution_pct': 9.6736}
- ssim: 0.7331
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

> OCR 对公式/上下标本身不可靠，逐条人工判断，不以本报告自动放行或拦截。
