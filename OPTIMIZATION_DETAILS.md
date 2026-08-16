# 多模态消融基准实验设计规格：量化验证 VLM / OCR / CV 主辅关系

> 交付日期：2026-08-16 · 文档性质：**实验设计规格（交由 Codex + GPT-5.6 Sol 构建并严格测试）**
> 本文档取代原《识图阶段三模融合优化细节报告》（关键基线事实保留于附录 A，全文不再另行存档）
> 预计规模：新增 12 个文件、修改 2 个文件、新增测试 ~35-45 例、4 次完整管线运行 + 4 次 Agent 视觉会话

---

## 0. 实施者须知（Codex + GPT-5.6 Sol 必读）

本文档自包含：实施本实验所需的全部架构事实、数据形状（均已对照真实产物核实）、约束、接口规格与验收标准都在文中。实施前通读全文，按 §11 步骤顺序执行，**不得跳过阶段门**。

### 0.1 冻结清单（一个字节都不能改，原因：哈希已嵌入历史证据链）

| 文件 | 冻结原因 |
|---|---|
| `tools/paddle_ocr_manifest.py` | Paddle 解释器运行、脚本哈希入历史 run 证据链 |
| `tools/geometry_refinement.py` | 同上 |
| `schemas/perception-manifest.schema.json` / `schemas/geometry-manifest.schema.json` | const 冻结对齐阈值 |
| `examples/target_figure.fixture.json` | 验收权威，被 PS1 哈希校验引用 |
| `tools/cross_modal_fusion.py` | **本实验为旁路评测，不改融合代码**；替代范式用离线模拟器（§6） |
| `tools/run_perception_gate.ps1` | 新图无需改动即可跑（验收断言仅对 fixture 哈希匹配的输入生效，见 §2.7） |

### 0.2 环境与命令惯例

```powershell
$HostPython = 'D:\opencv\env\python.exe'
& $HostPython -I -B -X utf8 <tools\xxx.py> ...      # 所有新工具一律此惯例
& $HostPython -I -B -m pytest -q                     # 现状：233 passed / 1 存量环境失败
& $HostPython -I -m ruff check --no-cache tools tests # line-length 100, E4/E7/E9/F
& $HostPython -I -B tools\check_project_hygiene.py --pretty
```

- 新工具只用标准库 + `import evidence_metrics`（见 §2.6）；文件 <800 行，超出则拆分
- 输出一律 JSON manifest 风格（schema_version / document_type / bindings sha256 / fail-closed）
- **任何工具写盘只能落在 `examples/generated/` 下**（output_policy 强制）；`benchmarks/gold/` 是人工源数据，工具只读
- 路径含中文与空格：pathlib + 引号包裹；Python 一律 `-X utf8`
- git 提交：conventional commits（feat/test/docs/chore），不添加 attribution trailer

### 0.3 验收标准概览（详 §13）

1. 全部新测试绿 + 既有 233 例零回归（容忍 1 个已知存量环境失败，见附录 A 已知问题 1）
2. ruff / hygiene 全 PASS
3. 4 张图完整评测产物齐备，S3 模拟计数与真实 fusion-manifest summary 逐项一致（内建断言）
4. `ablation-report.md` 按 §8 预注册规则给出每条规则的 SUPPORTED / REJECTED / INSUFFICIENT_DATA 判定与最终判答

---

## 1. 研究问题与假设

### 1.1 核心问题

识图（感知）管线有三条通道：**CV**（OpenCV 确定性分析：面板分割、几何精化）、**OCR**（PaddleOCR PP-OCRv6：文字候选）、**VLM**（协议化外层 Agent 原生视觉，由 GPT-5.6 Sol 会话亲自看图应答）。GPT-5.6 Sol 视觉理解能力强，但存在**细节幻觉**（坐标不准、编造元素——上一轮实测已有证据，附录 A 已知问题 3）。

**RQ1（主辅关系）**：三通道应取"VLM 为主 / OCR-CV 为辅"、"OCR-CV 为主 / VLM 为辅"还是"同等重要"？
**RQ2（融合范式）**：现行"授权分工"（CV 主坐标、OCR 主文字候选、VLM 只有提议权、人主裁决）与"数值加权投票"相比，在准确率 / 幻觉率 / 人审负担三轴上孰优？
**RQ3（经验权重）**：若非要给各通道赋数值权重，按任务维度（文字 / 坐标 / 公式 / 漏检）应如何取值？

### 1.2 预注册假设（实验前冻结，报告逐条对判）

| 假设 | 内容 | 对应判据 |
|---|---|---|
| H1 | VLM 坐标声明准确率显著低于 CV 锚（IoU≥0.5 口径），坐标授权应维持 CV | R1 |
| H2 | OCR 高置信候选召回高且错误率低，文字候选主导权应维持 OCR；VLM 仲裁有增益但不足以接管文字 | R2/R2B |
| H3 | "VLM-primary"反事实策略的逃逸人审错误率 ≥ 2× 现状授权分工 → VLM 不具备为主资格 | R3 |
| H4 | 等权/加权投票范式在人审负担或幻觉率上不优于授权分工 → 分维度授权即近似最优 | R3 + S5/S6 对比 |

### 1.3 动机（为什么现在做）

- 现行授权分工是**设计直觉 + 单图定性证据**，未经量化实验验证
- 金标只有 1 张图（`examples/target_figure.png`），统计效力不足
- 用户新增 3 张 CVPR 2026 真实论文图作为金标素材（§3.1），首次具备多图对比条件
- 观察线索：外部视觉通道对其中 1 张图（`03_...LLMind....png`）解析失败——图本身对 VLM 通道就存在难度差异，更需量化

---

## 2. 系统现状快照（实施者地图）

### 2.1 管线与数据流

```
.\autofigure.cmd -InputPath <img> -Device auto
  → tools/run_perception_gate.ps1 编排，产物落 examples/generated/runs/<run_id>/
     analyze_target.py      → analysis/          （CV：背景/前景/色板/象限）
     segment_panels.py      → segmentation/      （CV：kmeans+连通域 → region_candidates）
     paddle_ocr_manifest.py → ocr/               （OCR：text_candidates T####）
     geometry_refinement.py → geometry/          （CV：frame_candidates + text_geometry）
     prepare_agent_vision_task.py → agent-vision/（任务包 task-package.json + crops/ + INSTRUCTIONS.md + response-template.json）
  [管线外暂停点，Agent 手工两步]
     ① Agent 看 crops 填 agent-vision-response.json（§12 规程）
     ② python -I -B -X utf8 tools\validate_agent_vision.py --task-package <run>\agent-vision\task-package.json \
          --response <run>\agent-vision\agent-vision-response.json          # 盖章 → agent-vision-document.json
     ③ python -I -B -X utf8 tools\cross_modal_fusion.py \
          --ocr-manifest <run>\ocr\perception-manifest.json \
          --geometry-manifest <run>\geometry\geometry-manifest.json \
          --task-package <run>\agent-vision\task-package.json \
          --vision-document <run>\agent-vision\agent-vision-document.json \
          --segment-dir <run>\segmentation \
          --output-dir <run>\fusion     # → fusion-manifest.json + fusion-review-queue.md + fusion-overlay.png
```

