# 矢量创作车间升级计划(免费开源默认栈 + 付费候选项)

> 状态:**Phase 0/1/2 已实现并在真实案例上验证**(vtracer 微资产真矢量通道
> atomic-vector:provider 注册、freeze 资格预分类、`autofigure trace` 命令、convert
> 矢量分支、check 五门门禁,真实案例 01 全链路实测);**Phase 3/4/5 未实施**,其能力
> (Inkscape 修版会话、确定性几何整理、素材库通道)不得写入 README / SKILL.md 的能力
> 描述,不得在案例 provenance 中当作已具备的来源。未验证项在本文内一律显式标注
> `[待验证]`。
>
> 基线原则:**默认栈全部由免费开源组件构成**;付费/闭源方案只作为候选项列入 §5.3,
> 逐项披露成本与风险,不作默认。

## 0. 一句话定位

为 autofigure 增加一个**上游矢量创作/修版车间**,默认栈为 vtracer(确定性描摹)+
Inkscape(可视修版与 CLI 清洗)+ 免费科研矢量库(bioicons / SciDraw),把当前唯一非原生层
(授权位图微资产)升级为真矢量可编辑对象,并为 source 侧失败区域提供与 PowerPoint Live
对偶的可视修版会话;交付物形态(原生可编辑 PPTX)与全部现有质量门禁不变。

## 1. 目标与非目标

### 目标

1. **微资产真矢量化**:`atomic:` 微资产从「reference.png 紧边界位图裁剪(editable=false)」
   升级为「真矢量 SVG 片段 → 确定性编译为 PowerPoint 原生 freeform 组(editable=true)」,
   位图裁剪保留为回退层。
2. **source 侧修版会话**:失败区域中属于「视觉测量或候选几何错误」(source 失败)的部分,
   获得 Inkscape 可视最小修改会话;与 PowerPoint Live(backend 侧)形成对偶,二者职责
   互不替代。
3. **三源素材策略**:标准元素优先取自免费科研矢量库;案例特有复杂主体走确定性描摹;
   位图裁剪是最后回退。来源全部进 provenance 审计。

### 非目标(明确不做)

- 不改变交付物形态:产物仍必须是原生可编辑 PPTX,SVG 仍只是中间载体。
- 不抬高 OOXML 表达天花板:mesh gradient、混合模式、任意 mask、路径文字等仍禁止;
  任何上游产物必须落在现有 SVG 合同子集内,超集内容 strict 失败或降级为授权位图。
- 不引入 Ribbon 坐标点击、SendKeys、图像识别点击(沿用现行插件边界法律)。
- 不用任何 source 侧会话替代 strict 的 PowerPoint Live finalizer 证据(交付物是 PPTX,
  backend 侧证据不可替代;见 HIGH_FIDELITY.md「PowerPoint Live」节)。

## 2. 事实基础(全部为仓库内可复核证据)

### 2.1 几何层天花板已经被 custGeom 解决

`tools/pipeline/convert.py` 的 `_emit_freeform` 手写 OOXML `a:custGeom`,完整保留
`a:moveTo / a:lnTo / a:cubicBezTo`(三次贝塞尔);`_svg_dimension`/`_px` 保证像素坐标
确定性换算。冻结证据:`examples/route-comparison-modular-agent-route-ab.json`
(2026-08-23 受控 A/B 快照):svg-seeded 案例 `01-modular-agent` 196 个绑定对象
(含 freeform 12、freeform-arrow 7)在该快照中保存重开验证通过。注意:该案例此后
经历过 native-math 升级与再转换,当前 revision 的保存重开证据需在该案例下一次
strict 推进时重新获取;本结论只引用上述冻结快照。

**结论:轮廓几何层面不存在「PPT 画不出的形状」。** 真实约束在效果层(渐变类型、线端
marker 枚举、混合模式),这些是 OOXML 格式约束,引入任何上游创作工具都不会改变。

### 2.2 当前唯一非原生层:微资产位图

