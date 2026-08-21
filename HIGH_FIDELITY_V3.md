# Autofigure v3：高保真重建、PowerPoint Live 与插件边界

本文件描述 v3 的实际运行合同。它不承诺仅凭一次 Web SVG 就自动得到“一比一”结果；严格任务只有在所有关键区域通过后才能进入 `approved`。

## 输入模式与失败回退

项目保留三种来源模式：

- `svg_import`：接收 Web/VLM 给出的初版 SVG。
- `svg_repair`：保留可用 SVG 结构，只重做失败区域。
- `png_reconstruct`：可从只有 PNG 的新 case 直接进入，也可在初版 SVG 被拒绝后进入；以 `reference.png` 为唯一视觉基准生成模型无关的区域任务，默认配合 `hybrid_fidelity`。

只有 PNG、没有任何 Web SVG 时，执行：

```bat
autofigure prepare reference.png --case my-case --source-mode png_reconstruct
```

该命令直接冻结参考图、写入四份合同并生成 `qa/region-tasks.json`，不会把“去 GPT Web 获取 SVG”作为前置步骤。若已有 Web SVG 但被拒绝，则执行：

```bat
autofigure ingest examples\my-case --rejected --fallback png_reconstruct
```

会把状态切到 `qa_failed → repairing` 并生成同一任务协议。Codex、GPT Web、Kimi 或其他 VLM 均可返回 SVG、完整场景图或区域补丁；工具只认合同与稳定对象 ID，不认模型品牌。

能力边界必须明说：当前离线初版转换器的可渲染载体是 SVG；scene/region patch 适用于已有可渲染载体的局部修复，或交给 PowerPoint Live provider。测试已覆盖“只有 PNG 建案 → 任务包 → 摄取任意视觉执行者给出的 SVG 候选 → 原生可编辑 PPTX”，这证明第二入口的管线连通，不等于项目在没有视觉模型或人工判断时能自动完成一比一重建。

## 场景控制与状态机

每个 case 使用四个显式控制文件：

- `scene.json`：对象 ID、角色、源几何、层级与连接拓扑。
- `assets.json`：微资产来源、授权、边界和 `editable` 状态。
- `regions.json`：关键区域、颜色采样点和验收阈值。
- `bindings.json`：场景对象到 PowerPoint shape ID/name 的回读绑定。

容器和重复图元还必须在 SVG/scene 中保留显式布局关系。`data-layout-container` 约束文字、公式或图元不得越出指定容器；`data-repeat-group/data-repeat-axis/data-repeat-order` 约束同组图元等尺寸、同轴和规则中心距。`qa/layout-audit.json` 分别报告 source 与 backend：前者失败属于视觉测量/候选几何错误，后者失败属于转换或保存重开漂移。backend 还会以包级 OOXML 审计全部绑定对象的画布边界，覆盖高层 python-pptx 不暴露的 OMML Choice。

状态固定为：

```text
prepared → candidate → qa_failed → repairing → candidate → approved
```

`approved` 只能由 `autofigure check --profile strict` 写入。全图 mean/SSIM 只做诊断，不能覆盖任一关键区域失败。PPTX、参考图或绑定哈希漂移都会阻止批准。

## 箭头实现

- SVG 中的普通直线箭头被转换为 PowerPoint 原生 connector。
- 简单三角/开放箭头映射为 DrawingML `headEnd`/`tailEnd`，支持 `type`、`w`、`len`；不再默认拆成“线段 + 三角形”。
- 曲线路径保留可编辑 freeform，并使用原生 DrawingML 线端箭头；PowerPoint 会按路径末端切线定向。
- 无法原生表达的复杂 marker 会产生明确 warning，并将箭杆与自定义箭头物理分组；不会静默替换成默认箭头。
- 带 `data-source-id` / `data-target-id` 的直线 connector 会写入 OOXML 连接关系，并在 `bindings.json` 回读。

箭头审计已经修复“箭头把自身路径当目标边界”的错误，并增加：

- 嵌套 SVG transform 展平和奇异变换检查。
- 源/目标对象身份核对。
- 参考中心线 P95、端点误差、箭头切线角误差。
- 箭头交叉和文字碰撞检查。
- 逐箭头（`arrow-id` 或 `arrow-id:start|end`）头长校准；共享 marker 会按需克隆，避免一处校准改变全部箭头。

## 微资产边界

文字、公式、节点和箭头必须保持原生可编辑。照片、小地球等无法可靠矢量复原的复杂资产可以从已冻结的参考图按紧边界裁剪，但必须在 `assets.json` 中：

- 明确 `authorized=true` 及授权依据；
- 记录 bbox 和参考哈希；
- 标记 `editable=false`；
- 禁止覆盖整个画布或借位图掩盖正式结构。

授权裁剪图的 asset ID、裁剪图 SHA-256、不可约原因、紧边界声明和“无可重建正式内容”同时写入 PowerPoint shape Tags。这样 `assets.json` 的授权不会只停留在外部文件，`powerpoint-live` 保存重开后也能审计图片对象本身。

