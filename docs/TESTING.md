# 测试与证据门禁

“测试完全通过”指当前 Stage 所需检查全部绿色、Included Issues 的机械验收满足、没有新增回归，且 Deferred Issues 与范围外失败被明确列出。它不是用一个平均分替代局部或结构审查。

## 1. 可移植 CI

GitHub-hosted Ubuntu 与 Windows runner 执行：

```text
python -m ruff check tools tests .github/scripts
python -m compileall -q tools tests .github/scripts
python -m pytest tests -q
```

Ubuntu 安装除 Windows-only `pywin32` 外的相同依赖。测试不能依赖活动 PowerPoint 窗口、OCR 下载、GUI 点击或开发者机器绝对路径；需要 Office Math 的用例按能力探测显式 skip。

## 2. 治理与仓库合同

```text
python -m tools cases --check
python -m tools hygiene
python .github/scripts/check_pr_governance.py --self-test
python -m pytest tests/test_pr_governance.py tests/test_branch_lifecycle.py -q
```

治理测试至少覆盖：

- branch/Stage 唯一推导 authority contract 路径，contract 必须出现在 changed files；Contents/Blobs API 字节一致、严格 Schema、`pr_number`/refs/路径/current-head ref 绑定任一错误均失败；
- release 的唯一性只统计 release contracts、忽略发布 diff 中累积的历史 Stage contracts；两阶段 authoring 要求先有 open Draft release PR，再由 `release-preparation-vK` Stage 同时携带自身合同与绑定真实 release PR number 的 `release-vR` contract；K/R 独立，develop 前进时新 K 更新同一 release-vR，普通 Stage 不能偷写或累积 release contract；
- PR body metadata 必须逐字段镜像 contract，但 body 不是 authority；合并后篡改 body 不能改变或阻断 immutable contract 的 Included Issue 关闭集合；
- 共享同一 head SHA 的任意两个 open PR 都失败，避免 SHA 级 status context 跨 PR 污染；
- Epic 仅由 PR metadata 绑定，普通 Stage 与 `codex/<stage-slug>-vN` 的 slug/版本一致；
- `codex/integration-<stage-slug>-vN` 只允许 R2、slug/版本与 Stage 一致且 sunset 不超过 14 天；
- topic branch 的分页历史中任何更早 PR 都阻止新 PR 复用，同 PR reopen 因 number 相同而允许；
- 普通 Stage/integration 的 `scope@SHA` 精确等于 GitHub compare merge-base（真实 branch-point），任意旧祖先会失败；`head@SHA` 等于当前 head；release scope 精确等于 develop/head；
- Included/Deferred 是严格 Issue 列表、无自由文本且不重叠；Epic label、Included existence 与 `Parent Epic` 由 API fail closed 校验；
- 每个 Epic/Included/Deferred snapshot 精确绑定 Node ID、GraphQL `lastEditedAt`、title/body/labels SHA-256 与 managed-event cursor；编辑后改回、label add/remove、transfer、延迟 webhook 和不可信 close/reopen 都必须失败；
- develop merged finalizer 从 `merge_commit_sha` tree 读取 contract，验证唯一 parent 恰等于不可变 `contract.base_sha`（多提交 rebase/fast-forward 失败），分页读取 Issue events 重建 merge-time 状态，只接受 `completed` + trusted latest-close provenance，并只删除 SHA 未前进的 merged topic ref；closed-unmerged 分支保留冻结；
- release-preparation 在 receipt 完成前要求目标 R 当前仍 open+Draft，同时从完整 events 历史重建 merge-time open+Draft；同秒状态转换因无法排序而 fail closed，manual retry 不得使用当前快照或 `closed_at` 降级；
- 全部 Issue/ref 动作成功后创建 `autofigure-finalized/pr-N` lightweight tag 并验证 exact merge target；远端 ruleset 必须覆盖 exact namespace 的 creation/update/deletion 且只有 trusted Integration bypass。管理员取证的 actor 列表、ruleset id/updated_at 与 attestation digest 必须和 policy 闭合；Metadata-read API 隐藏 actor 时仍逐次核对 id/updated_at，任一远端编辑即失败。422/DELETE 404 只能在 exact tag/ref 回读后视为幂等成功，receipt 后 reopen 不触发旧 PR 重关；
- finalizer 在 mutation 前、receipt 前和 receipt 后复读 ledger/status；最后瞬间冲突必须向 original head 持久写 poison 并恢复 Included Issues为 open，使已有 receipt 明确不可作为终态；
- `pull_request_target`/schedule/workflow_dispatch 三种 payload 路径均不 checkout 或执行 PR head；schedule 只审计 open PR sunset，finalize 仅处理 closed event 或带 canonical `pr_number` 的定点 retry；
- PR workflow concurrency 使用 event exact-head SHA；旧 `edited` 与新 `synchronize` 事件不能互相改写新 head，final success 前重新分页确认该 SHA 只对应当前 open PR；
- applied remote rules 缺少 policy 中按 base 列出的任一 strict GitHub-App-bound status、PR-specific CODEOWNER/last-push/stale-review/thread-resolution approval、`non_fast_forward`、`deletion` 或目标分支唯一允许的 merge method 时，产生 `remote rollout blocker`；
- Stage rollback unit、release rollback unit 与 base branch 一致；
- 三类 scope threshold 产生 warning，解释必须覆盖每个实际触发 token，以及 `Atomic-Outcome`、`Shared-Failure-Mechanism`、`Shared-Validation`、`Rollback-Reason` 四个实质字段；
- 仅明列的派生 QA/evidence 不计入 1500 LOC；reference、external seed、scene、assets、regions、spec/manifest 等 case source 始终计入；
- 空 Bot allowlist、API/分页缺失、虚构 Bot login 均 fail closed。

