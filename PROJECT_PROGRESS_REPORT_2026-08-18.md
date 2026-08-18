# AI AutoFigure 重构进展与遗留问题报告

> 报告日期：2026-08-18  
> 项目路径：`D:\AI+科研\AI智能绘图（最终版）\AI autofigure`  
> 当前模式：`RECONSTRUCT_1TO1`  
> 默认策略：`standard`  
> 当前评审结论：**公式机械门已通过；全图保真门未通过；不得标记 `APPROVED`**

## 1. 总体结论

本轮重构已经完成大部分底层治理与通用能力建设：Figure Spec v4、分类优先的渲染策略、原子位图素材协议、OCR/几何校准框架、`standard|strict` 公式审计、单一 run 状态机、通用 PowerPoint 渲染器、Image2PPT ADR 与文档去重。当前代码实测为 `313 passed, 1 skipped`，Ruff 和 `git diff --check` 均通过。

ModularAgent 当前候选 R10 已修复“把 LaTeX/普通文本占位符当成原生公式”的错误。28 个公式已全部读回为 PowerPoint Office Math/MathZone，保留 canonical LaTeX 与 OMML 语义哈希，且完成保存、关闭、重开和两次 fresh render 闭环。

但是，当前图像保真度仍未达到项目绝对门槛：

- 全图 `mean_abs_rgb_delta=19.9987`，目标为 `<=18`。
- 最大差异 ROI 贡献 `9.4238%`，目标为 `<5%`。
- 中央专家分配区、路由交叉和部分局部几何仍是 major finding。
- 当前 run 的单一状态机仍停在 `PREFLIGHT_PASS`，未将已有渲染/机械审计证据正式追加到状态账本。

因此，当前准确说法是：**基础架构重构已取得实质进展，公式问题已解决，ModularAgent 较 V7 明显改善，但候选图仍不是可批准交付版。**

## 2. 判断边界

### 2.1 已确认事实

