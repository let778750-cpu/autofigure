# 开发与分支治理

## 模块 Epic 与版本化 Stage

```text
Module Epic #42（Issue；模块目标与 Stage DAG）
  ├─ dna-oracle@v1
  │    └─ codex/dna-oracle-v1 ── squash/delete ──────────┐
  ├─ corrector@v1                                       ├─→ develop
  │    └─ codex/corrector-v1 ── squash/delete ───────────┘
  └─ R2 例外：codex/integration-cross-route-v1
                    └─ 最多 14 天 ── squash/delete ─────→ develop

develop ── same-repository release PR + merge commit ──→ main
                                                        └─ @let778750-cpu 审批
```

只有 `main`、`develop` 是常驻分支。Epic 是模块级 Issue；Stage 是可独立验收、证据失效和回滚的版本单元；branch 只是 Stage 的短期运输载体。

## Stage 生命周期

1. **Plan**：Epic 冻结目标与非目标，Stage 记录 Included/Deferred Issues、依赖、验收、风险和 rollback unit。
2. **Branch**：从最新 `develop` 创建 `codex/<stage-slug>-vN`；Epic 只以 `#N` 写入 PR metadata，普通分支的 Stage slug/版本必须与 metadata 一致。
3. **Freeze**：`Scope-Freeze` 绑定范围提交；范围新增、Issue 迁移或验收重写要求新 Stage 版本。
4. **Implement**：实现和证据都只服务当前 Stage，避免把整个 Epic 一次性堆进同一 PR。
5. **Review**：allowlist 中的独立 Bot 创建 PR；Reviewer 检查 head-bound evidence、失效条件、反例和 Deferred Issues。`Implementation-Agent` 身份仍需人工复核。
6. **Integrate**：以 squash 合入 `develop`，squash commit 是 Stage rollback unit；可信 finalizer 通过 API 关闭完整 Included 集合。
7. **Sunset**：finalizer 仅在 ref SHA 仍等于 merged head 时删除 topic branch。同名分支出现在任何更早 PR 后都不能用于新 PR；closed-unmerged branch 保留、冻结并在确认废弃后人工删除。

治理器分页查询相同 head branch 的全部历史 PR；当前 PR number 被排除，因此 reopen 可继续，而任何更早 open/closed/merged PR 都阻止复用。PR diff、Issue、GitHub compare merge-base 与分支历史只通过 GitHub REST API读取。Actions 中缺少 Token、分页到上限、Issue/label/Parent Epic 不匹配或 API失败，都属于缺失证据并阻断。

## Head-bound authority contract

每个 PR 必须在自己的 diff 中新增或修改唯一 JSON authority contract；Schema 见 `.github/governance-contract.schema.json`。普通和 integration Stage 的路径只能由 branch 推导为 `.github/stage-contracts/<stage-slug>-vN.json`；release 的 diff 必须恰有一个 `.github/release-contracts/release-vN.json`，且文件内 `Stage` 与版本路径一致。合同绑定 `repository`、`pr_number`、`base_ref`、冻结的 `base_sha`、`head_ref`、Epic/Stage、Included/Deferred、scope、sunset、rollback、workstream、scientific mode、证据失效与 scope-threshold 理由。`issue_snapshots` 还必须按 Issue number 排序，用唯一对象逐个绑定 exact `number`、`role`（`epic`/`included`/`deferred`）、`title_sha256` 和 `body_sha256`；Stage 集合必须等于一个 Epic 加完整 Included/Deferred 集合，release 集合必须等于完整 Included/Deferred 集合。评论不属于范围合同，因此不绑定会随评论变化的 `updated_at`。

Stage 校验只对 Stage contract namespace 计唯一性；release 校验只对 release contract namespace 计唯一性，因此 `main...develop` 中累积的历史 Stage contracts 不会让 release 永久失败。只有 `codex/release-preparation-vK` 可在同一个 PR 中携带一个 `release-vR` contract；准备尝试版本 K 与发布版本 R 相互独立。它必须绑定已经存在的同仓 open Draft release PR。`develop` 前进时用新的 K 更新同一个 `release-vR`，不得累积第二份 release contract。release contract 用 `scope_freeze: current-head` 声明 exact-head 派生规则，body 才展开为 `scope@SHA`，避免文件包含自身 commit SHA。普通 Stage 修改 release contract会被拒绝。不得伪造未来 PR number；首次 bootstrap 只允许用户对固定 SHA/tree 作一次性外部人工例外。

