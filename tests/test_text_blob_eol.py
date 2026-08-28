"""仓库文本 blob 的 EOL 终局不变量：text 属性文件一律 LF blob。

回归背景：PR #26 的 b38c29f 在 CRLF 工作树上以 `* -text` 透传把
requirements.txt 的 CRLF 字节提交进了对象库（属性迁移不会重清洗
已被 smudge 的工作树文件，0868bc5 lf-only → 23e7baf crlf-only）。
`git ls-files --eol` 的 i/ 侧反映索引 blob 的实际字节，本测试让
任何 i/crlf 或 i/mixed 的文本 blob 直接在 CI 失败，把这类回归关进门禁。

ps1/cmd 虽钉 eol=crlf，但其 checkin 转换与其它 text 文件一致规范化为
LF blob，因此同样受本不变量约束。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_text_blobs_are_lf():
    if shutil.which("git") is None or not (PROJECT_ROOT / ".git").exists():
        pytest.skip("git 仓库不可用（源码包运行）")
    out = subprocess.run(
        ["git", "ls-files", "--eol"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    offenders = [
        line
        for line in out.splitlines()
        if line.split() and line.split()[0] in ("i/crlf", "i/mixed")
    ]
    assert not offenders, "存在 CRLF/混合 EOL 的文本 blob:\n" + "\n".join(offenders)
