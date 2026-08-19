# AI AutoFigure v2

把科研图 PNG 高保真重建为**原生可编辑 PowerPoint**。架构：**VLM-first, verify-light**——多模态大模型网页端（GPT / Kimi / Claude 等）负责看图重绘为 SVG，本工具负责确定性地把 SVG 转换为原生 PPTX 对象，并用轻量核验（文本比对 + 像素诊断 + 人审）兜底。

## 为什么是这个架构（2026-08-18 实测）

同一张 ModularAgent 参考图（1429×627）、同一把诊断尺（figure_lint）：

| 指标 | v1 重型管线（4 天 / ~30 轮 / 27k LOC） | GPT 直出 SVG | v2 转换后 PPTX 渲染 |
|---|---:|---:|---:|
| mean_abs_rgb_delta | 19.9987 | 16.4183 | **17.3963** |
| changed_pixel_ratio | 46.55% | 38.92% | **38.97%** |
| top_roi_loss | 9.42% | 10.25% | **9.25%** |

且 v2 产物 255 个对象全部原生可编辑（66 个文本框读回、渐变/自由曲线/箭头保留）。v1 已归档至 `legacy/`（缘由见 `legacy/README.md`）。

## 快速开始

```bat
REM 首次：建隔离环境
D:\anaconda\python.exe -m venv .venv
.venv\Scripts\pip install -r requirements-v2.txt

REM 四步
autofigure prepare <参考图.png> --case 01-my-figure
REM   → 按提示把 prompt.md 全文 + PNG 发给多模态大模型网页端（GPT / Kimi / Claude 等），取回 SVG 存入案例目录的 redraw.svg
autofigure convert examples\01-my-figure
autofigure check   examples\01-my-figure
autofigure math    examples\01-my-figure   REM 可选：公式升级原生 Office Math
```

## 环境

| 用途 | 环境 |
|---|---|
| v2 全部命令（convert/check/figure_lint） | 项目内 `.venv`（python-pptx / numpy / jsonschema / scikit-image / pywin32） |
| check 的 OCR 文本比对（只读单次调用） | `D:\paddle ocr\env\python.exe`（PP-OCRv6，锁定配置 `legacy/ocr-config.json`） |
| fresh render | 本机 PowerPoint COM（pywin32 直驱） |

`D:\paddle ocr` 与 `D:\opencv\env` 均为只读锁定运行时，不重装模型、不混装依赖。

## 测试

```bat
.venv\Scripts\python -m pytest tests\v2 -q
.venv\Scripts\python -m ruff check tools\v2 tests\v2
```

## 目录

- `tools/v2/`：v2 全部代码（common/prepare/convert/check/ocr_texts/render_export，约 1.6k 行）
- `tools/figure_lint.py`：像素诊断器（软信号，非门禁）
- `tools/powerpoint_native_math.py`：`math` 命令的公式升级引擎
- `references/v2-prompt-contract.md`：VLM 输出合同（提示词规范）
- `tests/v2/`：v2 测试套件
- `examples/`：每案例一个扁平目录（参考图/SVG/交付 PPTX/渲染/核验报告 + qa/ 诊断），索引见 `examples/README.md`
- `legacy/`：v1 重型管线归档（2026-08-18）