可信 checker 先向 current head 写 pending status，再 checkout owner-reviewed `main`；随后只通过 GitHub Contents 与 Git Blobs API 从 exact current head 读取合同，交叉核对 blob 字节，并按严格 Schema 解析为 inert JSON，绝不执行 PR 内容。PR metadata 是合同的逐字段可读镜像；`Evidence-Baseline` 由 current head SHA 派生，避免合同自引用。snapshot/final step 会比较 body SHA-256，仅用于防止镜像在一次检查中发生 TOCTOU 编辑；该 digest 不进入 authority、owner approval 或 finalizer。编辑 PR body 可以让镜像检查失败，但不能改变合同权威、Issue 关闭集合或 rollback unit。

commit status 以仓库和 SHA 为键，不以 PR number 为键。workflow 的 concurrency 因而使用事件携带的 exact head SHA，而不是 PR number；旧 `edited` 事件发现 branch 已被 `synchronize` 到新 SHA 时只使旧 SHA 失败，绝不改写新 SHA 的 status。checker 与 final success step 都分页读取仓库全部 open PR，final step 还会重新取证并要求该 SHA 此刻只属于当前 open PR。只要另一个 open PR 共享 SHA，所有相关 PR 都 fail closed。此串行与双重取证缩小竞态窗口，但 GitHub status API 没有“仅当仍唯一”条件写，不能提供跨 PR 的原子比较写入；真正的 PR-specific 屏障是远端 active rules 中的独立审批、CODEOWNER、dismiss-stale-review 与 last-push approval。合同中的 `pr_number`、`head_ref`、`base_ref` 进一步阻止同一 blob 被跨 PR/分支解释。

默认 `main` 上的 `issues` 事件 workflow 覆盖 edited、labeled/unlabeled、closed/reopened、transferred 等 Issue 级变更。它先把唯一 `### Primary Area` 幂等同步为七个规范 area 标签之一，再分页扫描 open 与 merged-unreceipted governed PR；只 checkout 可信默认分支，并通过 Contents/Blobs API 把 PR exact head 的 Stage contract（release-preparation 还包括其 release contract）作为 inert JSON 读取。若变更 Issue 被任一 snapshot 引用，或合同无法证明不引用，invalidator 会在该 exact head 的 `pr-governance/policy` 写带保留前缀的 failure。open PR 写入使用 repository+SHA concurrency，merged-unreceipted 使用 lifecycle PR concurrency，并取消仍在飞行的旧 validator/finalizer。checker/finalizer 还直接核对 snapshot 的 Node ID、GraphQL `lastEditedAt`、title/body/labels SHA-256 和完整 managed-event cursor，所以 webhook 延迟、编辑后改回或标签加后删除也不能覆盖 poison。必须提交新 snapshot/contract 形成新 head并重审；评论不会触发该流程。

`base_sha` 的本地合同检查也不能单独阻止旧 success：`develop` 在检查后前进时，status 仍挂在未变化的 topic head。每次 policy 检查都通过 `GET /repos/{owner}/{repo}/rules/branches/{base}` 读取 GitHub 当前生效规则；缺少 GitHub-App-bound `pr-governance/policy`、`strict_required_status_checks_policy=true` 或 PR-specific review rule 即产生 `remote rollout blocker`，不会发布 success。strict required checks 是合并时门禁，要求 topic branch 先基于最新 base 再重跑；不是合并后的审计。

develop merged finalizer 不读取合并后可编辑的 PR body。它从 immutable `merge_commit_sha` tree 读取同一路径合同，要求 merge commit 恰有一个 parent 且 `parent.sha == contract.base_sha`，并分页读取每个 Issue 的完整 events 历史来重建 merge 时刻的 open/closed 状态；当前 `state/closed_at` 快照不能替代历史证据。timeline 注入只补充 merge-time state，不能改写合同：finalizer 仍把当前 API 返回的原始 title/body 与 immutable `issue_snapshots` 哈希比较，Issue 内容漂移即停止。完整复验后才按合同的 `included_issues` 关闭 Issue，关闭态必须是 `state_reason=completed`，且 latest close event 的 actor 必须是 policy 中的 trusted finalizer。随后只删除仍精确指向原 PR head 的 topic ref；DELETE 竞争产生的 404 只有在 ref 精确读回不存在时才算幂等成功。

