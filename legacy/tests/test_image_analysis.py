from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import analyze_target  # noqa: E402
import segment_panels  # noqa: E402


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(path: Path) -> np.ndarray:
    rgb = np.full((48, 64, 3), 254, dtype=np.uint8)
    rgb[10:26, 12:30] = (20, 60, 180)
    rgb[28:40, 36:58] = (240, 210, 170)
    rgb[16:19, 5:59] = (15, 15, 15)
    Image.fromarray(rgb, mode="RGB").save(path)
    return rgb


class ImageAnalysisTests(unittest.TestCase):
    def test_near_white_background_is_not_foreground(self) -> None:
        rgb = np.full((20, 30, 3), 254, dtype=np.uint8)
        rgb[8:12, 13:17] = 0
        background = analyze_target.estimate_background_rgb(
            rgb,
            border_width=4,
            quantization=16,
        )
        self.assertEqual(background, (254, 254, 254))
        mask = analyze_target.foreground_mask(rgb, background, 24)
        self.assertFalse(mask[0, 0])
        self.assertEqual(int(mask.sum()), 16)

    def test_analysis_cli_is_provenanced_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.png"
            output = root / "analysis"
            source_rgb = make_fixture(source)

            with contextlib.redirect_stdout(io.StringIO()):
                first_return = analyze_target.main(
                    [str(source), "--output", str(output)]
                )
            self.assertEqual(first_return, 0)

            expected_files = {
                "inventory.json",
                "qTL.png",
                "qTR.png",
                "qBL.png",
                "qBR.png",
            }
            self.assertEqual(
                {path.name for path in output.iterdir()},
                expected_files,
            )
            inventory = json.loads((output / "inventory.json").read_text("utf-8"))
            self.assertEqual(inventory["canvas"]["background_hex"], "#FEFEFE")
            self.assertEqual(inventory["source"]["sha256"], file_hash(source))
            self.assertEqual(
                inventory["algorithm"]["version"],
                analyze_target.ALGORITHM_VERSION,
            )
            self.assertEqual(
                inventory["algorithm"]["script_sha256"],
                file_hash(TOOLS_DIR / "analyze_target.py"),
            )
            self.assertIn("numpy", inventory["runtime"])
            self.assertIn("pillow", inventory["runtime"])
            self.assertIn("scipy", inventory["runtime"])
            self.assertEqual(
                inventory["algorithm"]["parameters"]["foreground_distance_l1"],
                24,
            )

            height, width = source_rgb.shape[:2]
            expected_crops = {
                "qTL.png": source_rgb[: height // 2, : width // 2],
                "qTR.png": source_rgb[: height // 2, width // 2 :],
                "qBL.png": source_rgb[height // 2 :, : width // 2],
                "qBR.png": source_rgb[height // 2 :, width // 2 :],
            }
            for name, expected in expected_crops.items():
                actual = np.asarray(Image.open(output / name).convert("RGB"))
                self.assertTrue(np.array_equal(actual, expected), name)

            first_hashes = {
                name: file_hash(output / name) for name in expected_files
            }
            with contextlib.redirect_stdout(io.StringIO()):
                second_return = analyze_target.main(
                    [str(source), "--output", str(output)]
                )
            self.assertEqual(second_return, 0)
            second_hashes = {
                name: file_hash(output / name) for name in expected_files
            }
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(
                [path.name for path in output.iterdir() if path.name.startswith(".")],
                [],
            )

    def test_segmentation_is_seeded_rerunnable_and_observation_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.png"
            output = root / "segments"
            make_fixture(source)
            arguments = [
                str(source),
                "--output",
                str(output),
                "--clusters",
                "4",
                "--seed",
                "12345",
            ]

            with contextlib.redirect_stdout(io.StringIO()):
                first_return = segment_panels.main(arguments)
            self.assertEqual(first_return, 0)
            expected_files = {
                "panels.json",
                "panels_overlay.png",
                "mask_light.png",
                "mask_saturated.png",
            }
            self.assertEqual(
                {path.name for path in output.iterdir()},
                expected_files,
            )

            payload = json.loads((output / "panels.json").read_text("utf-8"))
            self.assertEqual(payload["canvas"]["background_hex"], "#FEFEFE")
            self.assertLess(payload["coverage_pct"]["ink"], 25.0)
            self.assertEqual(payload["source"]["sha256"], file_hash(source))
            self.assertEqual(
                payload["algorithm"]["script_sha256"],
                file_hash(TOOLS_DIR / "segment_panels.py"),
            )
            self.assertIn("opencv", payload["runtime"])
            self.assertEqual(payload["algorithm"]["parameters"]["random_seed"], 12345)
            self.assertIn("region_candidates", payload)
            self.assertNotIn("panels", payload)
            self.assertEqual(payload["interpretation"]["status"], "observation_only")
            self.assertEqual(payload["interpretation"]["verified_panel_count"], 0)
            self.assertIn("not verified semantic panels", payload["interpretation"]["disclaimer"])

            first_hashes = {
                name: file_hash(output / name) for name in expected_files
            }
            with contextlib.redirect_stdout(io.StringIO()):
                second_return = segment_panels.main(arguments)
            self.assertEqual(second_return, 0)
            second_hashes = {
                name: file_hash(output / name) for name in expected_files
            }
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(
                [path.name for path in output.iterdir() if path.name.startswith(".")],
                [],
            )

    def test_analysis_clis_contain_no_project_specific_absolute_path(self) -> None:
        for script_name in ("analyze_target.py", "segment_panels.py"):
            source = (TOOLS_DIR / script_name).read_text(encoding="utf-8")
            self.assertNotIn("D:/AI+", source)
            self.assertNotIn("D:\\AI+", source)


if __name__ == "__main__":
    unittest.main()
