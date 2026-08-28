"""tools.qa.renormalize 的双向 EOL 修复、载体 rebind 与拒绝语义测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.core import common
from tools.core.contracts import read_json, write_json
from tools.qa.renormalize import renormalize_case


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(tmp_path: Path) -> common.Run:
    from PIL import Image

    reference = tmp_path / "reference.png"
    Image.new("RGB", (60, 40), "white").save(reference)
    return common.create_run(
        reference,
        case="renorm",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )


def _seed_contract(run: common.Run, text: str, *, crlf: bool) -> None:
    data = text.encode("utf-8")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    run.external_seed_svg.write_bytes(data)
    provenance = read_json(run.provenance_path)
    provenance["external_svg_seed"] = {
        "kind": "svg",
        "role": "external-seed",
        "origin": "test",
        "source_name": "external-seed.svg",
        "canonical_path": "external-seed.svg",
        "sha256": _h(data),
    }
    write_json(run.provenance_path, provenance)


def test_crlf_bound_record_is_rebound_to_lf_bytes(tmp_path: Path):
    run = _run(tmp_path)
    text = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"/>\n'
    # 记录绑定 CRLF 字节，文件已是 LF（仓库规范形态）。
    _seed_contract(run, text, crlf=True)
    run.external_seed_svg.write_bytes(text.encode("utf-8"))

    notes = renormalize_case(run, apply=True)

    assert "seed:rebound" in notes
    record = read_json(run.provenance_path)["external_svg_seed"]
    assert record["sha256"] == _h(text.encode("utf-8"))
    assert renormalize_case(run, apply=False) == ["seed:consistent"]


def test_lf_bound_crlf_worktree_is_rewritten_to_lf(tmp_path: Path):
    run = _run(tmp_path)
    text = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"/>\n'
    _seed_contract(run, text, crlf=False)  # 记录绑 LF
    run.external_seed_svg.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    notes = renormalize_case(run, apply=True)

    assert "seed:rewritten-to-lf" in notes
    assert b"\r" not in run.external_seed_svg.read_bytes()
    assert renormalize_case(run, apply=False) == ["seed:consistent"]


def test_real_content_drift_is_refused_without_rebind(tmp_path: Path):
    run = _run(tmp_path)
    _seed_contract(run, '<svg width="1"/>\n', crlf=False)
    run.external_seed_svg.write_bytes(b'<svg width="2"/>\n')  # 内容真实变更

    notes = renormalize_case(run, apply=True)

    assert "seed:real-drift" in notes
    record = read_json(run.provenance_path)["external_svg_seed"]
    assert record["sha256"] != _h(b'<svg width="2"/>\n')  # 记录不被改写


def test_carrier_rebind_syncs_revision_chain(tmp_path: Path):
    run = _run(tmp_path)
    redraw = run.redraw_svg
    old = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="old"/>\n'
    redraw.write_bytes(old)
    scene = read_json(run.scene_path)
    scene["canonical_svg"] = {"sha256": _h(old), "source_role": "external-seed-proposal"}
    write_json(run.scene_path, scene)

    # 模拟历史缺口：redraw.svg 被后续流程改写，scene.canonical 未同步。
    current = old.replace(b'id="old"', b'id="fixed"')
    redraw.write_bytes(current)

    notes = renormalize_case(run, apply=True, rebind_carrier=True)

    assert "scene.canonical:rebound-to-current-bytes" in notes
    scene = read_json(run.scene_path)
    assert scene["canonical_svg"]["sha256"] == _h(current)
    # revision 链被 stamp_active_revision 同步到新 scene 语义。
    from tools.core.revisions import revision_id, scene_sha256

    meta = run.load_meta()
    assert meta["active_revision"]["revision_id"] == revision_id(scene)
    assert meta["active_revision"]["scene_sha256"] == scene_sha256(scene)
    receipt = read_json(run.revision_receipt_path)
    assert receipt["revision_id"] == revision_id(scene)
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]
