# 「things/details/action decide skills」范式辩证分析 + 对比实验

> 2026-08-21 · 起因：项目共建者提出范式主张——"根据具体任务需求决定调用哪些 skills 或不调用 skills，不能让需求还没开始做就绑定必须严格用 skills；很多时候不用 skills 效果更好"。本文严格查证开源资料与本项目实证，辩证分析该主张的逻辑边界，并以箭头修复为对象完成对比实验 E1；E2（skills 调用策略任务电池）给出可直接执行的协议。
>
> 阅读提示：本文不是立场文，正反证据都给了出处；结论在 §2.4，实验数据在 §4。

## 1. 范式陈述

把"skills"理解为**预置给模型的知识包/工作指令/规范文档**（本项目的 SKILL.md、输出合同、AGENTS.md；Claude Code 的 skills/rules；Cursor 的 rules）。主张拆成三句：

- **S1（路由权）**：调用哪些 skills 应由任务本身（things/details/action）在开工时决定，而不是预先强制绑定。
- **S2（可不用）**：应当允许"不用任何 skills"作为合法路径。
- **S3（经常更优）**：很多时候不套 skills 效果更好。

## 2. 开源资料查证

### 2.1 支持 S1/S2 的证据（按可信度排序）

1. **Anthropic 官方 Agent Skills 设计 = 渐进式披露（progressive disclosure）**：启动时模型只看到每个 skill 的 name+description（约 100 token），由**模型自主判断相关性**后才加载正文，再按需读引用文件。官方范式本身就是"description 触发、模型自主决定"，明确反对把全部知识预注入上下文。
   - 工程博客：https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
   - 平台文档：https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
2. **学术线：模型自主决定"何时调用"是主流研究方向**——Toolformer（Meta，NeurIPS 2023，自监督学习何时调何工具，https://arxiv.org/abs/2302.04761）；Adaptive-RAG（NAACL 2024，按查询复杂度路由"不检索/单步/多步"，优于固定 always-retrieve，https://arxiv.org/abs/2403.14403）；Self-RAG（reflection token 自主决定是否检索，https://arxiv.org/abs/2310.11511）。
3. **"套 skill 不一定更好"的直接学术证据**：《When Skills Don't Help: A Negative Result on Procedural Knowledge for Tool-Grounded Agents》（攻防安全域，注入程序性 skill 知识未提升任务表现，https://arxiv.org/html/2605.20023v1）。
4. **全局规则注入的实证危害**：Chroma《Context Rot》（上下文变长性能系统性退化且任务间不均匀，https://www.trychroma.com/research/context-rot）；philschmid 上下文工程系列（退化主因是相似指令过多/冲突，https://www.philschmid.de/context-engineering-part-2）；Cursor 官方论坛承认规则过多导致 agent 忽略规则、建议拆成 scoped 按需触发（https://forum.cursor.com/t/cursor-agent-knowingly-ignored-global-rules/150592）。

### 2.2 反对把 S1/S2/S3 推广为普遍规律的证据

1. **护栏必须是"模型劝不动的代码"**：Guardrails AI（RAIL 规范 + 确定性校验 + re-ask，https://github.com/guardrails-ai/guardrails）；Instructor（Pydantic 强制 schema + 自动重试，https://python.useinstructor.com/）；AWS 工程文《AI Agent Guardrails: Rules LLMs Cannot Bypass》给出"幻觉式合规"案例（模型未验证就确认，合规关键步骤必须确定性强制，https://dev.to/aws/ai-agent-guardrails-rules-that-llms-cannot-bypass-596d）。工业界在这些点上**不信任模型自主判断**。
2. **Claude Code 自身就是分层混合制的实现**：知识层（skills/memory/rules）按需触发；强制层（hooks）由 harness 执行——官方明确"the harness executes these, not Claude; memory/preferences cannot fulfill them"（https://code.claude.com/docs/en/hooks）。
3. **模型路由判断并不可靠**：工具调用 overuse/underuse 校准研究（https://arxiv.org/html/2604.19749v1）；文本安全不迁移到工具调用安全（Mind the GAP，https://www.researchgate.net/publication/400970937 ）；社区大量 skill 触发失败案例的根因就是 description 匹配不上（https://medium.com/ai-all-in/four-fixes-for-when-your-agent-picks-the-wrong-skill-d8f0e564dd52）。

### 2.3 该范式已有名字

"任务决定选哪个 skill"在业界与论文中已被命名并活跃研究：**skill routing / dynamic skill routing / dynamic skill selection / agentic skill discovery**（SkillRouter、EvoSkill、SoK: Agentic Skills 综述 https://arxiv.org/html/2604.04323v1 等）。方向正确且非首创，价值在于把边界条件讲清楚。

### 2.4 判定（结论）

| 子主张 | 判定 | 依据 |
|---|---|---|
| S1 路由权归任务 | **成立，但仅限"知识/启发/流程建议"层** | Anthropic 官方设计、Toolformer/Adaptive-RAG/Self-RAG、context rot 实证 |
| S2 允许不用 | **成立，同上限定** | When Skills Don't Help 负结果；Cursor rules bloat |
| S3 经常更优 | **部分成立，存在三个逻辑漏洞** | 见下 |

