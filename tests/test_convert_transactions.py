"""Failure atomicity for the schema-v4 offline conversion publication."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools.core import common
from tools.pipeline.convert import convert


def _case(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (200, 100), (245, 245, 245)).save(reference)
    run = common.create_run(
        reference,
        case="transaction-case",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
        'viewBox="0 0 200 100"><rect id="panel" x="10" y="10" '
        'width="180" height="80" fill="#EEEEEE" stroke="#222222"/>'
        '<text id="label" x="100" y="56" text-anchor="middle" '
        'font-size="16">atomic</text></svg>',
        encoding="utf-8",
    )
    return run


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_conversion_writer_failure_never_touches_formal_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(common, "PROJECT_ROOT", tmp_path / "project-root")
    run = _case(tmp_path)
    convert(run)
    before = _file_bytes(run.root)

    def fail_after_package(*_args, **_kwargs):
        raise RuntimeError("injected conversion QA failure")

    monkeypatch.setattr("tools.arrows.pptx_arrows.write_arrow_reports", fail_after_package)
    with pytest.raises(RuntimeError, match="injected conversion QA failure"):
        convert(run)

    assert _file_bytes(run.root) == before
    assert not (common.PROJECT_ROOT / ".autofigure-staging").exists()
    assert not list(run.root.rglob("*.publish"))
    assert not list(run.root.rglob("*.rollback"))


def test_partial_publication_failure_restores_every_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(common, "PROJECT_ROOT", tmp_path / "project-root")
    run = _case(tmp_path)
    convert(run)
    before = _file_bytes(run.root)

    from tools.core import transactions

    original = transactions._publish_replace
    replacements = 0

    def fail_during_publish(source: Path, destination: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 5:
            raise OSError("injected atomic publication failure")
        original(source, destination)

    monkeypatch.setattr(transactions, "_publish_replace", fail_during_publish)
    with pytest.raises(OSError, match="injected atomic publication failure"):
        convert(run)

    assert replacements == 5
    assert _file_bytes(run.root) == before
    assert not (common.PROJECT_ROOT / ".autofigure-staging").exists()
    assert not list(run.root.rglob("*.publish"))
    assert not list(run.root.rglob("*.rollback"))
