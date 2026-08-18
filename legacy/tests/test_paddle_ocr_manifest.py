from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "paddle_ocr_manifest.py"
CONFIG_PATH = PROJECT_ROOT / "ocr-config.json"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
RUNNER_PATH = PROJECT_ROOT / "tools" / "run_perception_gate.ps1"
HOST_RUNTIME_CONFIG_PATH = PROJECT_ROOT / "host-runtime.json"
HOST_RUNTIME_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "host-runtime-receipt.schema.json"
HOST_RUNTIME_VALIDATOR_PATH = PROJECT_ROOT / "tools" / "validate_host_runtime.py"


def load_adapter():
    module_name = "ai_autofigure_paddle_ocr_manifest_test"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load paddle_ocr_manifest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


HEAVY_MODULES = {
    "paddle",
    "paddleocr",
    "paddlex",
    "numpy",
    "PIL",
    "scipy",
    "cv2",
}
HEAVY_BEFORE = HEAVY_MODULES.intersection(sys.modules)
adapter = load_adapter()
HEAVY_AFTER_ADAPTER_IMPORT = HEAVY_MODULES.intersection(sys.modules)


def file_binding(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
    }


def make_host_runtime_receipt(run_id: str, source_sha256: str) -> dict:
    contract = json.loads(HOST_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    configured_python = (
        Path(contract["root"]) / contract["python_relative_path"]
    ).resolve()
    requirements_path = PROJECT_ROOT / contract["requirements_path"]
    return {
        "schema_version": "1.0.0",
        "created_at_utc": "2026-08-15T00:00:00Z",
        "status": "PASS",
        "context": {"run_id": run_id, "source_sha256": source_sha256},
        "bindings": {
            "runtime_config": file_binding(HOST_RUNTIME_CONFIG_PATH),
            "requirements": file_binding(requirements_path),
            "receipt_schema": file_binding(HOST_RUNTIME_SCHEMA_PATH),
            "validator": file_binding(HOST_RUNTIME_VALIDATOR_PATH),
        },
        "runtime": {
            "runtime_id": contract["runtime_id"],
            "configured_root": contract["root"],
            "resolved_root": str(Path(contract["root"]).resolve()),
            "expected_python": str(configured_python),
            "python_executable": str(configured_python),
            "expected_python_version": contract["python_version"],
            "python_version": contract["python_version"],
            "prefix": str(Path(contract["root"]).resolve()),
            "base_prefix": str(Path(contract["root"]).resolve()),
        },
        "isolation": {
            "required": True,
            "isolated": True,
            "ignore_environment": True,
            "no_user_site": True,
            "safe_path": True,
        },
        "packages": {
            "required": [
                {
                    "distribution": "opencv-python",
                    "expected_version": "4.13.0.92",
                    "actual_version": "4.13.0.92",
                    "passed": True,
                }
            ],
            "installed_inventory": [
                {"distribution": "opencv-python", "version": "4.13.0.92"}
            ],
            "installed_inventory_sha256": "A" * 64,
            "opencv_distributions": ["opencv-python"],
            "forbidden_distributions_present": [],
            "duplicate_distributions": [],
        },
        "modules": [
            {
                "distribution": "opencv-python",
                "module": "cv2",
                "origin": str(Path(contract["root"]) / "Lib" / "site-packages" / "cv2"),
                "within_prefix": True,
                "imported": True,
            }
        ],
        "smoke_tests": [{"name": "opencv_geometry", "passed": True, "detail": "ok"}],
        "checks": [{"name": "isolated_mode", "passed": True, "detail": "ok"}],
    }


def write_host_stage(
    directory: Path,
    artifact_name: str,
    *,
    source_sha256: str,
    python_executable: str,
    python_version: str,
) -> None:
    directory.mkdir(parents=True)
    (directory / artifact_name).write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source": {"sha256": source_sha256},
                "runtime": {
                    "python_executable": python_executable,
                    "python": python_version,
                },
            }
        ),
        encoding="utf-8",
    )


