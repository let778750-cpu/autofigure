---
name: ai-autofigure
description: "把科研图 PNG 高保真重建为原生可编辑 PowerPoint；支持 SVG 初版、PNG 区域回退、场景/shape 绑定、局部严格验收和 PowerPoint Live 失败区修复。"
---

# AI AutoFigure v3

## 核心原则

`reference.png` 及其 SHA-256 是唯一视觉基准。VLM 负责视觉理解和区域重建，工具负责可追溯转换、对象绑定和验收。一次 Web SVG 不保证成功；关键区域失败必须进入 repair，不能被全图均值或人工口头判断自动放行。

## 模式

- `svg_import`：初版 SVG。
- `svg_repair`：只修失败 SVG 区域。
- `png_reconstruct + hybrid_fidelity`：可从只有 PNG 的新 case 直接生成区域任务，也可在初版 SVG 被用户拒绝后进入。

执行 PNG 回退：

```text
autofigure ingest <case> --rejected --fallback png_reconstruct
```

直接从 PNG 建案：

```text
autofigure prepare <ref.png> --case <id> --source-mode png_reconstruct
```

该入口不依赖 GPT Web。当前离线初版仍以 SVG 为可渲染载体；scene/patch 需要已有载体或 PowerPoint Live，禁止把入口连通误报为无模型自动一比一重建。

## 必备合同

每个 case 必须有：

- `run.json`：模式、参考哈希、状态机。
- `scene.json`：对象 ID、角色、几何、层级和连接拓扑。
- `assets.json`：微资产授权、来源、bbox、可编辑性。
- `regions.json`：critical 区域及阈值。
- `bindings.json`：scene element 到 PowerPoint shape 的稳定绑定与回读。

状态仅允许：`prepared / candidate / qa_failed / repairing / approved`。只有 strict 零 blocker 才能进入 `approved`。

## 转换要求

- SVG 根尺寸与 viewBox 必须等于参考图像素坐标，且原点为 0,0。
- 文字、公式、节点和箭头保持原生可编辑。
- 普通直线箭头用 PowerPoint connector 和原生 `headEnd/tailEnd`。
- 曲线保留 freeform 路径并用原生线端箭头。
- 复杂 marker 不能静默替换；必须明确 custom-freeform 回退并物理分组。
- `data-source-id/data-target-id` 必须写入拓扑；可表达时写入 PowerPoint 连接关系。
- `stroke="none"` 必须显式清除 PowerPoint 默认轮廓。
- 容器内文字/公式必须有稳定 ID 并声明 `data-layout-container`；重复图元必须声明 `data-repeat-group/data-repeat-axis/data-repeat-order`；保存后的全部绑定对象必须通过 OOXML 感知的画布边界审计。
- 写实微资产只有经用户授权、紧边界、`editable=false` 时可从参考图裁剪。
- 禁止整图截图或用位图冒充文字、公式和正式结构。

## 箭头与区域 QA

箭头审计必须排除箭头自身路径，应用嵌套 transform，并检查对象身份、端点悬空、参考中心线、切线角、交叉和文字碰撞。校准优先使用 `arrow-id` 或 `arrow-id:start|end`，不得用共享 marker 的全局缩放误伤其他箭头。

strict 默认门槛：关键区 SSIM ≥ 0.85、Edge IoU ≥ 0.75；授权位图 SSIM ≥ 0.95；箭头端点 ≤ 画布对角线 0.25%、中心线 P95 ≤ 0.35%、角度 ≤ 3°。全图指标只报告，不放行。

布局审计必须同时报告 source SVG 与 backend PPTX。容器越界、重复图元尺寸差和同轴漂移默认容差 0.25 px；连续中心距范围默认容差 1 px。source 失败是视觉/坐标错误，backend 独立失败是转换漂移，均为 strict blocker。

## PowerPoint Live

先离线生成 candidate，只对失败区域执行：

```text
autofigure repair <case>
```

MCP 必须使用 managed visible session，按 scene/bindings 定位对象，修复后 inspect/audit、保存、关闭重开、fresh render，并提交 hash-bound live evidence。MCP 不可用时可以保留离线 candidate，但 hybrid strict 必须失败为 `live-evidence-missing`。

禁止 Ribbon 坐标点击、SendKeys 和图像识别点击。

## 插件

默认依赖只有 PowerPoint、`powerpoint-live` 和 Autofigure。第三方插件不得成为核心依赖：

- OneKeyTools10：仅隔离试点候选，当前不安装、不选用。
- iSlide：可选人工素材源。
- ThreeD Tools：明确三维案例后按需。
- OKPlus、英豪/LvyhTools、美化大师/OfficePLUS、动画大师、口袋动画：不进入自动化流程。

任何插件必须具备结构化 API、指定目标 shape、结果回读、幂等和 undo，才可成为 provider。

## 标准执行顺序

```text
autofigure prepare <ref.png> --case <id>
autofigure ingest <case> <candidate> --kind svg|scene|patch
autofigure convert <case>
autofigure layout <case>
autofigure check <case> --profile standard
（若失败）autofigure repair <case>
autofigure check <case> --profile strict --require-live
```

交付：可编辑 PPTX、PowerPoint fresh render、四份合同、区域/箭头报告、对照预览及明确的 workflow state。不得把 `candidate` 或 `qa_failed` 描述为完成。
