# Autofigure 当前 dirty worktree 基线与可审核 PR 拆分

> 审计时间：2026-08-25T09:44:30+08:00
>
> 审计方式：只读；未修改、暂存、提交或推送源仓库
>
> 审计对象：本地原始工作树（路径不入仓库，也不得作为 provenance 权威）

## 1. 锁定的 Git 基线

| 项目 | 审计结果 |
|---|---|
| 当前分支 | `main` |
| `HEAD` / 本地 `main` / 本地跟踪的 `origin/main` | `513c47b845b56e0b9dcf634d9a9ea5929e01a2a3` |
| 另一条本地分支 | `codex/high-fidelity-v3` @ `3071b9b14a80b488abb9bddcfb8ad09310dc7781` |
| 暂存区 | 0 项 |
| 已跟踪但未暂存 | 169 项，全部为 ` M` |
| 未跟踪 | 263 项 |
| 删除 / 重命名 / 冲突 | 0 项 |
| 普通 porcelain 概览 | **271 个顶层条目**；Git 将未跟踪目录折叠，不可作为治理文件数 |
| `--untracked-files=all` 精确清单 | **432 个文件**；后续盘点、拆分与验收统一采用此口径 |
| 排序后的全量 porcelain 状态摘要 SHA-256 | `a6e1c229fb34e32c18100dc4d6bdfc43ca9e707d9028346c0d279e6c7bd70d84` |
| `develop` | 原始审计时点无本地或远程跟踪引用；远端 `develop` 已于本轮从 `main@513c47b...` 初始化，尚无治理或 dirty worktree 内容 |

因此，后续不得在这个 dirty 目录中直接切换分支、reset、stash 后删除或批量暂存。应在干净的辅助 worktree 中检出已初始化的远端 `develop`，再建立各 stacked feature branch；原目录保留为只读取材基线，直到全量清单中的 432 个文件都被归档、迁移或明确排除。

用户界面或普通 `git status --porcelain` 看到的“约 271 项”，实质是顶层条目计数：未跟踪目录只占一项。本文所有分类数字均来自 `git status --porcelain=v1 --untracked-files=all`，所以治理总数固定采用 432 个文件，而不是 271 个折叠条目。

`git diff --shortstat` 当前报告 165 个内容差异文件、65,660 行新增、6,722 行删除。它少于 169 个 tracked 状态项，因为以下 4 个 JSON 被 `status` 标为修改，但普通内容 diff 没有列出；提取 PR 前必须单独判断这是换行/过滤器状态还是实际内容变化，不能盲目加入：

- `examples/reference-only/01-modular-agent-reference-only/qa/powerpoint-live-case/assets/asset_manifest.json`
- `examples/reference-only/01-modular-agent-reference-only/qa/powerpoint-live-case/assets/evidence_provenance.json`
- `examples/svg-seeded/01-modular-agent/qa/powerpoint-live-case/assets/asset_manifest.json`
- `examples/svg-seeded/01-modular-agent/qa/powerpoint-live-case/assets/evidence_provenance.json`

此外，Git 已对大量 Markdown/Python/SVG 报告 LF→CRLF 风险。任何换行规范化都必须单独成 PR；不得让整文件换行噪声混入功能修复。

## 2. 432 项的精确构成

### 2.1 按审查性质

| 审查类别 | tracked | untracked | 合计 | 处理原则 |
|---|---:|---:|---:|---|
| 工具、测试、根级/参考文档 | 37 | 34 | **71** | 按功能纵切，源码与对应测试同 PR |
| 案例输入与冻结合同 | 13 | 16 | **29** | 只含参考/seed、prompt、scene、regions、assets |
| 生成的 lineage 元数据 | 12 | 9 | **21** | 与生成证据一起审，不与案例源码混合 |
| 生成的 QA、预览、PPTX/SVG 和比较报告 | 106 | 204 | **310** | 每案例一个 evidence PR、路线 lineage 独立；必须绑定源码和工具 SHA |
| `examples/README.md` | 1 | 0 | **1** | 最后由案例索引 PR 机械生成 |
| **总计** | **169** | **263** | **432** |  |

分类口径：

