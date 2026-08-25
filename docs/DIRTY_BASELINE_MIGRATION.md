# Dirty Baseline 迁移清单

本清单把本地原始工作树的 432 个 dirty 文件冻结为互斥迁移组。原始工作树保持只读；绝对路径、原始 `history/` 内容和机器环境信息不进入仓库。分组合同见 `docs/dirty-baseline-migration.json`，逐文件证据见 `docs/dirty-baseline-files.jsonl`。

## 冻结基线

| 字段 | 值 |
|---|---|
| 基底 | `main@513c47b845b56e0b9dcf634d9a9ea5929e01a2a3` |
| 普通 porcelain 概览 | 271 个折叠条目，仅作人类界面参考 |
| 全量文件口径 | `git status --porcelain=v1 --untracked-files=all`，432 个文件 |
| tracked / untracked / staged | 169 / 263 / 0 |
| 全量 status SHA-256 | `a6e1c229fb34e32c18100dc4d6bdfc43ca9e707d9028346c0d279e6c7bd70d84` |
| 逐文件 inventory SHA-256 | `27dedc64966585ae51e2f9eadbba35ff95ac695a1fd77ce452d541e76379bd40` |
| 冻结时间 | 2026-08-25T10:50:10+08:00 |

status 摘要沿用采集时的 PowerShell 7 `Sort-Object` 当前文化排序见证：以 LF 连接并保留末尾 LF，按 UTF-8 编码后计算 SHA-256。清单中的 `status_order` 冻结了这一次见证顺序，避免 CI 因 PowerShell/.NET 排序实现差异产生另一摘要。

逐文件 inventory 按仓库相对路径升序排列。每行是 canonical JSON，记录精确 porcelain 状态、迁移组、tracked/untracked、HEAD blob OID、文件字节数和工作树 SHA-256；清单末尾保留 LF。8 个组还各自绑定 inventory SHA-256。任何取材动作都必须先复算摘要；摘要、逐文件内容、计数或基底提交任一不匹配时停止迁移并重新审计。

## 串行分组

迁移顺序严格为：

```text
geometry
  -> reference-contracts
  -> source-lineage
  -> strict-repair
  -> docs
  -> case-source
  -> case-evidence
  -> comparison
```

| 序号 | 分组 | tracked | untracked | 合计 | 前置分组 | 分组 status SHA-256 |
|---:|---|---:|---:|---:|---|---|
| 1 | `geometry` | 6 | 10 | 16 | 无 | `f60e5dc4106c88a90f1fd0ceebef29502ac539092d9baa31b790525979f86ade` |
| 2 | `reference-contracts` | 3 | 10 | 13 | geometry | `9a7b494734a0b4638d59324c21f11f1f58e8af4932787581e44fd0d45772793f` |
| 3 | `source-lineage` | 8 | 11 | 19 | reference-contracts | `75784bf29f3726e1c6f55b68e72f9ee2b1e315631224ec3ab5768af7ca2822ff` |
| 4 | `strict-repair` | 11 | 2 | 13 | source-lineage | `b5e2f7486b9fb682d76713e904ef1cb959edb3d50f68ce5d34500cbe25b6c747` |
| 5 | `docs` | 7 | 1 | 8 | strict-repair | `41d4d860bd3032af53fabe163ee47eaefc18ee7900a881c6c2b59898d61f14c3` |
| 6 | `case-source` | 13 | 16 | 29 | docs | `99ffe5c9d01155016c555a6f883871c69efa751c644d17852590c2ce8f9160c8` |
| 7 | `case-evidence` | 116 | 211 | 327 | case-source | `fd3fc04a00edc74540b517bc094355b0a228324e403690b0c98d54872f79e4ba` |
| 8 | `comparison` | 5 | 2 | 7 | case-evidence | `bc8484baf76cd3477c5fce5831fec668ebf8a55a5a765bac336bfe95155a0752` |
|  | **总计** | **169** | **263** | **432** |  |  |

对应的短期 Stage 分支依次为：

```text
codex/schema4-geometry-foundation-v1
codex/schema4-reference-contracts-v1
codex/schema4-source-lineage-v1
codex/schema4-strict-repair-v1
codex/schema4-doc-sync-v1
codex/case-<case-id>-source-v1
codex/case-<case-id>-evidence-v1
codex/route-comparison-v1
```

每个分支都从当时最新的 `develop` 创建，squash 合入后删除；后续范围使用新的 `-vN`，不继续复用已合并分支。

### 1. geometry

负责 ArrowSpec、箭头组合与视觉合同、PowerPoint 原生箭头、brace primitive、布局和主转换器。包含 8 个工具文件及其 8 个同名/集成测试：

- `tools/{arrow_composition,arrow_spec,arrow_visual,pptx_arrows,primitives,arrows,layout,convert}.py`
- `tests/test_{arrow_composition,arrow_spec,arrow_visual,pptx_arrows,primitives,arrows,layout,convert}.py`

`convert.py` 是共享热点，但当前 baseline 以完整文件归入 geometry；后续分组只能依赖它，不能复制另一份实现。

### 2. reference-contracts

负责 reference inventory、AssetSpec、region contract、visual contracts 和区域判定：

- `tools/{asset_spec,reference_inventory,region_contract,visual_contracts,regions}.py`
- `tests/test_{asset_contract_receipt,asset_spec,reference_inventory,region_contract,semantic_group_bindings,visual_contracts,regions,png_channel}.py`

该组以 geometry 为前置，因为 inventory、region、PPTX readback 和视觉合同消费 ArrowSpec、layout 与原生几何输出。

### 3. source-lineage

负责 source gate、normalize、事务、revision、QA lineage、schema 迁移和案例生命周期：

