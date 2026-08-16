from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "examples" / "target_figure.fixture.json"
APPROVED_TARGET_SHA256 = "239e74f150e1ba224d578183eef8d7194556607bc0375a9cf1a4828ce7c6ce04"


class TargetFigureFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.reference = FIXTURE_PATH.parent / cls.fixture["referenceFile"]

    def test_reference_hash_and_dimensions_are_locked(self) -> None:
        digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        self.assertEqual(digest, APPROVED_TARGET_SHA256)
        self.assertEqual(self.fixture["sha256"], APPROVED_TARGET_SHA256)
        self.assertEqual(digest, self.fixture["sha256"])

        with Image.open(self.reference) as image:
            self.assertEqual(image.size, (1536, 1024))
            self.assertEqual(image.size, (
                self.fixture["image"]["widthPx"],
                self.fixture["image"]["heightPx"],
            ))

    def test_expected_region_contract_is_complete(self) -> None:
        regions = self.fixture["expectedRegions"]
        self.assertEqual(len(regions), 9)
        self.assertEqual(len(regions), len(set(regions)))
        self.assertTrue(set(self.fixture["expectedPrimaryFlow"]).issubset(regions))

    def test_ocr_smoke_contract_preserves_uncertainty(self) -> None:
        smoke = self.fixture["ocrSmokeExpectations"]
        self.assertGreaterEqual(smoke["minimumDetectedTextBoxes"], 100)
        self.assertGreaterEqual(len(smoke["requiredExactAnchors"]), 6)
        self.assertGreaterEqual(len(smoke["mustRemainFormulaCandidates"]), 1)
        self.assertIn("authoritative_latex_for_all_formulas", self.fixture["requiredUnknowns"])
        self.assertFalse(self.fixture["expectedPolicy"]["mayGuessFormulaFromPixels"])

    def test_reference_is_measurement_only(self) -> None:
        policy = self.fixture["referencePolicy"]
        self.assertEqual(policy["referenceUsage"], "measurement_only")
        self.assertFalse(policy["referenceEmbedded"])
        self.assertFalse(self.fixture["expectedPolicy"]["mayUseReferenceCrop"])

    def test_agent_vision_and_fusion_contracts_preserve_authority(self) -> None:
        self.assertEqual(self.fixture["schemaVersion"], "1.2.0")
        vision = self.fixture["agentVisionExpectations"]
        self.assertEqual(vision["structureQueryCount"], 1)
        low, high = vision["panelProposalRange"]
        self.assertLessEqual(low, high)
        self.assertGreaterEqual(vision["formulaQueryMinimum"], 1)
        self.assertGreaterEqual(
            vision["formulaQueryMaximum"], vision["formulaQueryMinimum"]
        )
        self.assertEqual(vision["formulaSamplesRequired"], 3)

        fusion = self.fixture["fusionExpectations"]
        self.assertEqual(set(fusion["exactAnchorsAllowedTiers"]), {"TRIPLE", "PAIR"})
        self.assertTrue(fusion["formulaCandidatesMustRemainProposals"])
        self.assertEqual(
            fusion["proposalStatus"], "PROPOSAL_ONLY_NOT_AUTHORITATIVE"
        )
        self.assertTrue(fusion["tripleDoesNotWaiveHumanReview"])
        self.assertTrue(fusion["everyOcrCandidateHasFact"])
        self.assertEqual(
            set(fusion["reviewQueueBands"]),
            {"FOCUS_CONFLICT", "FOCUS_UNSUPPORTED", "FOCUS_SINGLE", "ROUTINE", "LOW"},
        )


if __name__ == "__main__":
    unittest.main()
