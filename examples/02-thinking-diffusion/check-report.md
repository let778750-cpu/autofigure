# check 报告（advisory，非门禁） — 02-thinking-diffusion

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 13.5469
- changed_pixel_ratio: 17.9703%
- top_roi: {'bbox': {'x': 960, 'y': 120, 'w': 480, 'h': 120}, 'mean_abs_rgb_delta': 23.3987, 'loss_contribution_pct': 8.6766}
- ssim: 0.72
- diff 图: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\02-thinking-diffusion\qa\diff.png
- 对照预览: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\02-thinking-diffusion\preview.png

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

> OCR 对公式/上下标本身不可靠，逐条人工判断，不以本报告自动放行或拦截。
