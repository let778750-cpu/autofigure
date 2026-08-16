# AI AutoFigure — 独立绘图工具

本目录是独立工具项目，与 `D:\AI+科研\课题研究` 的研究 Infra 状态、Gate、handoff、Task Packet 和 Result Envelope 无关。根目录针对该研究仓的治理规则不适用于这里，也不得修改该研究仓的任何文件或状态。

## 本地运行隔离

不需要研究 Infra 的 Run/Attempt，但**每次 AutoFigure 执行必须有自己的本地 run ID**：

```text
examples/generated/runs/<run_id>/
```

感知 manifest、OCR 候选、分析图、spec、preflight、渲染和审计证据都写入该目录。不得把 `examples/_analysis/` 或其他历史输出当作当前证据；参考 SHA、模型 SHA 或脚本 SHA 不一致时必须重跑。

## 授权与边界

- 允许直接读写本项目的 `SKILL.md`、`references/`、`schemas/`、`tools/`、`tests/` 和 `examples/`。
- 允许运行本项目工具及 `mcp.json` 注册的 scientific-illustrator PowerPoint/draw.io MCP。
- `D:\paddle ocr` 是只读的已安装运行时；本项目不得在其中下载、覆盖或更新模型。
- `D:\opencv\env\python.exe` 是宿主 CV/QA 的锁定 Conda 解释器；CV/QA 新入口必须使用绝对路径和 `-I -B -X utf8`。已被实机公式证据绑定的 `powerpoint_native_math.py` 暂用绝对解释器、`PYTHONNOUSERSITE=1` 与 `-s -B -X utf8`，避免为纯导入路径调整破坏当前证据哈希。升级仅能作为显式维护任务执行，且会使依赖旧 runtime receipt 的下游证据失效。
- 宿主环境只允许 `opencv-python`，不得与 contrib/headless wheel、Torch 或 Paddle 混装；PaddleOCR 只由其独立解释器执行。
- PNG 重绘的感知阶段必须由 Codex 自行调用项目根 `autofigure.cmd`；不得要求用户手动激活 Conda、输入 Python/PowerShell 命令，或绕过 canonical runner 直调分析/OCR 子脚本。该 `.cmd` 仅解决 Windows 启动与当前进程执行策略，不复制任何感知逻辑。
- canonical runner 在 OCR 后必须自动用 Host CV 执行 Phase-1 `geometry_refinement`；不得要求用户另跑脚本。其 `geometry-manifest.json`、overlay、lossless label atlas 与 ambiguity mask 只属于当前 run 的观察证据：公式、纵排、多行和污染区域必须显式降级，在 gold fixture 与 promotion gate 建立前不得作为 spec 几何真值。所谓对齐线是 ink-bottom alignment，不是可从 PNG 恢复的字体 baseline；箭头/连接器精测留给 Phase-2。
- Agent 视觉任务包（`agent-vision/`）、应答、盖章文档与融合产物只属于当前 run 的隔离证据。`agent-vision-response.json` 必须由外层 Agent 在会话内**亲自看图**填写，禁止从 OCR manifest 抄写答案（Q1 结构盘点独立性）；应答必须经 `validate_agent_vision.py` 校验、融合必须经 `cross_modal_fusion.py`，两者与 `prepare_agent_vision_task.py` 一样走 Host Python `-I -B -X utf8` 与 `output_policy`。视觉产物永不进入成品。
- 默认输出放到当前 run 目录，用户确认交付后再复制到明确的目标位置。
- `mcp.json` 中 Draw.io/PowerPoint 服务的工作目录位于项目外；所有 MCP save/export 参数必须传入当前 run 内经解析的**绝对路径**，不得依赖相对路径或服务端当前工作目录。

正式工作指令是 `SKILL.md` 及其按需引用的 `references/`。图像相似度只能作诊断；感知门禁、场景 preflight、对象结构与 fresh render 才能构成通过证据。
