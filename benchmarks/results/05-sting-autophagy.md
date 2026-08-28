# Case05 STING-autophagy 基准报告

生成自同名 JSON；数字一律机器采集，单样本如实标注，不伪造分位数。

## Fixture 校验

| 输入 | SHA-256（前 16 位） |
|---|---|
| `reference.png` | `ef0e94b0ee05e3af` |
| `external-seed.svg` | `79683946fc2b4fa2` |
| `external-seed-repaired.svg` | `a954bfd12aeb46a7` |

## 1. Case05 source-gate 修复阶梯（5 次采样）

| 候选 | 决策 | wall median (s) | min | max |
|---|---|---|---|---|
| original-seed | `reject` | 0.0174 | 0.0171 | 0.0283 |
| repaired-seed-unstamped | `repair` | 0.0198 | 0.0193 | 0.0204 |
| repaired-seed-stamped | `accept` | 0.0198 | 0.0197 | 0.0201 |

## 2. 确定性核心管线基线（8 个正式案例副本，各 1 次 cold）

| 案例 | convert (s) | math (s) | check (s) |
|---|---|---|---|
| `svg-seeded/01-modular-agent` | 6.5678 | 6.6826 | 9.1707 |
| `svg-seeded/02-thinking-diffusion` | 5.8701 | 0.1403 | 2.9337 |
| `svg-seeded/03-llmind` | 6.2361 | 6.5206 | 4.4813 |
| `svg-seeded/04-pareto-conditioned-diffusion` | 7.4396 | 0.2025 | 6.6621 |
| `svg-seeded/05-sting-autophagy` | 8.0495 | 0.275 | 21.1735 |
| `reference-only/01-modular-agent-reference-only` | 7.8346 | 8.4539 | 15.0846 |
| `reference-only/02-thinking-diffusion-reference-only` | 7.8927 | 0.1748 | 7.148 |
| `reference-only/04-pareto-conditioned-diffusion-reference-only` | 7.393 | 0.1972 | 7.1001 |

## 案例级 Case05 状态

- svg-seeded：`implemented` — 完整案例已落地（prepare→合同生成→freeze 391 对象→ingest 盖章变体 gate accept→convert→math→check standard），管线数字见 pipeline 层 svg-seeded/05-sting-autophagy 行。
- reference-only：`case-frozen-awaiting-candidate` — 案例合同已冻结；重绘候选须仅依据 fixture reference 由视觉执行者产出，候选落地后补跑该路线管线与作者阶段耗时（Issue #19）。

测量边界：当前进程及其子进程树（RSS/IO/CPU-wall）；PowerPoint COM 服务器经 RPC 激活、不在子进程树内时其资源不计入——如实记录测量边界

性能基准不授予 `approved`；任何失败如实保留。
