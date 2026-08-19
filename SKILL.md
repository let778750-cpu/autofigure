---
name: ai-autofigure
description: "VLM-first, verify-light：把科研图 PNG 经多模态大模型重绘为 SVG，再转换为原生可编辑 PowerPoint；适用于单图复刻、文字逐字可编辑、公式上下标保留、写实区域原子裁剪。"
---

# AI AutoFigure v2

架构原则：**VLM 负责看图重绘（SVG），工具负责确定性地转换与核验**。不做确定性图像感知（OCR 几何/区域分析），不做像素硬门；验收 = 文本可编辑读回 + check 报告人审。

## 四步流程

```text
autofigure prepare <ref.png>   → 建案例目录 + 生成提示词包 prompt.md
（你把 prompt + PNG 喂多模态大模型网页端（GPT / Kimi / Claude 等），取回 SVG 存入案例目录 redraw.svg）
autofigure convert <run_dir>   → SVG → 原生可编辑 PPTX + PowerPoint fresh render
autofigure check  <run_dir>    → 文本比对（advisory）+ figure_lint 诊断 + 对照预览
autofigure math   <run_dir>    →（可选）公式文本升级为原生 Office Math
```

案例目录：`examples/<case>/`（每个案例一个扁平目录，含 run.json 清单、reference.png、prompt.md、redraw.svg、redraw.pptx、render.png、preview.png、check-report.md、qa/）。案例目录即工作单元，重跑覆盖当前最佳，历史由 git 承担。

## 关键合同

- VLM 输出合同见 `references/v2-prompt-contract.md`：viewBox 必须等于原图像素；文字逐字 `<text>/<tspan>`（禁止画成路径）；公式斜体 + `baseline-shift` 上下标；箭头粗细/头部样式/弯折以原图为准，不得套用固定风格；无文字的写实元素（照片/截图/写实图标/纹理装饰）用 `<rect id="atomic:*">` 占位（convert 自动从参考图裁剪嵌入），含文字/公式内容与几何元素禁止占位；`<image>` 容错按 bbox 裁剪替代（覆盖画布 ≥50% 拒绝）。
- convert 映射：rect/circle/line/path → 原生形状或 custGeom 自由曲线（保留三次贝塞尔）；linearGradient → `a:gradFill`；dasharray → OOXML 合法 prstDash；marker → 自由曲线箭头；text → 原生文本框 runs。
- check 三件套全是 advisory：SVG 文本 vs 参考图 OCR 比对（含模糊匹配）、figure_lint 像素诊断（软信号，非门禁）、side-by-side 预览。逐条人审，不自动放行。

## 不可突破红线

- 交付的 PPTX 文字必须 100% 原生文本可编辑读回；禁止整图截图、位图/SVG 冒充文字或公式。
- 无文字写实区域必须走 `atomic:` 裁剪，不得让 VLM 用矢量近似冒充照片。
- 像素指标（mean/SSIM/ROI）只是诊断，不得作为发布硬门；也不得以诊断良好替代人审。
- `D:\paddle ocr` 只读（OCR 仅 check 环节单次调用）；不重装模型、不联网下载；`D:\opencv\env` 保持锁定，v2 代码一律在项目内 `.venv` 运行。
- 每个案例独立目录；重跑覆盖当前最佳，历史由 git 承担。`legacy/` 是 2026-08-18 归档的旧重型管线，除 math 命令复用其公式工具外不维护。

## 交付

可编辑 `.pptx`、fresh render、`check-report.md`（文本差异逐条已人工解释）、对照预览、run ID。