- `tools/{migrate_v4,normalize_source,qa_lineage,revisions,source_gate,transactions,cases,common,contracts,ingest,prepare,__main__}.py`
- `tests/test_{cases,contracts,convert_transactions,normalize_source,prepare,revisions,source_gate}.py`

该组建立 canonical scene 与派生物的身份，不包含任何案例 QA 或交付二进制。

### 4. strict-repair

负责 strict 检查汇总、repair plan、PowerPoint Live handoff、数学检查和 provider capability：

- `tools/{check,live_bridge,math,providers,repair,repair_plan}.py`
- `tests/test_{check,live_bridge,math,providers,repair,repair_plan,case01_regressions}.py`

该组只能消费前三组的公开合同；blocker 分类覆盖率不得被解释为候选通过。

### 5. docs

包含当前 dirty 的 8 个文档/索引文件：

- `README.md`、`README_EN.md`、`SKILL.md`
- `HIGH_FIDELITY.md`、`PROJECT_ARCHITECTURE.md`
- `references/prompt-contract.md`
- `POWERPOINT_ARROW_CAPABILITY_SPEC.md`
- `examples/README.md`

文档在代码门禁稳定后同步。`examples/README.md` 必须由案例索引命令机械验证，不能夹带案例证据。

### 6. case-source

仅选择两条案例路线目录的直接子文件，允许的 basename 为：

- `assets.json`
- `external-seed.svg`
- `prompt.md`
- `reference.png`
- `regions.json`
- `scene.json`

目录更深处同名文件不属于 case-source。例如 PowerPoint Live evidence 内的 reference 副本仍归 case-evidence。每个案例应再拆为独立 PR；reference-only 与 svg-seeded 不共享候选 scene、资产或坐标。

### 7. case-evidence

包含案例路线目录下除 case-source 之外的全部当前 dirty 文件，共 327 个：

- lineage：`bindings.json`、`provenance.json`、`run.json`
- QA：`qa/**`、readback、receipt、报告和诊断图
- 交付候选：`redraw.svg`、`redraw.pptx`、`render.png`、`preview.png`、`check-report.md`

该组不是可直接搬运的文件包。每个 case 使用一个 evidence Stage；其中
reference-only 与 svg-seeded 必须分别从冻结的 case-source 和确切工具链提交
重新生成，并保持独立 source、scene、artifact、QA 与 hash lineage。一个 case
Stage 可以共同验收两条路线，但不得让一条路线读取或覆盖另一条路线的候选。

### 8. comparison

`comparison` 是对原迁移序列的显式拆分，不是静默增加功能范围。冻结清单中
存在独立的共享比较器、比较器测试和跨路线报告；它们需要等待两条路线证据
同时固定，具有不同测试矩阵和独立 rollback unit，因此不应塞入任一 case 的
source/evidence Stage。对应唯一分支为 `codex/route-comparison-v1`。

包含比较器、比较器测试和四个当前 A/B 报告：

- `tools/compare.py`
- `tests/test_compare.py`
- `tests/test_route_ab_examples.py`
- `examples/route-comparison-*.json`
- `examples/route-comparison-*.md`

比较报告必须晚于所有受影响 case 的两条路线 evidence。comparison 只能读取
hash-bound 路线结果，报告仍按 case 隔离，不能成为跨路线候选共享通道。

## 生成证据失效规则

以下任一变化都会使相关 case-evidence 与 comparison 失效：

1. 全量 status、case-source 或目标 `develop` 基底与本清单不一致。
2. `convert.py`、`svggeom.py`、`arrow_spec.py`、`pptx_arrows.py`、`primitives.py` 或 `asset_spec.py` 变化；这些文件参与 compiler fingerprint。
3. reference、scene、regions、assets、bindings、revision 或 receipt 哈希变化。
4. PowerPoint saved/reopened readback、目标尺寸 render 或 strict blocker 清单缺失。

失效证据不得 cherry-pick 到新基底，也不得通过只改 JSON 哈希恢复有效性。处理方式是保留明确的诊断状态，并在目标 source/toolchain SHA 上重新生成完整证据；comparison 随两侧证据一起重建。

## 未分类处理

当前未分类文件数为 0。分类器必须 fail closed：

- 新路径未命中显式文件表或案例规则时，迁移立即停止。
- 未分类项不得自动归入 docs、case-source 或 case-evidence。
- 先记录路径角色、风险、依赖和验证命令，再经人工审查更新机器清单与 status SHA。
- 原始 `history/` 内容始终排除；Issue 只使用提炼后的事实、哈希、重现步骤和验收条件。

## 执行与验收

1. 原始工作树保持只读，在干净 worktree 中以 `develop` 为目标逐组取材。
2. 每组使用独立短生命周期分支并按上述顺序合并；不得并行修改共享热点后再做大合并。
3. 四个代码组每组都运行完整 pytest、ruff、compileall、cases check 和 hygiene。
4. case-source 与 case-evidence 均按案例拆分；case-evidence 内两条路线保持独立 lineage；共享 comparison Stage 最后生成。
5. 每个分组 PR 记录本清单的全量 status SHA、分组 status SHA、基底提交和验证结果。
6. 任何未分类项、失效证据或不匹配摘要都会阻断迁移，不得以人工文字 PASS 覆盖。

机器验证命令：

```text
python .github/scripts/verify_dirty_baseline.py verify
python .github/scripts/verify_dirty_baseline.py verify --source-root <只读原工作树根目录>
```

第一条只使用仓库内的脱敏清单，适合 CI；第二条额外逐文件比对指定工作树。重新 capture 只允许在明确审计后的基线更新中执行，且 `--source-root` 的绝对值不会写入任何仓库文件。
