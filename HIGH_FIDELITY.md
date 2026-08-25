# Autofigure 高保真执行合同（schema 4.0 当前实现）

## schema 4.0 当前执行合同

schema 4.0 是当前执行合同，其中 **Canonical Scene 是唯一构造事实源**。下文涉及
v3.1 的内容只用于说明旧案例和旧字段的迁移背景，不构成当前执行规则。

### Y 型入口和 source gate

1. Designer 先仅从当前 `reference.png` 冻结画布、对象、文字、关系、小目标和
   验收合同。
2. `reference-only` 的 Drawer 只可读本案例授权文件；`svg-seeded` 只可有一个不可变
   external seed。两路不得相互借用 SVG、PPTX、scene、坐标或裁片。
3. 所有源在修改案例事实前必须通过 schema 4.0 source gate。`accept` 才能 normalize；
   `repair` 只生成源修复任务；`reject` 只记 provenance，不留半产物。
4. 两路在 source gate 后都只能产生 Canonical Scene，再由同一确定性编译器生成
   SVG、PPTX、bindings 和 render。路线名不得选择不同的箭头、括号、字体或微资产算法。

### 语义规格和对象级证据

| 对象 | Canonical spec | 不可缺少的验收证据 |
|---|---|---|
| 箭头/连接 | `ArrowSpec` | 关系端点、中心线/切线、头型宽长、线宽/dash、单可见对象、OOXML 回读 |
| 括号 | `BraceSpec` | 方向、端点、双瓣、中央 cusp、对称/路径签名、单对象回读 |
| DNA/图标/图表/微资产 | `AssetSpec` | tight bbox、部件数、角色与交叉 topology、颜色/线宽、授权、可编辑性 |
| 文字/公式 | text/math spec | 精确文本、font family/size/weight/style、baseline、native binding、OCR |
| 容器/重复元素 | layout spec | 外接框、padding、顺序、轴线、间距、越界与碰撞检查 |

每个冻结对象必须同时闭合 reference 像素、Canonical Scene、源派生 SVG、原生
PowerPoint 对象、保存重开 readback 和最终 render。宽泛 ROI 或全图均值只能诊断；
它们不得覆盖箭头头部错位、括号 cusp 缺失、图标缩放、DNA 交叉拓扑、文字超框或
对象重叠。

微资产机会图也是冻结输入：`assets.json` 必须显式保存安全 policy 与
`microasset_opportunity_map`（空集合也不得省略），`qa/asset-contract-receipt.json` 绑定当前
reference、inventory 与 canonical asset-contract hash。转换阶段产生的 `assets[]` 被排除在该哈希之外。

### Designer → Drawer → Reviewer → Corrector

- **Designer**：冻结 closed-world inventory、视觉层级、关系、spec、critical region 和净距。
- **Drawer**：仅根据冻结合同产生 Canonical Scene，不把参考图整图嵌入，不绕过 source gate。
- **Reviewer**：使用当前 reference 和新鲜重编译/宿主回读证据独立检查；不采信 Drawer
  的自报坐标或旧 QA。
- **Corrector**：将每个 blocker 映射为有 `base_scene_sha256`、region/element 作用域和受保护
  对象的最小 `scene_patch`。补丁必须重编译全部派生物并回到 Reviewer。

Live 只可作 inspect/save-reopen/finalize，或在能够精确导出 scene patch 时作为编辑前端。
直接修改 PPTX 却不回写 Canonical Scene 会形成双真值，必须拒绝发布。

### 泛化门禁

禁止在工具、prompt、compiler 或 QA 中根据 case ID、文件名、案例序号、特定文字或
固定像素坐标走特判。特定参考的测量只存入其自身 regions/spec 合同；通用程序只读
schema、角色、capability 和阈值。每个修复除回归已知案例外，还必须通过 case-neutral
单元测试与至少一张未参与调参的 holdout 机制图。

## 输入必须显式

```bat
autofigure prepare reference.png --case seeded --input-route svg-seeded
autofigure prepare reference.png --case direct --input-route reference-only
```

未提供 `--input-route` 直接失败。旧 `--source-mode` 仅保留一版弃用检查，不能替代路线、不能推断 provenance。

`input_route` 一旦写入不可更改；`processing_mode` 是源处理策略的真实调度字段，source gate
或显式 fallback 可在 `svg_import`、`svg_repair`、`png_reconstruct` 间切换它：