`--vision-document` 是可选参数：**不传即 AGENT_VISION_ABSENT 退化模式**（OCR+CV 双通道）——本实验的 S2 对照策略直接用它（§6）。

### 2.2 四类查询协议（VLM 通道的任务边界）

定义于 `agent-vision-config.json`（4 个版本化中文提示词）与 `references/11-agent-vision-protocol.md`：

| 查询 | 输入 | VLM 职权 | 限额 |
|---|---|---|---|
| Q1 STRUCTURE_GLOBAL | 全图 + 四象限 crop，**不含任何 OCR 文本**（防锚定） | 独立提 panels/kind/阅读顺序/坐标（坐标仅 advisory） | 1 条 |
| Q2 CONFLICT_ARBITRATION | OCR 冲突候选 crop + selections（确定性打乱、剥置信度） | **只选不写**：SELECT 下标 或 REJECT_ALL | ≤48 |
| Q3 FORMULA_TRANSCRIPTION | FORMULA_LIKE 候选 crop | 独立采样 3 次转 LaTeX；工具端算自一致，全等才产出提议 | ≤16，samples=3 |
| Q4 MISS_SCAN | CV 有墨迹（area≥256/aspect≤8）但 OCR 无候选的区域 crop | 判 contains_text，可提 text_hypothesis | ≤24 |

### 2.3 融合机制（被评测的"现状策略"S3）

`tools/cross_modal_fusion.py`：**授权分工，非数值权重**。

- 三层对齐：VLM panel ↔ CV 锚池（segmentation region + geometry frame，`_match_anchor` 带面积比≥0.25 护栏）；MATCHED → **锚定 CV bbox、VLM 坐标弃用**；不一致 → UNSUPPORTED_VLM_CLAIM。文字按 candidate_id 直绑仲裁意见；漏检假设需 containment≥0.62 且相似度≥0.88 才算确认。
- tier：通道一致数 1/2/3 → SINGLE/PAIR/TRIPLE；VLM 选了非 primary 或 REJECT_ALL → CONFLICT
- 人审队列优先级（硬编码）：CONFLICT=100 / UNSUPPORTED_VLM_CLAIM=80 / SINGLE_VLM=70 / FORMULA=50 / 冲突PAIR=40 / PAIR=20 / TRIPLE=10
- `human_review_required` 恒 true（TRIPLE 不豁免人审）；公式恒 PROPOSAL_ONLY_NOT_AUTHORITATIVE

### 2.4 既有基线（冒烟与对比基准）

run `perception-20260816T025307Z-239e74f1-0153a9`（`examples/generated/runs/` 下，target_figure.png）：

| 指标 | 值 |
|---|---|
| 任务包 | 51 查询（1 结构 + 42 冲突 + 7 公式 + 1 漏检）、55 crop |
| OCR | 154 候选、42 冲突、mean confidence 0.970；8 个金标 anchor 全 found |
| VLM 观察 | 18/51 OBSERVED（上次为抽样；**本实验要求全覆盖**，§12） |
| 融合 | 167 事实：TRIPLE 1 / PAIR 93 / SINGLE 65 / CONFLICT 5 / UNSUPPORTED 3；focus 8（4.8%） |
| 公式 | 2 条三采样 INCONSISTENT 被拒，自一致提议 0 |

### 2.5 可复用资产

- `tools/evidence_metrics.py`（~205 行，仅标准库）：`normalize_text`（NFKC+casefold+去空白）、`text_similarity`（SequenceMatcher autojunk=False）、`bbox_iou` / `bbox_containment` / `bbox_overlap_score`（同时接受 `{x,y,w,h}` 与 `{x0,y0,x1,y1}`）、`normalize_latex_for_consistency` / `latex_samples_self_consistent`。**评分库直接 import，禁止复制逻辑**
- `tools/prepare_agent_vision_task.py` 中的 schema 校验 / 哈希绑定 / 原子写工具函数模式（照抄风格，不 import——各工具独立可运行）
- 6 个历史 run（同一张图字节级重放）：可直接作冒烟输入

### 2.6 数据形状（已对照真实产物逐字段核实，实施时以此为准）

**`ocr/perception-manifest.json`** 顶层键：`schema_version, run_id, created_at_utc, status, degradations, acceptance_checks, policy, source, runtime, models, configuration, scripts, upstream_stages, views, timings, raw_observations, text_candidates, summary, artifacts`。
`text_candidates[]`：`candidate_id`（T####）、`text`、`normalized_text`、`ocr_confidence`（float）、`confidence_band`（OCR_HIGH≥0.97 / OCR_MEDIUM≥0.85 / OCR_LOW / OCR_CONFLICT）、`bbox_source{x,y,w,h}`（float）、`bbox_envelope_source`、`polygon_source`、`primary_observation_id`、`source_views`、`agreement_count`、`observations`、`alternatives[]`、`review_flags[]`、`evidence_kind`、`requires_human_review`、`verification`。

**`geometry/geometry-manifest.json`** 顶层键：`artifacts, coordinate_system, created_at_utc, degradations, frame_candidates, implementation, inputs, mode, neighbor_pairs, policy, run_id, runtime, schema_version, source, status, summary, text_geometry`。
`frame_candidates[]`：`frame_id`、`bbox_source{x0,y0,x1,y1}`、`closed_contour_evidence`、`contour_pair_count`、`contour_rectangularity`、`contour_vertex_count`、`corners_source`、`edge_side_density`…。
`text_geometry[]`：`candidate_id`（绑定 T####）、`status`（MEASURED/INCONCLUSIVE）、`text`、`ink_bbox`、`ink_area_px`、`roi_bbox`、`quality_flags`、`reasons`…。

**`segmentation/panels.json`** 顶层键：`schema_version, source, algorithm, runtime, canvas, coverage_pct, clusters, region_candidates, interpretation`。
`region_candidates[]`：`candidate_id`（region-candidate-###）、`hex`、`cluster`、`area`、`bbox`（**[x,y,w,h] 整数数组**）、`cx/cy`、`aspect`、`status`。

**`agent-vision/task-package.json`** 顶层键：`schema_version, document_type, created_at_utc, run_id, status, degradations, source, inputs, policy, limits, prompt_templates, queries, summary, implementation`。
`queries[]`：`query_id`（V####）、`task_type`、`image{relative_path, sha256, size_bytes, width_px, height_px}`、`prompt_template{template_id, version}`、`payload`。
- Q1 payload：`{view, quadrant_crop_relative_paths}`
- Q2 payload：`{candidate_id, crop_bbox_source{x,y,w,h}, selections[{index, text}]}` ← **真值推导依赖 crop_bbox_source 与 selections**
- Q3 payload：`{candidate_id, crop_bbox_source, ...}`
- Q4 payload：`{region_candidate_id, crop_bbox_source{x,y,w,h}}`
- `summary`：`query_count / structure_query_count / conflict_query_count / formula_query_count / miss_scan_query_count / crop_count`