全部 Issue/ref 动作成功后，finalizer 创建 lightweight tag `autofigure-finalized/pr-<PR#>`，tag 必须精确指向 immutable `merge_commit_sha`。它在 mutation 前、receipt 前和 receipt 后三次复读完整 Issue ledger 与原 head poison；最后瞬间冲突会向原 head持久写 failure，并把已关闭 Included Issues 恢复为 open，使已有 receipt 明确非终态。下一次 closed-event 或定点重试先验证 poison，再验证远端 tag ruleset 与 exact target；receipt 已存在便停止，不会重关 rollback 后 reopened 的 Issue，也不会重删同名 ref。receipt namespace 的 ruleset 必须限制 creation/update/deletion、仅允许已核验的 finalizer Integration bypass，代码只 POST 新 ref、从不更新或删除 receipt。GitHub 对只有 Metadata-read 的 workflow token 隐藏 `bypass_actors`；因此 bootstrap 管理员必须把完整 actor 列表、ruleset id、`updated_at` 与 canonical attestation SHA-256 固定进受审 policy，runtime 再核对 exact id/`updated_at`/namespace/rules。任一远端编辑都会改变 `updated_at` 并 fail closed；不得为读取 actor 给 finalizer Administration-write。远端 ruleset/Integration id 未取证时 policy 保持 rollout blocked。这样多提交 rebase/fast-forward 会因父提交不是冻结 base 而失败；远端仍须禁用 rebase merge并保留合并 actor/ruleset 核验。

## Scope Freeze、Evidence 与 Rollback

- 普通 Stage/integration 的 `scope@SHA` 必须精确等于 PR `base.sha...head.sha` 的 GitHub compare `merge_base_commit.sha`，即真实 branch-point；不能使用 head 的任意旧祖先。新增范围应创建 `stage@v(N+1)`，而不是编辑一个持续扩张的 Stage。
- `head@SHA` 表示测试、报告、Reviewer 结论只对当前 head 有效；push 新提交后必须更新该字段并重跑证据。
- reference、evaluation oracle、scene、Schema、依赖或执行环境变化时，按 Evidence-Invalidation 重新采集受影响证据。
- 回滚到 `develop` 以完整 Stage squash 为单位。回滚要重新打开对应 Included Issues，标记旧证据失效，并用新版本 Stage 交付替代方案。

## Integration 例外

Integration 分支只用于不可安全拆分的连通 R2 集成验证。它必须：

- 使用 `codex/integration-<stage-slug>-vN`，base 为 `develop`，PR-Type/Risk 为 `integration`/`R2`；slug 与版本都匹配 Stage，Epic Issue 号不进入 path；
- 在正文说明普通 codex Stage 不足的技术原因、受保护对象、退出与删除计划；
- `Branch-Sunset` 不晚于 PR 创建后第 14 天，且每次 head/scope/oracle 变化都重跑证据；PR 事件和每日 schedule 刷新 required `branch-lifecycle/sunset`，过期标失败但不自动关闭；
- 以单一 squash 进入 `develop` 后删除，不成为其他 Epic 的基线或长期汇总线。

## 独立 Bot 契约

PR 作者的 GitHub账号类型必须为 `Bot`，login 必须出现在非空 allowlist，并与 `Implementation-Agent` 不同。当前 allowlist 故意为空，候选 `chatgpt-codex-connector[bot]` 仅作 expected 值；必须先从真实 Bot PR 的 API `user.login/type` 取证后才能加入。空 allowlist 是 rollout block，不允许任意 Bot。Bot 没有科学批准权，也不能替代 `main` 用户审批；`Implementation-Agent` 的真实性是人工检查项。

## GitHub 规则配置

### `develop`

- active rules 必须要求 PR、至少一次独立批准、dismiss stale reviews、CODEOWNER、last-push approval、解决所有会话、更新分支，并以 strict、GitHub-App-bound 方式要求 `pr-governance/policy`、`portable-tests (ubuntu-latest, py3.12)`、`portable-tests (windows-latest, py3.12)`、`case-contracts` 与 `branch-lifecycle/sunset`。
- 仅接受 `codex/<stage-slug>-vN` 或带 14 天 sunset 的 R2 `codex/integration-<stage-slug>-vN`；使用 squash merge。
- 要求 `branch-lifecycle/sunset`；合并后 finalizer 关闭 Included Issues 并验证删除 exact merged ref。阻止 direct/force push。
- 不允许把任何更早 PR 使用过的 branch name 再用于新 PR；closed-unmerged branch 只冻结，不自动删除。