- 案例输入/合同：`reference.png`、`external-seed.svg`、`prompt.md`、`assets.json`、`regions.json`、`scene.json`。
- lineage 元数据：`bindings.json`、`provenance.json`、`run.json`。
- 生成证据/产物：`qa/**`、`check-report.md`、`preview.png`、`render.png`、`redraw.svg`、`redraw.pptx`、`route-comparison-*.{json,md}`。
- 263 个未跟踪文件合计约 14.59 MB，其中生成证据/产物约 11.65 MB。当前状态还包含 25 个 PNG 和 12 个 PPTX；这些二进制文件不能用普通文本 diff 代替来源与哈希审查。

### 2.2 按案例与路线

| 案例/路线 | 输入与合同 | lineage | QA/产物 | 比较报告 | 合计 |
|---|---:|---:|---:|---:|---:|
| 01 reference-only | 4 | 3 | 65 | 0 | 72 |
| 01 svg-seeded | 3 | 3 | 65 | 0 | 71 |
| 01 A/B comparison | 0 | 0 | 0 | 2 | 2 |
| **案例 01 小计** | **7** | **6** | **130** | **2** | **145** |
| 02 reference-only | 5 | 3 | 44 | 0 | 52 |
| 02 svg-seeded | 3 | 3 | 42 | 0 | 48 |
| **案例 02 小计** | **8** | **6** | **86** | **0** | **100** |
| 03 svg-seeded | 3 | 3 | 3 | 0 | 9 |
| **案例 03 小计** | **3** | **3** | **3** | **0** | **9** |
| 04 reference-only | 5 | 3 | 46 | 0 | 54 |
| 04 svg-seeded | 6 | 3 | 41 | 0 | 50 |
| 04 A/B comparison | 0 | 0 | 0 | 2 | 2 |
| **案例 04 小计** | **11** | **6** | **87** | **2** | **106** |
| `examples/README.md` | 0 | 0 | 1 | 0 | 1 |
| **examples 总计** | **29** | **21** | **307** | **4** | **361** |

这里的“QA/产物”数量合并了 tracked 与 untracked；它只是工作树盘点，不表示这些产物已通过质量门禁。

## 3. 强制隔离与排除规则

1. **`history/` 原文不上传。** 当前 3 个问题记录文件被 `.gitignore` 的 `/history/` 明确忽略，不计入 432 项。它们包含本地路径、过程性判断、时点状态和原始叙述；只从中提炼事实、哈希、可复现步骤、期望结果和验收条件写成 GitHub Issue，不能用 `git add -f history/**` 绕过策略。
2. **源码与生成物不得同 PR。** `tools/**`、`tests/**` 或案例的输入/冻结合同 PR，不得夹带 `qa/**`、PPTX、PNG、render、preview、diff 或比较报告。唯一例外是最小、稳定、经审查的测试 fixture，且 PR 必须说明为何不能程序化构造。
3. **每个案例使用一个 evidence Stage，路线 lineage 仍严格独立。** reference-only 与 svg-seeded 在同一个 `codex/case-<id>-evidence-v1` Stage 内审查，但不能共用候选 scene、SVG、PPTX、bindings、坐标或 QA 基线；每条路线都必须有自己的 source/compiler/revision/reference 哈希链。A/B comparison 只能在两条路线证据都落定后，由共享的 `codex/route-comparison-v1` Stage 重新生成。
4. **案例 04 当前生成物保持隔离，不作为完成结果合并。** DNA 双螺旋 reference-fidelity 门禁未通过，案例状态为 `qa_failed`；现有 95 个 lineage/QA/产物/比较文件属于当前基线诊断证据，不进入 canonical result 路径。只有 reference-bound 合同与 scene 满足目标 Issue 验收后，才从冻结源码生成 canonical evidence。若当前候选作为 anti-example 保留，必须进入明确的 anti-example/Issue 证据位置并维持失败状态标签，不能冒充可复用基线。
5. **生成证据必须可追溯。** 每个证据 PR 都要记录基底 source PR SHA、工具链 SHA、reference SHA、scene/revision SHA、生成命令、严格检查状态和 blocker；缺任一绑定就是 `INCONCLUSIVE`，不能写 PASS。
6. **派生物先做 retention 审计。** 能闭合 manifest、saved/reopened readback、backend inventory、target-size preview 和最终 QA 决策的证据应保留；缓存、重复副本、临时 build、可无损重建且不闭合任何清单链路的中间文件应留在 CI artifact 或本地，不进入 Git 历史。
7. **不混入换行清理。** `.gitattributes` 目前只强制 `*.json text eol=lf`；Markdown/Python/SVG 的换行政策若要补齐，单开低风险机械 PR并证明语义 diff 为零。