**`agent-vision/agent-vision-document.json`**（盖章后）顶层键：`schema_version, document_type, created_at_utc, task_package, agent, policy, queries, validation`。
`queries[]`：`query_id, task_type, observation_status（OBSERVED/NOT_OBSERVABLE）, structure{panels[{panel_id, bbox_source, kind, reading_order_rank}]}, conflict{decision(SELECT/REJECT_ALL), selected_index, confidence_self_rating, reason_code}, formula{samples[3], self_consistency（工具回填）}, miss_scan{contains_text, text_hypothesis, reason_code}}`——按 task_type 只填对应子对象。

**`fusion/fusion-manifest.json`** 顶层键：`schema_version, document_type, run_id, created_at_utc, status, mode, degradations, policy, source, inputs, implementation, runtime, facts, review_queue, summary, artifacts`。
`facts[]`：`fact_id`（FUSE-####）、`fact_kind`（TEXT_CANDIDATE/REGION_STRUCTURE/FORMULA_TRANSCRIPTION）、`subject_id`（T#### 或 V####）、`channels{ocr,cv,vlm}`（bool）、`consistency_tier`、`conflict_reasons[]`、`detail`。
`review_queue[]`：`rank, band, priority`（167 项）。
`summary`：`fact_count, text_fact_count, region_fact_count, formula_fact_count, tier_counts{TRIPLE,PAIR,SINGLE,CONFLICT,UNSUPPORTED_VLM_CLAIM}, focus_item_count, vlm_query_count, vlm_observed_count, formula_proposal_count, self_consistent_formula_proposal_count, degradations`。

### 2.7 约束与雷区

1. **hygiene 当前对新图是 FAIL 状态**：`tools/check_project_hygiene.py` 的 `ALLOWED_ROOT_ENTRIES`（L23 起）不含 `benchmarks/`；examples 白名单（L104）= `REQUIRED_FIXTURES | {"generated"}`，3 张新 PNG 在 `examples/` 根会触发 `UNCLASSIFIED_EXAMPLES_ENTRY` → **实验步骤 0 必须先修**（§11）
2. **验收断言的哈希绑定**：`paddle_ocr_manifest.py` L1499 附近 `fixture_applied = source_hash == fixture_source_hash`——新图没有 fixture 也能跑完整门禁（acceptance 自动跳过），这正是金标系统独立新建的原因
3. `SKILL.md` 禁止远程 VLM/OCR API——VLM 通道 = 会话亲自看图，不是管线 API 调用
4. `tests/test_static_contract.py` 固定了 canonical references 集合——不要动 references/；README 修改不得破坏既有断言字符串
5. `benchmarks/gold/` 是"人工源数据"性质（如同 examples 原图），工具只读不写；所有生成物进 `examples/generated/benchmarks/`

---

## 3. 实验总体设计

### 3.1 金标素材（4 张，均已就位）

| goldId | 图像（相对项目根） | 尺寸 | 类型 |
|---|---|---|---|
| `target-figure` | `examples/target_figure.png` | 1536×1024 | AI 生成 Transformer-Mamba 多 panel 架构图（含 sparkline/热图/曲线子元素） |
| `cvpr01-modularagent` | `examples/01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png` | 1429×627 | 三区块模块化架构框图，文字多在色块内、小字密集 |
| `cvpr02-thinking-diffusion` | `examples/02_2026_CVPR_2026_Thinking_Diffusion_Penalize_and_Guide_Visual-Grounde.png` | 1513×554 | 双 panel（a/b）dMLLM 推理流程对比图，红绿框+色块嵌字 |
| `cvpr03-llmind` | `examples/03_2026_CVPR_2026_LLMind_Bio-inspired_Training-free_Adaptive_Visual_Re.png` | 1357×656 | 框架图（细节以标注时看原图为准；注意：外部 VLM 曾对此图解析失败） |

### 3.2 目录与数据流

```
benchmarks/                          ← 新根目录（步骤 0 入 hygiene 白名单）
  README.md                          ← 标注规程 + 会话规程 + 实验协议沉淀
  gold/
    index.json                       ← 金标索引：每个 gold 的路径+sha256（冻结）
    target-figure.gold.json
    cvpr01-modularagent.gold.json
    cvpr02-thinking-diffusion.gold.json
    cvpr03-llmind.gold.json
  decision-rules.json                ← 预注册结论判据（评测前 git 冻结，§8）
schemas/benchmark-gold.schema.json   ← 金标契约（步骤 1）
tools/benchmark_ablation_metrics.py  ← 纯函数评分库（通道级+策略模拟+权重推导，§5-§7）
tools/benchmark_modality_ablation.py ← 评测 CLI + --validate-gold（§9.1）
tools/benchmark_report.py            ← 汇总报告 CLI（§9.2）
tests/test_benchmark_gold_contract.py
tests/test_benchmark_ablation.py
examples/generated/benchmarks/
  gold-preview/<goldId>.png          ← 金标叠加预览（标注复核用）
  ablation-<UTC时间戳>/
    eval/<goldId>/evaluation.json    ← 每图评测产物
    ablation-summary.json            ← 跨图汇总 + 规则判定
    ablation-report.md               ← 中文最终报告
```

数据流：`run 目录（只读）+ 金标（只读）→ benchmark_modality_ablation → evaluation.json ×4 → benchmark_report + decision-rules.json → summary + report（含最终判答）`

### 3.3 偏置控制三原则（实施顺序的硬约束）

1. **金标先冻结**：金标标注（步骤 2）与决策规则（步骤 3）必须 git commit 在任何视觉会话（步骤 8）与评测（步骤 9）**之前**
2. **标注只看原图**：标注期间禁止打开该图任何 run 产物（perception-manifest / text_review / fusion 输出）
3. **规则预注册**：结论判据先于数据冻结，报告只按规则出判定，禁止事后挑指标

---

## 4. 金标系统规格

### 4.1 `schemas/benchmark-gold.schema.json`（字段级）

每图一个 `benchmarks/gold/<goldId>.gold.json`，`schemaVersion: "1.0.0"`，`documentType: "BENCHMARK_GOLD"`：

