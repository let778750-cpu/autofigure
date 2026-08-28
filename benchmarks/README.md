# benchmarks/ — 跨案例测量框架

> **Examples own truth. Benchmarks measure truth.**
> 案例事实（reference/seed/scene/产物/qa 证据）只存在于 `examples/<case>`；
> benchmarks 只拥有"如何测量一批案例"的方法，不拥有任何案例事实的第二份副本。

## 目录职责

| 内容 | 位置 | 说明 |
|---|---|---|
| 案例输入与证据（reference.png / seed / scene / qa） | `examples/<case>` | 单一真值；benchmark 以 manifest 引用 + SHA 锁定 |
| 推导链物证（原始 seed、修复 seed） | `fixtures/05-sting-autophagy/` | Case05 合同推导链的机器可验证物证：`repair_seed.py --check` 从原始件确定性重导出修复件，`verify_fixture()` 锁哈希——**CI 在役门禁，非历史归档** |
| Case05 建案 bootstrap 工具 | `bootstrap/` | `repair_seed.py`（确定性几何修复）、`build_case05_contracts.py`（合同推导）、`build_case05_cases.py`（建案驱动）。历史建案过程 + 在役可复现校验 |
| 跨案例基准套件 | `suites/pipeline_performance.py` | 原 `run_case05.py` 更名归位：真实职责是跨案例 runner（8 个正式案例、双路线） |
| 观测结果 | `results/pipeline-suite.json` + `.md` | 机器采集（JSON 权威，MD 确定性生成）；单样本如实标注，不伪造分位数 |

## suites/pipeline_performance.py

1. **Case05 gate 修复阶梯**：原始 seed（repair）→ 修复未盖章（repair，仅剩语义）→ 修复+盖章（accept），各 5 次采样
2. **确定性核心管线基线**：8 个正式案例临时副本 convert→math→check(standard) 逐阶段 wall/CPU/RSS/IO/产物哈希

用法：`python benchmarks/suites/pipeline_performance.py [--tiers gate|pipeline]`

## Case05（STING-autophagy）正式案例

- `examples/svg-seeded/05-sting-autophagy/` — 完整案例（prepare→合同→freeze 391 对象→ingest 盖章变体 gate accept→convert→math→check），QA 证据如实落盘（`qa_failed` 形态与其他案例一致）
- `examples/reference-only/05-sting-autophagy-reference-only/` — prepare→合同→freeze；停 ready 等待视觉执行者的重绘候选（**仅依据 reference.png，不以 seed 冒充**；候选落地后走 ingest→convert→math→check，Issue #19）

## 测量边界

- RSS/IO/CPU-wall 覆盖当前进程及子进程树；PowerPoint COM 服务器经 RPC 激活、不在子进程树内时其资源不计入。
- 本套件是 **Pipeline Performance Benchmark**（工程管线性能），不是重建智能基准；reference-only 重构质量/修复收敛等 Reconstruction Benchmark 待 Corrector/视觉作者能力落地后另行建套件（避免结构先于能力）。

## 红线

- 性能基准不授予 `approved`；任何 `qa_failed`/失败如实保留。
- fixture 物证字节不可变；manifest 引用（reference.png）随案例证据的合法迁移 PR 同步更新。
- 禁止在 benchmarks 重建案例事实副本。