三个逻辑漏洞（辩证修正）：

- **L1 以偏概全**：从"很多场景不套更好"推广到普遍规律。安全/合规/可复现域（支付验证、schema 校验、lint/CI/pre-commit、红线）的反例密集——错误代价越高，越不能交给模型自觉。
- **L2 范畴混淆**：skills（prompt 层、建议性知识）与 guardrails/gates（代码层、强制性）不是同一范畴。"decide skills"只对前者成立；后者的价值恰恰在于**不被任务细节决定、不可协商**。正确的问法不是"用不用 skills"，而是"这道动作该放 prompt 层还是 harness 层"。
- **L3 路由可靠性盲区**：按需调用依赖模型对任务与 skill 描述的相关性判断，实证会漏判/误判。任务的错误代价越高，纯"按需"的风险越大——需要用确定性触发器（路径匹配、显式命令、hooks）兜底。

**最佳表述（本文立场）**：**描述性知识按需路由，规范性约束代码强制；错误代价决定一道动作放在 prompt 层还是 harness 层。**

## 3. 本项目双重案例研究（范式在 autofigure 的两次实证）

### 3.1 正向案例：v1 → v2 就是"去过度绑定"

v1 重型确定性管线（27k LOC、约 30 轮迭代、4 天/图、mean 19.9987）把"如何看图"全部绑死在确定性 skill/管线里；v2 转为 VLM-first, verify-light（模型负责看图重绘，工具只做确定性转换与核验，约 1 天/图，三案例 mean 6.87-16.60，全部优于 v1）。**去掉过度预置的"技能绑定"、把路由权交给模型 + 轻核验，正是该范式主张的收益**——本项目整个 v2 就是 S1/S3 的活证据。

### 3.2 反向案例：箭头 bug 恰是"该强制处未强制"

01-modular-agent 的箭头缺陷（头线脱开/偏轴/比例失调）三轮修复（c244905 合同条款、586e1c1 实心化、d0aa01a 无关）都没修好，根因链：

1. **知识层条款无力**：合同/规范里箭头要求全是描述性文字（"以原图为准""不得套用固定风格"），没有任何可执行校验——模型"读了"但没人验证。
2. **确定性层缺位**：两个 convert 真 bug（曲线末端按弦方向而非切线放置 → 43-47° 偏轴；marker-start 180° 反向）+ 8 个 marker 全部 refX≠尖端，属于代码层几何事实，改提示词/改合同永远修不到。
3. **反馈回路失明**：一支箭头 ≈ 画布 0.04%，mean 对箭头修好修坏仅动 0.06（噪声级），top_roi 注意力被照片区吞掉，OCR 文本比对不覆盖几何——**缺陷类对整个反馈回路不可见**，VLM 自批评轮甚至把结构质量改坏了（见 E1：R1→R2 发现数 13→75）。

**教训（对应 L2）**：这不是"skills 用多了"也不是"用少了"，而是**层次放错**——该放 harness 层的确定性几何校验，被留在了 prompt 层当建议。2026-08-21 的修复正是把箭头规范从"描述性条款"升级为"机器可检子句 + `autofigure arrows` 确定性审计/修复"，一次归零（E1）。

## 4. 对比实验 E1（已完成）：箭头修复三臂

**对象**：01-modular-agent 的 42 处 marker 箭头。**度量**：`autofigure arrows` 结构审计发现数（同一把尺子量三臂）；mean/SSIM 为辅助软信号。**臂数据**：

| 臂 | 做法 | F1 锚点 | F2 比例 | F3 悬空 | 合计 | 头/线宽中位 |
|---|---|---:|---:|---:|---:|---:|
| A：VLM 自批评轮（R1→R2，2026-08-19 实测） | 模型看图自我修订箭头样式（实心化+放大） | 0→**42** | 7→**27** | 6→6 | 13→**75** | 3.33→**5.0** |
| B：确定性修复（R2 + convert 切线修复 + `arrows --fix --clamp-ratio`） | 纯代码几何归一，零模型参与 | **42→0** | 27→**6** | 6（不自动修） | 75→**12** | 5.0→**3.78** |
| C：混合（A→B 序列，即实际采用路径） | 先 VLM 定样式，后代码定几何 | — | — | — | **12** | 3.78 |

**结论**：

1. **VLM 自批评轮（纯 prompt 层循环）把像素指标修好了（mean 17.40→16.57）却把结构质量修坏了**（审计发现 13→75）——因为回路里没有能看见几何的传感器，这正是 S3 的适用域也是其失效域的同一枚硬币。
2. **确定性修复一次归零 F1**，且不改任何样式（颜色/填充/线宽逐字节保留）；`arrows --fix` 后的 PPTX 实测：三角尖端精确落在线端点、底边沉入、轴对称（(661,125)/(341,274)/(1104,182) 三处抽查与解析式 0.1px 吻合）。
3. **F3 端点悬空（6 处）三臂都治不了**——它是 VLM 布局级缺陷，审计定位后仍需人审/重绘决策：又一次"知识层看报告、决策层归人"的分层例证。
4. 像素指标对三臂几乎无分辨力（mean 16.63→16.60），再次确认：**凡反馈回路测不到的维度，改多少轮都不会收敛**——这是"为什么一直改不好"的最终答案。

