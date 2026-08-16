from __future__ import annotations

import os
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import analyze_target  # noqa: E402
import create_canvas_pptx  # noqa: E402
import finalize_perception_review  # noqa: E402
import paddle_ocr_manifest  # noqa: E402
import powerpoint_native_math  # noqa: E402
import preflight_scene  # noqa: E402
from output_policy import OutputPolicyError, resolve_output_path  # noqa: E402


GENERATED_ROOT = PROJECT_ROOT / "examples" / "generated"


def test_policy_refuses_stable_fixture_and_project_root_delivery() -> None:
    target = PROJECT_ROOT / "examples" / "target_figure.png"
    target_before = target.read_bytes()

    with pytest.raises(OutputPolicyError, match="examples.generated"):
        analyze_target.atomic_write_json(target, {"forbidden": True})
    with pytest.raises(OutputPolicyError, match="examples.generated"):
        resolve_output_path(PROJECT_ROOT / "final.pptx")

    assert target.read_bytes() == target_before


def test_policy_allows_generated_subtree_and_absolute_external_tmp(tmp_path: Path) -> None:
    generated = GENERATED_ROOT / "runs" / "policy-test" / "result.json"
    external = tmp_path.resolve() / "result.json"

    assert resolve_output_path(generated) == generated.resolve()
    assert resolve_output_path(external) == external.resolve()


def test_policy_rejects_relative_external_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OutputPolicyError, match="explicitly absolute"):
        resolve_output_path("result.json")


def test_relative_generated_path_from_project_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(PROJECT_ROOT)
    relative = Path("examples/generated/runs/policy-test/result.json")

    assert resolve_output_path(relative) == (PROJECT_ROOT / relative).resolve()


def test_absolute_external_writer_output_is_allowed(tmp_path: Path) -> None:
    output = tmp_path.resolve() / "result.json"

    analyze_target.atomic_write_json(output, {"status": "PASS"})

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "PASS"}


def test_native_math_cli_rejects_relative_output_from_external_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = powerpoint_native_math.main(
        [
            "compile",
            "--formula-id",
            "EQ-POLICY",
            "--latex",
            "x",
            "--mode",
            "inline",
            "--output",
            "relative-receipt.json",
        ]
    )

    assert exit_code == 3
    assert not (tmp_path / "relative-receipt.json").exists()


def test_preflight_cli_rejects_relative_output_from_external_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OutputPolicyError, match="explicitly absolute"):
        preflight_scene.main(
            [str(tmp_path / "missing-spec.json"), "--output", "relative-report.json"]
        )

    assert not (tmp_path / "relative-report.json").exists()


@pytest.mark.parametrize(
    "prefix",
    ["\\\\?\\", "\\\\.\\", "\\??\\", "//?/"],
)
def test_device_namespace_cannot_alias_and_overwrite_fixture(prefix: str) -> None:
    target = PROJECT_ROOT / "examples" / "target_figure.png"
    target_before = target.read_bytes()
    alias = prefix + str(target)

    with pytest.raises(OutputPolicyError, match="device namespace"):
        analyze_target.atomic_write_json(alias, {"forbidden": True})

    assert target.read_bytes() == target_before


def test_device_namespace_project_root_is_rejected(tmp_path: Path) -> None:
    device_root = "\\\\?\\" + str(PROJECT_ROOT)

    with pytest.raises(OutputPolicyError, match="project root.*device namespace"):
        resolve_output_path(tmp_path.resolve() / "result.json", project_root=device_root)


@pytest.mark.parametrize(
    "write",
    [
        lambda path: finalize_perception_review.atomic_write_json(path, {"x": 1}),
        lambda path: paddle_ocr_manifest.atomic_write_bytes(path, b"x"),
        lambda path: preflight_scene._write_json(path, {"x": 1}, pretty=False),
        lambda path: powerpoint_native_math._atomic_write_bytes(path, b"x"),
    ],
)
def test_low_level_writers_enforce_policy_before_mutation(write) -> None:
    forbidden = PROJECT_ROOT / "writer-bypass.json"
    assert not forbidden.exists()

    with pytest.raises((OutputPolicyError, paddle_ocr_manifest.ManifestError,
                        powerpoint_native_math.NativeMathError)):
        write(forbidden)

    assert not forbidden.exists()


def test_canvas_writer_refuses_root_output_before_creating_it() -> None:
    forbidden = PROJECT_ROOT / "final.pptx"
    existed_before = forbidden.exists()

    with pytest.raises(OutputPolicyError):
        create_canvas_pptx.create_blank_canvas_pptx(
            PROJECT_ROOT / "examples" / "target_figure.png",
            forbidden,
        )

    assert forbidden.exists() is existed_before


def test_resolve_blocks_symlink_escape_and_external_alias(tmp_path: Path) -> None:
    project = tmp_path / "project"
    generated = project / "examples" / "generated"
    stable = project / "examples" / "target.png"
    external = tmp_path / "external"
    generated.mkdir(parents=True)
    external.mkdir()
    stable.write_bytes(b"stable")
    escape = generated / "escape"
    alias = tmp_path / "stable-alias.png"
    try:
        os.symlink(external, escape, target_is_directory=True)
        os.symlink(stable, alias)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable for this test account")

    with pytest.raises(OutputPolicyError, match="symlink escape"):
        resolve_output_path(escape / "result.json", project_root=project)
    with pytest.raises(OutputPolicyError, match="examples.generated"):
        resolve_output_path(alias, project_root=project)
