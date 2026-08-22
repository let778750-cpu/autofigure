# Autofigure 本地协作规则

## 范围

本项目把参考科研图重建为原生可编辑 PPTX。正式指令见 `SKILL.md`、`PROJECT_ARCHITECTURE.md` 和 `HIGH_FIDELITY.md`。`legacy/` 除公式引擎兼容入口外不维护。

## 输入路线与案例

- 新案例必须显式 `--input-route reference-only|svg-seeded`。
- `input_route` 不可变，`processing_mode` 可回退。
- 案例只允许位于 `examples/reference-only/<id>/` 或 `examples/svg-seeded/<id>/`，ID 全局唯一。
- 每个案例根是单一扁平工作单元，机器证据统一进 `qa/`；不得堆版本子目录。
- 旧扁平案例只做兼容读取，不创建副本/符号链接。

## provenance 与隔离

- 参考图身份使用案例内 `reference.png`、相对路径和 SHA-256；不得恢复机器绝对路径权威。
- 不确定的模型/来源必须写 `null` 或 `unknown`。
- reference-only 受控重建禁止读取 seeded 案例的 SVG、PPTX、scene、bindings、assets、裁剪文件和候选坐标；只可共享冻结参考和路线无关 QA 阈值。

## 质量与状态

- 全图像素指标仅作诊断。
- strict 无 critical region 必须失败。
- 保存重开、bindings、区域、箭头、布局或 live evidence 任一 blocker 均保持 `qa_failed`。
- 不得把 candidate/qa_failed 宣传为完成；人审不等于机器自动 approved。

## 运行环境

- 工具与测试使用项目内 `.venv`。
- fresh render 使用本机 PowerPoint COM；不得用截图冒充。
- OCR 环境只读，不下载/更新模型。
- PowerPoint Live 只使用 case-bound managed session；禁止修改模板原件。
- 默认不安装第三方 Office 插件。禁止 Ribbon 坐标点击、SendKeys 和图像识别点击。

## 修改后验证

```bat
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python -m ruff check tools tests
.venv\Scripts\python -m compileall -q tools tests
autofigure cases --write-index
autofigure cases --check
autofigure hygiene
```

测试临时目录必须位于受控外部 basetemp，结束后删除；正式案例不得残留 mock、缓存、临时 candidate 或 PowerPoint Live session build。