`examples/reference-only/01-modular-agent-reference-only/assets.json`:两个微资产
`atomic:observation`、`atomic:environment-globe`,`source=reference_crop`、
`editable=false`、`atomic_raster_unit=true`。A/B 报告中它们是仅有的两个通过区域
(SSIM/Edge IoU 均为 1.0)——位图裁剪机制有效,但该层是全项目唯一以位图存在的层,
也是本计划的主要升级对象。用「可编辑性换像素完美度」必须显式授权,见 §8 末段。

### 2.3 失败区域集中在 source 侧,而 PowerPoint Live 管不到 source

`examples/route-comparison-modular-agent-route-ab.json`(同一冻结参考 SHA-256 的受控 A/B):

| 指标 | svg-seeded | reference-only |
|---|---:|---:|
| 可编辑箭头(bindings 计数) / 箭头审计发现(审计覆盖 41 条) | 46 / **0** | 40 / **57** |
| 关键区通过 | 2/8 | 2/9 |
| expert-gap-arrow-text-detail SSIM | 0.6915 | 0.6964 |
| mixture-arrow-occlusion-detail SSIM | 0.5802 | — |
| strict 状态 | qa_failed | qa_failed |

这类失败归因为 source 侧(视觉测量或候选几何错误,分类见 `tools/repair/repair_plan.py` 的
source_model/backend blocker 划分)。PowerPoint Live 会话只能修改 PPTX 对象,无法修正
source SVG 本身;`live-evidence-missing` 长期存在于 blocker 列表。**source 侧
需要一个能直接编辑矢量载体的可视会话**——这是 Phase 3 的动机。

### 2.4 外部证据:复杂有机主体的来源不是 tool call

- **定量上限**:SVGenius 基准(见 §12 出处 11;22 个主流多模态模型、2377 条查询)
  显示所有模型随 SVG 复杂度上升系统性退化,image-to-SVG 任务最强模型的 SSIM 仅
  0.37–0.56,远低于本项目既有 0.85–0.95 门禁档位。LLM 逐坐标生成精美有机形态,
  当前没有可行性。
- **模式参照**:cell-lct(见 §12 出处 8)是一个「AI 编排 + Illustrator 回放」的
  中文社区实现,其工作流为六段链:文字清单 → 清字 → 整图付费矢量化 → 文字以真
  `<text>` 回填 master SVG → 几何缓存一次解析 → 持久连接分批回放(每批 20–50 原子)。
  其中被确定性重建的只有文字;复杂主体来自整图矢量化服务。该项目无开源许可证、
  核心能力依赖不可复现的闭源付费服务,仅作模式参照,不引入任何依赖。
- **结论**:精美有机主体的期望来源是**确定性描摹引擎、素材库与人**,LLM 是编排者
  而非画手。本计划的三源策略与该证据一致;描摹引擎默认采用免费开源的 vtracer。

## 3. 诊断修正(为什么是这个设计而不是别的)

1. **「PPT 画不出很多形状」在几何层不成立**(§2.1)。若以「补形状能力」为动机接入
   上游创作工具,会在管线中段增加一个昂贵环节,而 custGeom 编译出口的天花板纹丝不动。
2. **真实缺口是创作车间与 source 修复**:web VLM 的 SVG 不稳定(用户实测与 §2.4 定量
   证据一致),reference-only 路线箭头审计发现 57 处;微资产层只能位图化。两者都指向
   「上游产出质量」,而非「中段表达能力」。
3. **因此上游工具的正确角色 = source authoring / source 修版 provider**,产物是
   SVG 合同子集内的片段或经修版会话产出的候选,经既有 `ingest → convert → check` 通道进入
   交付物;它们不直接产出 PPTX,也不绕过任何门禁。

## 4. 总体架构

```text
三源输入(三源素材策略,默认全部免费开源)
  A. 标准元素库(bioicons / SciDraw,逐图标 license 审计)   ──┐
  B. 案例特有矢量生成(默认 vtracer 确定性描摹)             ──┼→ SVG 合同子集片段
  C. 位图裁剪(现状机制,最后回退)                          ──┘        │
                                                                          ↓
外部 SVG 种子(svg-seeded,现状) ─┐                                 ingest(扩展 origin)
                                 ├→ master SVG(SVG_AUTHORING_CONTRACT)─→ convert(确定性编译,
执行者重绘(reference-only,现状)─┘                                      新增 vector 微资产分支)
                                                                          ↓
                                                        PPTX → check(standard/strict oracle)
                                                                          ↓
                                              失败区域 ┌─ backend 侧 → PowerPoint Live(现状)
                                                       └─ source 侧 → Inkscape 修版会话(新增,
                                                          与 Live 对偶)→ 回灌 ingest → convert → check
```

