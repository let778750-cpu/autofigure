from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import figure_lint  # noqa: E402


def image_pair_with_changed_pixels(count: int) -> tuple[np.ndarray, np.ndarray]:
    target = np.zeros((10, 10, 3), dtype=np.uint8)
    final = target.copy()
    final.reshape(-1, 3)[:count] = (1, 1, 1)
    return target, final


class FigureLintStrictRatioTests(unittest.TestCase):
    def test_exactly_three_percent_changed_pixels_passes_strict_ratio_gate(self) -> None:
        target, final = image_pair_with_changed_pixels(3)
        result = figure_lint.lint(target, final, "strict", tile_size=5)

        self.assertEqual(result["changed_pixel_ratio_pct"], 3.0)
        self.assertLessEqual(result["mean_abs_rgb_delta"], 3.0)
        self.assertTrue(result["diagnostic_pass"])
        self.assertIn("changed_pixel_ratio<=3%", result["threshold"])

    def test_more_than_three_percent_changed_pixels_fails_strict_ratio_gate(self) -> None:
        target, final = image_pair_with_changed_pixels(4)
        result = figure_lint.lint(target, final, "strict", tile_size=5)

        self.assertEqual(result["changed_pixel_ratio_pct"], 4.0)
        self.assertFalse(result["diagnostic_pass"])

    def test_size_mismatch_still_fails_even_when_overlap_is_identical(self) -> None:
        target = np.zeros((10, 10, 3), dtype=np.uint8)
        final = np.zeros((11, 10, 3), dtype=np.uint8)
        result = figure_lint.lint(target, final, "strict", tile_size=5)

        self.assertTrue(result["size_mismatch"])
        self.assertFalse(result["diagnostic_pass"])
        self.assertTrue(any("共同区域" in note for note in result["notes"]))
        self.assertTrue(any("不得据此通过" in note for note in result["notes"]))

    def test_reference_preview_bbox_is_excluded_from_native_only_metrics(self) -> None:
        target = np.zeros((10, 10, 3), dtype=np.uint8)
        final = target.copy()
        final[:2, :, :] = 255

        unmasked = figure_lint.lint(target, final, "strict", tile_size=5)
        masked = figure_lint.lint(
            target,
            final,
            "strict",
            tile_size=5,
            excluded_bboxes=[{"x": 0, "y": 0, "w": 10, "h": 2}],
        )

        self.assertFalse(unmasked["diagnostic_pass"])
        self.assertTrue(masked["diagnostic_pass"])
        self.assertEqual(masked["changed_pixel_ratio_pct"], 0.0)
        self.assertEqual(masked["excluded_reference_preview_pct"], 20.0)
        self.assertEqual(masked["evaluated_pixel_count"], 80)
        self.assertIsNone(masked["ssim"])
        self.assertTrue(any("SSIM" in note for note in masked["notes"]))

    def test_exclusions_cannot_cover_the_whole_target(self) -> None:
        target, final = image_pair_with_changed_pixels(0)
        with self.assertRaisesRegex(ValueError, "entire target canvas"):
            figure_lint.lint(
                target,
                final,
                "strict",
                tile_size=5,
                excluded_bboxes=[{"x": 0, "y": 0, "w": 10, "h": 10}],
            )


if __name__ == "__main__":
    unittest.main()
