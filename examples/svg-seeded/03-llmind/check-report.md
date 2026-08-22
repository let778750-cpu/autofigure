# check 报告（standard） — 03-llmind

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 6.1208
- changed_pixel_ratio: 12.462%
- top_roi: {'bbox': {'x': 120, 'y': 0, 'w': 240, 'h': 40}, 'mean_abs_rgb_delta': 39.3324, 'loss_contribution_pct': 6.93}
- ssim: 0.8868
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（0 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 12 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 11 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配
- CSF
- ∇θ ℒtext
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

## 箭头结构审计（arrows，advisory）

- 箭头单元 0（marker 引用 0 处，marker 定义 0 个）；头/线宽比例中位数 None（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 0 处 · F2 头/线宽比例失调 0 处 · F3 端点悬空 0 处 · orient 非 auto 0 处 · 手折箭羽 12 组

### 逐条发现
- [feather] line#0 end 端点 (1159,255): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#5 end 端点 (1202,280): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#10 end 端点 (1202,330): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#15 end 端点 (1159,355): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#20 end 端点 (1116,330): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#25 end 端点 (1116,280): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#30 end 端点 (1159,255): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#35 end 端点 (1202,280): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#40 end 端点 (1202,330): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#45 end 端点 (1159,355): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#50 end 端点 (1116,330): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）
- [feather] line#55 end 端点 (1116,280): 手折箭羽 4 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（standard）
- blockers: 3
- regions:no-critical-regions
- asset:atomic:photo-1:authorization-unverified
- asset:atomic:photo-2:authorization-unverified

> standard 结果为诊断；只有 strict 零 blocker 才能进入 approved。