```bat
autofigure ingest seeded --rejected --fallback png_reconstruct
```

这不会把 seeded 案例移动到 `reference-only`。

## reference-only 的真实边界

reference-only 建案会生成模型无关的 `qa/region-tasks.json`。执行者只需读取当前案例冻结的 `reference.png`；可以生成 scene、region patch 或内部 SVG 载体。

`prepare` 后的 `reference_inventory` 必须先由参考图盘点为闭世界对象清单：稳定 ID、类型、bbox、元素 ID、critical region，文字/公式还需精确文本与排印，箭头/括号/图标还需引用各自的可机读合同。所有 critical region 必须 `relations_exhaustive=true`并覆盖全部对象；零文字/箭头/图标/括号也需基于完整参考审查显式授权。运行 `autofigure freeze <case>` 后才能 ingest；receipt 与参考、inventory、regions、critical expectation 和 region tasks 同时绑定，任一漂移立即失效。不含该字段的旧案例仅保持可读兼容。

“没有外部 SVG 种子”不等于“无需视觉模型”。当前完整离线初版仍以 SVG 为可渲染载体，任意科研图的语义分解与坐标测量仍需 VLM 或人工。真实 ModularAgent 测试已经完成全链路，但 strict 只有 2/6 关键区通过，因此能力结论是“真实跑通但质量未成熟”。

受控 A/B 的构建隔离：除同一 `reference.png` 与路线无关验收 bbox/阈值外，reference-only 侧不得读取或复制 seeded 的 SVG、PPTX、scene、bindings、assets、裁剪文件或候选坐标。微资产必须从自己的参考图重新裁剪。

## 箭头

- 两条输入路线先生成统一 ArrowSpec；普通直线、附着 connector、固定折线/曲线和块箭头再确定性编译为恰好一个可见 PowerPoint 对象。
- 所有 `block_arrow` 也必须保存可验证的 canvas-space `path` 作为语义中心线；闭合 silhouette 不能替代端点/切线合同。已知 AutoShape 从其规范几何确定性派生，任意单一闭合 freeform 则必须在 SVG 上显式声明 `data-arrow-centerline="M … L/C …"`，缺失即失败。
- 原生线端精确保存头型、宽度和长度；复杂 marker 无法合并为单一轮廓时 strict 失败。
- 旧杆+独立头+group 仅作 standard 诊断，必须标记 `fidelity_loss`，不能作为正式兜底。
- 任何 z-order 问题必须通过对象顺序/PowerPoint z-order 修复，不能用截图遮盖。例如 mapping 灰箭头必须位于目标框边界之上。
- 头型/宽度/长度的 OOXML 读回与视觉物理尺寸是两项独立证据：前者证明 PowerPoint 保存了正确枚举，后者用当前 `reference.png` 哈希绑定的像素合同比较箭头头部 bbox、宽长、轮廓及障碍物净空。
- 案例可用 `arrow_visual_expectation` 冻结必须存在的逐箭头视觉合同清单；缺项、重复项或数量漂移均阻止 strict。SVG 上的 `data-head-length*` 属于单位不闭合的自报值，正式候选禁止使用；物理宽长只由 reference-pixels 合同测量。

审计硬项包括：

- F3 不得把箭头自身当目标边界；
- 目标对象身份与连接 topology；
- transform 展平；
- 端点误差、参考中心线 P95、头部切线角；
- 箭头交叉和标签碰撞；
- 逐箭头校准，不允许共享 marker 的全局改动误伤其他箭头。

参考阈值：端点误差 ≤ 画布对角线 0.25%，中心线 P95 ≤ 0.35%，头部角度误差 ≤ 3°。

## 容器与重复图元

所有容器内文字/公式必须声明稳定 ID、`data-layout-container` 和 padding。所有重复圆/节点必须声明 `data-repeat-group`、轴和唯一顺序。

工具分别审计 source 与 backend：

- 容器/画布越界默认 ≤0.25 px；
- 重复图元宽高差与同轴漂移 ≤0.25 px；
- 相邻中心距范围 ≤1 px。

source 失败表示视觉测量或候选几何错误；backend 独立失败表示转换/保存重开漂移。两者都可阻止 strict。mapping 公式超框和纵向圆组不齐必须作为通用合同失败，而不是只对案例 01 写死修复。

## 六个双色圆与颜色

