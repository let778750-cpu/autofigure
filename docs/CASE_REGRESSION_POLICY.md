# 案例回归策略

## Stage 影响矩阵

| 变更类型 | 必测范围 | 附加证据 |
|---|---|---|
| 单案例源或合同 | 该案例全部 critical regions；同 comparison group 的另一输入路线 | 独立候选、共同 reference/evaluation-oracle identity、无跨路线候选复用 |
| Scene/转换器/布局/箭头/数学 | 所有受影响案例与至少一条正反路线代表案例 | source → PPTX bindings → 保存重开 readback 分层结果 |
| QA、阈值、Schema 或状态迁移 | 全量单元测试、所有消费字段的案例、旧格式兼容样本 | 通过样本、失败样本、边界值与 mutation test |
| reference/oracle/compare | 同一 reference SHA 下的全部路线与区域 | oracle 哈希、语义 inventory、拓扑和几何等价性 |
| 治理/发布 | 治理正反例、scope thresholds、branch history、release topology | fail-closed API/branch/evidence 测试 |
| 纯文档 R0 | 文档链接、治理自测和 hygiene | 明确证明不改变运行或科学合同 |

路径风险下限由 `.github/governance-policy.json` 强制。实际影响更大时主动提升风险与测试范围。

## Stage 回归记录

每个受影响案例在 PR 中记录：

- Epic、Stage、Included/Deferred Issues、scope freeze 和当前 head；Epic 只在 metadata 中绑定，普通分支使用 `codex/<stage-slug>-vN`；
- 案例相对路径、`input_route`、workflow mode 与 reference SHA；
- scene/candidate/PPTX/render/QA hashes 和 evaluation oracle identity；
- critical region 的失败断言、当前结果与反例；
- 新增、消失和不变的 blocker；
- 执行命令、平台、时间与报告相对路径；
- 人工 Reviewer 对全图、局部裁剪和非声明的结论。

跨路线对照只共享指定参考和路线无关 evaluation oracle。reference-only 不得读取 seeded 候选的 scene、SVG、PPTX、bindings、assets、裁剪或坐标；两边内部自洽但 oracle 不一致时，对照门禁应失败。

## Scope 阈值与拆分

阈值是审查信号，不是自动切分算法：

- Included Issues 超过 3 个；
- source/test 文件超过 30 个；
- 排除 policy 明列的 derived evidence 后，增删 LOC 超过 1500。reference、external seed、scene、assets、regions、prompt、scientific spec 与 source/render manifest 等 case source 即使位于 `qa/` 下也不得豁免。

超过阈值时，PR 仍要保持一个可共同验证和共同回滚的 Stage。`Scope Threshold Explanation` 对每个实际触发项逐行使用 `included-issues`、`source-test-files`、`non-generated-loc` token，并另行填写 `Atomic-Outcome`、`Shared-Failure-Mechanism`、`Shared-Validation`、`Rollback-Reason`。每一字段都要说明共同结果、拆分失败机制、共同验证与回滚原子性；仅有泛化或填充文字不能通过。无法逐项解释则建立新 Stage 版本或拆出后续 Issue。

## 证据失效与关闭条件

Issue 只有在以下条件同时满足时才进入 Stage 的严格 `Included-Issues: #N, #M` 集合：

1. 原缺陷由测试或冻结证据稳定复现；
2. 当前 `head@SHA` 满足 Included Issues 的全部验收；
3. mutation/反例证明门禁对同类回归敏感；
4. 全量 CI、案例合同和影响面回归绿色；
5. 范围内 blocker 为零，Deferred Issues 有独立记录且没有恶化；
6. 独立 Reviewer 检查绑定当前 head 的证据。

由于 GitHub 不会因 PR 合入非默认 `develop` 自动兑现 closing keyword，本项目禁止依赖 `Closes/Fixes/Resolves` 文本。可信 `pull_request_target` finalizer 在确认 PR 已 merged 且 base 为 `develop` 后，从分页 Issue events 重建 merge-time open 状态，通过 API 关闭完整 Included 集合，并要求读回 `state_reason=completed` 与 trusted finalizer latest-close provenance。Issue/ref 全部闭合后才以 exact merge SHA 为 target 创建受保护 lightweight tag receipt；旧 receipt 使历史 finalizer 成为终态，rollback 后 reopened Issue 不会被重关。这个状态只表示 Stage 已进入 `develop`，不等于 `main` release、案例 `approved` 或科研发布。

Head、scope、reference/oracle、Schema 或执行环境变化后，对应证据失效。Scope 变化不在原 Stage 上继续叠加，而是建立新版本并重新选择 Included/Deferred Issues。

## 回滚与基线更新

Stage squash commit 是 `develop` 的最小回滚单元。回滚完整 Stage 后重新打开 Included Issues、标记该 Stage 的 approval/evidence 失效，并以新版本 Stage 重新交付；不能继续使用原 topic branch。

只有参考、科学合同或获批验收定义确实变化时才允许更新案例基线。PR 要说明权威来源、旧/新哈希、迁移影响和用户批准；为让失败测试变绿而移动阈值、裁剪区域或参考不是合法基线更新。
