# develop → main 发布合同

## 唯一路径

只有 `main` 与 `develop` 常驻。普通 `codex/<stage-slug>-vN` 和 R2 `codex/integration-<stage-slug>-vN` Stage 都以 squash 进入 `develop`；可信 finalizer 关闭 Included Issues，并在 ref 仍等于 merged head 后删除 branch。Epic 只由 PR metadata 绑定。`main` 只接受同仓库 exact `develop`、`PR-Type: release` 的 PR，并以 merge commit 建立发布边界。

release PR 由独立 Bot 创建。紧急变更仍建立 Epic/Stage、进入 `develop`，不能直接从 topic、fork、临时汇总或重建 release branch 进入 `main`。

Release contract 通过受审的两阶段准备协议取得真实 PR number，同时避免把 commit SHA 写入它自身：

1. 从当前 `develop@H0` 先创建 Draft release PR `R`，取得真实 `R#`；在合同尚未进入 `develop` 时，R 的治理检查预期 fail closed。
2. 从同一最新 `develop@H0` 创建并先以 Draft 打开 `codex/release-preparation-vK` Stage PR `S`，取得真实 `S#`。随后一次提交 S 的 `.github/stage-contracts/release-preparation-vK.json`（绑定 S#）和本次发布固定的 `.github/release-contracts/release-vR.json`（绑定已存在的 R#，并记录 `release_preparation: {pr_number: S#, base_sha: H0}`）。`K` 是准备尝试版本，`R` 是发布版本，两者不要求相等。
3. S 的 policy 与 merged finalizer 都验证：R 同仓、open、draft、exact `develop → main`；两个合同路径/版本、PR number、refs、Schema 和 head/merge-tree blob 闭合。finalizer 分页读取 R 的 events，既证明它在 S merge 时为 open+Draft，也要求 receipt 完成前当前仍为 open+Draft；closed event 与人工定点 retry 使用同一门禁，普通 Stage 不能写 release contract。
4. S squash 进入 `develop@H1` 后，R 自动前进到 H1。release contract 使用非自引用的 `scope_freeze: current-head`；治理器要求 R current head 恰等于 S 的 `merge_commit_sha`。S 的 Issue/ref finalization 全部成功后才创建 `autofigure-finalized/pr-S#` lightweight tag receipt，且该 tag 必须精确指向 H1；receipt tag ruleset 未经 API 核验时保持 fail closed。
5. receipt 完成后，Bot 才更新 R 的可读镜像、转 ready 并重跑 policy/CI；R body 的 `Scope-Freeze: scope@H1` 与 `Evidence-Baseline: head@H1` 由该 exact head 派生，用户只批准 H1。任何 H2 都使 H1 审批失效；必须由新的 `release-preparation-v(K+1)` 更新同一个 `release-vR.json` 并重新绑定真实 S#、base SHA 和新 merge head，不能累积第二份 release contract。旧 preparation receipt 保持不可变，K+1 以新 PR/new tag supersede，旧 finalizer 不会因 schedule 或 rollback reopen 被重放。

不得伪造 PR number、direct push 或把 H0 当 H1。首次安装治理的 bootstrap 仍是唯一人工例外：用户对固定 commit SHA 和完整 tree 做外部审核后执行一次性发布；它不能自称通过正在安装的自动门禁。

## 创建 release PR 前

- `develop` 已包含本批次目标 Stage squash，每个 squash 都能追溯 Epic、Stage、Included/Deferred Issues、scope freeze、证据和 rollback unit。
- release contract 的 `issue_snapshots` 对每个 Included/Deferred Issue 绑定 exact number/role/title/body SHA-256；release 的 `Epic: release` 不是 Issue，不能伪造 `epic` snapshot。
- 所有已合入 topic branch 均按 sunset 完成清理；没有 branch 在 squash 后继续增长或被复用。
- Ubuntu/Windows CI、案例合同、治理检查、影响面回归和所需 PowerPoint COM 证据全部绿色。
- Included Issues 的范围内 blocker 为零；Deferred Issues 与范围外失败有独立记录和无恶化证据。
- 所有 integration 例外已经退出并删除，或仍在有效 sunset 内但不被偷渡到 release 之外的范围。
- 文档、Schema、状态、风险与实际 diff 一致，`develop` 工作树和 `main...develop` 差异没有未解释文件。

