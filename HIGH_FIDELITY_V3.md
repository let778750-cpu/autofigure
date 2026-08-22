# Autofigure v3.1 高保真执行合同

## 输入必须显式

```bat
autofigure prepare reference.png --case seeded --input-route svg-seeded
autofigure prepare reference.png --case direct --input-route reference-only
```

未提供 `--input-route` 直接失败。旧 `--source-mode` 仅保留一版弃用检查，不能替代路线、不能推断 provenance。

`input_route` 一旦写入不可更改；`processing_mode` 可回退：

```bat
autofigure ingest seeded --rejected --fallback png_reconstruct
```

这不会把 seeded 案例移动到 `reference-only`。

## reference-only 的真实边界

reference-only 建案会生成模型无关的 `qa/region-tasks.json`。执行者只需读取当前案例冻结的 `reference.png`；可以生成 scene、region patch 或内部 SVG 载体。

“没有外部 SVG 种子”不等于“无需视觉模型”。当前完整离线初版仍以 SVG 为可渲染载体，任意科研图的语义分解与坐标测量仍需 VLM 或人工。真实 ModularAgent 测试已经完成全链路，但 strict 只有 2/6 关键区通过，因此能力结论是“真实跑通但质量未成熟”。

受控 A/B 的构建隔离：除同一 `reference.png` 与路线无关验收 bbox/阈值外，reference-only 侧不得读取或复制 seeded 的 SVG、PPTX、scene、bindings、assets、裁剪文件或候选坐标。微资产必须从自己的参考图重新裁剪。

## 箭头

- 普通直线、肘形和可连接曲线：PowerPoint 原生 connector。
- 原生线端支持头型、宽度、长度；服务端不支持时必须明确 custom-freeform 回退。
- 粗弯块箭头：单一闭合 freeform；确实无法单体表达时才允许杆+头，且必须分组、共享语义 ID、按切线校准。
- 任何 z-order 问题必须通过对象顺序/PowerPoint z-order 修复，不能用截图遮盖。例如 mapping 灰箭头必须位于目标框边界之上。

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

只有 `autofigure check --profile strict` 在零 blocker 时能写 approved。默认 critical 阈值：SSIM ≥0.85、Edge IoU ≥0.75。全图 mean/SSIM/changed 只作诊断，不能覆盖局部失败。

strict 无 critical region 时加入 `regions:no-critical-regions`。PPTX/参考/scene/bindings 哈希漂移、保存重开失败、绑定不完整、未授权裁剪均阻止批准。

## PowerPoint Live

```bat
autofigure repair <case>
autofigure check <case> --profile strict --require-live
```

managed session 必须显式绑定 case、project、target、revision 与幂等键。先 inspect/audit，只修改失败区域，保存、关闭重开、重新渲染并再次审计。

“会话能打开、对象能回读、保存重开哈希一致”只证明 backend integrity。没有针对当前失败区域的 hash-bound region result 时，不得写 `live-evidence.json`，strict 保留 `live-evidence-missing`。

## 插件边界

默认栈只有 PowerPoint、`powerpoint-live` 和 Autofigure。OneKeyTools10 仅隔离试点；iSlide 是人工素材源；ThreeD Tools 按明确三维案例再评估。插件安装成功不等于 AI 可调用；生产 provider 必须结构化调用、指定 shape、结果回读、幂等、undo，且不得用 Ribbon 坐标点击、SendKeys 或视觉点击。

## 验证命令

```bat
autofigure cases --write-index
autofigure cases --check
autofigure compare <seeded> <direct>
.venv\Scripts\python -m pytest tests\v2 -q
.venv\Scripts\python -m ruff check tools\v2 tests\v2
.venv\Scripts\python -m compileall -q tools\v2 tests\v2
```