```jsonc
{
  "schemaVersion": "1.0.0",
  "documentType": "BENCHMARK_GOLD",
  "goldId": "cvpr01-modularagent",
  "source": {
    "relativePath": "examples/01_....png",   // 相对项目根
    "sha256": "<64HEX 大写>",                  // 必须与 run 的 ocr manifest source.sha256 一致（fail-closed 绑定）
    "widthPx": 1429, "heightPx": 627
  },
  "annotation": {
    "protocol": "ORIGINAL_IMAGE_ONLY",        // const：反偏置声明
    "annotatedAtUtc": "2026-08-XX T...Z",
    "annotator": "AGENT_SESSION_HUMAN_VERIFIED",
    "formulaTextSource": "PAPER_SOURCE",      // 或 HUMAN_VERIFIED_VISUAL（target 图用此值）
    "verifiedByOverlay": true                  // 经 --validate-gold 叠加图复核后才可为 true
  },
  "panels": [{                                 // 结构真值（Q1 / CV / 坐标评测）
    "panelId": "GOLD-P01",
    "bboxSource": {"x0": 12, "y0": 8, "x1": 460, "y1": 620},   // 整数；x0<x1, y0<y1；与 geometry 坐标约定一致
    "kind": "PANEL",                           // 枚举：PANEL / AXIS / LEGEND / TITLE_BLOCK / DIAGRAM_GROUP
    "readingOrderRank": 1,                     // 全图唯一，1 起
    "labelText": "(a) ..."                     // 可 null；该 panel 标题锚文本（与 textAnchors 冗余校验）
  }],
  "textAnchors": [{                            // 文字真值（OCR recall / Q2 真值推导 / Q4 真值推导）
    "anchorId": "GOLD-T01",
    "text": "(a) Perception Module",           // exact string；评分用 normalize_text 全等
    "bboxSource": {"x": 14.0, "y": 20.0, "w": 180.0, "h": 24.0},  // OCR x,y,w,h 约定，float 可
    "category": "PANEL_LABEL",                 // PANEL_LABEL / CAPTION / BOX_TITLE / INLINE_NOTE / NODE_TEXT / AXIS_TICK / LEGEND_TEXT / OTHER
    "panelRef": "GOLD-P01"                     // 可 null
  }],
  "formulas": [{                               // 公式真值（Q3）；无公式则空数组
    "formulaId": "GOLD-F01",
    "latex": "s_t = A\\Delta s_{t-1} + B\\Delta x_t",
    "latexSha256": "<sha256(utf-8 latex) 小写HEX>",   // schema 校验自洽
    "bboxSource": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
    "authority": "PAPER_SOURCE"                // 或 HUMAN_VERIFIED_VISUAL
  }],
  "graphicsOnlyRegions": [{                    // 确定无文字的图形区（Q4 真值 = false 的来源）
    "regionId": "GOLD-G01",
    "bboxSource": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
    "description": "示意图箭头群"
  }],
  "exclusions": [{                             // 不可判区域：命中者不计分不判错（诚实逃生门）
    "exclusionId": "GOLD-X01",
    "bboxSource": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
    "reason": "SMALL_TEXT_ILLEGIBLE"           // SMALL_TEXT_ILLEGIBLE / OVERLAPPING_GRAPHICS
  }],
  "structureNotes": {
    "expectedPanelCountRange": [3, 9],
    "primaryFlow": ["GOLD-P01", "GOLD-P02"]    // panelId 有序列表，可空
  }
}
```

**标注规模要求**（低于此则该维度 INSUFFICIENT_DATA）：每图 textAnchors ≥15、panels 3–12、graphicsOnlyRegions ≥2（不足时如实标注并在 report 披露）。

**Q2 仲裁真值与 Q4 漏检真值不进金标**——它们依赖每次 run 的候选集，由评测工具确定性推导（§4.3），推导逻辑用单元测试固化。

### 4.2 金标标注规程（步骤 2 执行）

1. **只打开原图 PNG**。01/02/03 的公式与术语可对照论文原文（CVPR 2026 open access）；target 图公式按视觉可辨级标注（`HUMAN_VERIFIED_VISUAL`）。**禁止打开该图任何 run 产物**
2. 标注顺序：panels（结构+真值 bbox）→ 每 panel 内 textAnchors → formulas → graphicsOnlyRegions → exclusions。看不清的小字**进 exclusions，不猜写**
3. bbox 精度：评测主判据 IoU≥0.5，可容忍约 ±10% 像素误差。可用本地脚本把原图切象限/局部放大辅助（写盘只进 examples/generated/）
4. **叠加复核**：`--validate-gold` 渲染金标框叠加原图 → `examples/generated/benchmarks/gold-preview/<goldId>.png`，人眼复核迭代（框偏了改金标重渲染）至通过，置 `verifiedByOverlay=true`
5. 冻结：`index.json` 记录 4 个 gold 文件 sha256；git commit（消息建议 `docs(benchmark): freeze gold annotations for modality ablation`）

### 4.3 Q2 / Q4 真值推导算法（评测工具内实现，必须配单元测试）

```text
推导 Q2 真值（对每个 CONFLICT_ARBITRATION 查询 q）：
  crop = q.payload.crop_bbox_source          # {x,y,w,h}
  anchors = [a for a in gold.textAnchors
             if overlap_area(a.bbox, crop) / area(a.bbox) >= 0.5]   # anchor 面积≥50% 落入 crop
  if 任一 anchor 的 bbox 与 gold.exclusions 实质重叠（≥50%）:
      truth[q] = AMBIGUOUS_TRUTH（excluded，不进分母）
  elif len(anchors) == 0:
      truth[q] = REJECT_ALL
  else:
      matched = [s for s in q.payload.selections
                 if exists a in anchors: normalize_text(s.text) == normalize_text(a.text)]
      if len(anchors) == 1 and len(matched) == 1:
          truth[q] = SELECT(matched[0].index)
      elif len(matched) == 0:
          truth[q] = REJECT_ALL                # 真文本不在候选里 → 全拒才对
      else:
          truth[q] = AMBIGUOUS_TRUTH           # 多锚多匹配 → 排除

推导 Q4 真值（对每个 MISS_SCAN 查询 q）：
  crop = q.payload.crop_bbox_source
  anchors = [a for a in gold.textAnchors if bbox_overlap_score(a.bbox, crop) > 0]
  if anchors 非空:
      truth[q] = CONTAINS_TEXT（期望文本 = 相似度最高的 anchor.text）
  elif exists g in gold.graphicsOnlyRegions: overlap_area(crop, g.bbox) / area(crop) >= 0.8:
      truth[q] = NO_TEXT
  else:
      truth[q] = AMBIGUOUS_TRUTH（excluded）
```

冒烟阶段（步骤 6）校准：AMBIGUOUS 占比 >30% 时回改推导参数（如 anchor 落入比例阈值）或补充金标 exclusions，重新 commit——此时新实验尚未开跑，无偏置。

---

## 5. 通道级评分定义

所有文本匹配用 `evidence_metrics.normalize_text` 全等为主判据；`text_similarity ≥ 0.88` 记"宽松命中"单列报告。坐标匹配 `bbox_iou ≥ 0.5` 主判据（0.45/0.55 敏感性单列）。命中 `exclusions` 的断言一律 excluded（不进分子分母）。

### 5.1 OCR 通道

