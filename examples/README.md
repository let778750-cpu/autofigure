# examples/ — 案例索引

每个案例一个扁平目录：参考图、提示词包、VLM 取回的 SVG、交付 PPTX、fresh render、对照预览、核验报告都在案例根；机器诊断明细（metrics/diff/OCR 文本/转换摘要）在 `qa/`。重跑覆盖当前最佳，历史由 git 承担。

## 案例

### `01-modular-agent/`

- 论文：*ModularAgent: A Task-Aware Modular Framework for Joint Reasoning*（CVPR 2026）
- 来源文件：`01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png`（1429×627）
- SVG 来源：GPT 网页端直出（2026-08-18）
- 状态：**完成**。255 个原生对象、66 个文本全部可编辑读回；mean_abs_rgb_delta=17.40、SSIM=0.727、changed=38.97%（诊断口径，全面优于 v1 R10 的 19.9987/0.6535/46.55%）

### `02-thinking-diffusion/`

- 论文：*Thinking Diffusion: Penalize and Guide Visual-Grounded Reasoning*（CVPR 2026）
- 来源文件：`02_2026_CVPR_2026_Thinking_Diffusion_Penalize_and_Guide_Visual-Grounde.png`（1513×554）
- SVG 来源：按 `references/v2-prompt-contract.md` 合同手写（2026-08-18，Kimi 充当 VLM 环节验证合同可遵循性）
- 状态：**完成**。162 个原生对象、46 个文本全部可编辑读回、文本比对 SVG 侧 0 未匹配；mean_abs_rgb_delta=13.55、SSIM=0.720、changed=17.97%

### `03-llmind/`

- 论文：*LLMind: Bio-inspired Training-free Adaptive Visual Reasoning*（CVPR 2026）
- 来源文件：`03_2026_CVPR_2026_LLMind_Bio-inspired_Training-free_Adaptive_Visual_Re.png`（1357×656）
- SVG 来源：GPT 网页端直出（2026-08-19，用户自定义提示词；其中 2 处 `<image>` 内嵌照片经确定性正则改写为 `atomic:` 占位符，由 convert 从原图裁剪嵌入）
- 状态：**完成**。201 个原生对象（含 2 处 atomic 照片裁剪）、34 个文本框读回；mean_abs_rgb_delta=6.7743、SSIM=0.8697、changed=12.90%、top_roi=7.44%（三案例最佳）。SVG 侧未匹配 12 条经人审判读主要为 OCR 噪声（竖排文字、低对比面板文字、公式符号）

## 文件约定

| 文件 | 产生者 | 说明 |
|---|---|---|
| `run.json` | prepare | 案例清单：source SHA-256 + 尺寸绑定 |
| `reference.png` | prepare | 参考图拷贝 |
| `prompt.md` | prepare | GPT 网页端提示词包（合同见 `references/v2-prompt-contract.md`） |
| `redraw.svg` | 用户放入 | VLM 重绘输出 |
| `redraw.pptx` | convert | ★ 交付物（原生可编辑） |
| `render.png` | convert | PowerPoint fresh render |
| `preview.png` | check | 参考/渲染对照预览 |
| `check-report.md` | check | 核验报告（人审入口） |
| `qa/` | convert/check | metrics.json、diff.png、ocr-texts.json、convert-summary.json |