Provider 注册表(`tools/providers/providers.py` 的 `_CATALOG`)
新增两行;Illustrator 等付费栈见 §5.3,不进默认注册表:

| provider_id | role | 初始状态 | capabilities |
|---|---|---|---|
| `vtracer` | `source-authoring`(描摹) | `candidate-pilot`(Phase 2 完成前 `selected=false`) | `trace`, `svg-fragment-export`, `path-normalize`, `svg-contract-check` |
| `inkscape` | `source-repair`(修版/清洗) | `candidate-pilot`(同上) | `min-repair-session`, `cli-clean`(scour), `svg-contract-check` |

## 5. Provider 协议合规设计

### 5.1 通用纪律

完整实现 `ProviderAdapter` 六方法(`discover / health / capabilities / execute / inspect /
undo`):

1. **undo 语义按栈而异**:vtracer 是纯函数转换(输入哈希 → 输出),`execute` 天然幂等、
   `undo` 天然平凡(无外部状态);Inkscape GUI 修版是人工会话,以「会话前快照 + 请求包
   哈希绑定」满足 undo 与审计(与 `JournaledMockProvider` 语义对齐)。不满足 undo 的
   provider 不得进入生产栈。
2. **幂等键**:`execute(operation, idempotency_key)`;同键重复调用返回既有事务。会话
   显式绑定 case、reference SHA-256、revision、目标区域清单(与 `tools/repair/repair.py`
   `build_live_request` 的哈希绑定结构同构)。
3. **前台披露**:任何会把应用切到前台的栈,`health` 必须如实报告;会话期间不得并发
   操作其他前台应用。

### 5.2 默认栈(全部免费开源)

- **vtracer**(§12 出处 1):Rust 核心 + Python 绑定/CLI,原生全彩确定性描摹,无前台
  切换,可直接嵌入管线。输出为纯色 path 堆叠(无渐变、无文字对象),天然落在 SVG
  合同子集内。风险:PyPI 当前为 0.6.x/1.0.0a 阶段,版本定型前锁定版本号并记录进
  provenance。适用面已经 Phase 0 试点与真实案例 01 校准:平面插画类微资产可过 §8
  矢量档,照片类留 atomic-raster 位图层(资格由 freeze 预分类确定性判定)。
- **Inkscape**(§12 出处 2):GUI 承担 Phase 3 可视修版画布;CLI action + scour 承担
  确定性清洗与路径简化。直接命令行调用,**不经第三方 MCP 中间层**(inkscape-mcps
  最后代码提交为 2025-10、未发布 PyPI,引入无收益)。Inkscape 未预装时需用户确认后
  安装(免费,winget 可得)。

### 5.3 付费/闭源候选项(不作默认,逐项披露)

| 候选 | 成本 | 关键风险/边界 | 适用场景 |
|---|---|---|---|
| Adobe Illustrator + ie3jp/illustrator-mcp-server(§12 出处 3) | MCP 本身 MIT 免费;**Illustrator 为订阅制付费软件** | Windows 通道(PowerShell COM → DoJavaScript)作者自述未在真机实测;修改/导出类工具执行时切前台;与直接 ExtendScript/COM 直驱技术等同,MCP 层只加依赖不加能力 | 本机已有 Illustrator 订阅时的对照描摹臂与可视修版变体 |
| Adobe 官方 Illustrator MCP(Beta)(§12 出处 4) | Illustrator Beta 订阅 | 仅 Beta;40 工具;按 2026-08 官方文档:可创建文档/图层,不支持从零创建图形对象,不支持保存文档(仅导出 PNG/JPEG/SVG/PDF) | 同上,能力更受限 |
| vectorizer.ai / Vector Magic(§12 出处 9) | 约 $0.10–0.20/张,或 $9.95–9.99/月;Vector Magic 桌面版 $295 买断 | 闭源服务;输出需过合同子集校验;provenance 记 `conversion-service` | 复杂整图一次性矢量化 |
| alisaitteke/photoshop-mcp / loonghao/photoshop-python-api-mcp-server(§12 出处 5/6) | Photoshop 订阅;生成式功能另需 Firefly credits | 仅允许作用于已授权微资产层的光栅整理,禁止触碰正式结构(内部纪律) | 微资产位图回退层的光栅整理 |
| vtracer-mcp(自建) | 免费 | 暂缓:vtracer 是确定性 Python 库,管线内直调更简单可测;MCP 包装只在「AI 会话直接调描摹」场景有价值。届时参照 ie3jp 的工程模式(工具清单、幂等键、前台披露) | AI 会话内交互式描摹 |

