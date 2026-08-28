# legacy/ 历史归档取回指引

当前 tip 的 `legacy/` 工作树已收口（158 个文件、约 4.75 MiB，出处见 Issue #18）。Git 历史保持原状，全部内容仍可从下列提交精确恢复。

## 保留映射

| 历史路径 | 当前位置 | 说明 |
|---|---|---|
| `legacy/ocr-config.json` | `config/ocr-config.json` | 唯一仍被运行时读取的文件（`tools/pipeline/check.py` OCR 子进程配置），经 `git mv` 保留完整历史 |

## 归档内容（删除时点）

- `legacy/v1-final-evidence/` — v1 最终保护证据
- `legacy/schemas/`、`legacy/tests/`、`legacy/tools/`、`legacy/references/`、`legacy/native-math-poc/` — v1/v2 时代的合同、测试、工具与 PoC
- `legacy/PROJECT_ARCHITECTURE.md`、`legacy/PROJECT_PROGRESS_REPORT_2026-08-18.md` 等 — 旧架构与进度文档

## 取回命令

```bash
# 查看删除前最后一个仍包含 legacy/ 的提交
git log --oneline --diff-filter=D -- legacy/ocr-config.json

# 恢复单个文件到暂存区（不切分支）
git checkout <删除前提交> -- legacy/<path>

# 仅浏览，不落盘
git show <删除前提交>:legacy/PROJECT_ARCHITECTURE.md
```

删除发生在 `workspace-hygiene-legacy` 主题分支（Issue #18）；该分支合并前，`origin/develop` 的任意历史提交均可作为 `<删除前提交>`。