def write_bound_upstream_fixture(root: Path) -> tuple[str, str, Path, Path, Path, dict]:
    run_id = "runtime-binding-test"
    source_sha256 = "B" * 64
    runtime_directory = root / "runtime"
    analysis_directory = root / "analysis"
    segment_directory = root / "segmentation"
    runtime_directory.mkdir(parents=True)
    receipt = make_host_runtime_receipt(run_id, source_sha256)
    (runtime_directory / "host-runtime-receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    write_host_stage(
        analysis_directory,
        "inventory.json",
        source_sha256=source_sha256,
        python_executable=receipt["runtime"]["python_executable"],
        python_version=receipt["runtime"]["python_version"],
    )
    write_host_stage(
        segment_directory,
        "panels.json",
        source_sha256=source_sha256,
        python_executable=receipt["runtime"]["python_executable"],
        python_version=receipt["runtime"]["python_version"],
    )
    return (
        run_id,
        source_sha256,
        runtime_directory,
        analysis_directory,
        segment_directory,
        receipt,
    )


def observation(
    observation_id: str,
    view_id: str,
    text: str,
    score: float,
    x: float,
    y: float,
    w: float,
    h: float,
) -> dict:
    return {
        "observation_id": observation_id,
        "view_id": view_id,
        "view_kind": "full" if view_id == "full" else "tile",
        "text": text,
        "ocr_confidence": score,
        "bbox_source": {"x": x, "y": y, "w": w, "h": h},
        "polygon_source": [
            [x, y],
            [x + w, y],
            [x + w, y + h],
            [x, y + h],
        ],
        "textline_orientation_degrees": 0,
        "input_rotation_degrees": 0,
        "evidence_kind": "OCR_HYPOTHESIS",
        "verification_status": "UNVERIFIED",
    }


class LazyImportTests(unittest.TestCase):
    def test_adapter_import_is_lightweight(self):
        self.assertEqual(HEAVY_AFTER_ADAPTER_IMPORT, HEAVY_BEFORE)