排除项:kevinschaul/illustrator-mcp-server 与 KEYHAN-A/illustrator-mcp 均仅 macOS
(§12 出处 7),本项目为 Windows 环境。

## 6. 分阶段实施计划

### Phase 0 — 手动试点(gate:不通过则本计划终止于此)

人工完成微资产全链路,不写任何生产代码:

1. 取 `atomic:environment-globe`(bbox [1188,533,80,81],简单、边界清晰)与
   `atomic:observation` 两个微资产区域;
2. 用 vtracer 对区域参考裁剪执行彩色描摹,必要时调 1–2 个参数;
3. 人工核对 SVG 落在合同子集内(无 mesh gradient/mask/混合模式;渐变仅
   `linearGradient`;无 `<image>` 残留;文字未被转路径);
4. 走现有 `ingest --kind svg` / `convert` 试编译,确认 freeform 组生成、保存重开
   可通过;
5. 用现有紧边界 ink_contract 思路人工比对渲染差异,并与现状位图层(SSIM 1.0)对照。

产出:`docs/vtracer-pilot/` 试点 memo 与样例(已归档);§8 矢量档阈值已按试点数据
与真实案例 01 校准。

### Phase 1 — Provider 注册与基础设施(已实现)

- tools/providers/providers.py `_CATALOG` 已含 `vtracer` 行(role=`source-authoring`,
  `selected=false`,`status=candidate-pilot`);`VtracerAdapter` 实现六方法协议,
  vtracer 为纯函数幂等:`execute` 按幂等键去重(同键重放返回既有事务),`undo`
  平凡幂等,`health` 经 importlib.metadata 如实披露引擎版本(当前锁定 0.6.15,
  requirements.txt 钉版)。
- `autofigure providers --json` 输出可见;pytest 覆盖六方法协议与 undo 幂等。
- Inkscape 注册行与 adapter 属 Phase 3 范围,未实现。

### Phase 2 — 微资产真矢量通道(atomic-vector,已实现)

- **资格预分类**(tools/assets/asset_spec.py):freeze 时对每个带 bbox 的机会图项实测
  `compute_trace_eligibility`(tools/assets/asset_trace.py;4 bit 量化唯一色数为主判据:
  ≥256 photographic、≤128 且有硬边 flat-illustration、其余 ambiguous),写入
  `trace_eligibility` + `trace_eligibility_statistics` 冻结字段(成对出现,随
  receipt 哈希绑定;旧案例无此字段只读兼容)。
- **合同扩展**(tools/assets/asset_spec.py + 案例内 `assets.json`):资产表示
  `atomic-vector` 为 11 字段闭集合——`id`(`atomic:<slug>-vector`)、
  `editable=true`、`source=vtracer-trace`、`vector_source_svg`(案例内相对路径 +
  SHA-256)、`trace_method`、`trace_engine_version`、`authorization_basis`、
  `rights_status`、`fallback_atomic_raster`(指回原位图条目,保留回退)、
  `ink_contract_region_id`、`trace_eligibility`;`validate_atomic_vector_asset` /
  `audit_atomic_vector_assets` 校验闭集合。library / conversion-service 来源值
  属 Phase 5 与 §5.3,未实现。
