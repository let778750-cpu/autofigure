# 11 · 外层 Agent 原生视觉协议（协议化识图融合）

> 本文件是识图阶段多模态视觉通道的权威契约。管线不给任何远程 VLM/OCR API 发请求；
> 视觉由**外层 Agent（Codex/GPT-5.6 Sol 等会话模型）的原生视觉**履行，且只能按本协议
> 的任务包→应答→校验→融合链路进行。视觉输出永远是候选证据，不拥有文字或坐标授权。

## 1. 分工授权（谁拥有什么）

| 通道 | 拥有 | 永不拥有 |
|---|---|---|
| Host CV（确定性） | 像素可测事实：背景、调色板、ink bbox、ink-bottom 对齐、间距、框候选 | 文字内容、panel 语义 |
| 本机 PP-OCRv6 | 文字候选及其位置、冲突 alternatives | 语义真值（全部 UNVERIFIED） |
| 外层 Agent 原生视觉 | 结构/语义提议、受限仲裁、公式 LaTeX 提议、漏检巡查 | 任何最终权威 |
| 用户 / 可靠原文 | 文字与公式的唯一确认权 | — |

## 2. 四类查询（防幻觉核心：不是一次大问答）

| 类型 | 输入 | Agent 做什么 | 硬约束 |
|---|---|---|---|
| Q1 `STRUCTURE_GLOBAL` | 全图 + 4 象限 crop | 提 panels/kind/阅读顺序/图表类型 | **独立看图**：任务包不含任何 OCR 文本；不得引用其他查询结论 |
| Q2 `CONFLICT_ARBITRATION` | 冲突候选 crop + 打乱的 selections | 选 1 个 index 或 REJECT_ALL | **只选不写**：schema 无自由文本字段；selections 顺序打乱、剥离置信度 |
| Q3 `FORMULA_TRANSCRIPTION` | 公式候选 crop | 独立采样 3 次转写 LaTeX | 每次从头看图；自一致由 `validate_agent_vision.py` 计算回填，Agent 自报无效 |
| Q4 `MISS_SCAN` | OCR 无候选但 CV 有墨迹的区域 crop | 判断是否含文字 | `text_hypothesis` 仅参考假设，不是证据 |

任何查询看不清就填 `NOT_OBSERVABLE`——诚实逃生门，不是失败。

## 3. 工具链与调用顺序

```text
管线内（run_perception_gate.ps1 自动执行，geometry 之后）:
  prepare_agent_vision_task.py → agent-vision/task-package.json
                                  + crops/ + INSTRUCTIONS.md + response-template.json

管线外（暂停点，与 finalize_perception_review 平级的两步模式）:
  1. 外层 Agent 按 INSTRUCTIONS.md 逐 crop 填写 agent-vision-response.json
  2. validate_agent_vision.py  → agent-vision-document.json（盖章版，幂等可重跑）
  3. cross_modal_fusion.py     → fusion/fusion-manifest.json
                                  + fusion-review-queue.md + fusion-overlay.png
  4. finalize_perception_review.py --init --fusion-manifest … → 按优先级排序的决策模板
```

三个管线外工具全部幂等原子写；失败不污染上游已哈希绑定的产物。

## 4. 校验链（validate_agent_vision.py，fail-closed）

1. 任务包 schema 复验 + 每个 crop 文件哈希/尺寸核对；
2. 应答 schema 校验（`schemas/agent-vision.schema.json`，`additionalProperties:false`）；
3. `task_package.sha256`/`run_id`/`source_sha256` 三重绑定；
4. **查询全覆盖**：缺失或多余 query_id 一律拒绝（exit 2）；
5. 坐标合理性：画布内、非退化（面积 ≥ 16px）、panel ≤ 24、reading_order_rank 无重复；
6. Q2 `selected_index` 必须落在 selections 下标内；REJECT_ALL 不得带 index；
7. Q3 三样本齐全且 `self_consistency` 由工具计算（归一后全等才 `SELF_CONSISTENT_K3`）。

## 5. 融合与审核排序（cross_modal_fusion.py）

- **层 1 结构对齐**：VLM panel ↔ CV 锚池（segmentation 候选 + geometry 框候选）。
  IoU ≥ 0.45 或（containment ≥ 0.72 且面积比 ≥ 0.25）→ MATCHED，**锚定 CV bbox，VLM 坐标弃用**；
  分数 ≥ 0.3 → WEAK_MATCH；否则 `UNSUPPORTED_VLM_CLAIM`。面积比护栏防止整图大框吞掉全部子 panel。
- **层 2 文字对齐**：仲裁按 candidate_id 直绑；漏检假设需空间 containment ≥ 0.62 且文本相似度 ≥ 0.88 才算确认既有候选，否则记为 `NEW_TEXT_HYPOTHESIS` 独立事实。
- **层 3 公式**：按 candidate_id 直绑，提议恒标 `PROPOSAL_ONLY_NOT_AUTHORITATIVE`。
- **一致性层级**：`TRIPLE`（OCR+CV+VLM）> `PAIR` > `SINGLE` > `CONFLICT` > `UNSUPPORTED_VLM_CLAIM`。
- **审核队列**：priority CONFLICT=100 / UNSUPPORTED=80 / SINGLE_VLM=70 / 公式=50 / 带冲突 PAIR=40 / PAIR=20 / TRIPLE=10；band FOCUS_* 与 ROUTINE/LOW。
- **退化模式**：视觉文档缺失 → `AGENT_VISION_ABSENT`，全部事实退化为 OCR+CV 双通道，队列照常产出。管线与门禁**永不硬依赖**视觉。

## 6. 不变量（测试固化，违反即缺陷）

1. TRIPLE 一致**不豁免人审**：只降排序，不改 `requires_human_review`；
2. receipt 中不存在任何 VLM 来源的 `evidence.kind`（枚举本就只有 `user_confirmed`/`source_text`）；
3. 每个 OCR 候选 T#### 在融合事实中恰有一条 TEXT fact（细节零遗漏）；
4. 现有 perception/geometry manifest schema 与 `paddle_ocr_manifest.py` 一字节不动；
5. `SKILL.md` 的"禁止远程 VLM/OCR API"对本协议继续生效——原生视觉是会话能力，不是网络依赖。

## 7. 配置与阈值

全部阈值集中于 `agent-vision-config.json`（仿 `ocr-config.json` 先例，哈希绑定进任务包与融合 manifest）：
查询上限（冲突 48 / 公式 16 / 漏检 24 / panel 24）、crop（padding 12px / upscale 2.0）、
对齐阈值（0.45 / 0.72 / 面积比 0.25 / 0.3 / 0.62 / 0.88）、4 个版本化中文提示词模板。
改提示词文本必须 bump `version`，模板 sha256 随任务包落盘可审计。
