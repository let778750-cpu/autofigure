# 输入路线 A/B：modular-agent-route-ab

冻结参考 SHA-256：`792a16d4bd2c26cca9fca79668395a987825ab75eb2bc8a65f2d42a47c38a340`

| 输入路线 | 案例 | 对象数 | 可编辑文字 | 原生公式 | 可编辑箭头对象 | 箭头审计发现 | 关键区通过 | strict 状态 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| svg-seeded | `01-modular-agent` | 196 | 44 | 22 | 46 | 0 | 2/8 | failed |
| reference-only | `01-modular-agent-reference-only` | 186 | 45 | 22 | 40 | 57 | 2/9 | failed |

## 结论

- reference-only pipeline completed, but quality is not validated mature。
- 两条路线共用的只有冻结参考图与路线无关验收阈值；reference-only 候选未读取 svg-seeded 候选资产。
- 全图均值仅作诊断，任何关键区域失败都会阻止 approved。
- reference-only 的紧边界 observation/globe 微资产通过 2/2 个关键区；这验证 PNG 裁剪机制，不等于其余原生结构已经达标。
- PowerPoint 保存重开：svg-seeded=True，reference-only=True；PowerPoint Live 仍只有独立复核权限。

## 明细

机器可读指标见同名 JSON；区域 SSIM、Edge IoU、ΔE00、箭头问题代码和 blocker 均未省略。
