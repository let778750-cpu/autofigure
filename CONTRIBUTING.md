# Autofigure 贡献指南

本仓库是单人维护、agent 协作的科研项目。治理原则是**轻流程、重证据**：用两个常驻分支和主题分支组织工作，用机器验证和如实状态约束质量，不维护形式大于实质的治理机器。

## 1. 常驻分支与主题分支

- 常驻分支只有 `main` 与 `develop`。
- 工作按**主题 issue** 组织；每个主题一条分支，从最新 `develop` 创建：
  - `qa-state-release`：QA 状态语义与发布边界；
  - `reference-fidelity`：参考忠实度（跨路线评价真值、parity 门禁、修复闭环、子元素还原）。
- 同一主题下的小点持续追加到该主题分支，不为每个小点开新分支；也不把分支当永久容器——主题一个可验收段落收口就开 PR，合并后删除分支，下一段从最新 `develop` 重新建。
- 分支名用主题标识，不使用 agent 名（codex/gpt/...）作为前缀：agent 不是责任主体，责任人是 `@let778750-cpu`。

## 2. Issue 规则

- Issue 按主题聚合，不按治理阶段分串行链；每个 issue 说清范围、非目标和可机器验收的完成条件。
- 全部 P0 等于没有优先级：优先级用 `priority:p0/p1/p2` 如实标注。
- `history/` 只本地留档，不发布；issue 正文引用机器证据的相对路径与 SHA-256。

## 3. PR 流程

1. 从最新 `develop` 建主题分支，实现并本地验证（见 §4）。
2. 开 PR 到 `develop`，正文按模板填写；关联 issue 用 `Closes #N`，合并时自动闭环。PR 标题、正文与交付说明从最终 diff 重新生成，只陈述最终采用的状态（规则见 AGENTS.md "No Negative Echo"），收口时由 `hygiene` 机器兜底。
3. CI 全绿 + 本地五项检查全绿，且**不让任何案例状态回退**（当前所有案例为 `qa_failed`，"测试通过"指工具链检查，不等于案例 `approved`）。
4. `@let778750-cpu` 审核通过后 squash 合入 `develop`，删除主题分支。
5. `main` 只接受从 `develop` 发起的 release PR，由 `@let778750-cpu` 审批后 merge commit 合入。

紧急变更走同一路径，不绕过审核。

## 4. 本地验证（提交前必跑）

使用项目 `.venv`，测试临时目录放在受控外部 basetemp，结束后删除：

```bat
.venv\Scripts\python -m pytest tests -q --basetemp <受控外部目录>
.venv\Scripts\python -m ruff check tools tests
.venv\Scripts\python -m compileall -q tools tests
.venv\Scripts\python -m tools cases --check
.venv\Scripts\python -m tools hygiene
```

## 5. 证据与回滚

- 详细测试分层与 PowerPoint COM 条件门禁见 [docs/TESTING.md](docs/TESTING.md)；案例影响面与基线更新规则见 [docs/CASE_REGRESSION_POLICY.md](docs/CASE_REGRESSION_POLICY.md)。
- PR head 变化后，head 相关的测试与审核结论失效，重跑后再审。
- `develop` 的最小回滚单元是单个 PR 的 squash commit；回滚后重开对应 issue，从最新 `develop` 建新的主题分支重新交付，原分支不复活。
- 只有参考、科学合同或获批验收定义确实变化时才允许更新案例基线；为让失败测试变绿而移动阈值、裁剪区域或参考不是合法基线更新。