- **命令形态**(tools/assets/trace.py):`autofigure trace <case> --asset <id>
  [--allow-ambiguous]` 只对已授权 `reference_crop` 位图条目工作;按 bbox 从本案例
  reference.png 重裁,重裁哈希与条目 `source_sha256` 不一致即拒;photographic
  一律拒(留位图层),ambiguous 需显式 `--allow-ambiguous`;事务化写入(失败全
  回滚),产物 `assets/<slug>-vector.svg` + 输入裁剪留档,重跑幂等;冻结区
  (policy / microasset_opportunity_map)逐字节不动。provenance 记
  `asset_trace_history`(origin=`vtracer-provider`、候选 SHA-256、引擎版本、
  参数、eligibility 实测),`--candidate-origin` choices 已含 `vtracer-provider`。
- **描摹执行**(tools/assets/asset_trace.py):锁定参数 colormode=color、
  hierarchical=stacked、color_precision=6、path_precision=3,默认 spline;
  机械补齐 viewBox;`check_svg_contract_subset` 白名单 svg/g/path,越子集即拒。
- **convert 分支**(tools/pipeline/convert.py):`_emit_atomic` 矢量分支按「条目 id==元素
  id 或 `fallback_atomic_raster`==元素 id」匹配(歧义 fail closed);片段经
  custGeom 编译为单个原生 freeform group,**不经 `add_picture`**;bindings
  `object_kind="atomic-vector"`、`editable=true`;shape Tags 记录资产 id、源哈希、
  editable、origin、引擎版本。
- **check 门禁**:见 §8;五门(合同、原生性、区域保真、provenance、回退审计)
  报告写 `qa/atomic-vector-report.json`,blocker 命名
  `atomic-vector:<id>:<reason>`;失败时明确 `fallback-required` blocker,回退到
  既有 `atomic-raster` 路径是显式、留痕的上层工作流动作。
- **摄取合同**(tools/pipeline/prepare.py):`SVG_AUTHORING_CONTRACT` 第 4 条允许经授权的
  内联矢量组 `<g id="atomic:...">`(版本化,旧案例不回改、只按建案时合同审计)。

### Phase 3 — source 侧修版会话(与 PowerPoint Live 对偶)

- tools/repair/repair.py 新增 `--provider inkscape` 分支:`build_inkscape_request`
  与 `build_live_request` 同构(显式 case、reference SHA-256、revision、幂等键、
  失败区域清单、source SVG 片段、区域期望),产出 `qa/inkscape-session/` 请求包。
- 会话流程:Inkscape GUI 导入 source SVG → 最小修改(只碰失败区域)→ 导出 SVG →
  CLI + scour 清洗 → 合同子集校验 → `ingest --kind svg --candidate-role repair-candidate
  --candidate-origin inkscape-provider` → `convert` → `check`。
- 证据纪律与 `live-evidence` 同等:无真实区域修复结果时不得写
  `inkscape-session-evidence.json`;**strict 仍无条件要求 PowerPoint Live
  finalizer 证据,Inkscape 会话证据是 source 侧补充,不替代、不免除**。
- 分工边界写死:backend 失败(保存重开漂移、OOXML 读回不一致)→ PowerPoint Live;
  source 失败(视觉测量、候选几何)→ Inkscape 会话。

### Phase 4 — 确定性几何整理(可选,低优先)

- 在 authoring 期把 OOXML 表达不了的效果**预解析为可表达基元**(clip → 显式几何、
  复杂重叠 → 路径并集),经 Inkscape CLI action 执行,全程确定性、可留痕。

### Phase 5 — 标准素材库通道

- bioicons / SciDraw 等免费科研矢量库做逐图标 license 审计后进入 `assets.json`
  来源类型 `library-vector`(字段同 §Phase 2,`source=library`,附许可证记录)。
  **两库均为逐图混合许可**(CC-0/CC-BY/MIT 等),CC-BY 图标带署名义务,必须落入
  `rights_status`;license 审计是义务而非可选。
- 原则:**标准元素优先库(零成本、零不稳定)、案例特有元素走确定性描摹、位图裁剪
  最后回退**。

## 7. 代码与合同变更清单(逐文件)

