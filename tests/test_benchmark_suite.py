"""benchmarks 套件物证合同：manifest 引用、推导链物证在位、verify_fixture 通过。

归属原则（benchmarks/README）：案例事实单一真值在 examples；benchmarks 只以
path+sha256 引用。本测试把该不变量纳入 portable-tests（双平台）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (PROJECT_ROOT / "benchmarks").is_dir(), reason="源码包无 benchmarks 目录"
)


def test_verify_fixture_passes_with_external_reference():
    from benchmarks.suites.pipeline_performance import EXAMPLE_REFERENCE, verify_fixture

    fixture = verify_fixture()  # 缺文件/哈希漂移均 SystemExit → 测试失败
    record = fixture["immutable_inputs"]["reference.png"]
    # reference.png 是 manifest 引用而非本地副本。
    assert record["path"] == "examples/svg-seeded/05-sting-autophagy/reference.png"
    assert EXAMPLE_REFERENCE.is_file()


def test_derivation_chain_evidence_and_no_case_fact_duplicates():
    fixture_dir = PROJECT_ROOT / "benchmarks" / "fixtures" / "05-sting-autophagy"
    # 推导链物证在位（CI 在役门禁的校验对象）。
    assert (fixture_dir / "external-seed.svg").is_file()
    assert (fixture_dir / "external-seed-repaired.svg").is_file()
    # 案例事实副本已消除：fixtures 不得再持有 reference.png。
    assert not (fixture_dir / "reference.png").exists()