重复几何规则只约束尺寸、轴和间距，不允许六个圆共享一个未经测量的固定渐变。每个圆的渐变方向是独立视觉属性。颜色探针使用 ΔE00；案例 01 标准采样点上限为 5，并同时要求区域 SSIM/Edge IoU。

## 微资产

允许：用户授权、紧边界、不可约、无正式文字/公式/拓扑的参考图裁剪。

禁止：整图、宽松区域、包含可重建正式内容的裁剪，或用位图掩盖失败结构。

`assets.json` 和 PowerPoint shape Tags 必须共同记录：asset ID、参考哈希、bbox/紧边界、授权依据、rights uncertainty、不可约理由、`editable=false`。严格位图区域 SSIM ≥0.95。

## 状态与区域验收

```text
prepared → candidate → qa_failed/repairing → approved
```

只有 `autofigure check --profile strict` 在零 blocker 时能写 approved。critical 的硬底线为 SSIM ≥0.85、Edge IoU ≥0.75，案例级覆盖值只能提高，不能降低。小图标、括号和 caption 等小目标还应使用紧边界前景墨迹合同，比较 bbox、中心与面积；彩色 caption 使用 reference-bound subject bbox 与逐目标 obstacle bbox 的前景净空合同，同时验证目标仍存在且几何未漂移，避免与括号、省略号或箭头接触。若一个对象合同必须覆盖非连续目标而其外接矩形会吞入无关对象，可另外声明全局 `pixel_bbox` 作为像素比较 ROI；分析 `bbox` 仍承担对象/净距范围，二者都必须写入 `critical_region_expectation` 并精确冻结，禁止临时扩大白底刷分。障碍像素门使用目标色 mask、Edge IoU、核心前景 ΔE、尺寸和净距的组合；原始 RGB SSIM/均值仅作抗锯齿诊断。对经几何判定的低填充率细开放笔画，可使用 0.55 的 renderer-aware mask floor，但 Edge IoU、bbox、面积、颜色和原生绑定仍全部是硬门禁。全图 mean/SSIM/changed 只作诊断，不能覆盖局部失败。

strict 无 critical region 时加入 `regions:no-critical-regions`。案例可用 `critical_region_expectation` 冻结必需的关键区 ID 清单，防止对象级门禁被静默删除。PPTX/参考/scene/bindings 哈希漂移、保存重开失败、绑定不完整、ArrowSpec/PrimitiveSpec 读回失败、未授权裁剪均阻止批准。`python-pptx` 重开只能证明包可读，不能充当 PowerPoint 宿主保存重开。

strict 禁止 `--skip-ocr`，必须闭合 reference inventory 的精确文字清单与 SVG 文字，并无条件消费 PowerPoint Live finalizer 证据。`--require-live` 仅保留命令行兼容，不再是可选开关。

PowerPoint `save_candidate` 只能作为中间保存重开证据，通过 `autofigure repair --save-reopen-only` 发布后必须保留 `live-render-finalizer-unverified`。不得手工把项目生命周期审批或 evidence provenance 改为 PASS/REVIEWED 来绕过正式 finalizer。

## PowerPoint Live

```bat
autofigure repair <case>
autofigure check <case> --profile strict
```

managed session 必须显式绑定 case、project、target、revision 与幂等键。先 inspect/audit，只修改失败区域，保存、关闭重开、重新渲染并再次审计。

当前 PowerPoint Live 2.1.1 标记为 `arrow_authoring_unverified`，只能检查和保存重开；具体能力矩阵与启用条件见 `POWERPOINT_ARROW_CAPABILITY_SPEC.md`。

“会话能打开、对象能回读、保存重开哈希一致”只证明 backend integrity。没有针对当前失败区域的 hash-bound region result 时，不得写 `live-evidence.json`，strict 保留 `live-evidence-missing`。

## 插件边界

默认栈只有 PowerPoint、`powerpoint-live` 和 Autofigure。OneKeyTools10 仅隔离试点；iSlide 是人工素材源；ThreeD Tools 按明确三维案例再评估。插件安装成功不等于 AI 可调用；生产 provider 必须结构化调用、指定 shape、结果回读、幂等、undo，且不得用 Ribbon 坐标点击、SendKeys 或视觉点击。

## 验证命令

```bat
autofigure cases --write-index
autofigure cases --check
autofigure compare <seeded> <direct>
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python -m ruff check tools tests
.venv\Scripts\python -m compileall -q tools tests
```
