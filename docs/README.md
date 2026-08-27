# docs/ — 文档索引

单一职责分层：根 README 只保留介绍与快速开始；skill 保留执行顺序与红线；architecture 定义数据流与 schema；high-fidelity-contract 定义门禁；具体能力规格进入 `specs/`。

## 合同与架构

- [`architecture.md`](architecture.md) — schema 4.0 架构：Y 型双路线、Canonical Scene 唯一事实源、source gate 与确定性编译数据流。
- [`high-fidelity-contract.md`](high-fidelity-contract.md) — 高保真真执行合同：门禁、冻结清单、六维 QA 与验收边界。

## 规范

- [`specs/powerpoint-arrow-capability.md`](specs/powerpoint-arrow-capability.md) — PowerPoint Live provider 的箭头创作与读回能力规格。
- [`CASE_REGRESSION_POLICY.md`](CASE_REGRESSION_POLICY.md) — 案例回归策略：影响矩阵、回归记录与 Issue 闭环条件。
- [`TESTING.md`](TESTING.md) — 测试与证据门禁：可移植 CI、证据时效与 PowerPoint COM 条件门禁。
- [`legacy-archive.md`](legacy-archive.md) — legacy/ 历史归档取回指引（当前 tip 不携带归档工作树）。

## 计划与试点

- [`ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md`](ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md) — 矢量创作车间升级计划（免费开源默认栈）。
- [`vtracer-pilot/`](vtracer-pilot/) — vtracer 微资产描摹试点证据。

## 其他入口

- 仓库级 skill：[`../.agents/skills/ai-autofigure/SKILL.md`](../.agents/skills/ai-autofigure/SKILL.md)（Kimi/Codex 项目级发现路径）。
- 案例索引：[`../examples/README.md`](../examples/README.md)（`autofigure cases --write-index` 生成表）。
