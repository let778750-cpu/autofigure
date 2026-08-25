# Autofigure 贡献指南

本仓库采用“模块 Epic → 版本化 Stage → `develop` → `main`”闭环。只有 `main` 与 `develop` 常驻；每个实现 Stage 使用新的短期分支、独立证据和可回滚 squash，不把模块演进变成长寿 feature 分支。

## 1. 从 Issue 组织到模块 Epic

从对应模板创建记录：

- **模块 Epic**：冻结一个模块的最终目标、Stage DAG、非目标、证据失效和回滚边界；Epic 是 Issue，不是分支。
- **科学保真缺陷**：参考重建中的语义、拓扑、几何、微资产、编辑性或保存重开偏差，固定为 `R2`。
- **软件缺陷**：CLI、Schema、转换器、报告或自动化的可复现错误。
- **调查任务**：根因尚未确认时使用；调查结束要给出结论和后续实现 Issue，不能用调查关闭代替交付。

Epic 下的每个 Stage 使用 `<stage-slug>@vN`。正式审核后的范围外变化、互不
依赖或不能共同验收的 Issue 必须提升版本并创建新分支；审核前的小点也只有
通过下述 Stage 准入合同才能追加。`history/` 只提供整理来源，完整聊天、临时
绝对路径、重复快照和凭据不得直接发布。

同一 PR 尚未正式审核时允许追加小点的六项准入条件、强制拆分条件、七个
area 和 GitHub Project 字段合同见
[docs/WORKSTREAM_GOVERNANCE.md](docs/WORKSTREAM_GOVERNANCE.md)。不能满足该
准入合同的范围使用下一版本 Stage；“属于同一模块”本身不构成追加理由。

每个非 Epic Issue 必须由 Issue Form 的 `Parent Epic` 字段严格填写一个 `#N`。治理器通过 GitHub API 验证 Epic/Included Issue 存在、Epic 为 open 且带 `type:epic`、Included Issue 为 open，并验证其 `Parent Epic` 与 PR metadata 一致；API 证据缺失即阻断。authority contract 的 `issue_snapshots` 同时按 exact number/role/title/body SHA-256 冻结 Epic、Included 和 Deferred 全集合；`updated_at` 不得代替内容哈希，因为评论不属于 Stage 范围。

## 2. 分支与 PR

普通 Stage 从最新 `develop` 创建：

```text
codex/<stage-slug>-v<version>
```

例如 `Epic: #42`、`Stage: dna-oracle@v2` 对应 `codex/dna-oracle-v2`。Epic 只由 PR metadata 绑定，不进入 branch path。治理 bootstrap 使用 `Epic: #<治理 Epic>`、`Stage: governance-bootstrap@v1` 和 `codex/governance-bootstrap-v1`。流程为：

1. 在 Epic 中确认 Stage、Included/Deferred Issues、验收与 rollback unit。
2. 从当前 `develop` 创建全新的 `codex/<stage-slug>-vN` 分支，并记录 `scope@<SHA>`。
3. 实现者只完成当前 Stage；经 allowlist 验证的独立 GitHub Bot 创建到 `develop` 的 PR。
4. 当前 head 的证据和检查全部有效后，以 **squash merge** 合入 `develop`。
5. 合并到 `develop` 后，可信 finalizer 从 immutable `merge_commit_sha` tree 读取 head-bound JSON contract，关闭其中严格 `included_issues` 集合，并在 ref 仍等于 merged head 时删除 topic 分支。这个关闭表示 Stage 已进入 `develop`，不表示已发布到 `main`。
6. 同名 branch 出现在任何更早 PR 后都不能用于新 PR；closed-unmerged branch 保留并冻结，确认废弃后人工删除。
7. `main` 只接受同仓库 `develop` 发起的 `release` PR，由 `@let778750-cpu` 审批当前 head（从而绑定该 head 的 authority contract），并以 **merge commit** 合入；body digest 不参与安全决策。

