#!/usr/bin/env python3
"""Validate the isolated host-side CV runtime and emit a hash-bound receipt."""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "host-runtime.json"
CONFIG_SCHEMA_VERSION = "1.0.0"
RECEIPT_SCHEMA_VERSION = "1.0.0"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
SHA256_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
NAME_NORMALIZER = re.compile(r"[-_.]+")

EXIT_RUNTIME_INVALID = 2
EXIT_CONTRACT_OR_POLICY = 3
EXIT_INTERNAL_ERROR = 4

_CONTRACT_KEYS = {
    "schema_version",
    "runtime_id",
    "root",
    "python_relative_path",
    "python_version",
    "requirements_path",
    "requirements_sha256",
    "receipt_schema_path",
    "receipt_schema_sha256",
    "isolation_required",
    "allowed_opencv_distribution",
    "forbidden_distributions",
    "forbidden_imports",
    "smoke_tests",
}

_IMPORT_MODULES = {
    "numpy": "numpy",
    "pillow": "PIL",
    "scipy": "scipy",
    "opencv-python": "cv2",
    "scikit-image": "skimage",
    "python-pptx": "pptx",
    "jsonschema": "jsonschema",
    "matplotlib": "matplotlib",
    "latex2mathml": "latex2mathml",
    "lxml": "lxml",
    "pytest": "pytest",
    "ruff": "ruff",
}


class RuntimeContractError(ValueError):
    """Raised when the validator contract cannot be interpreted safely."""


def normalize_distribution(name: str) -> str:
    return NAME_NORMALIZER.sub("-", name).lower()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