## 5. 对比实验 E2（协议，待执行）

**问题**：skills 调用策略（绑定/不用/按需）对任务产出的质量-成本-合规有何影响？

### 5.1 设计

- **自变量（3 臂）**：
  - **A 绑定**：现状——SKILL.md + 合同 + AGENTS.md 全量注入工作指令（当前项目工作方式）。
  - **B 裸奔**：只给任务一句话，不注入任何 skills/规范。
  - **C 按需**：先给任务卡（things/details/action 三行），由执行模型输出"我判断需要加载哪些规范模块及理由"，仅加载其所选（范式主张的形态）。
- **任务电池（8 个，分层抽样自本项目真实任务史）**：
  | # | 任务 | 层 | 验收可机械判定度 |
  |---|---|---|---|
  | T1 | 新案例全流程（prepare→VLM→convert→check→math） | 综合 | 中（check 报告+人审） |
  | T2 | 箭头缺陷修复（SVG 几何） | 知识密集 | **高（arrows 审计计数）** |
  | T3 | 公式框 OMML 注入 | 机械-合规 | 高（math-summary + 测试） |
  | T4 | 新 SVG 按合同手写（02 案例复刻） | 知识密集 | 高（合同 lint + 文本比对） |
  | T5 | 写实照片区域处理决策 | 合规关键 | 高（atomic 占位 or 违约） |
  | T6 | README/文档改版 | 创造性 | 低（人审盲评） |
  | T7 | check 报告解读与放行决策 | 判断-合规 | 中（红线违例计数） |
  | T8 | 重构 convert 局部代码 | 工程创造 | 高（pytest 58 项） |
- **因变量**：① 验收通过（机械项）/盲评得分（人审项，评审不知臂别）；② 红线违例数（如位图冒充文字、截图冒充 render、OCR 滥用）；③ 轮次、token 消耗、墙钟；④ C 臂专项：路由查准率/查全率（该加载没加载、不该加载加载了）。
- **控制**：同一模型同版本、同温度；每任务每臂跑 3 次取中位；VLM 网页环节由同一人同口径操作。
- **预注册假设**（防止事后编故事）：
  - H1：T6/T8（创造性）上 C ≥ A（按需省 context、少教条）；
  - H2：T5/T7（合规关键）上 A ≥ C > B，B 出现红线违例；
  - H3：T2/T4（知识密集且验收机械）上 A ≈ C > B；
  - H4：C 的成本（token/轮次）≤ A；
  - H5：C 臂路由漏判集中在"任务描述未点名但规范必需"的场合（对应 L3）。
- **执行配方**：每臂一套 prompt 模板（A=SKILL+合同全文前置；B=单句任务；C=任务卡+自主选单）；结果落 `qa/e2/<task>/<arm>-<run>/`；汇总脚本算上述指标出表。
- **样本量与判定**：8 任务 × 3 臂 × 3 次 = 72 次；主判定看"机械可判定任务"的通过率差异（显著性用 Fisher 精确检验，n 小），人审项只报告效应量不作显著性断言。
- **成本估计**：约 72 次 agent 会话 + 8 次 VLM 网页环节人工；建议分 4 批执行，每批 2 任务。

### 5.2 已知限制

单模型单项目，外部效度有限；C 臂的路由质量依赖模型本身（H5 其实是范式 L3 的再次检验）；盲评人即项目共建者，存在偏好风险——结论只对本项目工作方式下结论，不外推普遍规律（吸取 L1 教训）。

## 6. 给本项目的操作建议（范式落地）

1. SKILL.md/合同保持"精简红线 + 按 subcommand 分节"（现状已接近），不扩写成大全——知识层越薄，路由越准（context rot 实证）。
2. 每一条"必须"级别的红线，问一句"它有没有对应的机器检查"：有 → 代码层强制（如文本读回、arrows 审计、viewBox 校验）；没有 → 要么补检查，要么诚实降级为"建议"并在 check 报告里给人审留位。
3. 新增规范条款时默认写成**可验证子句**（本文箭头条款的写法：数值带 + 审计命令），拒绝只有形容词的条款。
4. E2 跑通后，若 H1/H4 成立，可把日常开发工作流切到 C 形态（任务卡 + 按需加载），合规项仍走 A。

---

### 出处可信度说明

Anthropic 平台/工程文档为官方一手；arXiv 为同行评审或预印本；Chroma/philschmid/ClickHouse 为高质量工程研究；Medium/Reddit/论坛仅作社区佐证。所有 URL 于 2026-08-20 检索有效。