| 文件 | 变更 | 阶段 |
|---|---|---|
| `tools/providers/providers.py` | `_CATALOG` 加 `vtracer` / `inkscape` 行;两个 adapter | 1 |
| `tools/core/contracts.py` | `--candidate-origin` choices 增加 `vtracer-provider` / `inkscape-provider`;资产表示常量 `atomic-vector` / `library-vector` | 2/5 |
| `tools/pipeline/ingest.py` | origin choices 扩展;矢量片段摄取与合并路径 | 2 |
| `tools/pipeline/convert.py` | `_emit_atomic` vector 分支(复用 `_emit_freeform`,不经 `add_picture`);shape Tags 扩展 | 2 |
| `tools/pipeline/check.py` | 矢量微资产门禁(§8);回退审计 | 2 |
| `tools/repair/repair.py` | `--provider inkscape` 分支;`build_inkscape_request` | 3 |
| `tools/pipeline/prepare.py` | `SVG_AUTHORING_CONTRACT` 第 4 条占位规则追加「或经授权的内联矢量组 `<g id="atomic:...">`」;**版本化变更,旧案例不回改、只按建案时合同审计** | 2 |
| 案例合同 | `assets.json` 新表示;`regions.json` 的 `critical_region_expectation` 在 freeze 时冻结矢量微资产期望 | 2 |

## 8. 矢量微资产 QA 门禁

| 门禁项 | 判据 | 出处 |
|---|---|---|
| 紧边界 ink_contract | 前景 bbox、中心、面积(沿用小目标合同) | PROJECT_ARCHITECTURE.md §6 |
| 结构相似度 | SSIM ≥ **0.80**(矢量档,经校准;使用时必须 freeze 冻结) | docs/vtracer-pilot/README.md §3(插画类描摹源像素尺寸 SSIM 0.81–0.82,0.90 档不可达);案例 01 `qa/regions-report.json`(environment-globe 矢量实测 0.8065 过档) |
| 边缘重合 | Edge IoU ≥ **0.75**(沿用全局 critical 底线) | HIGH_FIDELITY.md;案例 01 矢量实测 0.8825 过档 |
| 颜色 | ΔE00 探针,案例冻结采样点 | HIGH_FIDELITY.md |
| 原生性 | 产物全部为原生 shape/freeform,无位图残留;`editable=true` 且 shape Tags 完整 | SKILL.md 转换规范 |
| 合同子集 | 无 mesh gradient / mask / 混合模式 / `<image>` / 文字转路径 | SVG_AUTHORING_CONTRACT |
| provenance | origin、候选 SHA-256、引擎版本、会话事务 ID 留档 | SKILL.md 案例合同 |

两点限定:

- 矢量资产达不到位图层的 1.0 是预期内的:用「可编辑性换像素完美度」必须是一个
  **显式、逐资产授权**的选择,禁止全局默认替换。
- 像素指标对矢量质量存在系统性盲区(StarVector,§12 出处 10:MSE 类像素指标无法
  刻画矢量质量);SSIM/Edge IoU 在此只作底线门禁,不作质量上限判据,阈值按上表
  出处校准、使用时必须 freeze 冻结。

## 9. 纪律边界(禁止事项)

1. 禁止任何上游产物携带 SVG 合同子集外效果进入正式候选(strict 失败,不豁免)。
2. 禁止栅格化任何正式结构(文字/公式/节点/箭头/拓扑)——微资产层之外的位图化
   沿用现行禁止条款。
3. 禁止把 source 侧会话证据当 strict finalizer;PowerPoint Live 证据不可替代。
4. 禁止「描摹引擎/素材库产出所以免检」:所有产物与 web VLM 候选同款门禁。
5. 禁止在 provenance 中猜测来源:origin 只能取枚举值,未知即 `unknown`;
   描摹引擎名称与版本必须如实记录。
6. 成本披露:默认栈(vtracer/Inkscape/素材库)免费开源;§5.3 候选项的成本与风险
   以该表为准,对外表述不得超出已验证事实。

## 10. 测试计划

- **单元**:provider 六方法协议、undo 幂等、同幂等键重放返回既有事务;
  SVG 片段合同子集校验(含拒绝样例);`_emit_atomic` vector 分支 golden 输出;
  vtracer 输出的确定性(同输入同输出字节)。
- **集成**:Phase 0 样例全链路(ingest→convert→math→check standard);回退路径
  (矢量失败 → atomic-raster)留痕。
