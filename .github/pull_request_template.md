<!--
本模板同时供人和 .github/scripts/check_pr_governance.py 读取。
请保留字段名和二级标题；删除尖括号占位符并填写可复核内容。
普通分支格式：codex/<stage-slug>-v<version>；Epic 只通过 metadata 绑定。
R2 integration 例外：codex/integration-<stage-slug>-v<version>，slug/版本均绑定 Stage，最长 14 天。
JSON authority contract 是唯一语义权威；本 metadata 块只是它的严格可读镜像。
-->

<!-- GOV-METADATA-START -->
Authority-Contract: <.github/stage-contracts/stage-slug-v1.json|.github/release-contracts/release-v1.json>
PR-Type: <feature|task-definition|integration|release>
Risk-Level: <R0|R1|R2>
Epic: <#123|release>
Stage: <stage-slug@v1|release@v1>
Included-Issues: <#123, #124；严格列表，不写 Closes/Fixes/自由文本>
Deferred-Issues: <none|#125, #126；严格列表>
Scope-Freeze: <scope@40-character-commit-sha；release contract 用 current-head，本镜像展开 exact head>
Branch-Sunset: <YYYY-MM-DD|not-applicable-for-release>
Evidence-Baseline: <head@40-character-current-head-sha>
Evidence-Invalidation: <head-change,scope-change,oracle-change；可追加项目条件>
Rollback-Unit: <stage-squash|release-merge>
Accountable-Owner: <@login>
Implementation-Agent: <实现者 login 或稳定 agent id；由人工复核，自动门禁仅检查与 Bot 不同>
Independent-PR-Author: <@bot-login>
Workstream: <模块 Epic 的工作流目录或短标识>
Scientific-Mode: <RECONSTRUCT_1TO1|NEW_OR_REDESIGN|NOT_APPLICABLE>
Closure-State: <ready|blocked>
<!-- GOV-METADATA-END -->

## 范围与非目标

<!-- REQUIRED：明确本 Stage 改什么、不改什么；不要把 Epic 的全部目标复制进单个 Stage。审核前追加内容也必须逐项满足 docs/WORKSTREAM_GOVERNANCE.md 的六项 Stage 范围准入条件；否则列入 Deferred 并建立下一版本 Stage。 -->

## Epic、Stage 与 Scope Freeze

<!-- REQUIRED：说明 Epic 目标、本 Stage 版本、严格 Included/Deferred 列表，以及 scope@SHA 冻结边界。Included 由 develop merged finalizer API 关闭。 -->

## 变更说明

<!-- REQUIRED：按行为或子系统说明改动；指出公共 CLI、Schema、报告字段或工作流接口变化。 -->

## 契约与权威影响

<!-- REQUIRED：说明是否改变科学语义、reference/scene/hash 权威、权限边界、状态迁移或发布语义。 -->

## 验证证据

<!-- REQUIRED：列出精确命令、结果、平台，以及绑定当前 head/artifact/oracle/hash 的证据。 -->

## 证据失效条件

<!-- REQUIRED：说明 head、scope、reference/oracle、依赖或环境变化后哪些证据必须重跑。 -->

## 回滚单元与恢复

<!-- REQUIRED：develop 为单个 Stage squash；main 为 release merge commit。说明回滚后如何恢复 Issue/证据状态。 -->

## 回归与反例证据

<!-- REQUIRED：列出受影响案例、未受影响基线、失败注入或能够证明门禁有效的反例。 -->

## 剩余风险与后续

<!-- REQUIRED：列出 Deferred Issues、已知 qa_failed、未知项与非声明；没有则说明证据为何足够。 -->

## 独立复核

<!-- REQUIRED：记录 Reviewer 与 PR 作者/实现者的分离，以及审查过的证据范围。 -->

## Scope Threshold Explanation

<!-- 仅触发时填写。内容必须逐字镜像 authority contract 的 scope_threshold_justification。 -->
included-issues: <!-- 若触发，说明该 Issue 集为何不可拆；未触发删除本行 -->
source-test-files: <!-- 若触发，说明这些文件的共同失败边界；未触发删除本行 -->
non-generated-loc: <!-- 若触发，说明代码量为何仍是原子结果；未触发删除本行 -->
Atomic-Outcome: <!-- 共同、可验收的单一结果 -->
Shared-Failure-Mechanism: <!-- 拆分时会产生的共同失败机制 -->
Shared-Validation: <!-- 覆盖整个 Stage 的共同验证证据 -->
Rollback-Reason: <!-- 为什么必须作为一个 squash 回滚 -->

## Integration 例外与 14 天退出

<!-- integration PR 必填：说明为何普通 Stage 无法完成、sunset 日期、拆除/删除计划和转回 develop 的条件；branch slug 必须等于 Stage slug。 -->

## 科学保真门禁（R2 必填）

<!-- R2 REQUIRED：逐项说明语义、拓扑、几何、区域、编辑性、保存重开和 reference-bound 结果。 -->

## 对抗性证据（R2 必填）

<!-- R2 REQUIRED：至少一个能复现缺陷并能验证当前结果的失败注入或 mutation test。 -->

## 发布审批（release 必填）

<!-- release REQUIRED：Scope-Freeze/Evidence-Baseline 均绑定 exact develop head；列出纳入 Stage/Issue、绿色检查、风险，以及 @let778750-cpu 对 exact head-bound contract 的审批。 -->