`PR Governance` 始终在 owner-reviewed 默认 `main` 上执行标准库 checker，先向 event exact head 写 pending，再 checkout；PR 文件只从 Pull Files API读取，authority contract 只从 exact-head Contents/Blobs API读取，rename 的旧路径也进入风险判断。snapshot/final body SHA-256 只做镜像 TOCTOU 检测，不参与 authority 或 owner approval。checker 还从 applied-rules API 读取 GitHub 当前生效规则：strict required checks 解决 base 前进后的 stale success，PR-specific review 解决 commit status 不是 PR-specific 的边界。缺失或 API 不完整一律失败。生命周期 workflow 也只运行 `main` 代码：audit job 仅写 status，finalizer job 从 immutable merge tree contract 和分页历史完整复验后才关闭 Included Issues、删除 exact ref、创建受保护 receipt。schedule payload 没有 `pull_request`，只通过 API 分页监督 open develop PR；merged PR 的异常恢复必须用显式 PR number 定点触发，任何写操作都随后读回验证。

## 3. Evidence invalidation

证据按 Stage 和 revision 绑定。以下变化至少触发对应重跑：

| 变化 | 失效证据 |
|---|---|
| PR head 变化 | 全部 head-bound CI、hash、Reviewer approval |
| PR body 变化 | 可读镜像重新校验；不改变 head-bound contract authority 或 finalizer 关闭集合 |
| Included/Deferred Issues、验收或非目标变化 | Scope Freeze；创建新 Stage 版本 |
| reference/evaluation oracle 或 critical regions 变化 | 区域指标、结构 gate、全图/裁剪审查 |
| scene/compiler/Schema 变化 | source→backend bindings、保存重开、兼容与 mutation tests |
| PowerPoint/字体/Office 环境变化 | COM fresh render、native readback、目标尺寸证据 |
| Integration sunset 到期 | 整个 integration exception；停止合并并重新规划 |

PR 的 `Evidence-Baseline` 必须精确等于当前 40 字符 head SHA。旧报告可作为历史证据保留，但不能支撑新 head 的 `ready`。

## 4. R2 科学保真验证

R2 证据包至少包含：

- reference、candidate、scene、PPTX、render 与报告 SHA-256；
- 受影响 critical region 的目标尺寸裁剪和 reference-bound 指标；
- 语义、拓扑、几何、微资产、文字、箭头、bindings 和可编辑性结果；
- 源模型与保存重开对象的分层诊断，不把内部自洽写成参考保真；
- 至少一个反例或 mutation test；
- 同一 reference 的两条路线各自独立候选与共同 evaluation oracle 的一致性证据。

Included Issues 的区域或结构 blocker 必须为零。Deferred Issues 可以保持独立 `qa_failed`，但要证明没有恶化并在 PR 中引用。

## 5. PowerPoint COM 条件门禁

普通 GitHub-hosted runner 没有受信任桌面 Office 会话，不会声称完成 PowerPoint 保存重开、fresh render 或 native Math/shape 复核。需要这些能力的 R2 Stage 人工触发 `CI`：

1. 设置 `run_powerpoint_live=true` 并提供 `examples/` 下案例目录。
2. `scientific-release` environment 由配置 Reviewer 授权。
3. `self-hosted, Windows, X64, powerpoint` runner 执行 COM fresh render。
4. 同一 head 执行 `strict --require-live` 并上传 fresh render 与 check report。

截图、缓存 PNG 或 detached JSON 不能冒充 fresh COM 证据。runner 或证据缺失时结论为 `INCONCLUSIVE`，Stage/release 保持阻塞。

## 6. 人工审核

独立 Reviewer 对当前 hash 的目标尺寸全图和局部裁剪做新鲜审查。进入 `main` 前，`@let778750-cpu` 对当前 release head 明确批准；current head 内容寻址绑定 release contract，不依赖可变 PR body digest。之后添加/切换 `governance:recheck` label 或人工 rerun。可信 job 显式向 exact head 写 policy/owner status；head、scope、oracle 或合同变化后重新审核，body 镜像变化必须修正但不能改变 authority。

首次 bootstrap workflow 还不在默认 `main`，不能自我证明。必须对 `codex/governance-bootstrap-v1` 固定 commit SHA 做外部人工审查并记录测试输出；发布到 `main`、配置 required checks/ruleset、观测真实 Bot `user.login/type` 并填充 allowlist 后，自动门禁才可被视为可信。