| 指标 | 定义 |
|---|---|
| `anchor_recall` | 金标 anchors 中被某候选 normalize 全等命中的比例 |
| `anchor_recall_lenient` | 同上但允许 similarity≥0.88 |
| `anchor_precision` | 有金标覆盖区域的候选中，与某 anchor 匹配（全等）的比例 |
| `candidate_count` / `conflict_rate` | 候选总数；（alternatives 非空 或 band=OCR_CONFLICT）的比例 |
| `high_confidence_count` / **`high_confidence_error_count`** | band=OCR_HIGH 数量；其中与金标不符的数量——**OCR 的"自信错误"，直接对标 VLM 幻觉指标** |

### 5.2 CV 通道

| 指标 | 定义 |
|---|---|
| `panel_recall_iou50` | 金标 panels 中，与锚池（region_candidates ∪ MEASURED 的 frame_candidates）贪心最大 IoU 匹配达到 ≥0.5 的比例（0.45/0.55 敏感性单列） |
| `false_anchor_count` | 锚池中匹配不上任何金标 panel 的锚数量（注意：子模块框不算错——只与 panels 池比对，子元素框不计入锚池时由标注的 DIAGRAM_GROUP 粒度决定，报告披露口径） |
| `measured_rate` | text_geometry 中 MEASURED 比例 |

### 5.3 VLM 通道（GPT-5.6 Sol 的"成绩单"，幻觉量化核心）

| 子维度 | 指标 | 定义 |
|---|---|---|
| Q1 结构 | `panel_recall` / `panel_precision` | VLM 提议 panels 与金标 panels 的匹配（提议 bbox 与金标 IoU≥0.5 算同一 panel；一对一贪心） |
| Q1 结构 | **`bbox_accuracy_iou50`** | OBSERVED panel 提议中 bbox 与对应金标 IoU≥0.5 的比例——**"细节幻觉"的直接量化** |
| Q1 结构 | `kind_accuracy` / `phantom_panel_count` | kind 判对比例；匹配不上任何金标 panel 的凭空提议数 |
| Q2 仲裁 | `arbitration_accuracy` | 按 §4.3 推导真值：SELECT 选对 + REJECT_ALL 判对 占可评（非 AMBIGUOUS）查询比例 |
| Q2 仲裁 | **`confident_wrong_selects`** | SELECT 且选错 且 confidence_self_rating 高档 的数量——"自信地错" |
| Q3 公式 | `strict_correct` / `lenient_correct`(similarity≥0.85) | 自一致提议与金标 latex（`normalize_latex_for_consistency` 全等 / 宽松）比对 |
| Q3 公式 | **`self_consistent_wrong`** | 三采样自一致但与金标不符的数量——**公式幻觉**（自一致≠正确） |
| Q4 漏检 | `precision` / `recall` | contains_text 判断对 §4.3 真值的准确率 |
| 诚实度 | `not_observable_rate_on_evaluable` | 可评查询中 NOT_OBSERVABLE 的比例（机会成本；会话规程要求全覆盖，§12） |

---

## 6. 策略模拟器 S0–S6（离线重放，不改 fusion 代码）

### 6.1 统一端到端三指标

| 指标 | 定义 |
|---|---|
| `asserted_accuracy` | 该策略"自信断言"的事实中正确的比例（文字断言=文本全等命中 anchor；结构断言=IoU≥0.5 命中 panel；公式断言=严格或宽松命中） |
| **`unreviewed_error_rate`** | **逃逸人审的断言中错误的比例（幻觉率代理）** |
| `human_burden` | 该策略路由给人的条目数 |

### 6.2 策略定义表（每策略的断言集 / 逃逸集 / 负担集，实现到规则级）

