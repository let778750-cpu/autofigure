# AI AutoFigure v2 — 独立绘图工具

本目录是独立工具项目。v2（2026-08-18 起）架构为 **VLM-first, verify-light**：多模态大模型网页端（GPT / Kimi / Claude 等）把参考图重绘为 SVG，本工具确定性地转换为原生可编辑 PPTX 并轻量核验。旧重型管线整体归档在 `legacy/`，除注明仍使用的部分外不维护、不修改。

## 本地运行隔离

每个案例一个扁平目录：`examples/<case>/`，prepare/convert/check 的产物全部写入该目录（诊断明细在 `qa/` 子目录）。案例目录即工作单元：重跑覆盖当前最佳，历史由 git 承担；不得在同一案例下堆叠历史版本子目录。

## 授权与边界

- 允许直接读写本项目的 `SKILL.md`、`README.md`、`references/`、`tools/`、`tests/`、`examples/`、`legacy/`。
- v2 代码一律在项目内 `.venv` 运行（基座 `D:\anaconda\python.exe`，依赖见 `requirements-v2.txt`）；不得装进其他环境。
- `D:\paddle ocr` 是只读运行时：OCR 仅 `check` 环节经 `tools/v2/ocr_texts.py` 单次调用，模型/配置以 `legacy/ocr-config.json` 为准，不下载、不更新。
- `D:\opencv\env` 保持锁定（仅 `opencv-python`），v2 不依赖它。
- fresh render 经 `tools/v2/render_export.py`（pywin32 直驱本机 PowerPoint COM），不得用截图冒充 render。
- `mcp.json` 注册的 MCP 服务是 v1 遗产，v2 不依赖；`autofigure math` 例外复用 `tools/powerpoint_native_math.py`（其解释器纪律见该文件头注释与 legacy 文档）。

正式工作指令是 `SKILL.md` 与 `references/v2-prompt-contract.md`。像素指标只作诊断；文本可编辑读回 + check 报告人审才是通过证据。
