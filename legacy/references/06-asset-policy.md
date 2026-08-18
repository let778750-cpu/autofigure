# 06 · 表示分类、原子素材与临时槽

本文是素材表示的唯一权威来源。Figure Spec v4 在冻结前先分类，不以反复试绘后的主观“失败”决定降级。

## 四类表示

| 类别 | 适用对象 | 终稿要求 |
|---|---|---|
| `native_required` | 文字、公式、数字/单位、坐标轴、图例文字、数据标签、普通结构箭头 | 必须原生可编辑 |
| `native_preferred` | 简单形状、基础图标、直线/折线/简单曲线 | 原生；边界案例最多试绘和修正一次 |
| `reference_atomic_asset` | 照片、纹理、复杂装饰图标、特殊轮廓或刷状箭头 | 可作为来源绑定的最终位图 |
| `manual_asset_slot` | 多语义对象纠缠、不可安全分离或来源不足的复合区域 | 仅临时预览/待填槽，阻断批准 |

`source_ambiguity` 不是表现策略，而是信息不足的阻塞状态。

## 原子素材准入

原子素材必须同时满足：

- 当前用户确认参考的 source SHA 与精确整数 bbox 已冻结；
- `semantic_object_count=1`，分类只能为 `ISOLATED` 或经确定性 alpha mask 验证的 `SEPARABLE`；
- 不含可重建文字、公式、轴/图例、面板边框、定量证据或邻近语义污染；
- 裁片保持源像素，不预先重采样；opaque RGB 回填差异为 0，边界 ring、裁切缺损和透明边缘进入独立审查；
- receipt 绑定 source、bbox、mask/alpha、输出 SHA、尺寸、用途和权利依据；
- PowerPoint 只允许平移、等比显示与声明的旋转，不允许拉伸、翻转、生成式补绘或内容修补。

复杂视觉箭头使用位图时，`edges[]` 仍须保留 from/to、方向、语义、箭头类和 `visual_asset_id`。箭头上的文字必须拆为原生文字；拆不开则是 `manual_asset_slot`。

原子素材参与最终视觉诊断，计入混合可编辑覆盖率，交付报告标为“来源绑定位图”；不显示 `REPLACE ME`，不降低发布上限。

## 临时槽与复合预览

`manual_asset_slot` 的 `mode` 可为 `empty/reference_preview/user_filled/backfilled_verified`，但元素类型未迁移为合规原子素材前始终降低发布上限。

- `empty`：轻描边可替换槽，显示 `ASSET SLOT - REPLACE ME`。
- `reference_preview`：仅由当前参考生成 hash-bound exact-pixel preview，对象名带 `_REPLACE_ME`，显示 `REFERENCE PREVIEW - REPLACE ME`；不计 native coverage，并从相似度诊断遮罩。
- `user_filled/backfilled_verified`：必须绑定实际文件 SHA、像素尺寸、provenance 与 rights basis；仍需独立检查内容边界。

任何包含多个语义对象、正式文本/公式/拓扑/轴/图例/边框/定量证据的复合截图都不能升级为原子素材。整图 wrapper 与面板截图始终禁止。

## 几何与回填检查

- source bbox、receipt bbox 与 spec bbox 的宽高比偏差不得超过 2%；目标显示只采用统一 scale。
- 读回确认图片对象名、z-order、显示 bbox、旋转、原始文件 SHA 与素材责任类。
- 原子素材检查内部像素、边界 ring、偏移、裁切缺损和透明边缘；slot 只检查槽几何、可替换性与周边净空，内部相似度为不适用。
- 回填后重新运行 preflight、figure lint、PPTX readback、fresh render 和独立区域审查。

实现入口：`tools/materialize_reference_atomic_asset.py`、`schemas/reference-atomic-asset.schema.json`、`tools/materialize_reference_preview.py` 与 Figure Spec v4 的 `asset_binding/slot_contract`。