紧急变更也遵循该路径。任何不符合版本化命名的本地 bootstrap 分支，在创建 PR 前都要先建立 Epic/Stage 并重命名。

## 3. R2 integration 例外

只有连通的 R2 Stage 无法在单个普通分支内安全验证时，才可使用：

```text
codex/integration-<stage-slug>-v<version>
```

Integration PR 仍只目标 `develop`，分支 slug/版本都必须与 `Stage` metadata 一致；Epic Issue 号不进入 path。PR 必须说明普通 Stage 不足的原因、完整退出条件和删除计划。`Branch-Sunset` 不得晚于 PR 创建后 14 天；可信 lifecycle workflow 在 PR 事件和每日 schedule 刷新 `branch-lifecycle/sunset` 状态。到期只标失败并阻断，不自动关闭 PR。Integration 分支不是第三条常驻集成线，合入后同样 squash 并删除。

## 4. PR 元数据与 scope 警告

保留模板字段和二级标题。关键字段含义：

- `Epic` 必须是带 `type:epic` 的 `#<Issue 号>`，且不编码进 branch path；普通与 integration 分支的 slug/版本都必须与 `Stage` 一致。
- `Included-Issues` 只能是 `#42, #44` 形式的严格列表；`Deferred-Issues` 只能是 `none` 或同格式列表。禁止 `Closes`、说明文字和其他自由文本，两者不能重叠。
- `Scope-Freeze: scope@<SHA>` 对普通 Stage/integration 必须精确等于 GitHub `base...head` compare 返回的 merge-base（真实 branch-point）；release 时必须精确等于 PR 的 `develop` head SHA。新增 Issue 或改变验收应创建新 Stage 版本。
- `Evidence-Baseline: head@<SHA>` 必须等于当前 PR head。
- `Authority-Contract` 必须是 branch/Stage 唯一推导且出现在 diff 中的 `.github/stage-contracts/<stage>-vN.json`，release 使用唯一 `.github/release-contracts/release-vN.json`。该 JSON 是语义 authority；PR metadata 只是严格镜像。
- Release 先创建 open Draft `develop → main` PR 取得真实编号，再由 `codex/release-preparation-vK` Stage 同时提交自己的 Stage contract 与绑定该 Draft PR 的 `release-vR` contract；K 是准备尝试版本、R 是发布版本，两者独立。develop 前进时新 K 更新同一 release-vR。后者使用 `scope_freeze: current-head`，由 R 的 exact head 派生 body scope/evidence，避免自引用。除此 Stage 外不得修改 release contract。
- `Evidence-Invalidation` 至少包含 `head-change,scope-change,oracle-change`，并追加环境或依赖条件。
- `Rollback-Unit` 到 `develop` 为 `stage-squash`，到 `main` 为 `release-merge`。
- `Branch-Sunset` 对 topic branch 使用 ISO 日期；release 写 `not-applicable`。

治理器在以下任一阈值被超过时产生 warning：Included Issues 超过 3 个、source/test 文件超过 30 个、非派生证据变更超过 1500 LOC。case source（包括 reference、external seed、scene、assets、regions 与相应 manifest/spec）始终计入；只有 policy 明列的派生 QA/evidence 可豁免。`Scope Threshold Explanation` 必须逐项包含实际触发的 `included-issues`、`source-test-files`、`non-generated-loc` token，并始终给出有实质内容的 `Atomic-Outcome`、`Shared-Failure-Mechanism`、`Shared-Validation`、`Rollback-Reason`；泛化文字不能通过。

## 5. 风险与证据

| 等级 | 典型变更 | 最低要求 |
|---|---|---|
| `R0` | 不影响行为的说明或低风险维护 | Issue 闭环、静态检查 |
| `R1` | 受限功能、兼容性、测试或开发文档 | 全量离线 CI、影响面回归 |
| `R2` | 科学语义/保真、参考与哈希权威、转换/QA、治理或发布边界 | 独立复核、正反例、目标区域/结构证据、全量 CI；需要时执行 PowerPoint COM 门禁 |