### `main`

- 只接受同仓库 exact `develop` 的 release PR，active rules 同样要求 strict status 与 PR-specific review，并以 GitHub-App-bound 方式要求 `pr-governance/policy`、`pr-governance/main-owner-approval`、两条 `portable-tests (...)` 与 `case-contracts`；`main` 不要求 `branch-lifecycle/sunset`。
- `@let778750-cpu` 的最新 `APPROVED` 必须绑定当前 head；该 head 已内容寻址地绑定 release contract。PR body digest 不再是安全权威。head 变化使旧批准失效；body 镜像变化由 policy 检查提示并需修正。批准后添加或切换 `governance:recheck` label（人工 rerun 也可），由可信 `pull_request_target` 重验。
- 使用 merge commit，阻止 direct/force push 和分支删除。

active `develop` rule 只允许 squash，active `main` rule 只允许 merge；两者都必须有 `non_fast_forward` 与 `deletion`，配合 `pull_request` rule 阻止 force push、常驻分支删除与 direct push。检查器从 applied-rules API 机器验证这些 ref guard、merge method、strict status 与 PR review 参数；任一规则缺失、处于 evaluate/disabled 或 status 未绑定 GitHub App 都保持 rollout blocked。该仓库是个人仓库，不把仅组织仓库可用的 merge queue 当作安全解。applied-rules 只证明当前生效规则；ruleset bypass actors 仍须用有管理读取权限的外部 bootstrap/release 审核确认没有未授权 bypass，合并后仍核对 `develop` 的单亲 squash 或 `main` 的双亲 release merge。

## Trusted workflow bootstrap

`pull_request_target` workflow 定义与执行代码都来自 owner-reviewed 默认 `main`；PR Governance 和生命周期 job 始终 checkout default branch，PR 的 base/head/body/diff 只作 API 数据，禁止 fetch/checkout/run 任何 PR revision 或下载执行 head artifact。pending status 在 checkout 前写入；policy success 只在 event head 仍是 current head、该 SHA 仍唯一属于当前 open PR、contract blob、Schema、镜像、active remote rules 与 API 证据全部通过后写回同一个 event head。由于该事件自身的 Actions check 绑定默认分支 SHA，可信 workflow 必须显式向 API current PR head 写 `pr-governance/policy`；main release 另写 `pr-governance/main-owner-approval`。远端 ruleset 只要求这些 current-head status context，并限制为受信 GitHub Actions App 来源，不能把默认分支上的 workflow job 名误配为 PR required check。治理政策只有经 `develop → main` release 审核后才生效。生命周期的只读 sunset audit 与拥有 Issue/ref 写权限的 finalizer 分 job；每日 `schedule` 只分页审计 open PR 的 sunset，不扫描或重放 merged PR。finalizer 只处理对应 `closed` event；异常恢复必须人工 `workflow_dispatch` 并填写唯一 canonical `pr_number`，同一 PR 的 event/retry 共享 concurrency group。release-preparation 在 receipt 完成前要求 target PR 当前仍 open+Draft，并同时用分页 events 证明它在 preparation merge 时也是 open+Draft，manual retry 不能降级此门禁。首次 `codex/governance-bootstrap-v1` 尚不能由它正在引入的 workflow 自证：外部 Reviewer 必须对固定 commit SHA、完整 tree、合同迁移和离线测试做独立审查，先以受审 release 进入 `main`，再配置并通过 API 读回 strict status、PR-specific review、receipt tag ruleset 与无未授权 bypass 的远端规则。此前任何“治理全绿”只能记为 manual external review，不能记自动 PASS。

## 状态与权威边界

- Canonical Scene、reference hash、bindings、保存重开证据和 QA 报告保持各自权威范围。
- runner 成功只证明对应检查执行；证据缺失为 `INCONCLUSIVE` 或 `FAIL`。
- source/backend 自洽不能替代 reference-bound 保真；全图均值不能覆盖 critical region、拓扑或可编辑性 blocker。
- Epic、Stage、branch、develop 与 main 都不自动把案例状态改成 `approved`。