class GeometryTests(unittest.TestCase):
    def test_full_plus_two_by_two_overlap_views(self):
        views = adapter.build_views(
            1000,
            800,
            rows=2,
            columns=2,
            overlap_px=100,
            upscale=2.0,
        )
        self.assertEqual(
            [view.view_id for view in views],
            [
                "full",
                "tile_r0_c0",
                "tile_r0_c1",
                "tile_r1_c0",
                "tile_r1_c1",
            ],
        )
        self.assertEqual(views[1].bbox_source, {"x": 0, "y": 0, "w": 550, "h": 450})
        self.assertEqual(views[2].bbox_source, {"x": 450, "y": 0, "w": 550, "h": 450})
        self.assertEqual(views[3].bbox_source, {"x": 0, "y": 350, "w": 550, "h": 450})
        self.assertEqual(views[4].bbox_source, {"x": 450, "y": 350, "w": 550, "h": 450})
        self.assertEqual(views[1].to_manifest()["ocr_input_size"], {"w": 1100, "h": 900})

    def test_quarter_turn_inverse_mapping_clockwise_90(self):
        view = adapter.ViewSpec(
            "review",
            "rotation_review",
            100,
            200,
            50,
            100,
            2.0,
            rotation_degrees=90,
            trigger_candidate_id="T0001",
        )
        # Original scaled point (u=20,v=40) maps to CW90 point (160,20).
        mapped = adapter.map_polygon_to_source([[160, 20]], view)
        self.assertEqual(mapped, [[110.0, 220.0]])
        self.assertEqual(view.to_manifest()["ocr_input_size"], {"w": 200, "h": 100})

    def test_quarter_turn_inverse_mapping_clockwise_270(self):
        view = adapter.ViewSpec(
            "review",
            "rotation_review",
            100,
            200,
            50,
            100,
            2.0,
            rotation_degrees=270,
        )
        # Original scaled point (u=20,v=40) maps to CW270 point (40,80).
        mapped = adapter.map_polygon_to_source([[40, 80]], view)
        self.assertEqual(mapped, [[110.0, 220.0]])

    def test_rotation_review_is_bounded_to_tall_candidates(self):
        candidates = [
            {"candidate_id": "T0001", "bbox_source": {"x": 10, "y": 20, "w": 10, "h": 40}},
            {"candidate_id": "T0002", "bbox_source": {"x": 100, "y": 100, "w": 80, "h": 20}},
        ]
        settings = {
            "enabled": True,
            "rotations_degrees": [90, 270],
            "vertical_aspect_ratio_min": 1.35,
            "padding_px": 12,
            "upscale": 2.0,
            "max_regions": 48,
        }
        views = adapter.build_rotation_review_views(candidates, 200, 200, settings)
        self.assertEqual(len(views), 2)
        self.assertTrue(all(view.trigger_candidate_id == "T0001" for view in views))
        self.assertEqual({view.rotation_degrees for view in views}, {90, 270})
        self.assertEqual(views[0].bbox_source, {"x": 0, "y": 8, "w": 32, "h": 64})

    def test_quarter_turn_tile_sweep_covers_first_pass_misses(self):
        base = adapter.build_views(1000, 800, overlap_px=100, upscale=2.0)
        settings = {
            "enabled": True,
            "tile_sweep_enabled": True,
            "tile_sweep_upscale": 1.0,
            "rotations_degrees": [90, 270],
            "vertical_aspect_ratio_min": 1.35,
        }
        review = adapter.build_quarter_turn_tile_sweep_views(base, settings)
        self.assertEqual(len(review), 8)
        self.assertEqual({view.rotation_degrees for view in review}, {90, 270})
        self.assertTrue(all(view.kind == "rotation_review" for view in review))
        self.assertTrue(all(view.trigger_view_id for view in review))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.dedupe = {
            "iou_threshold": 0.45,
            "containment_threshold": 0.72,
            "conflict_iou_threshold": 0.3,
            "conflict_containment_threshold": 0.62,
            "text_similarity_threshold": 0.88,
        }
        self.confidence = {"high_min": 0.97, "medium_min": 0.85}

    def test_duplicate_views_merge_but_keep_observations(self):
        merged = adapter.merge_observations(
            [
                observation("O00001", "full", "潮位", 0.98, 10, 10, 80, 20),
                observation("O00002", "tile_r0_c0", "潮位", 0.99, 11, 10, 79, 20),
                observation("O00003", "full", "风速", 0.96, 200, 10, 70, 20),
            ],
            self.dedupe,
            self.confidence,
        )
        self.assertEqual(len(merged), 2)
        tide = next(item for item in merged if item["text"] == "潮位")
        self.assertEqual(tide["agreement_count"], 2)
        self.assertEqual(tide["source_views"], ["full", "tile_r0_c0"])
        self.assertEqual(tide["alternatives"], [])
        self.assertEqual(tide["verification"]["status"], "UNVERIFIED")
        self.assertTrue(tide["requires_human_review"])

    def test_spatial_text_disagreement_becomes_alternative(self):
        merged = adapter.merge_observations(
            [
                observation("O00001", "full", "Mamba", 0.99, 10, 10, 100, 24),
                observation("O00002", "tile_r0_c0", "Marnba", 0.95, 10, 10, 100, 24),
            ],
            self.dedupe,
            self.confidence,
        )
        self.assertEqual(len(merged), 1)
        candidate = merged[0]
        self.assertEqual(candidate["confidence_band"], "OCR_CONFLICT")
        self.assertEqual(candidate["verification"]["status"], "CONFLICT")
        self.assertEqual(candidate["alternatives"][0]["text"], "Marnba")
        self.assertIn("OCR_CONFLICT", candidate["review_flags"])

    def test_high_ocr_score_never_self_confirms(self):
        candidate = adapter.merge_observations(
            [observation("O00001", "full", "Transformer", 0.9999, 10, 10, 100, 24)],
            self.dedupe,
            self.confidence,
        )[0]
        self.assertEqual(candidate["confidence_band"], "OCR_HIGH")
        self.assertEqual(
            candidate["verification"],
            {
                "status": "UNVERIFIED",
                "user_confirmed_text": None,
            },
        )


