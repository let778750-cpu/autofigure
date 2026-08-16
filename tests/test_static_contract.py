from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_reference_set_exists_and_legacy_names_are_gone() -> None:
    canonical = {
        "01-workflow-contract.md",
        "02-qa-gates.md",
        "03-style-principles.md",
        "04-publication-journal-standards.md",
        "05-png-authority-boundary.md",
        "06-manual-asset-slots.md",
        "07-microasset-classification.md",
        "08-anti-hallucination.md",
        "09-backend.md",
        "10-source-synthesis.md",
        "11-agent-vision-protocol.md",
    }
    references = {path.name for path in (ROOT / "references").glob("*.md")}
    assert canonical == references

    legacy = {
        "01-figure-spec-schema.md",
        "02-scientific-figure-format-rules.md",
        "03-top-conference-style-guide.md",
        "06-placeholder-slot-contract.md",
        "08-anti-hallucination-checklist.md",
    }
    assert references.isdisjoint(legacy)


def test_skill_requires_local_ocr_and_major_replan() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "PaddleOCR" in skill
    assert "PREFLIGHT_PASS" in skill
    assert "REGION_REPLAN" in skill
    assert "PASS/NO_OP" in skill
    assert "native_office_math" in skill
    assert "a14:m" in skill
    assert "MathZones" in skill
    assert "autofigure.cmd" in skill
    assert "geometry_refinement" in skill
    assert "ink-bottom alignment" in skill
    assert "promotion gate" in skill
    assert "不得要求用户手动输入 Python/PowerShell 命令" in skill
    assert "只用当前模型原生视觉" not in skill
    for filename in (
        "01-workflow-contract.md",
        "02-qa-gates.md",
        "06-manual-asset-slots.md",
        "08-anti-hallucination.md",
        "09-backend.md",
    ):
        assert f"references/{filename}" in skill


def test_public_launcher_is_thin_policy_safe_and_cwd_independent(tmp_path: Path) -> None:
    launcher = ROOT / "autofigure.cmd"
    assert launcher.is_file()
    source = launcher.read_text(encoding="utf-8")
    assert "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" in source
    assert "-NoProfile" in source
    assert "-NonInteractive" in source
    assert "-ExecutionPolicy Bypass" in source
    assert '"%~dp0tools\\run_perception_gate.ps1" %*' in source
    assert "D:\\opencv" not in source
    assert "D:\\paddle ocr" not in source

    if os.name != "nt":
        return
    runs_root = ROOT / "examples" / "generated" / "runs"
    before = {path.name for path in runs_root.iterdir()} if runs_root.exists() else set()
    missing_input = tmp_path / "missing target.png"
    command = f'call "{launcher}" -InputPath "{missing_input}" -Device auto'
    completed = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/s", "/c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    after = {path.name for path in runs_root.iterdir()} if runs_root.exists() else set()
    assert completed.returncode != 0
    assert after == before


def test_removed_unsafe_tools_do_not_return() -> None:
    retired = {
        "compare_images.py",
        "coordinate_helper.py",
        "crop_preserved_elements.py",
        "detect_office_environment.py",
        "run_powerpoint_vba_macos.py",
        "run_powerpoint_vba_windows.ps1",
        "vba_lint.py",
    }
    for filename in retired:
        assert not (ROOT / "tools" / filename).exists()


def test_native_office_math_tool_and_dependencies_are_declared() -> None:
    assert (ROOT / "tools" / "powerpoint_native_math.py").is_file()
    assert (ROOT / "tools" / "powerpoint_native_math_roundtrip.ps1").is_file()
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "latex2mathml==3.81.0" in requirements
    assert "lxml==6.1.0" in requirements


def test_source_authority_validator_is_public_and_non_self_authorizing() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    schema = ROOT / "schemas" / "source-authority.schema.json"
    validator = ROOT / "tools" / "validate_source_authority.py"

    assert schema.is_file()
    assert validator.is_file()
    assert "validate_source_authority.py" in readme
    assert "OCR、VLM 或 PNG 像素均不能成为 authority evidence" in readme


def test_json_contract_files_parse() -> None:
    paths = [
        ROOT / "host-runtime.json",
        ROOT / "ocr-config.json",
        ROOT / "schemas" / "figure-spec.schema.json",
        ROOT / "schemas" / "geometry-manifest.schema.json",
        ROOT / "schemas" / "perception-manifest.schema.json",
        ROOT / "examples" / "target_figure.fixture.json",
    ]
    for path in paths:
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict), path


