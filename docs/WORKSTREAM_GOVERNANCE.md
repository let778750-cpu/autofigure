# Workstream、Epic、Stage 与 Project 治理

本项目把“长期负责什么”和“一次交付什么”分开管理。长期模块不对应长期
feature branch；Git 中只有 `main` 与 `develop` 常驻。

| 对象 | 唯一职责 | 生命周期 | 安全权威 |
|---|---|---|---|
| Workstream / Epic | 管理一个领域的目标、Stage DAG、非目标与长期风险 | 长期 | open Epic Issue |
| Issue | 记录一个可复现问题、决定、验收与关闭证据 | 闭环前 | GitHub Issue 与 Parent Epic 关系 |
| Stage branch | 隔离一个阶段性、可共同验收和回滚的实现 | 短期 | exact-head JSON contract |
| PR | 承载一次审查、测试、合并与回滚事务 | 合并后结束 | exact-head contract、API 证据与 required checks |
| Project | 提供跨 Epic 的索引、排队和视图 | 长期 | 只作导航；不得覆盖合同、Issue 或证据状态 |

## 规范 Workstream

每个 Epic 和非 Epic Issue 至少绑定一个主 area；跨 area 依赖通过关联 Issue
表达，不通过共享长期分支表达。

| Label | 范围 |
|---|---|
| `area:visual-grammar` | 箭头、括号、连接器、线宽和端点 |
| `area:typography` | 字体、字号、公式、autofit 和 baseline |
| `area:member-geometry` | 子元素尺寸、相对比例、方向和 bbox |
| `area:microasset-fidelity` | 轮廓、内部拓扑、视角和参考还原度 |
| `area:asset-representation` | 原生重绘、受控 raster、来源和可编辑性 |
| `area:qa-repair` | Reviewer、Corrector、状态和发布门禁 |
| `area:route-parity` | reference-only、svg-seeded 与跨路线一致性 |

`area:` 前缀只保留上表七个规范值。每个 Issue 正文必须声明唯一
`Primary Area`，该值同时出现在标签中；Project 的 single-select `Area` 只镜像
这个字段，不能从多个标签猜测。架构、组件、案例、状态和目标等辅助维度分别
使用 `topic:`、`component:`、`case:`、`status:`、`target:`，不得伪装成第八个
Area。

## Stage 范围准入

新增的小点只有同时满足以下条件，才能留在当前 Stage branch。PR 尚未进入
正式审核时可按下列条件追加；正式审核已经开始时，必须先使既有审核和旧证据
失效，并在追加、更新合同和重跑验证后重新申请审核：

1. 服务于同一个可陈述的最终结果；
2. 属于同一失败机制和不变量；
3. 使用同一验收测试与 scientific mode；
4. 风险等级、Accountable Owner 和 Reviewer 边界一致；
5. 可以作为同一个 squash rollback unit；
6. 不引入新的 reference SHA、case lineage 或发布权限。

新增内容后必须更新 authority contract、Scope Threshold Explanation、
`Evidence-Baseline` 和受影响证据，并重新申请审核。任一条件无法证明时，
Deferred Issue 进入从最新 `develop` 创建的下一版本 Stage；“属于同一模块”
本身不是留在当前 PR 的理由。

正式审核读取的是合同内按 number 排序的 `issue_snapshots`：每个 Epic、Included
和 Deferred Issue 都有唯一 role、title SHA-256 与 body SHA-256。Issue 的 title、
body、label、open/closed 状态或归属在审核后发生变化，会由可信默认分支 workflow
把旧 head 永久标为失败；把文字改回或在同 head rerun 不能恢复。维护者必须更新
snapshot、提交新 head 并重新申请 Bot/人工审核。评论不改变 Stage 范围，不绑定
`updated_at`，也不触发此失效流程。

以下任一情形强制拆出下一 Stage：

- 可以独立验收、关闭或回滚；
- 更改不同共享 Schema、authority 或 CODEOWNER 边界；
- scope freeze 后新增此前未冻结的参考图、案例或 evidence lineage；
- 需要不同测试矩阵或 scientific mode；
- 与当前 PR 的关闭条件无关；
- 正式审核开始后才发现的范围外问题。

