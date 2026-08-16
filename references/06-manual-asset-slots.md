# 06 · 占位 Slot 契约 + 回填再校验闭环

> 用户原创 D2 + 佐证：`D:\自动AI科研绘图`（micro_asset_backfill_slots.json + backfill prompts）、
> `deepseek设计`（asset_plan 的 `route: gpt_placeholder`）、`xjb-skill-image-to-vba`（Preservation Geometry Contract）。
> 场景：PNG 里的「高质量子元素」（显微照片、3D 渲染、复杂图标、真实照片）不用 codex 调生图模型重生成（慢且幻觉重）。默认留空占位；在 `RECONSTRUCT_1TO1` 中，可用受控 `reference_preview` 先显示完整候选，再交用户替换。

## 0. 总原则：原生优先，预览不是完成品

对目标图中每个可见 element 依次判定：

1. 能由原生 text/Office Math/shape/freeform/connector/chart/built-in icon 合理表达，必须原生；
2. 只有能力审计确认属于照片级连续色调、复杂纹理、品牌保真、用户明确 keep-as-image 或不可分遮挡，才进入 slot；
3. slot 默认 `empty`；用户需要先看完整视觉时，可选择 `reference_preview`；
4. preview 只是目标裁片的候选预览，既不是语义证据，也不是最终素材，更不是可编辑对象；
5. 无法识别内容时走 `source_ambiguity`，不得用截图掩盖未知。

“截图”在本项目中不指操作系统截图。它只能是
`tools/materialize_reference_preview.py` 对当前 SHA 绑定源图做的 exact-pixel、零 padding、零重采样、lossless PNG 裁片。

## 1. 什么时候走占位（而非 codex 自画 / 重生成）

触发条件（满足任一 → 进入 slot 候选；仍须记录能力审计）：
- 显微图像 / 3D 渲染 / 真实照片 / 照片级插画。
- 照片级渐变/纹理/噪声/有机明暗，简单纯色或渐变无法复刻。
- logo / 商标 / 品牌元素。
- 忠实可编辑复刻需要大量 shape，且复杂度来自不可离散的连续视觉信息；单纯“shape 多”不能直接降级。
- 手绘/画风、笔触宽度变化无法用 Line/Freeform 表达。
- 用户明确「保留/不拆/keep as image」。

## 2. slot 几何契约（硬性，违反即本轮不通过）

1. **裁剪 bbox = manifest bbox_source = slot/element bbox**，禁 padding/外扩/内缩；保留 alpha，不预合成白底。
2. **插入保持宽高比**：`s = min(tw_pt/cw_px, th_pt/ch_px)`，实际宽 `cw_px*s`、高 `ch_px*s`；禁止 `Width=X : Height=Y` 硬拉（除非等比）。
3. **裁剪图与目标 bbox 比例一致**：偏差 > ±2% 说明裁剪坐标错，重新裁，不拉伸对齐。
4. **不旋转/翻转/蒙版**：默认 `Rotation=0, FlipH/V=false`；禁圆角/椭圆/freeform 蒙版。
5. **插入位置用 ratio-safe 坐标换算**（scale + offset），禁独立 X/Y 拉伸。
6. **命名可追溯**：`assets/<id>_<语义短名>.png`，manifest 记 SHA-1 前 8 位/文件大小。

## 3. 占位槽位的声明（slot 字段）

```yaml
- slot_id: asset_panel_b_01
  target_element_id: panel_b_raster_01
  semantic_role: "该素材解释什么"
  required_content: "用户需要生成/提供什么"
  forbidden_content: ["文字", "公式", "未确认结论"]
  bbox_px: {x: 0, y: 0, w: 0, h: 0}
  bbox_target: {x: 0, y: 0, w: 0, h: 0, unit: pt}
  aspect_ratio: 1.0
  fit_mode: contain | cover
  crop_intent: ""
  rotation_deg: 0
  z_order: 0
  safe_clearance: {value: 0, unit: pt}
  replacement_object_name: asset_panel_b_01_REPLACE_ME
  status: empty | user_filled | backfilled_verified
```