## 4. 推荐的 stacked PR 依赖顺序

所有分支均从干净 `develop` 或前一个已审 stacked PR 派生；每个 PR 必须能独立安装、导入、运行所列测试。共享热点文件用 hunk 级拆分；若拆后无法形成可运行中间态，则合并相邻两个功能 PR，不能提交一个已知红灯的过渡分支。

### `codex/governance-bootstrap-v1` — 在已初始化的干净 `develop` 上增加 GitHub 治理（不属于当前 432 项）

- 路径：未来的 `.github/**`、治理文档、CODEOWNERS、CI/ruleset 配置。
- 内容：以本轮已从 `main@513c47b...` 初始化的远端 `develop` 为基底；治理内容仍通过独立 feature PR 进入 `develop`。feature→develop 使用 squash；仅 develop→main 的 release PR 可进入 `main`；main 合并须用户审核。
- 风险：R2，错误的 base/head 或宽松保护会绕过审批边界。
- 验证：模拟 feature→develop 与 develop→main 两类 PR；验证 direct-to-main、force-push、未通过检查和未解决会话均被阻止。

### `codex/schema4-geometry-foundation-v1` — Canonical ArrowSpec、箭头组合与原生 primitive

- 主要路径：`tools/{arrow_spec,arrow_composition,arrow_visual,pptx_arrows,primitives}.py`，以及 `tools/{arrows,layout}.py`；`tools/{convert,live_bridge}.py` 仅暂存箭头/primitive 相关 hunks。
- 测试路径：`tests/test_{arrow_spec,arrow_composition,arrow_visual,pptx_arrows,primitives,arrows,layout}.py`，以及 `test_convert.py`、`test_live_bridge.py` 的对应 hunks。
- 风险：R2；影响 PPTX OOXML、箭头方向/箭头头型、几何和 readback，视觉正确但结构错误或结构正确但参考不忠实都必须失败。
- 定向测试：上述测试文件；增加反向箭头重叠、双向单对象、线帽/虚线映射、非均匀缩放、保存重开 readback 的负例。

### `codex/schema4-reference-contracts-v1` — Reference inventory、AssetSpec、Region/Visual contracts

- 主要路径：`tools/{reference_inventory,asset_spec,region_contract,visual_contracts}.py`，`tools/{regions,compare,providers}.py`；`tools/{contracts,convert,check}.py` 只暂存合同集成 hunks。
- 测试路径：`tests/test_{reference_inventory,asset_contract_receipt,asset_spec,region_contract,visual_contracts,semantic_group_bindings,regions,compare,check,png_channel,providers}.py`。
- 风险：R2；这是 fail-closed reference-fidelity 边界，错误可能让“内部自洽”被误报为“与参考一致”。
- 定向测试：冻结 receipt 漂移、漏对象、非闭世界关系、bbox/拓扑/成员漂移、route comparison 参考哈希不一致、critical region 缺失和阈值降低均必须失败。

### `codex/schema4-source-lineage-v1` — Schema 4 source admission、事务、revision 与 QA lineage

- 主要路径：`tools/{source_gate,normalize_source,transactions,revisions,qa_lineage,migrate_v4}.py`，`tools/{common,contracts,cases,prepare,ingest}.py`；`tools/{__main__,convert}.py` 只暂存该链路的 hunks。
- 测试路径：`tests/test_{source_gate,normalize_source,convert_transactions,revisions,prepare,cases,contracts}.py`，以及 `test_convert.py` 的 lineage/transaction hunks。
- 风险：R2；涉及 schema 迁移、源身份、失败回滚、canonical scene revision 和派生物过期判定。
- 定向测试：accept/repair/reject 三态、部分写入回滚、schema 向后兼容、reference/input route 不可变、scene 改动使未绑定当前 revision 的证据失效、绝对路径不得成为 provenance 权威。