- **回归**:`pytest -q` / `ruff check` / `compileall` / `autofigure cases --check` /
  `autofigure hygiene` 全绿;现有案例的合同与哈希不受影响。

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| vtracer 描摹质量不达阈值 | Phase 0/2 失败 | Phase 0 即 gate;回退 atomic-raster 永远保留;三源策略分散单源风险 |
| vtracer 版本未定型(0.6.x/1.0.0a) | 输出字节漂移 | 锁版本并入 provenance;接入时以 golden 测试钉住输出 |
| Inkscape 未预装 | Phase 3 不可执行 | 用户确认后安装;discover/health 如实报告缺失,不静默 |
| 矢量资产阈值定得过松/过紧 | 质量回退或永远过不了 | 初始值仅是建议,Phase 0 用真实数据校准后 freeze 冻结 |
| 新合同字段破坏旧案例兼容 | cases --check 失败 | 合同版本化;旧案例只读兼容,不回改 |
| 外部素材库逐图混合许可 | rights 风险(CC-BY 署名义务) | `rights_status` 字段沿用(记录不确定,不冒充已清权);逐图标审计 |
| 付费候选项被误当默认 | 成本/合规意外 | 默认注册表只含免费栈;候选项选用需显式授权并留痕 |

## 12. 技术出处

### 内部证据(本仓库)

- `examples/route-comparison-modular-agent-route-ab.json` / `.md` — A/B 冻结快照
  (2026-08-23):0 vs 57 箭头发现、逐区 SSIM、两路 qa_failed、196 绑定对象保存重开通过。
- `examples/reference-only/01-modular-agent-reference-only/assets.json` — 现行
  atomic-raster 微资产合同(editable=false、紧边界、授权依据、rights_status)。
- `examples/svg-seeded/01-modular-agent/provenance.json` — `web-vlm` origin 纪律先例。
- `tools/pipeline/convert.py` — `_emit_freeform`(手写 `a:custGeom`,`a:moveTo/a:lnTo/a:cubicBezTo`)、
  `_emit_atomic`(`add_picture` 位图路径)、`register_asset`、shape Tags。
- `tools/providers/providers.py` — `ProviderAdapter` 协议(六方法)、`_CATALOG`、
  `JournaledMockProvider`(幂等/undo 参照实现)。
- `tools/repair/repair.py` — `build_live_request` / `ingest_live_evidence`(哈希绑定请求与
  证据纪律先例);`tools/repair/repair_plan.py` — source_model/backend blocker 分类。
- `tools/pipeline/ingest.py` — `--candidate-origin` choices、`build_region_tasks`。
- `tools/pipeline/prepare.py` — `SVG_AUTHORING_CONTRACT`(双路线共享 SVG 输出合同)。
- `HIGH_FIDELITY.md` / `PROJECT_ARCHITECTURE.md` / `SKILL.md` — 质量门禁、插件
  provider 边界、交付纪律。

### 外部项目与文献

**默认栈(免费开源)**

1. **visioncortex/vtracer**(默认描摹引擎)—
   <https://github.com/visioncortex/vtracer>
   免费开源;Rust 核心 + Python 绑定/CLI;原生全彩描摹;输出为纯色 path 堆叠,
   无渐变/文字对象,天然落在合同子集内。
2. **Inkscape**(修版画布与清洗)— <https://inkscape.org/>
   免费开源(GPL);GUI 可视编辑 + CLI action + scour 清洗,直接命令行调用。
3. **duerrsimon/bioicons** — <https://github.com/duerrsimon/bioicons>
   免费科研矢量图标库(生物/化学);逐图混合许可(CC-0/CC-BY/MIT),CC-BY 带署名
   义务;Phase 5 标准元素来源(逐图标 license 审计)。
4. **SciDraw** — <https://scidraw.io/> 免费科研矢量库(Sainsbury Wellcome Centre
   支持);内容 CC-BY 或 CC0,页脚注明「CC-BY unless stated otherwise」。

**付费/闭源候选项(不作默认,见 §5.3)**

