# ADR-001 · Image2PPT 仅作设计审计样本

- 状态：Accepted
- 日期：2026-08-17
- 上游：https://github.com/Paul-Jeo/Image2PPT
- 审计基准 commit：`25d22eaad48cf003382133952f73918b73e02fe5`
- 当前上游 main（文档定稿时）：`eb7fc0033fbeb9c255014e0e8b918677e59588e5`
- License：MIT，本地 `LICENSE` SHA-256 `9DCC2F222BD3717345D4A03F9C4A969779FD6F6812D527714B92A9E4810407D0`

## 可验证边界

本地快照没有独立 `.git`；`git -C Image2PPT rev-parse HEAD` 会落到 AI AutoFigure 父仓，不能证明快照 commit。已核对关键文件与 `25d22ea...` 一致，但未证明全部文件逐字节一致：

| 文件 | 本地 SHA-256 |
|---|---|
| `SKILL.md` | `022F68903922AC4FCEC4CAA6EACF98BC9C1DDACFE0CADBD9CE76CAFEB475BE71` |
| `README.md` | `700D9AA5511AA60B7223B77A88D3C999A0694A4D1FEEA460047E259AB24FA7FE` |
| `LICENSE` | `9DCC2F222BD3717345D4A03F9C4A969779FD6F6812D527714B92A9E4810407D0` |

本地 UTF-8 测试结果为 `113 passed, 8 failed`，因此它不是可直接引入的稳定运行时依赖。

## 采纳/拒绝矩阵

| 主题 | 决定 | AI AutoFigure 落点 |
|---|---|---|
| 单一当前 manifest/状态 | 采纳 | `run-state.json` + 追加式 `run-events.jsonl` |
| 区域分解与 protected anchors | 采纳 | Figure Spec v4 的 group/children/edges 与 preflight |
| 薄箭头/填充箭头/复杂风格分类 | 采纳 | connector、filled native shape、style-bound atomic asset |
| atomicity/cropability/provenance | 采纳并收紧 | `reference_atomic_asset` receipt 与边界 QA |
| contact sheet/分阶段恢复 | 采纳思路 | run-local 视觉任务包与状态查询 |
| 固定 3–5 区域 | 拒绝 | 单图科研图以实测层级为准 |
| 云端 OCR/token | 拒绝 | 锁定本地 PaddleOCR，不远程上传 |
| 生成式 asset sheet | 拒绝 | 只允许确定性来源裁片 |
| 位图公式 | 拒绝 | 原生 Office Math |
| 多页通用 deck 为默认目标 | 拒绝 | 继续聚焦单张科研图 |
| Image2PPT 运行时/代码依赖 | 拒绝 | 所有接口在本项目独立实现 |

## 代码派生

本次没有复制 Image2PPT 源码；实现基于 AI AutoFigure 现有 schema、receipt、preflight 和 PowerPoint 操作批接口独立编写。本 ADR 保留设计来源和取舍记录，快照不进入运行时。