### `codex/schema4-strict-repair-v1` — Strict QA 汇总、deterministic repair plan 与 live handoff

- 主要路径：`tools/repair_plan.py`、`tools/{check,repair,live_bridge,math}.py`；`tools/{__main__,convert,regions,compare}.py` 只暂存最终编排 hunks。
- 测试路径：`tests/test_{repair_plan,repair,live_bridge,math,case01_regressions,route_ab_examples}.py`，以及 `test_check.py`、`test_regions.py`、`test_compare.py` 的最终集成 hunks。
- 风险：R2；错误会把 blocker 覆盖率误当成修复成功，或错误推进 `qa_failed → approved`。
- 定向测试：未知 blocker 必须失败、每个 blocker 恰好一个 owner、source/compiler blocker 禁止 live execution、保存重开不等于 final approval、缺 evidence 必须 INCONCLUSIVE/FAIL。

### `codex/schema4-doc-sync-v1` — 文档、CLI 和能力说明同步

- 路径：`README.md`、`README_EN.md`、`SKILL.md`、`HIGH_FIDELITY.md`、`PROJECT_ARCHITECTURE.md`、`references/prompt-contract.md`、`POWERPOINT_ARROW_CAPABILITY_SPEC.md`，以及 `tools/__main__.py` 尚未进入前述 PR 的纯 CLI 表面。
- 风险：R1；文档若先于实现，会制造不存在的保证。
- 验证：每条命令对当前 CLI `--help` 可用；中英文关键状态、schema 版本、输入路线与 strict 门禁一致；无绝对机器路径、无 `qa_failed` 完成性表述。

### `codex/case-<id>-source-v1` — 按案例提交输入与冻结合同

这些 PR 在 C1–C5 之后，彼此可并行，但每个案例编号一个 PR：

| PR | 路径模式 | 当前文件数 | 风险 | 必测 |
|---|---|---:|---|---|
| S1 case01 source | 两条 case01 路线的 `{prompt,assets,regions,scene}` 变化 | 7 | R2 | schema、reference identity、两路线隔离、freeze receipt 可重建 |
| S2 case02 source | 两条 case02 路线的 `reference/external-seed/prompt/assets/regions/scene` 变化 | 8 | R2 | 同上，并验证新增 reference-only 路线不读取 seeded 候选 |
| S3 case03 source | `svg-seeded/03-llmind/{assets,regions,scene}.json` | 3 | R2 | schema、seed identity、critical-region 闭合 |
| S4 case04 source/fix | 两条 case04 路线的 11 个输入/合同文件 | 11 | R2 | DNA 的 reference-bound bbox、相对大小、方向、链/横档拓扑、区域指标和跨路线 evaluation oracle；未通过前保持 Draft/Blocked |

S4 不能直接照搬当前 11 个文件；它是承接 DNA Issue 的修复 PR。当前合同仅作为基线诊断证据，目标合同以 Issue 验收条件为准。

### `codex/case-<id>-evidence-v1` — 每案例提交一次、每路线保持独立 lineage

| 案例 Stage 内的路线证据单元 | 路径模式 | 当前候选文件数 | 前置依赖 |
|---|---|---:|---|
| case01 route a / b | case01 reference-only / svg-seeded 的 lineage、`qa/**`、交付物 | 68 / 68 | case01 source Stage + 生成工具 SHA |
| case02 route a / b | case02 reference-only / svg-seeded 的 lineage、`qa/**`、交付物 | 47 / 45 | case02 source Stage + 生成工具 SHA |
| case03 route | case03 svg-seeded 的 lineage、`qa/**`、交付物 | 6 | case03 source Stage + 生成工具 SHA |
| case04 route a / b | case04 reference-only / svg-seeded 的 lineage、`qa/**`、交付物 | 49 / 44 | 满足目标 Issue 验收的 case04 source Stage；当前候选隔离 |