`codex/case-<id>-evidence-v1` 是初始 scope freeze 的显式双路线合同：同一案例
可以在一个 Stage 中共同验收 reference-only 与 svg-seeded，但两条 lineage 的
source/compiler/revision/reference 哈希、候选资产和 QA 目录必须互相独立。冻结后
再增加路线或案例仍触发拆分。跨路线 comparison 使用不同测试矩阵和独立回滚
单元，因此不属于该例外，而是在两路证据落定后进入共享的
`codex/route-comparison-v1`。

Included Issues 超过 3 个、source/test 文件超过 30 个或非生成代码增删超过
1500 LOC 时触发范围复审。阈值不是自动失败：PR 必须逐项解释共同结果、
失败机制、验证和回滚原子性；解释不成立就拆分。

只有无法分阶段保持 `develop` 全绿的连通 R2 迁移可使用
`codex/integration-<stage-slug>-vN`。它必须有单一负责人、完整 CI、最长
14 天 sunset 和明确删除条件；它不是第三条常驻集成线。

## GitHub Project 视图合同

Project 名称固定为 `Autofigure Workstreams`。建议字段与枚举如下：

| 字段 | 类型 | 值或格式 |
|---|---|---|
| `Status` | single select | Backlog、Ready、In progress、In review、Integrated、Released、Blocked |
| `Area` | single select | 精确镜像 Issue 的唯一 Primary Area；值限于七个规范 area 的短名 |
| `Workstream` | text | 严格镜像 authority contract 的 Workstream，例如 `repository-baseline` 或 `case04-dna-closure` |
| `Epic` | text | `#N · epic-slug` |
| `Stage` | text | `stage-slug@vN`；未分配时为 `unassigned` |
| `Risk` | single select | R0、R1、R2 |
| `Evidence` | single select | Missing、Frozen、Valid、Invalidated |
| `Target` | single select | develop、main |
| `Release` | text | `release@vN` 或 `unreleased` |

最低视图为：

- `Workstream roadmap`：先按 Area、再按 Workstream 与 Epic 分组，显示所有 open Issue；
- `Active Stages`：只显示 Ready 到 In review，按 Stage 分组；
- `Case04 fidelity`：过滤 microasset-fidelity、qa-repair、route-parity；
- `Release readiness`：过滤 Integrated 且未 Released，显示 Evidence 与 Risk。

Project 字段不是关闭、合并或发布权威。Project 与 Issue/contract 不一致时，
自动化必须停止并修正 Project；不得反向改写 Issue、reference、scene、artifact
或 QA 哈希来迎合看板状态。

## Case04 DNA 的 Stage 链

Case04 属于 `area:microasset-fidelity`，但不建立永久 DNA 分支。依赖链为：

```text
codex/reference-oracle-v1
  -> codex/qa-state-delivery-v1
  -> codex/object-corrector-v1
  -> codex/cross-route-parity-v1
  -> codex/case04-dna-fidelity-v1
```

最后一个 Stage 只有同时证明下列事实才能关闭：两条路线绑定同一 reference
SHA；左右 DNA 实例分别符合冻结 oracle；相对大小、倾斜、交叉相位、内部连接
和轮廓一致；scene、PPTX 读回和渲染证据闭环；没有截图遮盖或跨案例几何
复用；strict QA、跨路线 parity 和人工审查全部通过。任一项缺证据时状态只能是
`FAIL` 或 `INCONCLUSIVE`，不能因管线运行成功而标记 approved。

## AI 审核读取顺序

Reviewer 不需要扫描整个仓库。按以下顺序即可恢复当前事务：

1. Epic 的目标、Stage DAG、非目标和依赖；
2. Included/Deferred Issues 的复现、决定和验收；
3. PR 的 JSON authority contract 与严格正文镜像；
4. head-bound 测试、科学证据和反例；
5. 仅在上述材料声明的相对路径内查看 diff。

dirty baseline 的串行迁移见 `docs/DIRTY_BASELINE_MIGRATION.md`；测试、回滚和
`develop -> main` 发布边界分别见 `docs/TESTING.md`、
`docs/CASE_REGRESSION_POLICY.md` 与 `docs/DEVELOP_TO_MAIN_RELEASE.md`。
