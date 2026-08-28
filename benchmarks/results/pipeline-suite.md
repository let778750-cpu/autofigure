# Pipeline 基准报告（Case05 gate 阶梯 + 跨案例管线基线）

生成自 pipeline-suite JSON；数字一律机器采集，单样本如实标注，不伪造分位数。

## Fixture 校验

| 输入 | SHA-256（前 16 位） |
|---|---|
| `reference.png` | `ef0e94b0ee05e3af` |
| `external-seed.svg` | `79683946fc2b4fa2` |
| `external-seed-repaired.svg` | `a954bfd12aeb46a7` |

## 1. Case05 source-gate 修复阶梯（5 次采样）

| 候选 | 决策 | wall median (s) | min | max |
|---|---|---|---|---|
| original-seed | `reject` | 0.0242 | 0.0226 | 0.0405 |
| repaired-seed-unstamped | `repair` | 0.0301 | 0.0293 | 0.0309 |
| repaired-seed-stamped | `accept` | 0.0292 | 0.0278 | 0.0298 |

## 2. 确定性核心管线基线（8 个正式案例副本，各 1 次 cold）

| 案例 | convert (s) | math (s) | check (s) |
|---|---|---|---|
| `svg-seeded/01-modular-agent` | 7.4089 | 7.4337 | 12.427 |
| `svg-seeded/02-thinking-diffusion` | 6.3895 | 0.1287 | 3.4727 |
| `svg-seeded/03-llmind` | 6.0087 | 6.068 | 3.5614 |
| `svg-seeded/04-pareto-conditioned-diffusion` | 6.7639 | 0.1261 | 4.5475 |
| `svg-seeded/05-sting-autophagy` | 7.016 | 0.21 | 14.2606 |
| `reference-only/01-modular-agent-reference-only` | 6.7022 | 6.7799 | 8.8954 |
| `reference-only/02-thinking-diffusion-reference-only` | 6.5509 | 0.1082 | 4.1424 |
| `reference-only/04-pareto-conditioned-diffusion-reference-only` | 6.6011 | 0.0946 | 3.9272 |

## 案例级 Case05 状态

- svg-seeded：`implemented` — 完整案例已落地（prepare→合同生成→freeze 391 对象→ingest 盖章变体 gate accept→convert→math→check standard），管线数字见 pipeline 层 svg-seeded/05-sting-autophagy 行。
- reference-only：`case-frozen-awaiting-candidate` — 案例合同已冻结；重绘候选须仅依据 fixture reference 由视觉执行者产出，候选落地后补跑该路线管线与作者阶段耗时（Issue #19）。

测量边界：当前进程及其子进程树（RSS/IO/CPU-wall）；PowerPoint COM 服务器经 RPC 激活、不在子进程树内时其资源不计入——如实记录测量边界

性能基准不授予 `approved`；任何失败如实保留。

