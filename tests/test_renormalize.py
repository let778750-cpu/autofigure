"""tools.qa.renormalize 的双向 EOL 修复、载体 rebind 与拒绝语义测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

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

    renormalize_case(run, apply=True)

    # 预规范化把文件统一为 LF 后，绑定直接一致（无需 rewritten 标签）。
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


def _carrier(scene: dict, text: str, sha: str) -> None:
    """按 bind_canonical_svg 的真实形态构造 carrier（含内联 content）。"""
    scene["canonical_svg"] = {
        "encoding": "utf-8",
        "content": text,
        "sha256": sha,
        "source_role": "external-seed-proposal",
        "materialized_path": "redraw.svg",
    }


def test_carrier_rebind_syncs_revision_chain(tmp_path: Path):
    run = _run(tmp_path)
    redraw = run.redraw_svg
    old = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="old"/>\n'
    redraw.write_bytes(old)
    scene = read_json(run.scene_path)
    _carrier(scene, old.decode("utf-8"), _h(old))
    write_json(run.scene_path, scene)

    # 模拟历史缺口：redraw.svg 被后续流程改写，scene.canonical 未同步。
    current = old.replace(b'id="old"', b'id="fixed"')
    redraw.write_bytes(current)

    notes = renormalize_case(run, apply=True, rebind_carrier=True)

    assert "scene.canonical:rebound-to-current-bytes" in notes
    scene = read_json(run.scene_path)
    assert scene["canonical_svg"]["sha256"] == _h(current)
    # content 合同：canonical_svg_text 不再抛错，且等于载体字节。
    from tools.core.revisions import canonical_svg_text

    assert canonical_svg_text(scene).encode("utf-8") == current
    # revision 链被 stamp_active_revision 同步到新 scene 语义。
    from tools.core.revisions import revision_id, scene_sha256

    meta = run.load_meta()
    assert meta["active_revision"]["revision_id"] == revision_id(scene)
    assert meta["active_revision"]["scene_sha256"] == scene_sha256(scene)
    receipt = read_json(run.revision_receipt_path)
    assert receipt["revision_id"] == revision_id(scene)
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]


def test_crlf_bound_carrier_rebound_syncs_content(tmp_path: Path):
    run = _run(tmp_path)
    text_crlf = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"/>\r\n'
    text_lf = text_crlf.replace("\r\n", "\n")
    # 文件已是 LF（仓库规范形态），记录绑定 CRLF 形态：#26 回归的输入形态。
    run.redraw_svg.write_bytes(text_lf.encode("utf-8"))
    scene = read_json(run.scene_path)
    _carrier(scene, text_crlf, _h(text_crlf.encode("utf-8")))
    write_json(run.scene_path, scene)

    notes = renormalize_case(run, apply=True)

    assert "scene.canonical:rebound" in notes
    # 重绑必须同时把 content 规范为 LF 文本（scene.json 文件级 LF 重写从不
    # 触碰 JSON 字符串内的 \r\n 转义），canonical_svg_text 无损通过。
    scene = read_json(run.scene_path)
    from tools.core.revisions import canonical_svg_text

    assert canonical_svg_text(scene) == text_lf
    assert scene["canonical_svg"]["sha256"] == _h(text_lf.encode("utf-8"))
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]


def test_content_stale_carrier_is_detected_and_repaired(tmp_path: Path):
    run = _run(tmp_path)
    stale = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="stale"/>\n'
    current = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="current"/>\n'
    run.redraw_svg.write_bytes(current)
    scene = read_json(run.scene_path)
    # file==sha256 而 content 陈旧：#26 回归的最终形态（check 门禁盲区）。
    _carrier(scene, stale.decode("utf-8"), _h(current))
    write_json(run.scene_path, scene)

    # check 模式只报告，不改写。
    assert "scene.canonical:content-mismatch" in renormalize_case(run, apply=False)

    notes = renormalize_case(run, apply=True)

    assert "scene.canonical:content-rebound" in notes
    from tools.core.revisions import canonical_svg_text

    scene = read_json(run.scene_path)
    assert canonical_svg_text(scene).encode("utf-8") == current
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]


def test_check_gate_flags_carrier_content_mismatch(tmp_path: Path):
    from tools.pipeline.check import _source_gate_blockers

    run = _run(tmp_path)
    stale = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="stale"/>\n'
    current = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="current"/>\n'
    run.redraw_svg.write_bytes(current)
    scene = read_json(run.scene_path)
    _carrier(scene, stale.decode("utf-8"), _h(current))
    write_json(run.scene_path, scene)
    meta = run.load_meta()
    write_json(
        run.source_gate_report_path,
        {
            "schema_version": "4.0.0",
            "kind": "source_gate_report",
            "route_gate": {"input_route": meta.get("input_route")},
            "reference": {
                "expected_sha256": meta.get("source_sha256"),
                "actual_sha256": meta.get("source_sha256"),
            },
            "decision": "accept",
        },
    )

    blockers = _source_gate_blockers(run)

    assert "scene:carrier-content-mismatch" in blockers
    assert "scene:carrier-redraw-mismatch" not in blockers  # file 与绑定一致

    # 修复后门禁放行。
    renormalize_case(run, apply=True)
    assert "scene:carrier-content-mismatch" not in _source_gate_blockers(run)


def test_consistent_case_is_noop_without_revision_churn(tmp_path: Path):
    run = _run(tmp_path)
    text = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" id="ok"/>\n'
    run.redraw_svg.write_bytes(text.encode("utf-8"))
    scene = read_json(run.scene_path)
    _carrier(scene, text, _h(text.encode("utf-8")))
    write_json(run.scene_path, scene)

    # 首次 apply 建立完整 revision 链（首次 stamp 不算 churn）。
    renormalize_case(run, apply=True)
    before = run.scene_path.read_bytes()
    before_redraw = run.redraw_svg.read_bytes()

    notes = renormalize_case(run, apply=True)

    # 已一致案例：零改写、零 revision churn、note 全 consistent。
    assert "scene.canonical:consistent" in notes
    assert not [n for n in notes if "rebound" in n or "rewritten" in n or "stale" in n]
    assert run.scene_path.read_bytes() == before
    assert run.redraw_svg.read_bytes() == before_redraw
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]


def test_revision_stale_is_detected_and_restamped(tmp_path: Path):
    run = _run(tmp_path)
    text = '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"/>\n'
    run.redraw_svg.write_bytes(text.encode("utf-8"))
    scene = read_json(run.scene_path)
    _carrier(scene, text, _h(text.encode("utf-8")))
    write_json(run.scene_path, scene)
    renormalize_case(run, apply=True)  # 建立完整 revision 链

    # 模拟 #26 遗留形态：指纹算法/绑定字节演进后 active_revision 未 restamp。
    meta = run.load_meta()
    meta["active_revision"]["compiler_fingerprint"] = "0" * 64
    write_json(run.meta_path, meta)

    notes = renormalize_case(run, apply=False)
    assert any(n.startswith("revision:stale[") for n in notes)

    notes = renormalize_case(run, apply=True)

    assert any(n.startswith("revision:restamped[") for n in notes)
    from tools.core.revisions import lineage_blockers

    assert lineage_blockers(run) == []
    assert renormalize_case(run, apply=False) == ["scene.canonical:consistent"]


def test_rebind_guard_rejects_concurrent_carrier_change(tmp_path: Path):
    from tools.qa.renormalize import _rebind_scene_carrier

    run = _run(tmp_path)
    current = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"/>\n'
    run.redraw_svg.write_bytes(current)
    scene = read_json(run.scene_path)
    _carrier(scene, current.decode("utf-8"), _h(current))
    write_json(run.scene_path, scene)

    # expected_old_sha 与 scene 实际绑定不符（TOCTOU / 操作对象错位）→ 拒绝。
    with pytest.raises(ValueError, match="rebind guard"):
        _rebind_scene_carrier(run, scene, current, expected_old_sha="f" * 64)


def test_pending_note_matching_is_bracket_aware():
    from tools.qa.renormalize import _is_pending

    # #26 退出码漏计隐患：详情段内的冒号曾使朴素 rsplit 判定失效。
    assert _is_pending("lineage:stale[lineage:qa-report-hash-mismatch:qa/a.json]")
    assert _is_pending("revision:stale[lineage:compiler-fingerprint-mismatch]")
    assert _is_pending("revision:restamped[lineage:artifact-bindings-mismatch]")
    assert _is_pending("scene.canonical:content-mismatch")
    assert _is_pending("scene.canonical:rebound-to-current-bytes")
    assert _is_pending("seed:real-drift")
    assert not _is_pending("scene.canonical:consistent")
    assert not _is_pending("regions_sha256:consistent")
    assert not _is_pending("lineage:rebuilt")