5. **alisaitteke/photoshop-mcp** —
   <https://github.com/alisaitteke/photoshop-mcp>
   102 工具、生成式 AI、Web UI;社区项目,与 Adobe 无隶属;生成式功能需 Adobe
   账户与 Firefly credits。
6. **loonghao/photoshop-python-api-mcp-server** —
   <https://github.com/loonghao/photoshop-python-api-mcp-server>
   包装 photoshop-python-api(Windows COM)的 MCP;Windows-only;备选。
7. **kevinschaul/illustrator-mcp-server** 与 **KEYHAN-A/illustrator-mcp** —
   <https://github.com/kevinschaul/illustrator-mcp-server> /
   <https://github.com/KEYHAN-A/illustrator-mcp>
   均经 AppleScript 桥(后者为 Swift 应用),仅 macOS → 排除。
8. **yrui-cmd/cell-lct**(模式参照,不作依赖)—
   <https://github.com/yrui-cmd/cell-lct>
   Codex Desktop Skill;六段式工作流(文字清单 → 清字 → 整图付费矢量化 → 文字回填
   → 几何缓存一次解析 → 分批回放 Illustrator);「Local raster tracing is forbidden」
   为其合同原文。注意:无开源许可证;核心能力依赖不可复现的闭源付费服务与
   Codex 内置图像能力;本文仅采纳其工作流模式观察,不引入任何依赖或内容。

**候选栈(付费软件驱动)**

9. **vectorizer.ai / Vector Magic**(转换服务)—
   <https://vectorizer.ai/pricing> / <https://vectormagic.com/pricing>
   约 $0.10–0.20/张或 $9.95–9.99/月;Vector Magic 桌面版 $295 买断。
10. **ie3jp/illustrator-mcp-server** —
    <https://github.com/ie3jp/illustrator-mcp-server>
    MIT;npm `illustrator-mcp-server`;工具 60+(README 标题 63、明细表 65);稳定版
    Illustrator CC 2024+;支持从零创建、保存文档、导出 SVG/PNG;README 明示「修改和
    导出类工具执行时会把 Illustrator 切到前台」;**Windows 通道(PowerShell COM →
    DoJavaScript)作者自述尚未在真机实测**;Illustrator 本身为订阅制付费软件。
11. **Adobe 官方 Illustrator MCP(Beta)** —
    <https://helpx.adobe.com/illustrator/desktop/connect-with-other-apps-and-tools/about-using-ai-tools-with-illustrator.html>
    Illustrator Beta 内置(版本号「30.4+」出自 ie3jp README,Adobe 官方页未标注);
    40 工具;按 2026-08 官方文档:可创建/打开文档与图层,可复制/删除/重命名对象、
    编辑简单文本,不支持从零创建图形对象,不支持保存文档(仅导出 PNG/JPEG/SVG/PDF)。

**学术参照**

12. **StarVector**(图像→SVG 代码模型)— <https://arxiv.org/abs/2312.11556>
    同时指出像素级指标(MSE 类)无法刻画矢量质量——§8 门禁设计的限定条件出处。
13. **DeTikZify**(NeurIPS 2024;图→TikZ 程序合成)—
    <https://proceedings.neurips.cc/paper_files/paper/2024/hash/9a8d52eb05eb7b13f54b3d9eada667b7-Abstract-Conference.html>;
    代码 <https://github.com/potamides/detikzify>。实测 MCTS 闭环反馈是重要增效器,
    质量基座仍是基础模型与训练数据;当前模型已到 v2.5。
14. **MatPlotAgent**(执行/视觉反馈闭环)—
    <https://github.com/thunlp/MatPlotAgent>(arXiv 2402.11453)。
15. **PPTAgent**(编辑式生成路线的比较性证据)—
    <https://github.com/icip-cas/PPTAgent>(中科院软件所,EMNLP 2025,
    <https://aclanthology.org/2025.emnlp-main.728/>);编辑现有结构优于从零生成
    为其核心设计论点,基线对比支持该方向(无隔离消融)。
16. **SVGenius**(SVG 生成基准)— <https://arxiv.org/abs/2506.03139>
    22 个主流模型 image-to-SVG 的 SSIM 上限 0.37–0.56——§2.4 定量证据。
17. **Model Context Protocol** — <https://modelcontextprotocol.io/>