def test_mcp_server_entrypoints_are_absolute_and_exist() -> None:
    configuration = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    servers = configuration["mcpServers"]
    assert set(servers) == {"powerpoint-live", "drawio-live", "drawio-file-utils"}
    for name, server in servers.items():
        cwd = Path(server["cwd"])
        script = Path(server["args"][0])
        assert cwd.is_absolute(), name
        assert cwd.is_dir(), name
        assert script.is_absolute(), name
        assert script.is_file(), name
        assert script.parent.parent == cwd, name


def test_generated_artifact_layout_is_explicit_and_legacy_work_is_forbidden() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / "references" / "01-workflow-contract.md").read_text(encoding="utf-8")
    backend = (ROOT / "references" / "09-backend.md").read_text(encoding="utf-8")
    runner = (ROOT / "tools" / "run_perception_gate.ps1").read_text(encoding="utf-8")
    pytest_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for contract in (skill, agents, readme, workflow, runner):
        assert "examples/generated/runs" in contract.replace("\\", "/")
        assert "work/runs" not in contract.replace("\\", "/")
    assert "--basetemp" not in pytest_config
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "tempfile.mkdtemp" in conftest
    assert "pytest_unconfigure" in conftest
    assert (ROOT / "tools" / "check_project_hygiene.py").is_file()
    assert "MCP" in skill and "绝对路径" in skill
    assert "MCP" in agents and "绝对路径" in agents
    assert "MCP" in backend and "绝对路径" in backend
    assert "python tools\\" not in readme


def test_curated_native_math_manifest_points_only_to_current_hash_bound_evidence() -> None:
    case_root = ROOT / "examples" / "generated" / "native-math-poc"
    manifest = json.loads((case_root / "case-manifest.json").read_text(encoding="utf-8"))
    current = manifest["current"]

    assert manifest["authority"] == "current"
    assert current["status"] == "MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW"
    primary_records = (
        manifest["source"],
        manifest["plan"],
        current["injected_pptx"],
        current["injection_report"],
        current["roundtripped_pptx"],
        current["roundtrip_receipt"],
        current["audit"],
    )
    for record in primary_records:
        artifact = case_root / record["path"]
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert digest == record["sha256"]
        assert artifact.stat().st_size == record["bytes"]

    render_records = current["renders"]
    assert len(render_records) == 5
    render_directories = {(case_root / record["path"]).parent for record in render_records}
    assert len(render_directories) == 1
    render_directory = render_directories.pop()
    assert {path.resolve() for path in render_directory.glob("*.png")} == {
        (case_root / record["path"]).resolve() for record in render_records
    }
    for record in render_records:
        artifact = case_root / record["path"]
        assert artifact.stat().st_size == record["bytes"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == record["sha256"]

    finalizer_digest = hashlib.sha256(
        (ROOT / "tools" / "powerpoint_native_math.py").read_bytes()
    ).hexdigest()
    roundtrip_digest = hashlib.sha256(
        (ROOT / "tools" / "powerpoint_native_math_roundtrip.ps1").read_bytes()
    ).hexdigest()
    output_policy_digest = hashlib.sha256(
        (ROOT / "tools" / "output_policy.py").read_bytes()
    ).hexdigest()
    assert current["finalizer_python_sha256"] == finalizer_digest
    assert current["roundtrip_script_sha256"] == roundtrip_digest
    assert current["output_policy_sha256"] == output_policy_digest

    receipt = json.loads(
        (case_root / current["roundtrip_receipt"]["path"]).read_text(encoding="utf-8")
    )
    receipt_render_records = []
    for record in receipt["renders"]:
        receipt_render_records.extend(
            [
                (record["path"], record["byte_length"], record["sha256"]),
                (
                    record["verification_path"],
                    record["verification_byte_length"],
                    record["verification_sha256"],
                ),
            ]
        )
    receipt_render_records.extend(
        (record["path"], record["byte_length"], record["sha256"])
        for record in receipt["counterfactual_renders"]
    )
    receipt_render_map = {}
    for raw_path, byte_length, digest in receipt_render_records:
        resolved = Path(raw_path).resolve()
        assert resolved.parent == render_directory.resolve()
        receipt_render_map[resolved] = (byte_length, digest)
    assert receipt_render_map == {
        (case_root / record["path"]).resolve(): (record["bytes"], record["sha256"])
        for record in render_records
    }
    assert receipt["roundtrip_script_sha256"] == roundtrip_digest

    audit = json.loads((case_root / current["audit"]["path"]).read_text(encoding="utf-8"))
    assert audit["status"] == current["status"]
    assert audit["findings"] == []
    assert audit["evidence_binding_sha256"] == current["audit"]["evidence_binding_sha256"]
    assert audit["evidence_binding"]["finalizer_python_sha256"] == finalizer_digest
    assert audit["evidence_binding"]["roundtrip_script_sha256"] == roundtrip_digest
