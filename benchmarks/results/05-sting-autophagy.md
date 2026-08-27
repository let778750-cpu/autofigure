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
| original-seed | `reject` | 0.0247 | 0.0236 | 0.0419 |
| repaired-seed-unstamped | `repair` | 0.0296 | 0.0289 | 0.0309 |
| repaired-seed-stamped | `accept` | 0.03 | 0.0279 | 0.0302 |

## 2. 确定性核心管线基线（7 个正式案例副本，各 1 次 cold）

| 案例 | convert (s) | math (s) | check (s) |
|---|---|---|---|
| `svg-seeded/01-modular-agent` | 12.3752 | 8.0289 | 15.6881 |
| `svg-seeded/02-thinking-diffusion` | 8.8611 | 0.1288 | 4.4411 |
| `svg-seeded/03-llmind` | 6.9547 | 6.398 | 3.9765 |
| `svg-seeded/04-pareto-conditioned-diffusion` | 6.9192 | 0.1332 | 5.7968 |
| `reference-only/01-modular-agent-reference-only` | 7.2519 | 7.2184 | 12.6018 |
| `reference-only/02-thinking-diffusion-reference-only` | 6.9494 | 0.1323 | 5.9145 |
| `reference-only/04-pareto-conditioned-diffusion-reference-only` | 7.008 | 0.1434 | 6.2824 |

## 案例级 Case05 基准状态

case-level: `pending-contract-authoring` — 案例级（建案→freeze→ingest→convert→math→check）基准需要经视觉审阅的 reference-inventory/regions/arrow-visual 合同集（zero_count_authorizations 的 basis 固定为 full-reference-review，无法在无视觉能力的会话中诚实合成）；合同集编写完成后由本 runner 补跑案例级与 reference-only 作者阶段。结构性发现已回写 Issue #19。

测量边界：当前进程及其子进程树（RSS/IO/CPU-wall）；PowerPoint COM 服务器经 RPC 激活、不在子进程树内时其资源不计入——如实记录测量边界

性能基准不授予 `approved`；任何失败如实保留。
