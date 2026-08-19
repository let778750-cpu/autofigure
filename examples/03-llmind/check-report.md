# check 报告（advisory，非门禁） — 03-llmind

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 6.7708
- changed_pixel_ratio: 12.8953%
- top_roi: {'bbox': {'x': 120, 'y': 0, 'w': 280, 'h': 40}, 'mean_abs_rgb_delta': 40.0446, 'loss_contribution_pct': 7.4411}
- ssim: 0.8698
- diff 图: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\03-llmind\qa\diff.png
- 对照预览: D:\AI+科研\AI智能绘图（最终版）\AI autofigure\examples\03-llmind\preview.png

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 12 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 11 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配
- CSF
- ∇θ ℒext
- Pixel Budget
- Latent Encoding Layer
- Transformation
- MLP Network
- Perceptual Loss
- Visual Encoder
- Text Encoder
- <q1, q2 ... qn>
- Forward Pass
- Backward Pass

### OCR 侧未匹配
- #eJe
- _atetE  ayr
- Vis der
- et   der
- 中i  det
- rtion
- er   oss
- <bb>
- For ass
- L N rk
- acs

> OCR 对公式/上下标本身不可靠，逐条人工判断，不以本报告自动放行或拦截。
