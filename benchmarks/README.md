# benchmarks/ — 性能与资源基准

## Case05（STING-autophagy）

- `fixtures/05-sting-autophagy/` — 不可变输入（哈希见 `fixture.json`）：
  - `reference.png`（2100×1324，两份原始副本逐字节相同，收口为单份）
  - `external-seed.svg`（原始 seed，2048×1291，字节未动；文件名自 `extral-seed.svg` 规范重命名）
  - `external-seed-repaired.svg`（确定性几何修复：画布缩放、use 展开、样式内联、稳定 ID；由 `repair_seed.py` 生成，`--check` 校验确定性）
- `repair_seed.py` — 修复生成器（纯确定性，可复跑校验）
- `build_case05_contracts.py` — 从修复 seed 矢量几何确定性推导 regions/reference_inventory 合同集（38 text / 18 arrow / 335 shape；icon/brace 零计数授权；派生关系声明见 fixture.json `derivation_disclosure`）
- `build_case05_cases.py` — 案例构建驱动：盖章（freeze receipt 的 inventory_sha256）→ ingest（repair-candidate，gate accept）→ convert → math → check
- `run_case05.py` — 基准 runner：
  1. **gate 修复阶梯**：原始（reject）→ 修复未盖章（repair）→ 修复+语义盖章（accept）各 5 次采样
  2. **确定性核心管线基线**：8 个正式案例临时副本 convert→math→check(standard) 逐阶段计时（含 Case05 seeded）
- `results/05-sting-autophagy.json` + 同名 `.md` — 机器采集结果（JSON 为权威，MD 确定性生成）

## 正式案例（examples/）

- `examples/svg-seeded/05-sting-autophagy/` — 完整案例：prepare(--seed 修复 seed) → 生成合同 → freeze(391 对象) → ingest 盖章变体(gate accept, svg_repair) → convert → math → check(standard)，QA 证据如实落盘（`qa_failed` 形态与其他案例一致）。
- `examples/reference-only/05-sting-autophagy-reference-only/` — prepare → 生成合同 → freeze；停 ready 等待视觉执行者的重绘候选（不以 seed 冒充；候选落地后走 ingest→convert→math→check）。

## 测量边界

- RSS/IO/CPU-wall 覆盖当前进程及子进程树；PowerPoint COM 服务器经 RPC 激活、不在子进程树内时其资源不计入。
- reference-only 路线的作者阶段（VLM 依 reference.png 重绘）仍未执行；候选落地后补跑该路线的管线基准与作者耗时记录（Issue #19）。

## 红线

- 性能基准不授予 `approved`；任何 `qa_failed`/失败如实保留。
- 单样本标注为单样本，不以 3 次样本伪造 p95；统计仅报 median/min/max。
- fixture 字节不可变；任何变更须经 Issue 记录并同步 `fixture.json` 哈希。
