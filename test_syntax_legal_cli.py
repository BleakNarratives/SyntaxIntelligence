#!/usr/bin/env python3
"""Tests for the Syntax Legal Mind terminal presentation layer."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from SyntaxIntelligence import syntax_legal_cli as cli


_COMPLETED = {
    "status": "completed",
    "task_id": "task-1",
    "digest": {
        "audit_id": "audit-1",
        "severity_counts": {"HIGH": 1, "LOW": 2},
        "safe_to_draft": False,
        "high_count": 1,
        "high_findings": [
            {"rule": "EXISTENCE", "citation_fp": "abc123", "excerpt": "REDACTED"}
        ],
    },
}
_OFFERED = {"status": "offered", "task_id": "task-1"}


class TestSyntaxLegalCli(unittest.TestCase):
    def test_raw_digest_is_stable_and_unstyled(self) -> None:
        stream = io.StringIO()
        exit_code = cli._print_digest(
            _OFFERED, _COMPLETED, stream=stream, styled=False,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Legal Mind convened → adapter status: completed", output)
        self.assertIn('"HIGH": 1', output)
        self.assertNotIn("\033[", output)

    def test_tui_digest_has_cinematic_markers_and_red_accent(self) -> None:
        stream = io.StringIO()
        exit_code = cli._print_digest(
            _OFFERED, _COMPLETED, stream=stream, styled=True,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("SYNTAX // LEGAL MIND", output)
        self.assertIn("CITATION INTEGRITY DOSSIER", output)
        self.assertIn("HIGH-SEVERITY REVIEW", output)
        self.assertIn("DO NOT DRAFT", output)
        self.assertIn("\033[31m", output)

    def test_tui_failure_is_nonzero_and_explains_missing_result(self) -> None:
        stream = io.StringIO()
        exit_code = cli._print_digest(
            _OFFERED, None, stream=stream, styled=True,
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("no result", stream.getvalue())

    def test_supports_color_respects_no_color(self) -> None:
        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        stream = FakeTTY()
        with patch.dict("os.environ", {"TERM": "xterm", "NO_COLOR": "1"}, clear=False):
            self.assertFalse(cli._supports_color(stream))

    def test_completed_without_digest_is_incomplete(self) -> None:
        stream = io.StringIO()
        incomplete = {"status": "completed", "task_id": "task-2", "digest": None}
        exit_code = cli._print_digest(
            _OFFERED, incomplete, stream=stream, styled=True,
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("AUDIT INCOMPLETE", stream.getvalue())

    def test_main_uses_raw_path_with_mocked_swarm(self) -> None:
        swarm = Mock()
        swarm.convene_legal.return_value = {
            "offered": _OFFERED,
            "adapter_result": _COMPLETED,
        }
        output = io.StringIO()
        with patch("SyntaxIntelligence.syntax_core.SyntaxSwarm", return_value=swarm):
            with contextlib.redirect_stdout(output):
                exit_code = cli.main(["--raw", "42 U.S.C. § 1983"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Legal Mind convened → adapter status: completed", output.getvalue())
        swarm.convene_legal.assert_called_once_with("42 U.S.C. § 1983", use_llm=False)


if __name__ == "__main__":
    unittest.main()
