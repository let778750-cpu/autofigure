from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import validate_host_runtime as validator  # noqa: E402


CONTRACT_PATH = PROJECT_ROOT / "host-runtime.json"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "host-runtime-receipt.schema.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_runtime_contract_pins_only_files_and_interpreter_not_package_versions() -> None:
    contract = validator.load_contract(CONTRACT_PATH)
    requirements = validator.parse_requirements(PROJECT_ROOT / contract["requirements_path"])

    assert contract["root"] == r"D:\opencv\env"
    assert contract["python_relative_path"] == "python.exe"
    assert contract["python_version"] == "3.12.13"
    assert contract["requirements_sha256"] == _sha256(PROJECT_ROOT / "requirements.txt")
    assert contract["receipt_schema_sha256"] == _sha256(SCHEMA_PATH)
    assert "package_versions" not in contract
    assert ("opencv-python", "4.13.0.92") in requirements


def test_requirements_parser_rejects_non_exact_or_duplicate_pins(tmp_path: Path) -> None:
    loose = tmp_path / "loose.txt"
    loose.write_text("numpy>=2\n", encoding="utf-8")
    with pytest.raises(validator.RuntimeContractError, match="exact"):
        validator.parse_requirements(loose)

    duplicate = tmp_path / "duplicate.txt"
    duplicate.write_text("opencv-python==1\nopencv_python==1\n", encoding="utf-8")
    with pytest.raises(validator.RuntimeContractError, match="duplicate"):
        validator.parse_requirements(duplicate)


def test_distribution_policy_rejects_other_opencv_and_torch_paddle_families() -> None:
    opencv, forbidden = validator.find_forbidden_distributions(
        ["opencv-python", "opencv-contrib-python", "torchvision", "paddleocr", "numpy"],
        allowed_opencv="opencv-python",
        explicitly_forbidden=["opencv-contrib-python", "torch", "paddleocr"],
    )

    assert opencv == ["opencv-contrib-python", "opencv-python"]
    assert forbidden == ["opencv-contrib-python", "paddleocr", "torchvision"]


def test_distribution_metadata_must_be_unique_after_name_normalization() -> None:
    assert validator.find_duplicate_distributions(
        ["packaging", "opencv-python", "opencv_python", "Pillow", "pillow"]
    ) == ["opencv-python", "pillow"]
    assert validator.find_duplicate_distributions(["opencv-python", "numpy"]) == []


def test_context_binding_is_all_or_nothing() -> None:
    with pytest.raises(validator.RuntimeContractError, match="supplied together"):
        validator.validate_runtime(
            config_path=CONTRACT_PATH,
            project_root=PROJECT_ROOT,
            run_id="runtime-test",
            source_sha256=None,
        )


def test_cli_without_isolated_mode_cannot_pass(tmp_path: Path) -> None:
    output = tmp_path.resolve() / "not-isolated.json"
    cache_directory = TOOLS_ROOT / "__pycache__"
    bytecode_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in cache_directory.glob("output_policy*.pyc")
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(PROJECT_ROOT / "tools" / "validate_host_runtime.py"),
            "--config",
            str(CONTRACT_PATH),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=120,
        check=False,
    )

    assert completed.returncode != 0
    if output.exists():
        receipt = json.loads(output.read_text(encoding="utf-8"))
        assert receipt["status"] == "FAIL"
        isolated = next(item for item in receipt["checks"] if item["name"] == "isolated_mode")
        assert isolated["passed"] is False
    bytecode_after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in cache_directory.glob("output_policy*.pyc")
    }
    assert bytecode_after == bytecode_before


def test_output_policy_rejects_project_root_receipt() -> None:
    forbidden = PROJECT_ROOT / "host-runtime-receipt.json"
    assert not forbidden.exists()

    exit_code = validator.main(
        [
            "--config",
            str(CONTRACT_PATH),
            "--output",
            str(forbidden),
        ]
    )

    assert exit_code == validator.EXIT_CONTRACT_OR_POLICY
    assert not forbidden.exists()


def test_live_isolated_runtime_emits_schema_valid_hash_bound_pass_receipt(
    tmp_path: Path,
) -> None:
    contract = validator.load_contract(CONTRACT_PATH)
    python = Path(contract["root"]) / contract["python_relative_path"]
    if not python.is_file():
        pytest.skip(f"configured host runtime is not installed: {python}")
    output = tmp_path.resolve() / "host-runtime-receipt.json"
    source_sha256 = "A" * 64
    completed = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            str(PROJECT_ROOT / "tools" / "validate_host_runtime.py"),
            "--config",
            str(CONTRACT_PATH),
            "--output",
            str(output),
            "--run-id",
            "runtime-test",
            "--source-sha256",
            source_sha256,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
    assert receipt["status"] == "PASS"
    assert receipt["context"] == {
        "run_id": "runtime-test",
        "source_sha256": source_sha256,
    }
    assert receipt["runtime"]["python_executable"] == str(python.resolve())
    assert receipt["runtime"]["python_version"] == contract["python_version"]
    assert receipt["isolation"]["isolated"] is True
    assert receipt["packages"]["opencv_distributions"] == ["opencv-python"]
    assert receipt["packages"]["forbidden_distributions_present"] == []
    assert receipt["packages"]["duplicate_distributions"] == []
    assert all(item["within_prefix"] for item in receipt["modules"])
    assert all(item["passed"] for item in receipt["smoke_tests"])
    assert receipt["bindings"]["runtime_config"]["sha256"] == _sha256(CONTRACT_PATH)
    assert receipt["bindings"]["requirements"]["sha256"] == _sha256(
        PROJECT_ROOT / "requirements.txt"
    )
    assert receipt["bindings"]["receipt_schema"]["sha256"] == _sha256(SCHEMA_PATH)
    assert receipt["bindings"]["validator"]["sha256"] == _sha256(
        PROJECT_ROOT / "tools" / "validate_host_runtime.py"
    )
