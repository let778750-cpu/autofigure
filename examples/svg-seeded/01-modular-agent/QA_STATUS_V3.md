# 案例 01：v3.1 本地审核状态

日期：2026-08-21。该文件记录本次实现后的事实状态，不是批准记录。

## 当前结论

- 工作流最终必须保持 `qa_failed`；当前仍有 5 个全图级 strict blocker，不能标记 complete/approved。
- 当前 PPTX SHA-256：`3031a42f9befb4bba598f93b4cbd6d2caf5e148de5325b36ecea760631154c4d`。
- PowerPoint 保存重开回读：1 页、196 个对象、196 个场景对象均有 binding；22 个公式为原生 Office Math。
- 箭头结构审计：41 个箭头单元、42 个 marker 引用；F1/F2/F3/F5/F6/F7/F8/F9/F10 均为 0。右上灰箭头在面板上层；两个授权复杂微资产均为紧边界原图裁剪位图，`editable=false`。
- Environment globe 不是 SVG 仿画：PPTX 图片媒体与 `reference.png` 的 `[1188,533,80,81]` 裁剪逐字节、逐像素一致。
- 未安装、启用或调用 OneKeyTools10、iSlide、ThreeD Tools 或其他第三方 PPT 插件。

## 布局硬门现状

### mapping 公式

- 容器 `[769,34,181,48]`，标签 `[790,46,106.6,34]`，公式 OMML `[867,46,76.4,34]`（PowerPoint Live 回读）；标签与公式对容器的最大越界均为 `0 px`。
- 参考图可见暗像素横向范围为标签 `791–872`、公式 `887–924`；当前 PowerPoint 原生渲染公式为 `885–922`，未触及容器右边界 `950`。
- 转换器按 `data-layout-container` 归属对透明文本余量做容器级裁切（画布级之外）。

### 橙色/青色三圆排列

- 橙色中心 `(367,239)/(367,277)/(367,314)`，青色中心 `(368,399)/(368,437)/(368,474)`；两组尺寸均为 `34×34 px`，中心距均为 `38/37 px`，交叉轴漂移均为 `0 px`。
- 参考图像素质心估计：橙色组约 `37.71/36.76 px`，青色组约 `37.82/36.56 px`；`1 px` 的相邻距差属参考图整数像素量化。
- SVG 源、保存重开的 PPTX 和 PowerPoint Live 回读三者完全一致。

## 通用规则与误差归因

- 容器关系必须显式声明 `data-layout-container`，重复元素必须逐项声明稳定 ID、group、axis 和 order；几何关系一律来自显式声明。
- `layout-audit.json` 同时核对 SVG source 与 PPT backend。仅 source 失败表示视觉测量/候选坐标错误；仅 backend 失败表示 SVG→PPT/OMML 漂移；两端都失败则先修源再复核转换器。
- 默认硬门：容器或画布越界 `≤0.25 px`，同组尺寸差 `≤0.25 px`，交叉轴漂移 `≤0.25 px`，连续中心距最大差 `≤1 px`。
- 本案例 OOXML 感知的画布审计覆盖全部 196 个绑定对象，1429×627 画布四向越界均为 0；布局报告 `PASS`、finding 为 0。
- 通用 `slides_test.py` 的灰边检测在本文件上是假阳性：它应把对象平移 `89.72 px`，但 `mc:AlternateContent` 中 22 个 OMML Choice 的实际平移均为 0，因而把公式留在新增灰边。原始 PPTX 的 370 组普通/OOXML bounds 实测无越界，PowerPoint 原生渲染也无越界。项目因此以原包 OOXML bounds + 保存重开 + 原生渲染为批准依据。
- 布局误差按三类分开报告——候选源坐标错误、转换器容器约束缺失、参考测量/量化对齐偏差；全图 SSIM 不用于推断原因。

## 严格区域结果

| 区域 | SSIM | Edge IoU | 结果 |
|---|---:|---:|---|
| Task-Guided Expert Allocator | 0.7454 | 0.7549 | 失败：SSIM 未达 0.85 |
| 六个双色状态圆（x≈923，非本轮橙/青栈） | 0.6077 | 0.8672 | 失败：SSIM 未达 0.85 |
| Rollout 箭头区 | 0.7267 | 0.7887 | 失败：SSIM 未达 0.85 |
| Observation 箭头区 | 0.8631 | 0.6862 | 失败：Edge IoU 未达 0.75 |
| Observation 微资产 | 1.0000 | 1.0000 | 通过 |
| Environment globe 微资产 | 1.0000 | 1.0000 | 通过 |

六个双色状态圆的 12 个方向采样点全部通过，最大 ΔE00 为 2.2187（门槛 5.0），但该区域整体 SSIM 尚未通过。全图 mean `14.7877`、SSIM `0.7762`、Edge IoU `0.8308` 仅作报告指标，未覆盖局部失败。

当前 5 个 blocker：

- `region:task-guided-allocator-topology`
- `region:six-bicolor-state-circles`
- `region:rollout-arrow-topology`
- `region:observation-arrows`
- `live-evidence-missing`

## PowerPoint Live 回读

- 使用本机 `powerpoint-live` 2.1.1 在可见托管副本中检查，会话 `3a946aa4-e3e8-47b1-84ed-580b2a5cc06a`、revision 0；审核后已关闭。
- 服务端结构审计 0 个 hard finding、0 个 warning；PowerPoint 原生导出 SHA-256 与离线 render SHA-256 相同：`a3ecb65aac4f35ed725d6d8bdf0da63f50500659aadeab8bd95850ce0e1cf4f3`。
- 保存候选与重开文件 SHA-256 相同：`7cbdabd6362ae6d2110f302939bf91dee288fdc84e3b539babc08f7401d587cb`；保存前后对象 inventory SHA-256 同为 `f6fce486c19a15ccd957514f7d0bad466fbde1f195390efa8c21377174ec1c88`。
- 本证据只证明 mapping、橙/青重复组与保存重开稳定性，不代表四个仍失败区域已完成 live 修复。因此没有生成会清除全局 blocker 的 `live-evidence.json`。

## 只有 PNG 的第二入口

- `autofigure prepare <ref.png> --input-route reference-only` 会冻结参考哈希、生成区域任务并默认进入 `png_reconstruct + hybrid_fidelity`，不要求先取得 Web SVG。
- 已使用同一 ModularAgent 参考图建立隔离的 `examples/reference-only/01-modular-agent-reference-only/`：只共享参考图和路线无关验收阈值，不读取本案例的 SVG、PPTX、scene、bindings、assets、裁剪文件或候选坐标。
- 真实对照已完成 candidate 摄取、原生 PPTX 转换、22 个 Office Math、保存重开、PowerPoint Live 审计和 strict；结果为 2/6 关键区通过，保持 `qa_failed`。结论只能写“全链路跑通但质量未成熟”。
- 统一报告见 `examples/route-comparison-modular-agent-route-ab.md`；当前全量测试为 `110 passed`，Ruff、compileall、案例合同检查与 `git diff --check` 通过。

详细机器证据见 `qa/layout-audit.json`、`qa/live-layout-review.json`、`qa/regions-report.json`、`qa/arrows-audit.json` 和 `qa/convert-summary.json`。
