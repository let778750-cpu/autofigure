# 10 · 来源抽取、去重与冲突裁决

> 本文件用于审核，非运行时必读。目标是保留跨案例成立的规范，删除 provider 客户端、历史运行产物、重复 schema、固定模板和与当前逻辑冲突的旧规则。

## 来源与保留精华

| 来源 | 保留 | 不带入 |
|---|---|---|
| `claude设计` | 交叉采样反幻觉、可恢复公式源、比例与安全区、语义色/数据色、反模板、生成与审查职责分离 | 外部视觉客户端、`.env`、历史 `vision_raw`、个案坐标、编译缓存 |
| `codex设计` | 目标物理宽度 profile、Core Gate/Quality Signal/Profile 三层模型、证据双通道、单一权威源、自动化最多 CANDIDATE | 旧插件缓存、重复 skills/schemas、过度治理、与官方 MCP 重复的审计/渲染脚本 |
| `deepseek设计` | 源像素坐标、参考哈希、稳定对象 ID、measurement uncertainty、父子关系、route hints、precision_policy/qa_plan | GLM/VLM 客户端、`.env`、案例专用 palette/bbox |
| `D:\自动AI科研绘图` | 冻结目标、目标元素注册、严格对象映射、约束回归、父子 containment、公式不猜测、白盒结构+渲染证据、Core/Quality 分层 | image2 生成/回填、远程 VLM/OCR、draw.io-first、固定三轮、18 阶段文件树 |
| `xjb-skill-image-to-vba` | 纵横比安全映射、element manifest、先骨架后细节、保留几何意图 | VBA 默认后端、Office 宏依赖、用裁片保留高质量子元素、静默缩放或无明确失败条件的 `compare_images` |
| 官方 `scientific-illustrator` | 实时 PowerPoint/draw.io/WPS MCP、原生对象、分区构建、Designer–Drawer–Reviewer–Corrector 循环 | 不复制代码/长说明，声明依赖复用 |

官方后端核验基线：GitHub 主线 `v1.5.4`，commit `3a44435da8715b7d380d5b594259e3f495c5b336`；含 `drawio-live`、`drawio-file-utils`、`powerpoint-live` 三个 MCP server。

## 去重后的唯一规范域

1. `01-workflow-contract.md`：状态、元素注册、坐标与证据。
2. `02-qa-gates.md`：Core Gate / Quality Signal / Profile 三层 + 红线。
3. `03-style-principles.md`：反模板 + 美化暂缓 + 权威顺序。
4. `04-publication-journal-standards.md`：期刊逐刊数值（深表）。
5. `publication-profiles.yaml`：可执行 profile（物理单位）。
6. `05-png-authority-boundary.md`：权威拆分。
7. `06-asset-policy.md`：表示分类、原子素材与临时槽的唯一契约。
9. `08-anti-hallucination.md`：反幻觉 + 对旧逻辑的反驳。
10. `09-backend.md`：后端复用边界 + MCP 注册。
11. `11-agent-vision-protocol.md`：外层 Agent 原生视觉协议（任务包/四类查询/校验/融合/不变量）。

相同规则只在一个文件定义，其他文件链接引用。

## 关键冲突裁决

| 冲突 | 裁决 |
|---|---|
| 旧流程 draw.io-first vs 当前主要交付 PPTX | 默认 PowerPoint，用户选择才 draw.io |
| OCR vs 原生视觉 | 采用已锁定的本机 PP-OCRv6 作确定性文字候选；原生视觉负责结构，用户/可靠原文负责关键语义；禁远程或生成式 OCR 自证。三模融合裁决细化：PP-OCRv6 管文字候选；Host CV 管像素可测事实；外层 Agent 原生视觉按 `references/11-agent-vision-protocol.md` 任务包协议管结构提议、受限仲裁（只选不写）、公式 LaTeX 提议（多采样自一致、PROPOSAL_ONLY）与漏检巡查；TRIPLE 一致不豁免人审，融合只改变审核排序；用户/原文仍是文字与公式唯一授权 |
| image2 回填 / 参考裁片 vs 用户手动生成高质量子元素 | 一律改为诚实可替换的 `manual_asset_slot` |
| 一比一复刻 vs 自动改成投稿规范 | 分离 Fidelity 与 Publication 两个 verdict；源固有问题由用户选优先级 |
| 全部正交连线 vs 反馈/轨迹/注意力的语义曲线 | 路由由语义与障碍决定；安全/端点/拓扑是硬门，形状是策略 |
| 纯白底/删除 footer 的通用死规则 vs 有色目标/严格复刻 | 只在对应 profile 或用户授权规范化时执行；保真模式保留并记录风险 |
| 原生视觉 bbox 被当成精确测量 | 记录置信度 + `uncertainty_px`，通过对象读回和渲染迭代收敛 |
| SSIM / 整体观感作为最终真值 | 相似度只作辅助诊断；对象映射/文本/拓扑/几何/编辑性/局部证据不可缺 |
| reviewer 必须是另一模型 vs 轻量单 skill | 保留逻辑角色隔离 + 新鲜证据；自动化最多 CANDIDATE，批准交用户 |
| “Reviewer 修好坏初稿” vs 首稿质量优先 | 把 OCR、画布、碰撞和文字适配前移到 preflight；major finding 回 `REGION_REPLAN`，Corrector 只做 minor patch |

## 对旧逻辑的反驳

1. 「视觉模型看懂后即可一次性一比一」不成立：原生视觉擅长语义与大结构，但小字/端点/细线/遮挡/精确 bbox 必须靠保存后对象读回 + 多轮渲染校准。
2. 「量化规范越多越可靠」不成立：能证明错误的规则才适合硬门；固定卡片比例/颜色数/对象数/统一正交路线应是 profile 或软信号，否则把科研图模板化。
3. 「修得更规范就等于更好的一比一」不成立：源 PNG 可能本身违反投稿规范；不声明偏离就自动修正，会同时破坏保真与审计诚实性。
4. 「复杂微资产嵌入越多越接近原图」不成立：它提高像素相似度却降低编辑性并掩盖视觉幻觉；人工槽位诚实承认能力边界，也更高效。
5. 「单一 SSIM 高就完成」不成立：缩放/背景/大面积相同区域会掩盖关键文字、箭头和局部结构错误。
6. 「同一视觉模型第二遍会自动纠正第一遍误读」不成立：共享误解会相关传播；必须引入 OCR 候选、显式未知、源/模型哈希和确定性 preflight。
