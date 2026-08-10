#!/usr/bin/env python3
"""Focused tests for SyntaxIntelligence.boardroom_dispatcher."""

from __future__ import annotations

import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SyntaxIntelligence.boardroom_dispatcher import BoardroomDispatcher
from SyntaxIntelligence.hardened_engine import HardenedSwarm


class TestBoardroomDispatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.swarm = HardenedSwarm()
        self.boardroom = BoardroomDispatcher(self.swarm)
        self.boardroom.register("boardroom_test")

    def test_high_risk_review_is_human_gated(self) -> None:
        result = self.boardroom.execute("boardroom_test", "review_001", {
            "decision": "Which checkout fix should ship first?",
            "options": ["Tokenized guest checkout", "Rewrite the payment service"],
            "findings": [{
                "title": "Checkout timeout spike",
                "severity": "high",
                "category": "api_health",
                "impact": "Lost conversions",
                "confidence": 0.8,
            }],
        })
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [item["role"] for item in result.findings],
            ["chairman", "devils_advocate", "brown_hat"],
        )
        self.assertEqual(result.metadata["risk_level"], "high")
        self.assertEqual(result.metadata["recommendation"], "hold_for_review")
        self.assertFalse(result.metadata["execution_allowed"])
        self.assertTrue(result.metadata["action_item"])

    def test_bottleneck_input_can_drive_reversible_pilot(self) -> None:
        result = self.boardroom.execute("boardroom_test", "review_002", {
            "bottlenecks": [{
                "area": "documentation",
                "severity": "medium",
                "titles": ["Missing install path"],
            }],
        })
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metadata["risk_level"], "moderate")
        self.assertEqual(result.metadata["recommendation"], "pilot_reversibly")

    def test_empty_input_fails_without_action(self) -> None:
        result = self.boardroom.execute("boardroom_test", "review_003", {})
        self.assertEqual(result.status, "failed")
        self.assertIn("needs a decision", result.summary)
        self.assertNotIn("action_item", result.metadata)

    def test_untrusted_text_is_bounded_and_nonfinite_confidence_ignored(self) -> None:
        result = self.boardroom.execute("boardroom_test", "review_004", {
            "decision": "D" * 1000,
            "findings": [{
                "title": "T" * 1000,
                "severity": "medium",
                "category": "C" * 500,
                "impact": "I" * 1000,
                "confidence": float("nan"),
            }],
        })
        self.assertEqual(result.status, "completed")
        self.assertLessEqual(len(result.summary), 1000)
        self.assertEqual(result.confidence, 0.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