- `empty`：codex 只画占位组（轻描边矩形 + 居中短标签 `ASSET SLOT <id>` + 可选纵横比），不写生成提示长文。
- `reference_preview`：插入 receipt 绑定的 exact-pixel PNG，同时叠加原生可见标签 `REFERENCE PREVIEW — REPLACE ME`；预览图片 shape 自身使用 `replacement_object_name`，便于 PowerPoint「更改图片」。
- 占位必须是**诚实可见、一键可替换**的 editable 对象（`replacement_object_name` 供 PowerPoint「替换图片」定位）。
- **不得使用任意截图、模糊缩放图、面板裁片、整图 wrapper 或生成图来掩盖空槽。** 只有满足本节全部约束的 `reference_preview` 例外。

扩展字段：

```yaml
  mode: empty | reference_preview | user_filled | backfilled_verified
  native_capability_audit:
    backend: powerpoint | drawio
    outcome: SLOT_REQUIRED
    tested_families: [primitive_shapes, freeform_paths, builtin_icons]
    reason_codes: [PHOTOGRAPHIC_CONTINUOUS_TONE]
    assessed_at_utc: "..."
  preview: null | {
    manifest_path: "...reference-preview.json",
    manifest_sha256: "...",
    decomposition_mode: ATOMIC | MINIMALLY_NONSEPARABLE,
    decomposition_note: "...",
    contains_reconstructable_text: false,
    contains_formula: false,
    contains_connector: false,
    contains_axis_or_legend: false,
    contains_panel_border: false,
    contains_quantitative_evidence: false,
    visible_disclosure_required: true,
    qa_similarity_masked: true,
    native_coverage_credit: false,
    replace_before_approval: true
  }
```

## 3.5 状态诚实（SLOTS / REFERENCE_PREVIEWS）

- 存在 `empty` 或尚未验证的 `user_filled` 必需槽时，整体状态只能是 `CANDIDATE_WITH_SLOTS`。
- 存在任一 `reference_preview` 时，整体状态只能是更严格的 `CANDIDATE_WITH_REFERENCE_PREVIEWS`；不得对外发布为终稿，也不得升级 `APPROVED`。
- 空槽只验收 bbox/纵横比/语义/层级/可替换性/周边净空；内部像素相似度记为 `NOT_APPLICABLE_UNTIL_FILLED`。
- 用户填充后，检查：未拉伸、裁切符合意图、文字/公式未烘焙进图、色调与局部风格协调、无重叠、分辨率满足目标尺寸。

## 4. 用户回填 → 再校验闭环

1. 用户把原创/授权图片放入 slot（PowerPoint 中替换空槽或名为 `*_REPLACE_ME` 的 preview 图片）。
2. 回填后**重跑 figure_lint**（关键，防手动填充破坏排版）：
   - 图片宽高比 vs slot bbox 偏差 < 2%？
   - 图片 bbox 与 slot bbox 在 ratio-safe 映射后误差 < 画布短边 2%？
   - 未覆盖周边可编辑对象（`surrounding_editable_objects` 无重叠）？
   - 未引入文字/公式/箭头/图例/边框（除非 slot 契约允许）？
   - provenance：回填图非 target crop、非原图裁片；替换后 PPT readback 不再存在 reference-preview receipt 对应图片 hash。
3. 不达标 → 调整 slot bbox / 重新填充，而不是在 VBA/COM 里硬拉尺寸。
4. 达标 → 记录 `slot.status = backfilled_verified`，进入终稿。

## 5. 与「codex 自画」的分界

- 可原生画（`exact_vector`/`native_editable`）→ codex 用 PPT 原生对象画，**不占位**。
- 常规工具图标（搜索/代码/文档/图表/文件夹/警告/勾选等）→ **优先用 PPT 内置图标/stencil**，不占位、不重生成。
- 仅「照片级/复杂不可原生」→ 占位交用户；若需要先看完整候选，才启用受控 `reference_preview`。

## 6. 最终答复必须区分

明确列出「可编辑对象」与「占位待填子元素」两类，让用户知道哪些还需手动处理；回填后给出最终 figure-lint 结果。