任何 head 变化都会使 head-bound 测试和审核失效。Scope、reference/evaluation oracle、依赖版本或 PowerPoint 环境改变时，按 PR 的 Evidence-Invalidation 清单重跑相关证据。`Closure-State: ready` 只表示当前 Stage 的 Included Issues 达标，不会批准 Deferred Issues 或其他 `qa_failed` 案例。

## 6. 可信 workflow 与首次引导

`PR Governance` 使用 `pull_request_target`，按 event exact-head SHA 串行，先向该 SHA 写 pending，再 checkout owner-reviewed 默认 `main`；通过 REST API读取 PR diff、Issue、compare merge-base、历史 PR、全部 open PR、该 SHA 的完整 status history 与 base branch 的 active remote rules，并通过 Contents/Blobs API只读解析 exact-head contract。base/head revision 绝不 checkout、fetch、安装或运行。final success 前再次要求该 SHA 只属于当前 open PR，旧事件不会把结果写到 synchronize 后的新 SHA。可信 `issues` workflow 对 edited、label/state、transfer 等变更扫描全部 open 及 merged-unreceipted governed PR；open PR 使用 exact-head concurrency，已合并 PR 使用 lifecycle PR concurrency，并写入不可在同 SHA 覆盖的 policy failure。finalizer 还直接校验 Node ID、lastEditedAt、标签哈希和完整受管事件游标，因此延迟 webhook、编辑后改回或标签加后删除也不能绕过。恢复必须提交更新后的 snapshots 形成新 head并重审，评论除外。由于 commit status 仍是仓库+SHA 共享且 API 不提供原子条件写，远端规则还必须提供 PR-specific CODEOWNER/last-push approval；strict GitHub-App-bound required status checks 在 base 前进时直接阻止旧 success 合并。规则缺失是机器可读 rollout blocker。生命周期 workflow 同样始终执行 `main` 版本，并把 sunset 审计（只读 Issue/PR、仅写 status）与 merged finalizer（写 Issue/ref/status）拆成独立 job；finalizer 不依赖合并后可编辑 body。`schedule` 没有 `pull_request` payload，监督器会分页查询 open/closed PR；任何 API、分页、完整复验或 mutation 读回缺失都 fail closed。

这些 workflow 只有进入默认分支 `main` 后才可信生效。`codex/governance-bootstrap-v1` 首次引导必须由外部 Reviewer 对固定 commit SHA、workflow diff 和离线测试做人工审核；在发布到 `main`、远端 required checks/ruleset 配置完成且真实 Bot PR 的 `user.login/type` 被观测并写入非空 allowlist 前，状态是 rollout blocked，不能声称自动治理 PASS。`Implementation-Agent` 目前只是声明字段，自动检查只能验证它与 Bot 字符串不同，真实身份由人工复核。

## 7. 本地验证

使用项目 `.venv`，测试临时目录放在受控外部 basetemp：

```powershell
.venv\Scripts\python -m pytest tests -q --basetemp "$env:TEMP\autofigure-pytest"
.venv\Scripts\python -m ruff check tools tests .github/scripts
.venv\Scripts\python -m compileall -q tools tests .github/scripts
.venv\Scripts\python -m tools cases --check
.venv\Scripts\python -m tools hygiene
python .github\scripts\check_pr_governance.py --self-test
python -m pytest tests\test_pr_governance.py tests\test_branch_lifecycle.py -q
```

分层测试、案例回归和发布要求见 [docs/TESTING.md](docs/TESTING.md)、[docs/CASE_REGRESSION_POLICY.md](docs/CASE_REGRESSION_POLICY.md) 与 [docs/DEVELOP_TO_MAIN_RELEASE.md](docs/DEVELOP_TO_MAIN_RELEASE.md)。