严格模式会阻止未授权的 reference crop。案例 01 的 observation 与 environment globe 已按用户要求登记为授权微资产。

## PowerPoint Live 混合修复

离线转换始终先生成可审计初版。只有失败区域需要打开可见 PowerPoint 画布：

```bat
autofigure repair examples\my-case
```

该命令生成 `qa/live-repair-request.json`，其中包含参考哈希、失败区域、场景 ID、shape 绑定和所需能力。MCP 操作者必须使用 managed session，完成对象检查、区域修复、保存、关闭重开、重新渲染，并返回 hash-bound `live-evidence.json`。AI 没有自行放行权限。

当前 `powerpoint-live` 服务端只接受 Scene `2.0.0/2.1.0`，而 Autofigure 的正式场景合同是 `3.0.0`。`repair` 因此会在 `qa/powerpoint-live-case/` 生成一份只用于 MCP 的派生 Scene 2.1 桥接包及服务端要求的七份合同；`scene.json` 仍是唯一源事实，禁止把 Scene 3.0 直接提交给服务端。桥接使用服务端内置的 `journal-double-column` profile 来通过合同解析，但画布宽高仍严格取冻结参考图的像素尺寸，不会按 profile 改变构图。

建立并关闭只读托管会话只能证明“合同可解析、PowerPoint 可见画布可打开、对象可回读”，不等于完成 live 修复。只有实际修复后执行保存/重开、重新渲染并写入与当前 PPTX 哈希一致的 `live-evidence.json`，`live-evidence-missing` 才能消失。

项目根目录 `mcp.json` 只配置 `powerpoint-live`。`tools/powerpoint_mcp_launcher.mjs` 会寻找当前 Codex scientific-illustrator 插件中的服务器，也支持用 `AUTOFIGURE_POWERPOINT_SERVER` 明确指定；不再写死某台电脑的绝对路径。

PowerPoint MCP 不可用时仍可输出离线初版，但 `hybrid`/strict 任务会保留 `live-evidence-missing` blocker。

## 第三方插件选择

第三方插件不是核心依赖，也不应全部安装。

| 插件 | 决策 | 自动化边界 |
|---|---|---|
| OneKeyTools10 | 唯一隔离试点候选，当前不安装 | 只有兼容、安全、许可、结构化 API、shape 回读、幂等与 undo 全部通过后才可成为 provider |
| iSlide | 可选人工素材源 | 不视为本地按钮级 API，不能恢复参考图原始资产 |
| ThreeD Tools | 明确三维案例后按需验证 | 不进入二维静态图默认流程 |
| OKPlus、英豪/LvyhTools | 首期排除 | 能力重叠或没有已验证 API |
| 美化大师、OfficePLUS | 排除自动化 | 可能改变参考风格 |
| 动画大师、口袋动画 | 排除 | 当前交付是静态科研图 |

统一 provider 协议为 `discover / health / capabilities / execute / inspect / undo`。原生 PowerPoint provider 永远优先。生产实现禁止 Ribbon 坐标点击、SendKeys 和图像识别点击。

查看本机实际发现结果：

```bat
autofigure providers --json
```

“插件安装成功”和“AI 可结构化调用”是两个不同条件。当前实现不会安装、启用或修改任何第三方 Office 加载项。

## 严格验收

```bat
autofigure check examples\my-case --profile strict --require-live
```

默认关键区域阈值为 SSIM ≥ 0.85、Edge IoU ≥ 0.75；授权位图微资产为 SSIM ≥ 0.95。箭头参考阈值为端点误差不超过画布对角线 0.25%、中心线 P95 不超过 0.35%、头部角度误差不超过 3°。颜色探针使用 ΔE00，案例 01 的六圆上限为 5。

布局硬门默认值：容器越界 ≤0.25 px；重复图元宽高差 ≤0.25 px、横轴/纵轴中心漂移 ≤0.25 px、相邻中心距范围 ≤1 px。1 px 间距容差只用于吸收参考图整数像素分配的 N/N+1 步长，不允许把明显不均匀排列解释成抗锯齿。

任何 blocker 都会返回非零退出码并写入 `qa_failed`，不会把未通过的结果伪装成 complete。

`autofigure math` 注入 OMML 后会保存重开并同步刷新 `scene.json`、`bindings.json` 与 PPTX artifact hash；公式位于 `mc:AlternateContent` 时使用包级 OOXML 名称/公式计数回读，避免把 python-pptx 不暴露公式 shape 的限制误判为绑定丢失。

同理，不能用“扩展画布后平移高层 shape”的通用检查替代本项目的 OMML 感知审计：若检查器只移动 AlternateContent 的 fallback 而漏移 Choice，灰边中的公式是假阳性。批准依据必须是原包 bounds、PowerPoint 保存重开回读与原生渲染三者一致。