class HostRuntimeBindingTests(unittest.TestCase):
    def test_valid_receipt_binds_host_stage_interpreter_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            (
                run_id,
                source_sha256,
                runtime_directory,
                analysis_directory,
                segment_directory,
                _receipt,
            ) = write_bound_upstream_fixture(Path(directory))

            stages, degradations = adapter.validate_upstream_bindings(
                host_runtime_dir=runtime_directory,
                analysis_dir=analysis_directory,
                segment_dir=segment_directory,
                run_id=run_id,
                source_sha256=source_sha256,
            )

            self.assertEqual(
                [stage["name"] for stage in stages],
                ["host_runtime", "analysis", "segmentation"],
            )
            self.assertEqual(degradations, [])
            self.assertEqual(
                [item["relative_path"] for item in stages[0]["files"]],
                ["host-runtime-receipt.json"],
            )

    def test_tampered_or_ambiguous_receipt_fails_closed(self):
        mutations = {
            "status": lambda receipt: receipt.update(status="FAIL"),
            "context": lambda receipt: receipt["context"].update(source_sha256="C" * 64),
            "binding": lambda receipt: receipt["bindings"]["validator"].update(
                sha256="D" * 64
            ),
            "failed_check": lambda receipt: receipt["checks"][0].update(passed=False),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                (
                    run_id,
                    source_sha256,
                    runtime_directory,
                    _analysis_directory,
                    _segment_directory,
                    receipt,
                ) = write_bound_upstream_fixture(Path(directory))
                mutate(receipt)
                (runtime_directory / "host-runtime-receipt.json").write_text(
                    json.dumps(receipt), encoding="utf-8"
                )
                with self.assertRaises(adapter.ManifestError):
                    adapter.validate_host_runtime_receipt(
                        runtime_directory,
                        run_id=run_id,
                        source_sha256=source_sha256,
                    )

        with tempfile.TemporaryDirectory() as directory:
            (
                run_id,
                source_sha256,
                runtime_directory,
                _analysis_directory,
                _segment_directory,
                _receipt,
            ) = write_bound_upstream_fixture(Path(directory))
            (runtime_directory / "duplicate.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(adapter.ManifestError, msg="extra files must be rejected"):
                adapter.validate_host_runtime_receipt(
                    runtime_directory,
                    run_id=run_id,
                    source_sha256=source_sha256,
                )

    def test_host_stage_runtime_mismatch_fails_closed(self):
        for field, wrong_value in (
            ("python_executable", r"D:\wrong\python.exe"),
            ("python", "0.0.0"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                (
                    run_id,
                    source_sha256,
                    runtime_directory,
                    analysis_directory,
                    segment_directory,
                    _receipt,
                ) = write_bound_upstream_fixture(Path(directory))
                inventory_path = analysis_directory / "inventory.json"
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                inventory["runtime"][field] = wrong_value
                inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

                with self.assertRaises(adapter.ManifestError):
                    adapter.validate_upstream_bindings(
                        host_runtime_dir=runtime_directory,
                        analysis_dir=analysis_directory,
                        segment_dir=segment_directory,
                        run_id=run_id,
                        source_sha256=source_sha256,
                    )

    def test_unbound_host_runtime_is_explicitly_inconclusive(self):
        stages, degradations = adapter.validate_upstream_bindings(
            host_runtime_dir=None,
            analysis_dir=None,
            segment_dir=None,
            run_id="runtime-binding-test",
            source_sha256="B" * 64,
        )

        self.assertEqual(stages, [])
        self.assertEqual(
            degradations,
            [
                "ANALYSIS_ARTIFACTS_NOT_BOUND",
                "HOST_RUNTIME_RECEIPT_NOT_BOUND",
                "SEGMENTATION_ARTIFACTS_NOT_BOUND",
            ],
        )


class ReproducibilityTests(unittest.TestCase):
    def test_atomic_json_has_no_partial_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            adapter.atomic_write_json(target, {"value": "∆", "ok": True})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["value"], "∆")
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_model_hash_validation_fails_closed(self):
        config = copy.deepcopy(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config["paddle_root"] = str(root)
            models_root = root / config["models_root_relative_path"]
            for role, model in config["models"].items():
                model_dir = models_root / model["relative_path"]
                model_dir.mkdir(parents=True)
                payloads = {
                    "inference.json": b"{}",
                    "inference.pdiparams": (role + " weights").encode(),
                    "inference.yml": f"Global:\n  model_name: {model['name']}\n".encode(),
                }
                for filename, payload in payloads.items():
                    path = model_dir / filename
                    path.write_bytes(payload)
                    model["files"][filename] = hashlib.sha256(payload).hexdigest().upper()

            evidence = adapter.validate_model_files(config)
            self.assertEqual(set(evidence), set(config["models"]))
            broken = (
                models_root
                / config["models"]["text_detection"]["relative_path"]
                / "inference.pdiparams"
            )
            broken.write_bytes(b"tampered")
            with self.assertRaises(adapter.ManifestError):
                adapter.validate_model_files(config)

    def test_pinned_config_and_schema_are_well_formed(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        adapter.validate_config(config)
        self.assertEqual(config["models"]["text_detection"]["name"], "PP-OCRv6_medium_det")
        self.assertEqual(config["models"]["text_recognition"]["name"], "PP-OCRv6_medium_rec")
        self.assertEqual(
            set(config["runtime"]["packages"]),
            {"paddleocr", "paddlex", "paddle", "numpy", "pillow", "scipy", "opencv"},
        )
        self.assertEqual(
            config["runtime"]["packages"]["opencv"],
            {
                "distribution": "opencv-contrib-python",
                "version": "4.10.0.84",
                "import_version": "4.10.0",
            },
        )
        self.assertNotIn("fixture_gate", config)
        self.assertEqual(
            config["acceptance_fixture_relative_path"],
            "examples\\target_figure.fixture.json",
        )
        self.assertEqual(
            schema["properties"]["policy"]["properties"]["ocr_is_ground_truth"]["const"], False
        )
        self.assertEqual(
            schema["properties"]["policy"]["properties"]["network_access"]["const"],
            "NETWORK_NOT_REQUESTED_BY_PIPELINE",
        )
        self.assertEqual(
            set(schema["$defs"]["upstreamStage"]["properties"]["name"]["enum"]),
            {"host_runtime", "analysis", "segmentation"},
        )

    def test_target_fixture_is_the_only_acceptance_threshold_authority(self):
        config = adapter.load_config(CONFIG_PATH)
        fixture, evidence = adapter.load_acceptance_fixture(config)
        serialized_config = json.dumps(config, ensure_ascii=False)
        self.assertNotIn("minimumDetectedTextBoxes", serialized_config)
        self.assertNotIn("requiredExactAnchors", serialized_config)
        self.assertEqual(
            evidence["source_sha256"],
            fixture["sha256"].upper(),
        )

        expectations = fixture["ocrSmokeExpectations"]
        anchors = expectations["requiredExactAnchors"]
        formulas = expectations["mustRemainFormulaCandidates"]
        minimum = expectations["minimumDetectedTextBoxes"]
        candidates = [{"text": anchor} for anchor in anchors]
        candidates.extend(
            {
                "text": formula,
                "review_flags": ["FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"],
            }
            for formula in formulas
        )
        candidates.extend({"text": f"filler-{index}"} for index in range(minimum - len(candidates)))
        acceptance = adapter.evaluate_acceptance(fixture["sha256"], candidates, fixture)
        self.assertTrue(acceptance["fixture_applied"])
        self.assertTrue(acceptance["passed"])

        stricter_fixture = copy.deepcopy(fixture)
        stricter_fixture["ocrSmokeExpectations"]["minimumDetectedTextBoxes"] = minimum + 1
        stricter = adapter.evaluate_acceptance(fixture["sha256"], candidates, stricter_fixture)
        self.assertFalse(stricter["passed"])

    def test_fixture_anchor_may_be_reconstructed_from_overlapping_same_line_candidates(self):
        config = adapter.load_config(CONFIG_PATH)
        fixture, _ = adapter.load_acceptance_fixture(config)
        expectations = fixture["ocrSmokeExpectations"]
        split_anchor = "(d) Transformer-Mamba 混合编码器（并行建模）"
        candidates = [
            {"text": anchor}
            for anchor in expectations["requiredExactAnchors"]
            if anchor != split_anchor
        ]
        candidates.extend(
            [
                {
                    "text": "(d) Transformer-Mar",
                    "bbox_source": {"x": 628, "y": 21.5, "w": 188, "h": 25.5},
                },
                {
                    "text": "ormer-Mamba 混合编码器（并行建模）",
                    "bbox_source": {"x": 720, "y": 23.5, "w": 296.5, "h": 21},
                },
            ]
        )
        candidates.extend(
            {
                "text": formula,
                "review_flags": ["FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"],
            }
            for formula in expectations["mustRemainFormulaCandidates"]
        )
        minimum = expectations["minimumDetectedTextBoxes"]
        candidates.extend({"text": f"filler-{index}"} for index in range(minimum - len(candidates)))

        acceptance = adapter.evaluate_acceptance(fixture["sha256"], candidates, fixture)

        self.assertTrue(acceptance["passed"])
        split_check = next(
            check for check in acceptance["checks"] if check["name"] == f"fixture_anchor:{split_anchor}"
        )
        self.assertTrue(split_check["passed"])

    def test_spatial_merge_does_not_join_distant_same_row_fragments(self):
        first = {
            "text": "(d) Transformer-Mar",
            "bbox_source": {"x": 10, "y": 10, "w": 180, "h": 25},
        }
        distant = {
            "text": "ormer-Mamba 混合编码器（并行建模）",
            "bbox_source": {"x": 900, "y": 10, "w": 300, "h": 25},
        }

        self.assertIsNone(adapter.spatially_merge_candidate_pair(first, distant))

    def test_fixture_formula_holdout_requires_formula_review_flag(self):
        config = adapter.load_config(CONFIG_PATH)
        fixture, _ = adapter.load_acceptance_fixture(config)
        expectations = fixture["ocrSmokeExpectations"]
        candidates = [{"text": anchor} for anchor in expectations["requiredExactAnchors"]]
        candidates.extend({"text": formula, "review_flags": []} for formula in expectations["mustRemainFormulaCandidates"])
        candidates.extend(
            {"text": f"filler-{index}"}
            for index in range(expectations["minimumDetectedTextBoxes"] - len(candidates))
        )

        acceptance = adapter.evaluate_acceptance(fixture["sha256"], candidates, fixture)

        self.assertFalse(acceptance["passed"])
        self.assertTrue(
            any(
                check["name"].startswith("fixture_formula_candidate:") and not check["passed"]
                for check in acceptance["checks"]
            )
        )

    def test_checked_in_schema_validates_constructed_candidate(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        candidate = adapter.merge_observations(
            [observation("O00001", "full", "潮位", 0.999, 10, 10, 80, 20)],
            {
                "iou_threshold": 0.45,
                "containment_threshold": 0.72,
                "conflict_iou_threshold": 0.3,
                "conflict_containment_threshold": 0.62,
                "text_similarity_threshold": 0.88,
            },
            {"high_min": 0.97, "medium_min": 0.85},
        )[0]
        candidate_schema = {"$defs": schema["$defs"], "$ref": "#/$defs/candidate"}
        adapter.validate_against_schema(candidate, candidate_schema)
        candidate["verification"]["status"] = "CONFIRMED"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_against_schema(candidate, candidate_schema)

    def test_acceptance_fails_closed_for_zero_candidates(self):
        config = adapter.load_config(CONFIG_PATH)
        fixture, _ = adapter.load_acceptance_fixture(config)
        acceptance = adapter.evaluate_acceptance(
            "0" * 64,
            [],
            fixture,
        )
        self.assertFalse(acceptance["passed"])
        self.assertFalse(acceptance["fixture_applied"])

    def test_runner_calls_reentrant_clis_without_hot_patching(self):
        runner = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn("tools\\analyze_target.py", runner)
        self.assertIn("tools\\segment_panels.py", runner)
        self.assertIn("tools\\validate_host_runtime.py", runner)
        self.assertIn("tools\\geometry_refinement.py", runner)
        self.assertIn("schemas\\geometry-manifest.schema.json", runner)
        self.assertIn("host-runtime.json", runner)
        self.assertIn("$HostPython", runner)
        self.assertIn("$PaddlePython", runner)
        self.assertIn("-Interpreter $HostPython", runner)
        self.assertIn("-Interpreter $PaddlePython", runner)
        self.assertIn("-Name 'geometry' -Interpreter $HostPython", runner)
        self.assertIn("$GeometryExitCode", runner)
        self.assertIn("-AllowedExitCodes @(0, 3)", runner)
        self.assertIn("Geometry exit/status mismatch", runner)
        self.assertIn("'--ocr-manifest'", runner)
        self.assertIn("'--host-runtime-receipt'", runner)
        self.assertIn("'--host-runtime-dir'", runner)
        self.assertIn("'--output'", runner)
        self.assertIn("examples\\generated\\runs", runner)
        self.assertNotIn("work\\runs", runner)
        self.assertIn("Project-local perception output must stay under", runner)
        self.assertIn("CanonicalRunsRoot", runner)
        self.assertIn("outside the project must be an explicit absolute path", runner)
        self.assertIn("Refusing to claim a non-fresh perception run", runner)
        self.assertIn("Refusing to reuse a pre-existing run subdirectory", runner)
        self.assertNotIn("New-Item -ItemType Directory -Path $_ -Force", runner)
        self.assertNotIn("IsPathFullyQualified", runner)
        self.assertIn("REMOVED_AFTER_STAGE", runner)
        self.assertIn("cleanup target escaped the owned run", runner)
        self.assertIn("Win32 or NT device namespace path", runner)
        self.assertIn("cannot traverse a symlink or junction", runner)
        self.assertIn("tools\\output_policy.py", runner)
        self.assertIn("-I -S -B -X utf8", runner)
        self.assertIn("PolicyOutput.Count -ne 1", runner)
        self.assertIn("CanonicalExamplesRoot", runner)
        self.assertIn("Manifest run_id mismatch", runner)
        self.assertIn("acceptance_fixture_relative_path", runner)
        self.assertNotIn("fixture_gate", runner)
        self.assertIn("NETWORK_NOT_REQUESTED_BY_PIPELINE", runner)
        self.assertIn("GEOMETRY_OBSERVATIONS_READY", runner)
        self.assertIn("GEOMETRY_INCONCLUSIVE", runner)
        self.assertIn("geometry-manifest.json", runner)
        self.assertIn("geometry-overlay.png", runner)
        self.assertIn("geometry-label-atlas.png", runner)
        self.assertIn("geometry-ambiguity-mask.png", runner)
        self.assertIn("Assert-ExactJsonPropertySet", runner)
        self.assertIn("Open-ReadOnlyEvidenceSnapshot", runner)
        self.assertIn("[System.IO.FileShare]::Read", runner)
        self.assertIn("Assert-EvidenceSnapshotUnchanged", runner)
        self.assertIn("$GeometryManifestSnapshot", runner)
        self.assertIn("$PerceptionManifestSnapshot", runner)
        self.assertIn("$HostRuntimeReceiptSnapshot", runner)
        self.assertIn("$FrozenSourceSnapshot", runner)
        self.assertIn("-Name 'geometry-contract-verify' -Interpreter $HostPython", runner)
        self.assertIn("'--verify-manifest'", runner)
        self.assertIn("size does not match its manifest record", runner)
        self.assertIn("hash does not match its manifest record", runner)
        self.assertIn("candidate_count differs from the OCR manifest", runner)
        self.assertIn("promotion_allowed", runner)
        self.assertIn("GEOMETRY_MANIFEST=", runner)
        self.assertIn("GEOMETRY_OVERLAY=", runner)
        self.assertIn("schema_version = '1.3.0'", runner)
        self.assertIn("tools\\prepare_agent_vision_task.py", runner)
        self.assertIn("agent-vision-config.json", runner)
        self.assertIn("-Name 'agent-vision-pkg'", runner)
        self.assertIn("'--geometry-manifest'", runner)
        self.assertIn("'--verify-package'", runner)
        self.assertIn("SEGMENTATION_SKIPPED_BY_CALLER", runner)
        self.assertIn("COMPLETE_TASK_PACKAGE_READY", runner)
        self.assertIn("AGENT_VISION_TASK=", runner)
        self.assertIn("AGENT_VISION_INSTRUCTIONS=", runner)
        self.assertIn("agent_vision_pkg = $AgentVisionStageStatus", runner)
        self.assertNotIn("ROOT = Path", runner)
        self.assertNotIn("vis = A.copy", runner)
        self.assertNotIn("runpy", runner)


if __name__ == "__main__":
    unittest.main()
