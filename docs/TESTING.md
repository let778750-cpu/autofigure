# 测试与证据门禁

"测试完全通过"指工具链检查全部绿色、没有新增回归，且已知失败被明确列出。它不是用一个平均分替代局部或结构审查，也不等于任何案例达到 `approved`。

## 1. 可移植 CI

GitHub-hosted Ubuntu 与 Windows runner 执行：

```text
python -m ruff check tools tests
python -m compileall -q tools tests
python -m pytest tests -q
python -m tools cases --check
python -m tools hygiene
```

Ubuntu 安装除 Windows-only `pywin32` 外的相同依赖。测试不能依赖活动 PowerPoint 窗口、OCR 下载、GUI 点击或开发者机器绝对路径；需要 Office Math 的用例按能力探测显式 skip。

## 2. 证据失效

证据按 PR head 绑定。以下变化至少触发对应重跑：

| 变化 | 失效证据 |
|---|---|
| PR head 变化 | 全部 head-bound CI、hash、人工审核结论 |
| reference 或 critical regions 变化 | 区域指标、结构 gate、全图/裁剪审查 |
| scene/转换器/Schema 变化 | source→backend bindings、保存重开、兼容与 mutation tests |
| PowerPoint/字体/Office 环境变化 | COM fresh render、native readback、目标尺寸证据 |

旧报告可作为历史证据保留，但不能支撑新 head 的结论。

## 3. R2 科学保真验证

涉及科学语义/保真、参考与哈希权威、转换/QA 的改动，证据包至少包含：

- reference、candidate、scene、PPTX、render 与报告 SHA-256；
- 受影响 critical region 的目标尺寸裁剪和 reference-bound 指标；
- 语义、拓扑、几何、微资产、文字、箭头、bindings 和可编辑性结果；
- 源模型与保存重开对象的分层诊断，不把内部自洽写成参考保真；
- 至少一个反例或 mutation test；
- 同一 reference 的两条路线各自独立候选与共同 evaluation oracle 的一致性证据。

范围内关键区或结构 blocker 必须为零；范围外已知 `qa_failed` 要保持独立记录并证明没有恶化。

## 4. PowerPoint COM 条件门禁

普通 GitHub-hosted runner 没有受信任桌面 Office 会话，不会声称完成 PowerPoint 保存重开、fresh render 或 native Math/shape 复核。需要这些能力时人工触发 `CI` workflow：

1. 设置 `run_powerpoint_live=true` 并提供 `examples/` 下案例目录。
2. `self-hosted, Windows, X64, powerpoint` runner 执行 COM fresh render。
3. 同一 head 执行 `strict --require-live` 并上传 fresh render 与 check report。

截图、缓存 PNG 或 detached JSON 不能冒充 fresh COM 证据。runner 或证据缺失时结论为 `INCONCLUSIVE`。

## 5. 人工审核

`@let778750-cpu` 对绑定当前 head 的全图与局部裁剪证据做审查后 PR 才可合入 `develop`；进入 `main` 的 release PR 需要显式批准。head、scope、reference/oracle 或环境变化后重新审核。
