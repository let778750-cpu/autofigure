# legacy/ — 旧重型管线归档（2026-08-18）

本目录是 AI AutoFigure v1（perception-first 重型管线）的完整归档，于 v2 转型时整体移入。

## 为什么归档

2026-08-18 的对比实验（同一参考图、同一把 figure_lint 尺子）：

| 指标 | v1 R10（4 天 / ~30 轮 / 27k LOC） | GPT 直出 SVG（分钟级） | v2 转换后 PPTX |
|---|---:|---:|---:|
| mean_abs_rgb_delta | 19.9987 | 16.4183 | 17.3963 |
| SSIM | 0.6535 | 0.7578 | — |
| changed_pixel_ratio | 46.55% | 38.92% | 38.97% |

VLM 一次性完成了 v1 约 60% machinery（双解释器 OCR、几何精炼、agent-vision、跨模态融合、source-authority）承担的"从 PNG 提取结构/几何/内容"工作，且质量更高；治理半边的信噪比不足（详见当次会话分析）。项目遂转型为 VLM-first, verify-light。

## 内容

- `tools/`：旧 38 个工具（感知、preflight、spec、渲染、状态机等）
- `schemas/`、`references/`：旧 19 个 schema 与 14 份治理文档（含 adr/）
- `tests/`：旧 313 测试（归档态，不再运行）
- `PROJECT_ARCHITECTURE.md`、`PROJECT_PROGRESS_REPORT_2026-08-18.md`：旧架构图与最终进展报告
- `ocr-config.json`、`host-runtime.json`、`agent-vision-config.json`、`policy-profiles.json`、`publication-profiles.yaml`：旧配置

## 仍在使用的部分

- `legacy/tools/powerpoint_native_math.py`（+roundtrip ps1）：v2 `autofigure math` 可选命令的公式升级引擎，通过 `tools/` 根下的同名保留副本调用。
- `legacy/ocr-config.json`：v2 check 的 OCR 模型/推理参数来源（`D:\paddle ocr` 只读运行时）。

旧 run 证据（143MB）已于 2026-08-18 晚清理；v1 最终交付物三件套（R10 PPTX + 目标尺寸渲染 + 差异图）存档于 `legacy/v1-final-evidence/`。v1 的两次保护性提交：`a59b78e`（转型前现场）、`5e3d9b2`（归档）。