| 策略 | 断言集（自信输出） | 逃逸人审集 | 人审负担 |
|---|---|---|---|
| **S0 OCR-only** | band=OCR_HIGH 候选的 primary text | 全部断言 | 非 HIGH 候选数（MEDIUM/LOW/CONFLICT 全给人） |
| **S1 CV-only** | 锚池全部作为结构断言 | 全部断言 | 0 |
| **S2 OCR+CV** | **真跑**：`cross_modal_fusion.py` 不传 `--vision-document`，输出到 `<run>\fusion-s2-control\`；断言 = tier∈{PAIR,TRIPLE} 的事实（text 以 OCR primary 为准、structure 以 CV 锚 bbox 为准） | PAIR/TRIPLE 中非 FOCUS 带者 | SINGLE 数 + FOCUS 带数 |
| **S3 现状授权分工** | **真跑**：现有 `<run>\fusion\fusion-manifest.json`（不重算）；断言 = 全部事实（text=OCR primary、structure=锚定 bbox、formula 不断言因 PROPOSAL_ONLY） | **非 FOCUS 带事实**（现实假设：人只审 focus 队列） | `focus_item_count` |
| **S4 VLM-primary 反事实** | ① Q1 OBSERVED panels 的 **VLM bbox**（不用 CV 锚）② Q2 SELECT 决策 → 文本断言=所选 selection 的 text ③ Q3 自一致提议的 latex ④ Q4 text_hypothesis 作为文字断言 | 全部断言（信任 VLM，无人审） | 0 |
| **S5 等权投票反事实** | 按维度投票：**文字**投票者={OCR primary 文本, VLM（SELECT→所选文本；REJECT_ALL→反对 primary；无意见→弃权）}，CV 只投"存在性"不投内容；**结构**投票者={CV 锚 bbox, VLM claim bbox}；**公式**投票者={VLM（自一致才投）}。一致→断言；分歧→人审 | 多数决出的断言 | 分歧条数 |
| **S6 推导权重投票** | 同 S5，但票权 = §7 的 logit 权重（按维度按通道）；权重差 < δ=0.5 视为平票 → 人审 | 加权多数决断言 | 平票 + 分歧条数 |

> 设计说明：三通道并不在所有维度都有投票资格（CV 不识文字内容、OCR 不识结构框）——**投票矩阵是稀疏的**。这个事实本身就是"同等重要"假设需要检验的内容之一，报告中需明示。

### 6.3 范式对比（回答 RQ2）

S3 vs S5/S6 在同一张 (asserted_accuracy, unreviewed_error_rate, human_burden) 三轴上对比；S4 是"VLM 为主"的极端化；S0/S1/S2 是"OCR/CV 为主"的消融底座。7 策略同图同金标可比。

---

## 7. 经验权重推导（回答 RQ3）

对每个任务维度 d ∈ {text, structure, formula, miss} 与每个有资格通道 c：

```
p(d,c) = (该维度该通道正确数 + 1) / (可评数 + 2)     # Laplace 平滑
w(d,c) = clamp(ln(p/(1-p)), -4, +4)                  # logit 截断，防小样本极端值
```

- text：OCR 用 anchor_precision；VLM 用 arbitration_accuracy（SELECT 选对率）
- structure：CV 用 panel_recall_iou50；VLM 用 bbox_accuracy_iou50
- formula：VLM 用 strict/lenient correct rate；OCR 无内容票（列 N/A）
- miss：CV 用区域定位命中（Q4 crop 与 graphicsOnly/anchor 的吻合率）；VLM 用 Q4 precision

输出 `derived_weights` 表进 evaluation.json 与报告；S6 直接消费该表。**权重是"描述性产物"**——即使 S6 不胜出，权重表也回答了"各通道各维度强在哪"。

---

## 8. 预注册结论判据（`benchmarks/decision-rules.json`，评测前 git 冻结）

完整文件内容（实施步骤 3 原样写入并 commit）：

```json
{
  "schemaVersion": "1.0.0",
  "registeredAtUtc": "<实施时填>",
  "experiment": "modality-ablation-v1",
  "minSamplePerDimension": 20,
  "rules": [
    {
      "ruleId": "R1-COORD-AUTH",
      "dimension": "coordinates",
      "condition": {
        "vlm.q1.bbox_accuracy_iou50": {"op": "<", "value": 0.80},
        "cv.panel_recall_iou50": {"op": ">=", "value": 0.95}
      },
      "conclusion": "维持坐标授权 CV_AND_OCR_MEASUREMENT_ONLY（VLM 坐标仅 advisory）",
      "converse": "若 vlm.q1.bbox_accuracy_iou50>=0.95 且 cv.panel_recall_iou50<0.80 → 重议坐标授权"
    },
    {
      "ruleId": "R2-TEXT-AUTH",
      "dimension": "text",
      "condition": {
        "ocr.anchor_recall": {"op": ">=", "value": 0.90},
        "vlm.q2.arbitration_accuracy": {"op": "<", "value": 0.90}
      },
      "conclusion": "维持 OCR 主文字候选 + VLM 只选不写"
    },
    {
      "ruleId": "R2B-CONFLICT-PRIORITY",
      "dimension": "text",
      "condition": {
        "vlm.q2.arbitration_accuracy": {"op": ">=", "value": 0.95},
        "vlm.q2.confident_wrong_selects": {"op": "==", "value": 0}
      },
      "conclusion": "可考虑将 CONFLICT 队列优先级降至 FORMULA 之下（降低人审置顶负担）"
    },
    {
      "ruleId": "R3-PARADIGM",
      "dimension": "e2e",
      "condition": {
        "S4.unreviewed_error_rate / S3.unreviewed_error_rate": {"op": ">", "value": 2.0}
      },
      "conclusion": "拒绝 VLM-primary；若同时 S5/S6 的 unreviewed_error_rate > S3 → 授权分工范式胜出（同等权重被拒）"
    },
    {
      "ruleId": "R4-FORMULA",
      "dimension": "formula",
      "condition": {
        "vlm.q3.strict_correct_rate": {"op": "<", "value": 0.50}
      },
      "conclusion": "公式提议维持 PROPOSAL_ONLY_NOT_AUTHORITATIVE"
    },
    {
      "ruleId": "R5-QUEUE-VALUE",
      "dimension": "e2e",
      "condition": {
        "focus_true_conflict_coverage": {"op": ">=", "value": 0.90}
      },
      "conclusion": "审核队列设计有效：focus 带覆盖绝大多数真分歧（真分歧=断言错误或通道不一致的事实）"
    }
  ],
  "finalAnswerMapping": {
    "R1&R2 均成立且 S3 三轴不劣于 S4/S5/S6": "OCR-CV 为主、VLM 为辅（= 现状授权分工获数据支持）",
    "VLM 各维准确率全面 >= OCR/CV 且 R3 不触发": "VLM 为主方向成立，需扩大样本复验",
    "各维互有胜负且 S5/S6 与 S3 无显著差异": "同等重要（分维度授权即近似最优）",
    "任一维度可评样本 < minSamplePerDimension": "该维度 INSUFFICIENT_DATA，不出硬结论"
  }
}
```

判定语义：条件全满足 → SUPPORTED；任一不满足 → REJECTED（converse 若有则一并报告）；引用指标的可评样本数 < `minSamplePerDimension` → INSUFFICIENT_DATA。`focus_true_conflict_coverage` = focus 带事实数 / 全部"断言错误或通道不一致"事实数。

---

## 9. 工具接口规格

### 9.1 `tools/benchmark_modality_ablation.py`（评测 CLI）

```
& $HostPython -I -B -X utf8 tools\benchmark_modality_ablation.py `
  --run-dir examples\generated\runs\<run_id> `
  --gold benchmarks\gold\<goldId>.gold.json `
  --output-dir examples\generated\benchmarks\ablation-<case>\eval\<goldId>\ `
  [--fusion-manifest examples\generated\runs\<run_id>\fusion\fusion-manifest.json]     # 缺省自动探测 <run>\fusion\
  [--s2-control-manifest examples\generated\runs\<run_id>\fusion-s2-control\fusion-manifest.json]  # 同上自动探测
```

- **绑定校验**（fail-closed）：gold.source.sha256 == ocr manifest source.sha256 == task-package source.sha256；不一致 exit 2
- **降级语义**：vision 文档缺失 → 仍评 OCR/CV/S0–S2，VLM/S3/S4 记 degradation，exit 3；fusion manifest 缺失 → S3 跳过并记 degradation，exit 0
- **`--validate-gold <gold.json> --preview-dir <dir>`**：只做 schema 校验 + sha256/尺寸核验 + 叠加预览渲染（panels 绿框、anchors 蓝框、exclusions 灰框、formulas 橙框），不评 run

输出 `evaluation.json`：

```jsonc
{
  "schema_version": "1.0.0",
  "document_type": "MODALITY_ABLATION_EVALUATION",
  "run_id": "...", "gold_id": "...", "created_at_utc": "...",
  "bindings": { "gold": {"path","sha256"}, "ocr_manifest": {"path","sha256"}, "geometry_manifest": {...},
                "segmentation": {...}, "task_package": {...}, "vision_document": {...}|null,
                "fusion_manifest": {...}|null, "s2_control_manifest": {...}|null },
  "channel_scores": {
    "ocr":  {"anchor_recall","anchor_recall_lenient","anchor_precision","candidate_count",
             "conflict_rate","high_confidence_count","high_confidence_error_count"},
    "cv":   {"panel_recall_iou50","panel_recall_iou45","panel_recall_iou55",
             "false_anchor_count","measured_rate"},
    "vlm":  {"q1":{"panel_recall","panel_precision","bbox_accuracy_iou50","kind_accuracy","phantom_panel_count","observed_count"},
             "q2":{"evaluable_count","ambiguous_excluded","select_correct","reject_all_correct",
                   "wrong","confident_wrong_selects","arbitration_accuracy"},
             "q3":{"self_consistent_count","strict_correct","lenient_correct","self_consistent_wrong"},
             "q4":{"evaluable_count","precision","recall"},
             "honesty":{"evaluable_not_observable_count","not_observable_rate_on_evaluable"}}
  },
  "strategy_scores": {
    "S0"|"S1"|"S2"|"S3"|"S4"|"S5"|"S6": {
      "available": true, "assertion_count","assertion_error_count","asserted_accuracy",
      "unreviewed_error_count","unreviewed_error_rate","human_burden",
      "breakdown": {"text":{...},"structure":{...},"formula":{...}} }
  },
  "derived_weights": { "text":{"ocr":0.0,"vlm":0.0}, "structure":{"cv":0.0,"vlm":0.0},
                       "formula":{"vlm":0.0}, "miss":{"cv":0.0,"vlm":0.0} },
  "exclusions_summary": {"ambiguous_q2","ambiguous_q4","excluded_assertions"},
  "crosscheck": { "s3_recomputed_tier_counts": {...}, "matches_fusion_summary": true },
  "degradations": []
}
```

**内建一致性断言**：S3 路径下用通道产物重算 tier 计数，与 fusion-manifest `summary.tier_counts` 逐项比对，不一致 → exit 2（防工具自身 bug）。

### 9.2 `tools/benchmark_report.py`（汇总 CLI）

```
& $HostPython -I -B -X utf8 tools\benchmark_report.py `
  --eval examples\generated\benchmarks\ablation-<case>\eval\*\evaluation.json `
  --rules benchmarks\decision-rules.json `
  --output-dir examples\generated\benchmarks\ablation-<case>\
```

