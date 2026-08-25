# 案例回归策略

## 影响矩阵

| 变更类型 | 必测范围 | 附加证据 |
|---|---|---|
| 单案例源或合同 | 该案例全部 critical regions；同 comparison group 的另一输入路线 | 独立候选、共同 reference/oracle identity、无跨路线候选复用 |
| Scene/转换器/布局/箭头/数学 | 所有受影响案例与至少一条正反路线代表案例 | source → PPTX bindings → 保存重开 readback 分层结果 |
| QA、阈值、Schema 或状态迁移 | 全量单元测试、所有消费字段的案例、旧格式兼容样本 | 通过样本、失败样本、边界值与 mutation test |
| reference/oracle/compare | 同一 reference SHA 下的全部路线与区域 | oracle 哈希、语义 inventory、拓扑和几何等价性 |
| 纯文档 | 文档链接与 hygiene | 明确不改变运行或科学合同 |

## 回归记录

每个受影响案例在 PR 中记录：

- 案例相对路径、`input_route`、processing mode 与 reference SHA；
- scene/candidate/PPTX/render/QA hashes；
- critical region 的失败断言、当前结果与反例；
- 新增、消失和不变的 blocker；
- 执行命令、平台、时间与报告相对路径；
- 人工 Reviewer 对全图、局部裁剪和非声明的结论。

跨路线对照只共享指定参考和路线无关评价基准。reference-only 不得读取 seeded 候选的 scene、SVG、PPTX、bindings、assets、裁剪或坐标；两边内部自洽但基准不一致时，对照门禁应失败。

## 关闭条件与如实状态

Issue 只有在以下条件同时满足时才由 PR 的 `Closes #N` 闭环：

1. 原缺陷由测试或冻结证据稳定复现；
2. 当前 head 满足该 issue 的全部验收；
3. mutation/反例证明门禁对同类回归敏感；
4. 全量 CI 与影响面回归绿色；
5. 范围内 blocker 为零，范围外已知失败有独立记录且没有恶化；
6. Reviewer 检查绑定当前 head 的证据。

Issue 闭环只表示该主题段落已进入 `develop`，不等于 `main` release、案例 `approved` 或科研发布。

## 回滚与基线更新

`develop` 的最小回滚单元是单个 PR 的 squash commit。回滚后重新打开对应 issue、标记其审核与证据失效，从最新 `develop` 建新的主题分支重新交付；原主题分支不复活。

只有参考、科学合同或获批验收定义确实变化时才允许更新案例基线。PR 要说明权威来源、旧/新哈希、迁移影响和用户批准；为让失败测试变绿而移动阈值、裁剪区域或参考不是合法基线更新。