- 每个案例只建立一个 evidence Stage/PR；表中的 `a`、`b` 是该 PR 内两个不可互相读取的路线证据单元，不是两个长期或可复用分支。
- 每个路线证据单元都必须在干净 checkout 中从头再生成，并证明再次运行不产生非预期 diff。
- `run.json.status`、check report 和 PR 文案必须一致。若仍为 `qa_failed`，只能作为明确 anti-example/诊断证据，不得作为 canonical result 合并。
- 生成二进制需同时提供 target-size reference/candidate 对照图、hash/readback/inventory 摘要；审查者不应只下载 PPTX 肉眼查看。

### `codex/route-comparison-v1` — A/B comparison 最后作为一个共享原子 Stage 提交

- case01 comparison：2 个文件，在 case01 evidence Stage 内两条路线的确切 SHA 固定后生成。
- case04 comparison：2 个文件，在 case04 evidence Stage 内两条路线均通过目标 Issue 验收后重新生成；当前 2 个 comparison 文件仅作基线诊断证据。
- 风险：R1/R2；比较器不得把同 reference SHA 误当成语义 oracle 相同，也不得跨路线读取对方候选资产。
- 验证：交换路线顺序、改变一侧 scene/region/inventory 哈希、制造 DNA 拓扑不一致时 comparison 必须显式失败。

这是相对最初七类迁移步骤显式拆出的第八个串行 Stage：comparison 使用不同测试矩阵，同时是可独立失效、重生成和回滚的事务，因此不能临时塞回任一案例 evidence PR，也不按案例复用 comparison 分支。

### IDX — 案例索引机械更新

- 仅更新 `examples/README.md`，当前 1 个 tracked 修改。
- 在所有准备合并的案例 PR 后运行 `autofigure cases --write-index`，随后 `autofigure cases --check`；索引 PR 不混入案例内容或手写结果。

## 5. 每个 PR 的统一验证门槛

源码/测试 PR 至少执行：

```bat
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python -m ruff check tools tests
.venv\Scripts\python -m compileall -q tools tests
autofigure cases --check
autofigure hygiene
```

案例源码和证据 PR 还要执行其路线的 `freeze`（适用时）、`convert`、`repair`（存在可执行修复时）和：

```bat
autofigure check <case> --profile strict
autofigure compare <svg-seeded-case> <reference-only-case>
```

测试临时目录使用受控外部 `basetemp`；完成后验证正式案例内没有 mock、cache、临时 candidate、PowerPoint Live build 或活动 session 残留。`autofigure cases --write-index` 只在 IDX 分支运行并检查其 diff 仅有索引。

## 6. 提取执行的安全顺序

1. 在任何取材前重新运行只读 status，要求普通 porcelain 仍为 271 个折叠条目、`--untracked-files=all` 仍为 432 个文件，且全量摘要仍匹配；若不匹配，先生成新的基线文档，不在旧计数上继续。
2. 保持原 dirty 目录不切分支；在干净辅助 worktree 中检出本轮已初始化的远端 `develop`，核对其起点仍为 `513c47b...`。
3. 依次构建并审核 `schema4-geometry-foundation-v1` → `schema4-reference-contracts-v1` → `schema4-source-lineage-v1` → `schema4-strict-repair-v1` → `schema4-doc-sync-v1`；共享热点使用 hunk 级选择，不复制无关案例文件。
4. 合并工具链后，每个案例从最新 `develop` 建立一个 `case-<id>-source-v1`；case04 必须先关闭 DNA 合同问题。
5. 在每个 source PR 的确切合并 SHA 上建立同案例的 `case-<id>-evidence-v1` 并重新生成两条独立路线 lineage；不直接搬运未绑定目标 SHA 或已失效的 QA。
6. 所有需要比较的案例两路证据分别通过后，才建立一次 `route-comparison-v1` 生成 A/B comparison；最后机械更新 IDX。
7. 每个 feature PR squash 到 `develop`；`develop` 全量 CI 通过且用户审核 release PR 后，才以 develop→main 的合并提交进入 `main`。

这个拆分把 71 个代码/测试/文档项、50 个案例源/lineage 项和 310 个生成证据项分成可独立质询的审查单元，避免用大批自动生成文件掩盖核心算法、合同或 DNA reference-fidelity 的真实变化。
