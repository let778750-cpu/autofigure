# PNG-only reconstruction contract

本案例从冻结的 `reference.png` 直接开始，不要求、也不依赖 GPT Web 或其他网页端预先生成 SVG。

- 画布：1429×627 px
- 参考图 SHA-256：`792a16d4bd2c26cca9fca79668395a987825ab75eb2bc8a65f2d42a47c38a340`
- 区域任务：`qa/region-tasks.json`
- 默认保真策略：`hybrid_fidelity`

执行者可以是 Codex、其他 VLM 或人工操作员。必须逐区读取参考图并保持稳定对象 ID；文字、公式、规则形状和箭头保持原生可编辑。只有在 `assets.json` 明确授权时，复杂且不可忠实矢量化的微资产才允许使用紧边界参考图裁剪。

所有容器内文字/公式必须声明 `data-layout-container`；所有重复图元必须声明 `data-repeat-group/data-repeat-axis/data-repeat-order`。工具会同时审计 SVG 源坐标和保存重开的 PowerPoint shape，借此区分视觉测量错误与转换漂移。

候选通过 `autofigure ingest` 返回。离线初版当前以 SVG 作为可渲染载体；完整 scene/region patch 可以用于已有载体的修复，或交给 PowerPoint Live provider。任务协议与模型品牌无关，但视觉推理仍需要模型或人工执行，不能把“入口已连通”误写成“PNG 已自动一比一重建”。