## release Scope、Evidence 与 Rollback

Release metadata 使用：

- `Authority-Contract: .github/release-contracts/release-vN.json`；release diff 中必须恰有这一份版本化合同，文件绑定真实 PR number、`head_ref: develop`、`base_ref: main` 和全部发布语义；
- `Epic: release`、`Stage: release@vN`；
- JSON contract 使用 `scope_freeze: current-head` 避免自引用；body 镜像的 `Scope-Freeze: scope@<develop SHA>` 由 exact current head 派生并必须等于该 SHA；
- `Evidence-Baseline: head@<同一 develop SHA>` 绑定检查和审批；
- `Branch-Sunset: not-applicable`，因为 develop 是常驻集成线；
- `Rollback-Unit: release-merge`。

若 `develop` 在审查期间前进，release head、contract、scope、CI、证据和审批全部失效。Bot 必须更新 release contract 及其 PR body 镜像、重跑检查并请求 `@let778750-cpu` 审核新 head；不能把旧批准解释为覆盖自动扩大的 release。任一被引用 Issue 的 title/body、label、open/closed 状态或归属发生变更时，可信 Issue invalidator 会永久标记旧 head 失败；即使把文字改回，也必须由新的 release-preparation Stage 在新 head 重建 snapshots 并重审。Issue 评论不参与 snapshot。单独编辑 PR body 只会造成镜像不一致，不会改变合同 authority。

Release merge commit 是 `main` 的审计/回滚边界，Stage squash 是 `develop` 的问题隔离单元。发现单个 Stage 有问题时，先通过新版本 rollback Stage 在 `develop` 撤销该 squash，再走新的 release；严重整批问题也要建立受审查的回滚 Stage/release，不能直接改写 `main` 历史。

## release PR 内容

PR 必须列出：

- `develop` 当前 SHA，相对 `main` 的完整 Stage squash/PR 清单；
- 每个 Epic/Stage 的 Included/Deferred Issues、风险、验收和回滚单位；
- required checks、R2 证据包、PowerPoint 条件门禁和所有 evidence invalidation 条件；
- Schema/CLI/状态兼容影响、迁移方式、剩余风险与非声明；
- 独立 Bot、实现者、Reviewer、责任人和最终用户审批的可追溯身份。

## 审批与合并

1. PR Governance 验证 exact `develop`、release metadata、scope/evidence 绑定、风险和证据完整性。
2. CI required checks 全部绿色，所有会话解决且没有冲突。
3. `@let778750-cpu` 对当前 head 提交 `APPROVED`，随后添加/切换 `governance:recheck` label（或人工 rerun）；检查验证 login、最新有效状态与 `commit_id`，并把结果写入 exact head 的 `pr-governance/main-owner-approval` status。current head 已通过 Git tree 内容寻址绑定 release contract，PR body digest 不参与安全决策。
4. 使用 **Create a merge commit**；不得 squash、rebase 或手工 cherry-pick release PR。
5. 合并后核对 `main` 顶端为预期双亲 release merge，Stage/Issue 清单与发布记录一致。

GitHub 账户层级不能按 base 限制合并方法时，只把 `main` 合并权限授予获准 release actor，并由它通过 merge API 选择 `merge`。CI 支持 `merge_group`，但 governance required checks 尚未配置前不得启用 merge queue。远端 actor/ruleset、receipt tag namespace 的 exact Integration bypass、non-empty verified Bot allowlist、sunset status 和合并后核验任一未启用时，治理保持 fail-closed rollout block。

首次治理 bootstrap 不能由它正在引入的 `pull_request_target` workflow 自证，因为 trusted workflow 必须先存在于默认 `main`。`codex/governance-bootstrap-v1` 必须由外部 Reviewer 对固定 commit SHA、workflow 权限/checkout/diff 和离线测试做人工审核；经 `develop` release 到 `main` 后再配置远端 required checks。此前不宣称自动治理 PASS。

`main` 表示用户接受的仓库基线，不自动等同于案例 `approved`、公开科研发布或无条件复用许可；这些结论仍由各自合同和证据决定。

## 发布异常

检查失败、审批缺失、head/scope/oracle 变化、冲突或证据不完整时，release PR 保持开放。问题通过新 Stage 在 `develop` 处理后再发布；不能改用临时分支绕开 exact-head 规则。