1. 当前指定参考图为 `1429×627` PNG，SHA-256 为 `792a16d4bd2c26cca9fca79668395a987825ab75eb2bc8a65f2d42a47c38a340`。
2. 当前隔离 run 为 [`perception-20260817T134032Z-792a16d4-41842d`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/)。
3. 当前最新规范修订为 [`figure-spec.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/figure-spec.v9.json)，preflight 结果为 `PASS`。
4. 当前最新 PowerPoint case 为 [`powerpoint-case-v23`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/)。
5. R10 含 244 个递归对象、5 个原生分组、86 个连接器/拓扑载体、9 个声明过来源的位图原子素材、28 个原生公式对象；244 个对象均有元素绑定。
6. PowerPoint 递归机械审计在 `text_overflow_tolerance=1 pt` 下为 0 hard failure、0 warning。
7. 原生公式审计结果为 `MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW`，28/28 MathZone，0 finding，0 violation。
8. 公式 fresh render 与 verification render 的 SHA-256 一致，为 `8a855f776eb57a9b4f75b7b37a8445185071a5eb1bfaa0f5c7ef75099e5c11b0`。
9. 最终 R10 PPTX 包审计为 0 hard failure、0 warning；包内媒体数为 9，均命中素材 manifest，未嵌入整张指定参考图，无宏和外部链接。
10. 本次全量测试实测为 `313 passed, 1 skipped in 92.89s`；Ruff 与 `git diff --check` 均通过。
11. 本地 `Image2PPT/` 快照已退出工作树，采纳/拒绝决定保留在 [`references/adr/001-image2ppt-comparison.md`](references/adr/001-image2ppt-comparison.md)。
12. Git 工作树当前仍为 dirty：存在大量已修改/已删除文件与 25 个未跟踪新文件，未形成可审阅的提交边界。

### 2.2 合理推测

1. 当前最大视觉差异的主因已从“全局错误近似”收敛到“中央专家分配区和路由几何仍不够准确”。
2. R10 与 R9 的 1429×627 渲染哈希相同，说明本次公式框安全余量修正保留了视觉中心，修复的是机械可保存性而非改变字形。
3. 编辑已终结的 MathZone 后直接走普通 candidate save，PowerPoint 可产生 `mc:Fallback` 兼容图片，导致通用素材包审计暂时拒绝。正确流程应是几何修正后重新进入 inject/finalize，而不是将这些 Office 内部 fallback 当成用户素材登记。R10 经重注入后包内回到 9 个正式素材且包审计通过。

### 2.3 未验证假设

1. OCR `consensus_auto` 策略已有实现和测试，但尚未用真实多案例数据集完成版本化校准与生产抽样闭环。
2. Geometry promotion 框架已有实现和合成测试，但仓库内尚无每个可晋升类别不少于 30 个真实标注实例的 gold fixtures。因此生产几何仍应保持 `observation_only`。
3. `strict` 公式模式已通过单元/合成合同测试，但当前 ModularAgent 正式证据只跑了 `standard`。
4. Thinking Diffusion 与 LLMind 尚未用本次最终代码完成新 run 回归。
5. 不同 Office/PowerPoint 版本的像素级渲染差异尚未建立跨版本基线。

## 3. 按实施计划拆分的完成度

| 模块 | 状态 | 已完成 | 未完成/限制 |
|---|---|---|---|
| 基线冻结 | 已完成 | 保留旧 V7 视觉基线、当前参考 SHA、新 run 证据和测试基线 | run 状态未追加到最新阶段 |
| Figure Spec v4 | 基本完成 | 增加 `group`、`native_shape`、`reference_atomic_asset`、`manual_asset_slot`、render strategy、geometry source、review risk、增强 edge 合同 | schema 枚举中仍保留 `micro_asset` 兼容入口，尚未完全删除过载类型 |
| v3→v4 迁移 | 已完成 | 实现独立迁移器，输出进入新文件，不原地改写旧 spec | 未对所有历史 run 批量迁移，符合旧 run 只读原则 |
| 分类优先渲染 | 已完成 | `native_required/native_preferred/reference_atomic_asset/manual_asset_slot` 策略模块化 | 需更多真实图例检验边界分类 |
| 原子素材协议 | 已完成 | 来源 SHA、bbox、mask/alpha、输出 SHA、变换限制、边界 ring 和回填差异检查 | 自动抠图在复杂背景上的泛化性仍未充分验证 |
| 通用 PowerPoint 渲染器 | 已完成 | 移除 ModularAgent ID、固定正弦路径、固定投影和假图标；支持连接器、via 线段链、原生分组、z-order 和文本框卫生 | 曲线无显式 cubic contract 时仍正确拒绝，未做近似回退 |
| 显式 `via` | 已完成 | 显式路径点优先于 `filled_native` 直线快捷分支，已加回归测试 | 尚需在更多带折点的真实粗箭头上验证 |
| OCR 风险分级 | 代码完成/生产校准未完成 | 增加 calibration receipt、`consensus_auto`、固定抽样与错误升级合同 | 缺真实生产 fixture 和多案例运行证据 |
| Geometry gold promotion | 代码完成/数据未完成 | 增加校准、promotion receipt、P50/P95 门槛和高风险排除 | 缺每类 `>=30` 真实标注实例，当前不应生产晋升 |
| 原生公式 `standard|strict` | 已完成 | 注入、关闭重开、MathZone、语义哈希、可见性、两次 fresh render；`standard` 为默认 | 当前实例未运行整 deck `strict` |
| 单一 run 状态机 | 实现完成/当前 run 接入未完成 | 已实现 hash-bound `run-state.json` 和追加式账本，限制 runner 自授权 | 当前 run 状态仍停在 `PREFLIGHT_PASS`，与 R10 已渲染/机械审计事实不同步 |
| CLI 兼容 | 基本完成 | 保留单一 canonical runner，增加 policy profile、resume/status 参数支持 | 需从新 run 完整实测状态恢复与阶段跳转 |
| Image2PPT 吸收 | 已完成 | 建立 ADR，采纳 manifest/分区/箭头分类/cropability/provenance 等理念，拒绝运行时依赖 | 上游后续变化不会自动同步，需人工 ADR 维护 |
| 文档去重 | 基本完成 | 精简 `SKILL.md`，合并素材政策，将 V3 历史决定收口到 ADR，删除重复正文 | 工作树尚未形成提交，需最终链接和发布包复核 |
| ModularAgent 重建 | 部分完成 | 公式、图标素材、连接器、分组和全图指标显著改善 | 中央 ROI 与局部几何未达绝对门槛，不得批准 |
| Thinking Diffusion 回归 | 未完成 | 参考图存在 | 未用最新代码创建新 run |
| LLMind 回归 | 未完成 | 参考图存在 | 未用最新代码创建原生+照片/复杂箭头混合 run |

## 4. 当前规范、产物与哈希

| 证据 | 路径 | SHA-256 |
|---|---|---|
| Figure Spec v9 | [`figure-spec.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/figure-spec.v9.json) | `4c1d4cb39f6cbb5b939eb1e7867a755ff5915896de1fdf8e0a7593579df27185` |
| Preflight v9 | [`preflight.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/preflight.v9.json) | `e5be64a6e4b694dba4e2f1aef702e512fc067b80fe2d0539042fe94f34efc342` |
| Scene graph | [`scene_graph.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/design/scene_graph.json) | `a42369d3493486b28c3183df92e061846f1a924057d552700e6220587c9601a8` |
| Render plan | [`render_plan.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/design/render_plan.json) | `a6458542d984326f0df1583d70f44df404d0117e5c74a515faa499186fd90e6e` |
| Native math plan | [`native-math-plan.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/native-math-plan.v9.json) | `664292018a025fd31552ef9d225846eb4058e14b2abd2880b98d30b23c1c335e` |
| Injection report | [`native-math-injection-report.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/native-math-injection-report.v9.json) | `66d96005736a883266b36d6dbcb7990fde13ed879f57c851b4bfe2aecdcf0823` |
| Roundtrip receipt | [`native-math-roundtrip-receipt.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/native-math-roundtrip-receipt.v9.json) | `1f6aa2098d66f17c8c10fbb5cbf2e544a766e365a0f53effb846d65d21af146c` |
| Final math audit | [`native-math-final-audit.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/native-math-final-audit.v9.json) | `88d3e53988f389b9cabc3286ae7cb06de2834ad78bcdfcf6337718e3743f5710` |
| R10 PPTX | [`modularagent-v4-r10.pptx`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.pptx) | `d534b8d7f7b968588213edaf8497591bfb5f3aa8e4f0de6951aa38961f88bde3` |
| R10 目标尺寸渲染 | [`modularagent-v4-r10.render-1429x627.png`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.render-1429x627.png) | `df9af6fb4100f7fc0a3464745b538f73e6800219b56c3b705910c161c8fff451` |
| R10 差异图 | [`modularagent-v4-r10.diff.png`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.diff.png) | `cad28d8052c7137bc46a0a13e992a6d6c4282da2298add4de2e46ba7860f2b11` |

## 5. 公式问题专项说明

### 5.1 之前为什么是错的

之前的问题不是“还没审核到公式”，而是流程将注入前的 LaTeX/普通文本占位状态当成了可接受结果，而终稿门禁没有强制要求：

1. `a14:m` 与 `m:oMath|m:oMathPara` 结构读回；
2. canonical LaTeX 与 OMML 语义哈希一致；
3. PowerPoint 保存、关闭、重开后 MathZone 仍存在；
4. 公式可见性和全 deck fresh render 一致；
5. 不得用 textbox/PNG/SVG/EMF 伪装原生公式。

这是实质性的流程逻辑错误，用户对此的质疑成立。

### 5.2 当前修复结果

- 28 个公式均为原生 Office Math/MathZone。
- 最终审计 finding 为 0，violation 为 0。
- 完成关闭重开、MathZone 读回、可见性扫描和 fresh render。
- 两份 fresh render 哈希一致。
- 对 `formula.task-embedding`、`formula.reward.t1`、`formula.reward.t2` 增加了保存重开后仍安全的公式框余量。
- 最终 PowerPoint 递归审计在 1 pt 容差下为 0 hard/0 warning。

### 5.3 仍需固化的流程规则

任何对已 finalize 公式对象的几何、z-order 或文本框修改，都必须将候选稿退回公式 injection/finalize 阶段，重新生成注入报告、roundtrip receipt 和 final audit。不得复用旧公式证据哈希。

## 6. ModularAgent 视觉改善与未达标区域

### 6.1 V7 与 R10 量化对比

| 指标 | V7 重算基线 | R10 | 变化 | 绝对门槛 | 结论 |
|---|---:|---:|---:|---:|---|
| Mean absolute RGB delta | 23.0421 | 19.9987 | -3.0434（约 -13.2%） | `<=18` | 改善，未达标 |
| Changed pixel ratio | 49.2456% | 46.5523% | -2.6933 个百分点 | 诊断项 | 改善 |
| Top ROI loss contribution | 20.4172% | 9.4238% | -10.9934 个百分点 | `<5%` | 明显改善，未达标 |
| SSIM | 0.5925 | 0.6535 | +0.0610 | 副指标 | 改善 |

R10 同时优于 V7 的两个全图主诊断值，最大差异 ROI 也明显改善。但项目验收要求是达到绝对门槛，而不是只要优于旧版即算通过。

### 6.2 当前最大差异区域

- ROI：`x=480, y=80, w=400, h=160`。
- ROI 平均 RGB 差异：`30.977`。
- 该 ROI 损失贡献：`9.4238%`。
- 主要内容：中央 Task-Guided Expert Allocator、专家列、上方任务路由和跨区域连线。

### 6.3 当前仍存在的视觉问题

1. 中央专家列的局部间距、标题几何和路由交叉尚未与参考对齐。
2. 右侧 trajectory 的局部曲线/箭头关系虽已显著优于 V7，仍存在路径与层级偏差。
3. 部分底部箭头、面板灰度和边界细节仍不够贴近参考。
4. 尽管全图指标改善，不能用全图平均值掩盖上述 major ROI。

## 7. 已完成的主要工程变更

### 7.1 规范与策略

- 升级 [`schemas/figure-spec.schema.json`](schemas/figure-spec.schema.json)。
- 增加 [`policy-profiles.json`](policy-profiles.json) 及对应 schema。
- 增加 [`tools/render_strategy.py`](tools/render_strategy.py) 作为分类优先的策略层。
- 增加 [`tools/migrate_figure_spec_v3_to_v4.py`](tools/migrate_figure_spec_v3_to_v4.py)。
- 增加 [`tools/run_state.py`](tools/run_state.py) 和 [`schemas/run-state.schema.json`](schemas/run-state.schema.json)。

### 7.2 OCR 和几何

- 增加 [`tools/calibrate_ocr_consensus.py`](tools/calibrate_ocr_consensus.py)。
- 增加 [`tools/apply_ocr_consensus.py`](tools/apply_ocr_consensus.py)。
- 增加 [`tools/calibrate_geometry_promotion.py`](tools/calibrate_geometry_promotion.py)。
- 增加 [`tools/promote_geometry_observations.py`](tools/promote_geometry_observations.py)。
- 保留 Phase-1 为观察证据，未默认将公式、纵排、多行、污染区或箭头拓扑晋升为几何真值。

### 7.3 原子素材

- 新增 [`tools/materialize_reference_atomic_asset.py`](tools/materialize_reference_atomic_asset.py)。
- 新增 [`schemas/reference-atomic-asset.schema.json`](schemas/reference-atomic-asset.schema.json)。
- 合并原素材槽位与微素材政策为 [`references/06-asset-policy.md`](references/06-asset-policy.md)。

### 7.4 PowerPoint 通用渲染

- 大幅精简 [`tools/build_powerpoint_draw_batch.py`](tools/build_powerpoint_draw_batch.py)，移除案例专用逻辑。
- 增加通用路径几何 [`tools/powerpoint_path_geometry.py`](tools/powerpoint_path_geometry.py)。
- 增加文本框卫生层 [`tools/powerpoint_text_frame_hygiene.py`](tools/powerpoint_text_frame_hygiene.py)。
- 支持原生分组、组内对象读回、线路载体 z-order 和 leaf 对象恢复。
- 修正 `font_size_pt` 被再次按像素缩放的单位错误；只有 `font_size_px` 转为 PowerPoint point。
- 修正带 `via` 的 `filled_native` 箭头静默忽略路径点的逻辑漏洞。

### 7.5 原生公式

- 统一使用 [`tools/build_native_math_plan.py`](tools/build_native_math_plan.py)，移除重复旧入口。
- [`tools/powerpoint_native_math.py`](tools/powerpoint_native_math.py) 增加 `standard|strict` 审计策略。
- `standard` 保留两次 fresh render 和风险触发式严审；`strict` 保留全量反事实审计能力。

### 7.6 文档和 ADR

- 精简 [`SKILL.md`](SKILL.md)，保留入口、状态顺序与不可突破红线。
- 工作流权威合同收口到 [`references/01-workflow-contract.md`](references/01-workflow-contract.md)。
- QA 收口到 [`references/02-qa-gates.md`](references/02-qa-gates.md)。
- Image2PPT 对比收口到 ADR 001。
- V3 与旧优化文档的有效历史决定收口到 [`references/adr/002-v3-policy-history.md`](references/adr/002-v3-policy-history.md)。

## 8. 测试与代码健康状态

### 8.1 当前实测

```text
313 passed, 1 skipped in 92.89s
Ruff: PASS
git diff --check: PASS
```

与实施前记录的 `289 passed, 1 skipped` 相比，当前净增 24 个通过测试。新增覆盖包括：

- Figure Spec v4 与 v3→v4 迁移；
- 渲染分类和素材政策；
- run 状态一致性和授权上限；
- OCR 校准与几何 promotion；
- 原子素材提取/回填；
- `via` 路径、原生分组、z-order 和文本框；
- 公式 `standard|strict` 策略；
- 禁止 ModularAgent 案例 ID/正弦路径/假图标进入通用渲染器。

### 8.2 当前仓库风险

1. 工作树尚未提交，大量变更仍混在同一工作区内。
2. Git diff 统计约为 2,537 行新增、4,647 行删除；尽管删除主要来自案例专用渲染与重复文档，仍需在提交前进行最后人工 diff 审阅。
3. 25 个新文件仍是 untracked，如果不纳入版本控制，会导致新测试在其他环境不可复现。
4. 存在 LF→CRLF 警告，当前不影响测试，但应由 `.gitattributes`/仓库行尾策略统一处理，不应在提交时制造无意义全文行尾 diff。

## 9. 遗留问题与优先级

### P0：阻断发布

#### P0-1 中央 ROI 视觉保真未达标

- 影响：阻断 `INDEPENDENT_REVIEW_PASS`、`RELEASE_CANDIDATE` 和 `APPROVED`。
- 当前证据：Top ROI loss `9.4238% > 5%`。
- 处理建议：当前 run 已经历有限次数的全图修正，不应继续无界重试。应将当前视觉审查记录为 `STALLED`，然后在新 run ID 中只对中央 ROI 重新测量与重建。

#### P0-2 run 状态与实际证据不同步

- 当前 `run-state.json.current_state=PREFLIGHT_PASS`。
- 实际已有 R10 渲染、包审计、PowerPoint 机械审计和公式 final audit。
- 处理建议：先生成 hash-bound 机械/独立视觉评审 JSON，再依法追加 `RENDERED`、`MECHANICAL_PASS`，最后因视觉门未通过追加 `STALLED`。不得直接跳到发布阶段。

#### P0-3 尚无独立审查人批准

- 自动工具的成功上限只能是 `INDEPENDENT_REVIEW_REQUIRED`。
- 当前 `approval_status=PENDING`。
- 未解决视觉 major finding 前，不应请求用户批准。

### P1：高优先级工程债务

#### P1-1 公式修改后回退 inject/finalize 尚未自动化

当前已通过手工编排验证正确流程，但 canonical runner 需要显式检测“已 finalize MathZone 发生几何/z-order 变更”，并强制失效旧公式 receipt、重新 inject/finalize。

#### P1-2 Geometry 真实 gold fixtures 缺失

当前只能证明校准算法和门槛代码可运行，不能证明真实图像上的 P50/P95 误差目标已达成。

#### P1-3 OCR 真实校准与抽样闭环缺失

需建立固定 fixture 版本、source SHA 决定的抽样列表与错误区域整体升级证据。

#### P1-4 `micro_asset` 兼容入口仍在 schema

计划要求删除过载的 `micro_asset`，但当前 Figure Spec schema 仍保留该类型作兼容入口。需明确两种选择之一：

1. 标记 deprecated，只允许 v3 迁移器读取，v4 frozen spec 禁止出现；或
2. 完全删除并升级所有历史 fixture。

建议采用第 1 种，可维护性更好。

#### P1-5 Thinking Diffusion 与 LLMind 回归未执行

在这两个案例通过前，不能声称纯原生路径和原生+原子位图混合路径已全部无回归。

### P2：中优先级维护问题

1. 将当前大量变更拆成可审阅提交。
2. 统一行尾策略，减少 LF/CRLF 噪音。
3. 补充当前 PowerPoint 版本的锁定渲染基线，并将跨版本差异标记为不同 profile，不承诺像素级跨版本一致。
4. 在完成 3 个正式案例后执行一次深度 anti-rot 检查和生成物清理。

## 10. 建议的后续实施顺序

1. **先闭合当前 run 的状态与审查证据。** 为 R10 生成机械审计 receipt 和独立视觉审查 receipt，追加合法状态转移，最终记录视觉 `STALLED`。
2. **不在当前 run 无限继续修图。** 新建 run，只冻结中央 ROI 问题清单，重新测量专家列与路由。
3. **将公式后编辑回退规则自动化。** 任何影响 MathZone 对象的操作都强制重新 inject/finalize。
4. **建立真实 OCR/geometry fixtures。** 优先收集水平单行墨迹框、清晰框体和同排间距；每类至少 30 个实例。
5. **运行 Thinking Diffusion 新 run。** 验证纯原生路径。
6. **运行 LLMind 新 run。** 验证原生+照片/复杂箭头混合路径。
7. **最后处理 Git 提交与发布包。** 先纳入新文件，检查删除项的链接，再做发布包清理；不提前删除证据。

## 11. 验收条件当前状态

| 验收项 | 状态 | 说明 |
|---|---|---|
| 现有测试全部通过 | PASS | `313 passed, 1 skipped` |
| schema/迁移/状态/OCR/几何/素材/箭头/公式新测试 | PASS（代码层） | 真实 OCR/geometry fixture 仍缺 |
| 通用渲染器无 ModularAgent 案例硬编码 | PASS | 静态测试禁止 ID/正弦路径等 |
| ModularAgent 文字与公式结构 | PASS（公式）/部分待视觉复核（文字几何） | 28/28 原生 MathZone |
| 箭头方向/路径/右侧轨迹 | WARNING | 显著改善，但局部 major ROI 未达标 |
| PPTX 读回对象/分组/z-order/连接端点 | PASS（机械） | 244 对象、5 分组、86 连接器/载体 |
| 复合参考截图为零 | PASS（当前 final 包） | 9 个媒体均为 manifest 素材，整图 SHA 未嵌入 |
| 未声明位图为零 | PASS（当前 final 包） | 包审计 0 hard/0 warning |
| R10 同时优于 V7 全图两个主指标 | PASS | Mean delta 和 changed ratio 均改善 |
| R10 绝对视觉门槛 | FAIL/WATCH | Mean delta 19.9987；Top ROI 9.4238% |
| Thinking Diffusion 回归 | NOT RUN | 阻断宣称纯原生路径全面稳定 |
| LLMind 回归 | NOT RUN | 阻断宣称混合路径全面稳定 |
| 独立评审与用户批准 | PENDING | 当前不应进入批准 |

## 12. Token 与模型使用策略

1. schema、哈希、状态迁移、包结构、OCR/geometry 校准数学门槛和所有确定性回归测试不调用模型。
2. 简单、重复的视觉预筛只能在完成版本化校准后使用低成本模型；未校准前不得以模型“看起来没问题”代替机械证据。
3. 科研语义、公式内容、最大差异 ROI、独立区域评审和发布判断仍由主审完成。
4. 每个视觉问题设置有限修正次数；未持续改善时记录 `STALLED`，不得用反复生成消耗 token 掩盖分类或 spec 错误。

## 13. 当前可用但不可批准的候选

- PowerPoint：[`modularagent-v4-r10.pptx`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.pptx)
- 目标尺寸渲染：[`modularagent-v4-r10.render-1429x627.png`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.render-1429x627.png)
- 差异图：[`modularagent-v4-r10.diff.png`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/powerpoint-case-v23/build/final/modularagent-v4-r10.diff.png)
- 公式终审：[`native-math-final-audit.v9.json`](examples/generated/runs/perception-20260817T134032Z-792a16d4-41842d/native-math-final-audit.v9.json)

这些产物用于当前进展评估和后续定点修正，不是用户已批准的发布交付物。

## 14. 最终项目判断

本轮重构已经纠正了三个核心方向性问题：

1. 从“无限原生优先”改为“先分类、再选表示”；
2. 从“全面禁止参考原子素材”改为“来源绑定、边界可审计的位图 fallback”；
3. 从“公式看起来像 LaTeX”改为“必须是可读回的 Office MathZone”。

但整个项目尚不能宣布完成。当前最需要克制的是：不再用全图重生成去碰运气，而是先闭合状态和审查证据，然后在新 run 中只解决已定位的中央 ROI。在该 ROI 达到绝对门槛、Thinking Diffusion 与 LLMind 回归完成、run 状态账本闭合之前，项目状态应继续是 **工程重构进行中，发布未就绪**。