输出两件：
- `ablation-summary.json`：跨图池化计数、逐图分解、每条规则 `{ruleId, metrics_used, sample_sizes, verdict: SUPPORTED|REJECTED|INSUFFICIENT_DATA, evidence}`
- `ablation-report.md`（中文）固定六节：①通道×维度胜任度表（含敏感性列）②S0–S6 七策略三轴对比表 ③经验权重表与范式对比 ④预注册规则逐条判定 ⑤**最终判答**（按 §8 finalAnswerMapping，明确到"VLM 为主 / OCR-CV 为主 / 同等重要 / 分维度授权"四选一）⑥局限声明（n=4、单次会话采样、标注者=实施 Agent 的潜在偏置）

---

## 10. 测试要求

### 10.1 `tests/test_benchmark_gold_contract.py`

- 4 个 gold 文件 schema 合法；source.sha256/width/height 与实际 PNG 一致；panel bbox 在画布内且 x0<x1/y0<y1；anchor bbox 非退化（w,h>0）；formula latexSha256 自洽；readingOrderRank 唯一；index.json 覆盖全部 gold 且哈希匹配；decision-rules.json 结构合法（ruleId 唯一、op ∈ {<,<=,==,>=,>}、minSample 为正整数）

### 10.2 `tests/test_benchmark_ablation.py`（合成 fixture，纯内存构造小 manifest，tmp_path，不写项目树）

- OCR：anchor recall/precision 计数、conflict_rate、high_confidence_error_count
- CV：IoU 贪心匹配（一锚多金、IoU=0.49 边界、region+frame 两池合并）
- Q2 真值推导三分支（正确 SELECT / REJECT_ALL / AMBIGUOUS 排除）+ exclusions 命中排除
- Q3 严格 vs 宽松正确、self_consistent_wrong 计数
- Q4 真值三分支（anchor 相交→true；graphicsOnly≥80%→false；否则排除）
- S0–S6：手工构造三通道分歧案例，逐策略断言 assertion/error/burden 计数与预期完全相等（每策略至少 1 正 1 负 1 边界）
- 权重推导单调性（p↑ ⇒ w↑）、clamp 上下界、Laplace 平滑
- crosscheck：tier 重算 == summary（构造含 TRIPLE/PAIR/CONFLICT 的案例）
- fail-closed：gold 与 run 源哈希不一致 exit 2；缺 fusion → S3 跳过 + degradation + exit 0；缺 vision → exit 3 且 OCR/CV/S0–S2 照评
- report：规则三态触发；minSample 阈值生效（构造样本不足案例 → INSUFFICIENT_DATA）

---

## 11. 实施步骤（严格按序，阶段门不过不进下一步）

