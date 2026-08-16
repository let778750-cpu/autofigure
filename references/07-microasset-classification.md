# 07 · 微资产四分类 + Atomic Raster

> 来源：`D:\自动AI科研绘图`（micro_asset 分类 + atomic raster + 内置图标优先）、
> `codex设计`（auditable_concept / exact_vector / creative_raster / forbidden）、上游 `scientific-illustrator`（atomic_raster_unit）。
> 微资产 = 小而独立、有科研语义、可定位可替换的位图/矢量对象，用于 draw.io/PPT 原生无法复刻的局部细节。

## 0. 三分类决策（元素注册表用，见 01）

| 决策类 | 判定 | 展开到四分类 |
|---|---|---|
| `native_editable` | 可用原生对象可靠复刻 | `exact_vector` / `auditable_concept` / `forbidden`（拆可编辑） |
| `manual_asset_slot` | 照片级/复杂不可原生 | `creative_raster` → 空槽，或受控 reference preview 后交用户替换（见 06） |
| `source_ambiguity` | 无法可靠识别且不能安全省略 | 阻塞区域，交用户确认 |

## 1. 四分类（对每个子元素判定其一）

| 类 | 含义 | 处理 |
|---|---|---|
| `exact_vector` | 可用原生 primitive/内置图标精确复刻（数据轴/简单几何/曲线示意） | codex 原生画，不占位不重生成 |
| `auditable_concept` | 概念示意（韦恩图/网络节点/融合符号等），可 SVG/矢量画 | codex 原生画（可溯源概念） |
| `creative_raster` | 照片级/复杂不可原生（显微/3D/照片/复杂图标） | **slot 交用户填充**；候选阶段可用 hash-bound reference preview（见 06） |
| `forbidden` | 含正式文字/公式/箭头/拓扑/坐标轴/定量证据/整图背景的位图 | 禁止，拆成可编辑对象 |

## 2. Atomic Raster（原子位图）硬约束

- 每张最终保留位图 = **一个语义单一的不可再分视觉场**（`atomic_raster_unit=true`）。reference preview 也优先遵守；若源图存在无法无损拆开的遮挡拼贴，只能声明 `MINIMALLY_NONSEPARABLE + NONSEPARABLE_OCCLUSION`，且仍必须交用户替换。
- 禁止一张图仍含可拆分内容：预测网格、mask 对比、多张照片/通道、可编辑标题/标签/边框/箭头/图例/轴/表格/规则图。
- 发现可拆分内容 → 拆成多个原子位图 + 把文字/框/网格/图例/箭头/轴/标注重建为可编辑对象。
- 每张保留位图须声明：`raster_reason`、`source_is_tightly_cropped`（或显式 crop）、`atomic_raster_unit=true`、`contains_reconstructable_content=false`、`decomposition_note`。
- 只裁最小有用范围，不整图插入；保留 asset 放 `assets/` 子目录，命名 `assets/<id>_<短名>.png`。

## 3. 内置图标优先（硬约束）

- 搜索/代码/数据库/文档/图表/文件夹/用户/机器人/状态 token/警告/勾选/简单设备图标 → **必须先**用 draw.io/Visio/PPT 内置图标、stencil 或原生矢量 primitive。
- 只有工具能力审计记录「内置/原生无法复刻且有价值的局部细节」后，才允许 image2 微资产或占位。

## 4. 微资产内容约束

- **尽量无文字**；有文字必须裁掉/遮盖，在 PPT 用可编辑文本重写。
- `transparent_or_clean_background`（透明或纯白底），禁拼贴接缝/风格漂移。
- 主机制微资产占机制区 35-65%（最终论文宽度下可读内部）；辅助微资产 ≤ 相邻主模块面积 30%；不得缩成不可读 thumbnail。
- 禁装饰贴纸/过度卡通/营销化/光效 3D/无关对象。

## 5. 与占位的关系

- `creative_raster` → 走 06 的空槽或 reference preview（最终都交用户）；`forbidden` → 拆可编辑，绝不能截图；`exact_vector`/`auditable_concept` → codex 原生画。
- 占位回填后的图片，同样须满足 atomic raster + provenance（非 target crop/原图裁片）。