@functools.cache
def _load_output_policy() -> tuple[type[Exception], Callable[..., Path]]:
    policy_path = Path(__file__).resolve().with_name("output_policy.py")
    spec = importlib.util.spec_from_file_location("ai_autofigure_output_policy", policy_path)
    if spec is None or spec.loader is None:
        raise RuntimeContractError(f"Cannot load output policy: {policy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OutputPolicyError, module.resolve_output_path


def _atomic_write_json(
    path: str | os.PathLike[str],
    payload: Mapping[str, Any],
    *,
    project_root: Path,
) -> Path:
    _error_type, resolve_output_path = _load_output_policy()
    destination = resolve_output_path(path, project_root=project_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _project_file(project_root: Path, value: str, *, label: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else project_root / raw
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(project_root.resolve(strict=False))
    except ValueError as exc:
        raise RuntimeContractError(f"{label} must remain inside project root: {value}") from exc
    return resolved


def _require_nonempty_string(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeContractError(f"{key} must be a non-empty string")
    return value


def load_contract(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(f"Cannot read runtime contract {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeContractError("runtime contract root must be an object")
    keys = set(payload)
    missing = sorted(_CONTRACT_KEYS - keys)
    extra = sorted(keys - _CONTRACT_KEYS)
    if missing or extra:
        raise RuntimeContractError(
            f"runtime contract keys mismatch; missing={missing}, extra={extra}"
        )
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise RuntimeContractError(
            f"unsupported runtime contract schema_version: {payload.get('schema_version')!r}"
        )
    for key in (
        "runtime_id",
        "root",
        "python_relative_path",
        "python_version",
        "requirements_path",
        "requirements_sha256",
        "receipt_schema_path",
        "receipt_schema_sha256",
        "allowed_opencv_distribution",
    ):
        _require_nonempty_string(payload, key)
    if payload["isolation_required"] is not True:
        raise RuntimeContractError("isolation_required must be true")
    for key in ("forbidden_distributions", "forbidden_imports", "smoke_tests"):
        values = payload.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item for item in values)
            or len(set(values)) != len(values)
        ):
            raise RuntimeContractError(f"{key} must be a non-empty unique string array")
    for key in ("requirements_sha256", "receipt_schema_sha256"):
        if not SHA256_PATTERN.fullmatch(payload[key]):
            raise RuntimeContractError(f"{key} must be a 64-character SHA-256")
        payload[key] = payload[key].upper()
    root = Path(payload["root"])
    if not root.is_absolute():
        raise RuntimeContractError("root must be an absolute path")
    relative_python = Path(payload["python_relative_path"])
    if relative_python.is_absolute() or ".." in relative_python.parts:
        raise RuntimeContractError("python_relative_path must be a contained relative path")
    if normalize_distribution(payload["allowed_opencv_distribution"]) != "opencv-python":
        raise RuntimeContractError("allowed_opencv_distribution must be opencv-python")
    unknown_smokes = sorted(set(payload["smoke_tests"]) - set(_SMOKE_TESTS))
    if unknown_smokes:
        raise RuntimeContractError(f"unknown smoke tests: {unknown_smokes}")
    return payload


def parse_requirements(path: Path) -> list[tuple[str, str]]:
    requirements: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise RuntimeContractError(
                f"requirements must use exact distribution==version pins; line {line_number}: {line!r}"
            )
        distribution, version = (part.strip() for part in line.split("==", 1))
        normalized = normalize_distribution(distribution)
        if not distribution or not version or any(token in version for token in ";# "):
            raise RuntimeContractError(f"invalid exact requirement at line {line_number}: {line!r}")
        if normalized in seen:
            raise RuntimeContractError(f"duplicate requirement: {distribution}")
        seen.add(normalized)
        requirements.append((distribution, version))
    if not requirements:
        raise RuntimeContractError("requirements file contains no exact pins")
    return requirements


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _file_binding(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve(strict=False)),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _installed_inventory() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if name and version:
            records.append({"distribution": name, "version": version})
    records.sort(key=lambda item: (normalize_distribution(item["distribution"]), item["version"]))
    return records


def find_forbidden_distributions(
    installed_names: Sequence[str],
    *,
    allowed_opencv: str,
    explicitly_forbidden: Sequence[str],
) -> tuple[list[str], list[str]]:
    allowed = normalize_distribution(allowed_opencv)
    explicit = {normalize_distribution(item) for item in explicitly_forbidden}
    opencv: list[str] = []
    forbidden: list[str] = []
    for display_name in installed_names:
        normalized = normalize_distribution(display_name)
        if normalized.startswith("opencv-") or normalized == "opencv":
            opencv.append(display_name)
            if normalized != allowed:
                forbidden.append(display_name)
        if (
            normalized in explicit
            or normalized.startswith("torch")
            or normalized.startswith("paddle")
        ):
            forbidden.append(display_name)
    return sorted(set(opencv), key=str.casefold), sorted(set(forbidden), key=str.casefold)


def find_duplicate_distributions(installed_names: Sequence[str]) -> list[str]:
    """Return normalized names represented by more than one metadata record."""
    counts: dict[str, int] = {}
    for display_name in installed_names:
        normalized = normalize_distribution(display_name)
        counts[normalized] = counts.get(normalized, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def _module_records(
    requirements: Sequence[tuple[str, str]],
    *,
    runtime_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for distribution, _version in requirements:
        normalized = normalize_distribution(distribution)
        module_name = _IMPORT_MODULES.get(normalized)
        if module_name is None:
            records.append(
                {
                    "distribution": distribution,
                    "module": "<UNMAPPED>",
                    "origin": None,
                    "within_prefix": False,
                    "imported": False,
                }
            )
            checks.append(
                _check(
                    f"module_origin:{normalized}",
                    False,
                    "direct requirement has no audited import-module mapping",
                )
            )
            continue
        try:
            module = importlib.import_module(module_name)
            origin_value = getattr(module, "__file__", None)
            origin = Path(origin_value).resolve(strict=False) if origin_value else None
            inside = origin is not None and _path_is_within(origin, runtime_root)
            records.append(
                {
                    "distribution": distribution,
                    "module": module_name,
                    "origin": str(origin) if origin is not None else None,
                    "within_prefix": inside,
                    "imported": True,
                }
            )
            checks.append(
                _check(
                    f"module_origin:{normalized}",
                    inside,
                    f"{module_name} origin={origin}; runtime_root={runtime_root}",
                )
            )
        except Exception as exc:  # import failures are runtime evidence
            records.append(
                {
                    "distribution": distribution,
                    "module": module_name,
                    "origin": None,
                    "within_prefix": False,
                    "imported": False,
                }
            )
            checks.append(_check(f"module_origin:{normalized}", False, f"import failed: {exc}"))
    return records, checks


def _smoke_opencv_geometry() -> str:
    import cv2
    import numpy as np

    image = np.zeros((128, 128), dtype=np.uint8)
    cv2.rectangle(image, (16, 18), (110, 96), 255, 2)
    cv2.line(image, (12, 112), (116, 8), 255, 2)
    edges = cv2.Canny(image, 40, 120)
    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        image, connectivity=8
    )
    detected_lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(edges)[0]
    if not contours or component_count < 2 or stats.shape[1] != 5 or detected_lines is None:
        raise AssertionError("OpenCV contour/component/LSD smoke produced no geometry")

    reference = np.zeros((64, 64), dtype=np.float32)
    reference[18:34, 22:42] = 1.0
    shifted = np.roll(np.roll(reference, 4, axis=0), -3, axis=1)
    (dx, dy), response = cv2.phaseCorrelate(reference, shifted)
    if abs(dx + 3.0) > 0.1 or abs(dy - 4.0) > 0.1 or response < 0.5:
        raise AssertionError(f"OpenCV phase correlation mismatch: {(dx, dy, response)}")
    return (
        f"cv2={cv2.__version__}; contours={len(contours)}; "
        f"components={component_count}; lines={len(detected_lines)}"
    )


def _smoke_scipy_distance_geometry() -> str:
    import numpy as np
    from scipy.ndimage import distance_transform_edt

    mask = np.ones((9, 9), dtype=np.uint8)
    mask[4, 4] = 0
    distances = distance_transform_edt(mask)
    if distances[4, 4] != 0 or abs(float(distances[4, 5]) - 1.0) > 1e-12:
        raise AssertionError("SciPy EDT did not preserve exact one-pixel distance")
    diagonal = float(distances[5, 5])
    if abs(diagonal - 2**0.5) > 1e-12:
        raise AssertionError(f"SciPy EDT diagonal mismatch: {diagonal}")
    return f"one_pixel={distances[4, 5]:.12f}; diagonal={diagonal:.12f}"


def _smoke_skimage_registration() -> str:
    import numpy as np
    from skimage.registration import phase_cross_correlation

    reference = np.zeros((64, 64), dtype=np.float32)
    reference[12:24, 19:37] = 1.0
    moving = np.roll(np.roll(reference, 3, axis=0), -4, axis=1)
    shift, error, _phase = phase_cross_correlation(reference, moving, upsample_factor=10)
    if not np.allclose(shift, (-3.0, 4.0), atol=0.05):
        raise AssertionError(f"scikit-image registration mismatch: {shift}")
    if not np.isfinite(error):
        raise AssertionError(f"scikit-image registration returned non-finite error: {error}")
    return f"recovery_shift={shift.tolist()}; error={float(error):.12f}"


def _smoke_pillow_numpy_roundtrip() -> str:
    import numpy as np
    from PIL import Image

    source = np.zeros((17, 19, 3), dtype=np.uint8)
    source[2:15, 4:16] = (12, 133, 241)
    restored = np.asarray(Image.fromarray(source, mode="RGB"))
    if not np.array_equal(source, restored):
        raise AssertionError("Pillow/NumPy in-memory RGB round-trip changed pixels")
    return f"shape={list(restored.shape)}; dtype={restored.dtype}"


def _smoke_scientific_stack() -> str:
    import jsonschema
    import latex2mathml.converter
    import lxml.etree
    import matplotlib
    from pptx import Presentation

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    jsonschema.validate({"ok": True}, {"type": "object", "required": ["ok"]})
    mathml = latex2mathml.converter.convert(r"x^2+y^2")
    parsed = lxml.etree.fromstring(mathml.encode("utf-8"))
    presentation = Presentation()
    figure = plt.figure(figsize=(1, 1))
    plt.close(figure)
    if parsed is None or len(presentation.slides) != 0:
        raise AssertionError("scientific authoring stack smoke failed")
    return "jsonschema, latex2mathml, lxml, matplotlib(Agg), and python-pptx passed"


_SMOKE_TESTS: dict[str, Callable[[], str]] = {
    "opencv_geometry": _smoke_opencv_geometry,
    "scipy_distance_geometry": _smoke_scipy_distance_geometry,
    "skimage_registration": _smoke_skimage_registration,
    "pillow_numpy_roundtrip": _smoke_pillow_numpy_roundtrip,
    "scientific_stack": _smoke_scientific_stack,
}


def _run_smoke_tests(names: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in names:
        try:
            detail = _SMOKE_TESTS[name]()
            records.append(_check(name, True, detail))
        except Exception as exc:  # smoke failure is recorded, never promoted to pass
            records.append(_check(name, False, f"{type(exc).__name__}: {exc}"))
    return records


def validate_runtime(
    *,
    config_path: Path,
    project_root: Path,
    run_id: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if (run_id is None) != (source_sha256 is None):
        raise RuntimeContractError("run_id and source_sha256 must be supplied together")
    if run_id is not None and RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise RuntimeContractError(f"invalid run_id: {run_id!r}")
    if source_sha256 is not None and SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise RuntimeContractError("source_sha256 must contain exactly 64 hexadecimal characters")

    project_root = project_root.resolve(strict=False)
    config_path = _project_file(project_root, str(config_path), label="runtime config")
    config = load_contract(config_path)
    requirements_path = _project_file(
        project_root, config["requirements_path"], label="requirements_path"
    )
    schema_path = _project_file(
        project_root, config["receipt_schema_path"], label="receipt_schema_path"
    )
    requirements = parse_requirements(requirements_path)
    checks: list[dict[str, Any]] = []

    requirements_hash = sha256_file(requirements_path)
    schema_hash = sha256_file(schema_path)
    checks.append(
        _check(
            "requirements_hash",
            requirements_hash == config["requirements_sha256"],
            f"expected={config['requirements_sha256']}; actual={requirements_hash}",
        )
    )
    checks.append(
        _check(
            "receipt_schema_hash",
            schema_hash == config["receipt_schema_sha256"],
            f"expected={config['receipt_schema_sha256']}; actual={schema_hash}",
        )
    )

    runtime_root = Path(config["root"]).resolve(strict=False)
    expected_python = (runtime_root / config["python_relative_path"]).resolve(strict=False)
    actual_python = Path(sys.executable).resolve(strict=False)
    actual_prefix = Path(sys.prefix).resolve(strict=False)
    actual_base_prefix = Path(sys.base_prefix).resolve(strict=False)
    actual_version = platform.python_version()

    checks.extend(
        [
            _check(
                "runtime_root_exists",
                runtime_root.is_dir(),
                f"runtime_root={runtime_root}",
            ),
            _check(
                "python_executable",
                expected_python == actual_python,
                f"expected={expected_python}; actual={actual_python}",
            ),
            _check(
                "python_version",
                actual_version == config["python_version"],
                f"expected={config['python_version']}; actual={actual_version}",
            ),
            _check(
                "sys_prefix",
                actual_prefix == runtime_root and actual_base_prefix == runtime_root,
                f"prefix={actual_prefix}; base_prefix={actual_base_prefix}; root={runtime_root}",
            ),
            _check(
                "isolated_mode",
                bool(sys.flags.isolated)
                and bool(sys.flags.ignore_environment)
                and bool(sys.flags.no_user_site)
                and bool(sys.flags.safe_path),
                (
                    f"isolated={sys.flags.isolated}; ignore_environment={sys.flags.ignore_environment}; "
                    f"no_user_site={sys.flags.no_user_site}; safe_path={sys.flags.safe_path}"
                ),
            ),
        ]
    )

    inventory = _installed_inventory()
    inventory_by_name: dict[str, list[str]] = {}
    for record in inventory:
        inventory_by_name.setdefault(normalize_distribution(record["distribution"]), []).append(
            record["version"]
        )

    required_packages: list[dict[str, Any]] = []
    for distribution, expected_version in requirements:
        versions = inventory_by_name.get(normalize_distribution(distribution), [])
        actual_version_value = versions[0] if len(versions) == 1 else None
        passed = versions == [expected_version]
        required_packages.append(
            {
                "distribution": distribution,
                "expected_version": expected_version,
                "actual_version": actual_version_value,
                "passed": passed,
            }
        )
        checks.append(
            _check(
                f"package_version:{normalize_distribution(distribution)}",
                passed,
                f"expected={expected_version}; installed={versions}",
            )
        )

    installed_names = [record["distribution"] for record in inventory]
    duplicate_distributions = find_duplicate_distributions(installed_names)
    checks.append(
        _check(
            "unique_distribution_metadata",
            not duplicate_distributions,
            f"duplicate_normalized_names={duplicate_distributions}",
        )
    )
    opencv_distributions, forbidden_present = find_forbidden_distributions(
        installed_names,
        allowed_opencv=config["allowed_opencv_distribution"],
        explicitly_forbidden=config["forbidden_distributions"],
    )
    allowed_opencv = normalize_distribution(config["allowed_opencv_distribution"])
    normalized_opencv = [normalize_distribution(item) for item in opencv_distributions]
    checks.append(
        _check(
            "single_opencv_distribution",
            normalized_opencv == [allowed_opencv],
            f"allowed={allowed_opencv}; installed={opencv_distributions}",
        )
    )
    checks.append(
        _check(
            "forbidden_distributions_absent",
            not forbidden_present,
            f"present={forbidden_present}",
        )
    )

    forbidden_imports_present: list[str] = []
    for module_name in config["forbidden_imports"]:
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, AttributeError, ValueError):
            available = False
        if available:
            forbidden_imports_present.append(module_name)
    checks.append(
        _check(
            "forbidden_imports_absent",
            not forbidden_imports_present,
            f"importable={forbidden_imports_present}",
        )
    )

    modules, module_checks = _module_records(requirements, runtime_root=runtime_root)
    checks.extend(module_checks)
    smoke_tests = _run_smoke_tests(config["smoke_tests"])
    checks.extend(
        _check(f"smoke:{record['name']}", record["passed"], record["detail"])
        for record in smoke_tests
    )

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "context": {
            "run_id": run_id,
            "source_sha256": source_sha256.upper() if source_sha256 is not None else None,
        },
        "bindings": {
            "runtime_config": _file_binding(config_path),
            "requirements": _file_binding(requirements_path),
            "receipt_schema": _file_binding(schema_path),
            "validator": _file_binding(Path(__file__).resolve()),
        },
        "runtime": {
            "runtime_id": config["runtime_id"],
            "configured_root": config["root"],
            "resolved_root": str(runtime_root),
            "expected_python": str(expected_python),
            "python_executable": str(actual_python),
            "expected_python_version": config["python_version"],
            "python_version": actual_version,
            "prefix": str(actual_prefix),
            "base_prefix": str(actual_base_prefix),
        },
        "isolation": {
            "required": True,
            "isolated": bool(sys.flags.isolated),
            "ignore_environment": bool(sys.flags.ignore_environment),
            "no_user_site": bool(sys.flags.no_user_site),
            "safe_path": bool(sys.flags.safe_path),
        },
        "packages": {
            "required": required_packages,
            "installed_inventory": inventory,
            "installed_inventory_sha256": sha256_json(inventory),
            "opencv_distributions": opencv_distributions,
            "forbidden_distributions_present": forbidden_present,
            "duplicate_distributions": duplicate_distributions,
        },
        "modules": modules,
        "smoke_tests": smoke_tests,
        "checks": checks,
    }
    return receipt


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Runtime contract; relative paths are resolved from --project-root",
    )
    parser.add_argument("--output", required=True, help="Validation receipt JSON output")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root used for contract containment and output policy",
    )
    parser.add_argument("--run-id", help="Optional run ID; requires --source-sha256")
    parser.add_argument(
        "--source-sha256",
        help="Optional frozen source SHA-256; requires --run-id",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(args.project_root).resolve(strict=False)
    try:
        receipt = validate_runtime(
            config_path=Path(args.config),
            project_root=project_root,
            run_id=args.run_id,
            source_sha256=args.source_sha256,
        )
        destination = _atomic_write_json(args.output, receipt, project_root=project_root)
    except RuntimeContractError as exc:
        print(f"HOST_RUNTIME_CONTRACT_REJECTED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_OR_POLICY
    except Exception as exc:
        output_policy_error, _resolver = _load_output_policy()
        if isinstance(exc, output_policy_error):
            print(f"OUTPUT_POLICY_REJECTED: {exc}", file=sys.stderr)
            return EXIT_CONTRACT_OR_POLICY
        print(f"HOST_RUNTIME_VALIDATOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    print(f"HOST_RUNTIME_{receipt['status']}: {destination}")
    return 0 if receipt["status"] == "PASS" else EXIT_RUNTIME_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