| # | 步骤 | 产出 / 阶段门 | 依赖 |
|---|---|---|---|
| 0 | **修 hygiene**：`check_project_hygiene.py` 的 `ALLOWED_ROOT_ENTRIES` 加 `"benchmarks"`；examples 白名单新增 `BENCHMARK_SOURCE_IMAGES` 集合收纳 3 张新 PNG | `--pretty` PASS | 无（**当前对新图 FAIL，最先做**） |
| 1 | 写 `schemas/benchmark-gold.schema.json` + `--validate-gold`（含叠加预览渲染）+ `test_benchmark_gold_contract.py` | ruff + pytest 绿 | 0 |
| 2 | **金标标注 4 张图**（§4.2 规程），逐图叠加复核 → `index.json` 冻结 → **git commit** | 金标冻结提交 | 1 |
| 3 | 写 `benchmarks/decision-rules.json`（§8 原样）→ **git commit**（任何评测发生之前） | 规则冻结提交 | 与 2 同期 |
| 4 | 写 `tools/benchmark_ablation_metrics.py` + `test_benchmark_ablation.py` | 单测绿 | 1 |
| 5 | 写 `benchmark_modality_ablation.py` 评测 CLI + `benchmark_report.py` + 补测试 | 工具闭环 | 4 |
| 6 | **冒烟**：现有 run `perception-20260816T025307Z-239e74f1-0153a9` + target-figure 金标跑 evaluate。门：①crosscheck tier 计数一致 ②42 条 Q2 真值可推导且 AMBIGUOUS <30%（超了回改 §4.3 参数或补 exclusions 并重新 commit 金标——此时无偏置） | 冒烟 evaluation.json | 2,3,5 |
| 7 | 3 张新图跑门禁：`.\autofigure.cmd -InputPath "examples\01_....png" -Device auto`（02/03 同；注意路径引号） | 3 个新 run 目录，gate exit 0 | 0 |
| 8 | **Agent 视觉会话 ×4**（§12 规程）→ validate 盖章 → fusion 出 `<run>\fusion\`；每 run 另跑 **S2 对照**（fusion 不传 `--vision-document`，输出 `<run>\fusion-s2-control\`） | 每 run：document.json + fusion/ + fusion-s2-control/ | 7（target 图复用现有 run 的 task-package 重填全覆盖 response） |
| 9 | 跑 4 组 evaluate → report → 结论 | ablation-summary.json + ablation-report.md | 6,8 |
| 10 | 收尾：`benchmarks/README.md`（规程沉淀）；`README.md` 加"模态消融基准"用法一节（不动既有断言字符串）；全量 pytest + ruff + hygiene | 全部验证绿 | 9 |

---

## 12. Agent 视觉会话操作规程（GPT-5.6 Sol 亲自执行部分）

> 本实验的 VLM 通道数据由实施会话亲自看图产生。**上一轮是 18/51 抽样观察；本实验要求全覆盖**——诚实率本身是被测指标（§5.3），看不清才允许 NOT_OBSERVABLE。

1. 读 `<run>\agent-vision\INSTRUCTIONS.md` 与 `task-package.json` 的 `summary`，确认查询配额（预期 1 结构 + ≤48 冲突 + ≤16 公式 + ≤24 漏检）
2. `cp response-template.json agent-vision-response.json`，按 task_type 分四批填：
   - **Q1**（1 条）：看 `crops/full.png` + 四象限 crop，**独立**提 panels——禁止参考任何 OCR 产物（防锚定，AGENTS.md 已明令）
   - **Q2**（最大批）：逐个查看 `crops/conflict/T****.png`，对照 payload.selections 填 SELECT/REJECT_ALL；**每 ~10 条跑一次 validate_agent_vision.py 早失败早改**（validator fail-closed 会立刻指出下标/覆盖错误）
   - **Q3**：每个 `crops/formula/T****.png` **独立采样 3 次**（每次从头看图，不回看上次答案）；自一致由工具计算回填，勿自报
   - **Q4**：`crops/miss/*.png` 判 contains_text；提 text_hypothesis 需确有把握（fusion 层有 containment≥0.62 且相似度≥0.88 门槛）
3. **Q1 独立性红线**：response 必须会话内亲自看图填写，禁止从 OCR manifest 抄答案
4. 终检 validate 通过 → fusion → S2 对照 fusion
5. `agent.declared_model` 如实填写；response/document 哈希绑定即为可审计证据
6. **偏置隔离**：会话期间可以看该 run 的 crops（那是任务输入），但**禁止查看金标文件内容**（benchmarks/gold/）——金标是评分答案

---

## 13. 验收标准（Definition of Done）

1. `pytest -q`：233 既有 + 新增全部通过（唯一容忍：附录 A 已知问题 1 的存量环境失败）
2. `ruff check --no-cache tools tests` 零告警
3. `check_project_hygiene.py --pretty` PASS（含 benchmarks/ 与 3 张新图）
4. 4 个 evaluation.json 齐备，每个 crosscheck `matches_fusion_summary: true`
5. `ablation-report.md` 六节齐备；每条规则判定可追溯到 summary JSON 数字；最终判答明确四选一
6. 金标与决策规则的 git commit 时间戳先于任何评测产物时间戳（偏置控制可审计）
7. `benchmarks/README.md` 沉淀了标注规程与会话规程的实操记录（含 AMBIGUOUS 占比、NOT_OBSERVABLE 率等执行统计）

---

## 14. 风险与规避

| 风险 | 规避 |
|---|---|
| 金标 bbox 主观性 | IoU≥0.5 主判据 + 0.45/0.55 敏感性列进报告；叠加预览人工复核必经（§4.2） |
| Q2 真值推导歧义（crop 跨多锚） | AMBIGUOUS 进 excluded 并报告占比；步骤 6 冒烟校准（<30%），超了在无偏置窗口期修正 |
| 视觉会话单次采样不可复现 | response 哈希绑定 + declared_model 记录；报告明示 n=1/图；规则带 minSample，不足即 INSUFFICIENT_DATA 不出硬结论 |
| NOT_OBSERVABLE 过多稀释 VLM 指标 | 会话规程要求全覆盖（§12）；诚实率单列指标使取舍透明 |
| n=4 样本量小 | 池化 + 逐图双呈现；结论措辞严格按预注册规则；finalAnswerMapping 含"需扩大样本"分支 |
| 误改冻结文件 | §0.1 冻结清单；S2 用重跑产出新目录 `fusion-s2-control/`，绝不动融合代码 |
| hygiene / 静态契约回归 | 步骤 0 白名单化；不动 references/；README 改动保持既有断言字符串 |
| 03 图对 VLM 通道可能整体难解析 | 这本身就是有效数据点：如实记录 NOT_OBSERVABLE / 低观察率，报告单列该图分析，不剔除 |
| 新工具超 800 行 | 三文件拆分：metrics 纯函数库 / evaluator CLI / report CLI |
| 评分工具自身 bug 污染结论 | crosscheck 内建断言（tier 重算==summary）+ §10.2 合成 fixture 全路径测试 |

---

## 附录 A：上一轮优化基线（原《识图阶段三模融合优化细节报告》关键事实，2026-08-16）

**变更**：架构图节点 Z（GPT-5.6 Sol / 多模态视觉）协议化为四阶段：任务包生成（管线内，`prepare_agent_vision_task.py`）→ Agent 看图应答 → 校验盖章（`validate_agent_vision.py` 九步 fail-closed）→ 跨模态融合排序（`cross_modal_fusion.py`），全程哈希绑定、视觉永不拥有文字/坐标授权。新增 11 文件（含 `evidence_metrics.py`、`agent-vision-config.json`、三份 schema、`references/11-agent-vision-protocol.md`）、修改 12 文件（PS1 加 agent-vision-pkg 阶段、finalize 加 fusion 排序等），gate schema 1.2.0→1.3.0。

**E2E 实测**（run `perception-20260816T025307Z-239e74f1-0153a9`，见 §2.4 表）：无融合时人审需逐条过 154 候选；融合后前 8 名（4.8%）覆盖全部真分歧；两条公式三采样 INCONSISTENT 被自一致机制拒绝（按设计工作）。

**有意保持不变（反幻觉底线，本实验继续遵守）**：三份 perception/geometry/review schema 一字节未动；TRIPLE 不豁免人审；receipt evidence.kind 无 VLM 通道；视觉坐标永不直接采用；SKILL.md 禁止远程 VLM/OCR API 继续生效。

**已知问题**（3 条）：
1. `test_powerpoint_native_math.py::test_roundtrip_helper_rejects_project_outputs_before_writing[OutputPath]` 存量环境失败：中文 Windows 下 pytest subprocess stderr 解码异常，与该轮改动无关
2. 该轮 E2E 为抽样观察（18/51）；生产使用应全量观察——**本实验步骤 8 即落实全量**
3. 外部视觉通道（auto → qwen3.7-plus / glm-5v-turbo）在大图坐标上不可靠（结构盘点坐标被 UNSUPPORTED 正确拦截），小裁剪图文字判断良好——**本实验 §5.3 `bbox_accuracy_iou50` 即对该问题的系统量化**

## 附录 B：约束速查

- Host Python：`D:\opencv\env\python.exe`；一律 `-I -B -X utf8`
- 写盘仅 `examples/generated/`；金标 benchmarks/gold/ 工具只读
- 冻结清单见 §0.1；hygiene 白名单见 §2.7；验收断言哈希绑定见 §2.7
- 所有阈值优先复用 `agent-vision-config.json` 既有值（IoU 0.45 / containment 0.72 / 面积比 0.25 / 文字 containment 0.62 / 相似度 0.88）；本实验新增阈值：评测 IoU 主判据 0.5（敏感性 0.45/0.55）、宽松文本 0.88、宽松公式 0.85、anchor 落入 crop 比例 0.5、graphicsOnly 覆盖比 0.8、投票平票 δ=0.5——全部集中在 `benchmark_ablation_metrics.py` 模块级常量区，禁止散落硬编码
