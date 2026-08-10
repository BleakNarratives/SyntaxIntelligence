"""
Test suite for autoclaw_validator.py — Spec §2 hard/testable layer.

Run from `bleaknarratives/Syntax-Intelligence/`:
    python3 -m unittest test_autoclaw_validator -v

Or as part of the suite:
    python3 -m unittest discover -s bleaknarratives/Syntax-Intelligence -p 'test_*.py'
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from autoclaw_validator import (
    EASTER_BLOOM,
    ResourceCeiling,
    ResourceCeilingExceeded,
    ResourceFinding,
    ResourceSnapshot,
    SandboxViolation,
    Severity,
    SAYING,
    _try_emit_to_crash_log,
    assert_within_sandbox,
    autoclaw_protect,
    take_snapshot,
)


def _make_test_ceiling(**overrides) -> ResourceCeiling:
    """Return a tiny ceiling that's safe for tests (never OOMs the test process).

    RSS ceiling is 50 MB which is above the Python interpreter's
    baseline (~15-25 MB) so a real test process won't trip the
    RSS_BLOCK check before the budget check the test is exercising.
    50 MB is still small enough that an OOM-prone test run would
    surface quickly.
    """
    base = dict(
        rss_block_bytes=500_000_000,      # 500 MB (safe margin over test-process RSS)
        rss_warn_pct=0.5,
        swap_block_bytes=10_000_000,      # 10 MB
        time_budget_seconds=0.5,          # half a second
        token_budget=10,
        sandbox_root=Path(tempfile.gettempdir()).resolve(),
        mode="HALT",
        poll_interval_seconds=0.05,
        signal_grace_seconds=0.05,
        kill_on_breach=False,
    )
    base.update(overrides)
    return ResourceCeiling(**base)


# ════════════════════════════════════════════════════════════════
# DATACLASS VALIDATION
# ════════════════════════════════════════════════════════════════

class TestResourceCeilingValidation(unittest.TestCase):
    def test_defaults_construction(self):
        c = ResourceCeiling()
        self.assertTrue(c.sandbox_root.is_absolute())
        self.assertEqual(c.mode, "HALT")
        self.assertGreater(c.rss_block_bytes, 0)
        self.assertGreater(c.time_budget_seconds, 0)

    def test_relative_sandbox_root_rejected(self):
        with self.assertRaises(ValueError):
            ResourceCeiling(sandbox_root=Path("relative/path"))

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            ResourceCeiling(mode="INVALID_MODE")

    def test_zero_poll_interval_rejected(self):
        with self.assertRaises(ValueError):
            _make_test_ceiling(poll_interval_seconds=0.0)

    def test_zero_rss_block_rejected(self):
        with self.assertRaises(ValueError):
            _make_test_ceiling(rss_block_bytes=0)

    def test_rss_warn_bytes_property(self):
        c = _make_test_ceiling(rss_block_bytes=1000, rss_warn_pct=0.8)
        self.assertEqual(c.rss_warn_bytes, 800)

    def test_ceiling_to_dict_round_trip_includes_warn(self):
        c = _make_test_ceiling()
        d = c.__dict__ if hasattr(c, "__dict__") else None
        # __dict__ is restricted by frozen; use the internal helper
        from autoclaw_validator import _ceiling_to_dict
        out = _ceiling_to_dict(c)
        self.assertIn("rss_block_bytes", out)
        self.assertIn("rss_warn_bytes", out)
        self.assertIn("sandbox_root", out)


# ════════════════════════════════════════════════════════════════
# SNAPSHOT
# ════════════════════════════════════════════════════════════════

class TestTakeSnapshot(unittest.TestCase):
    def test_snapshot_returns_dataclass(self):
        s = take_snapshot()
        self.assertIsInstance(s, ResourceSnapshot)
        self.assertEqual(s.pid, os.getpid())
        self.assertIsInstance(s.timestamp, float)
        self.assertIsInstance(s.rss_bytes, int)
        self.assertIsInstance(s.swap_bytes, int)
        self.assertIsInstance(s.threads, int)

    def test_snapshot_tokens_and_wallclock_propagate(self):
        s = take_snapshot(tokens_used=42, wallclock=2.5)
        self.assertEqual(s.tokens_used, 42)
        self.assertEqual(s.wallclock_seconds, 2.5)

    def test_snapshot_to_row_shape(self):
        s = take_snapshot(tokens_used=7, wallclock=1.0)
        row = s.to_row()
        expected_keys = {"pid", "timestamp", "rss_bytes", "swap_bytes",
                         "vm_peak_bytes", "threads", "wallclock_seconds",
                         "tokens_used"}
        self.assertTrue(expected_keys.issubset(row.keys()))


# ════════════════════════════════════════════════════════════════
# SANDBOX ENFORCEMENT
# ════════════════════════════════════════════════════════════════

class TestSandboxPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aclaw_test_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

    def test_path_inside_root_passes(self):
        p = self.tmp / "inside.txt"
        out = assert_within_sandbox(p, self.tmp)
        self.assertEqual(out, p.resolve())

    def test_path_outside_root_raises(self):
        outside = Path(tempfile.gettempdir()).parent / "definitely_outside.txt"
        with self.assertRaises(SandboxViolation):
            assert_within_sandbox(outside, self.tmp)

    def test_relative_path_resolved_against_cwd(self):
        # Caller passes a relative path — resolves against cwd; if cwd
        # is inside sandbox (we set it via chdir), it passes.
        old = os.getcwd()
        try:
            os.chdir(str(self.tmp))
            out = assert_within_sandbox("relative.txt", self.tmp)
            self.assertTrue(str(out).startswith(str(self.tmp.resolve())))
        finally:
            os.chdir(old)

    def test_traversal_attack_blocked(self):
        # "../escaped" from inside self.tmp tries to leave.
        traversal = self.tmp / ".." / "escaped.txt"
        # Resolve the traversal — if it lands outside, we block it.
        with self.assertRaises(SandboxViolation):
            assert_within_sandbox(traversal, self.tmp)


# ════════════════════════════════════════════════════════════════
# DECORATOR — HALT mode raises; TRACE mode logs; OFF mode passes
# ════════════════════════════════════════════════════════════════

class TestAutoclawProtectModes(unittest.TestCase):

    def test_off_mode_passes_through_unchanged(self):
        ceiling = _make_test_ceiling(mode="OFF")

        @autoclaw_protect(ceiling)
        def add(a, b):
            return a + b

        # Even if we'd breach every other gate, OFF is opt-out cheap.
        self.assertEqual(add(2, 3), 5)

    def test_halt_mode_raises_on_time_budget_breach(self):
        ceiling = _make_test_ceiling(
            mode="HALT", time_budget_seconds=0.05,
            poll_interval_seconds=0.01,
            kill_on_breach=False,  # default = graceful raise, not signal-kill
        )

        @autoclaw_protect(ceiling)
        def slow():
            time.sleep(0.4)  # > budget

        with self.assertRaises(ResourceCeilingExceeded) as ctx:
            slow()
        # Findings should include a time-budget finding.
        codes = [f.code for f in ctx.exception.findings]
        self.assertIn("TIME_BUDGET_EXCEEDED", codes)

    def test_no_signal_without_kill_on_breach(self):
        """Documents that graceful path does NOT send SIGTERM.

        If kill_on_breach were default-on, this test would DIE the
        process via SIGTERM. Verify it stays alive through a breach.
        """
        import signal as _sig
        ceiling = _make_test_ceiling(
            mode="HALT",
            time_budget_seconds=0.05,
            poll_interval_seconds=0.01,
            kill_on_breach=False,
        )

        @autoclaw_protect(ceiling)
        def slow():
            time.sleep(0.3)
            return "completed"

        try:
            with self.assertRaises(ResourceCeilingExceeded):
                slow()
        except _sig.SIGTERM:  # pragma: no cover
            self.fail("kill_on_breach=False should NOT trigger SIGTERM")
        # Process is still here — assertion above is the real proof.

    def test_trace_mode_does_not_raise_on_breach(self):
        ceiling = _make_test_ceiling(
            mode="TRACE", time_budget_seconds=0.05,
            poll_interval_seconds=0.01,
        )

        @autoclaw_protect(ceiling)
        def slow_trace():
            time.sleep(0.3)

        # Should NOT raise in TRACE mode.
        slow_trace()

    def test_findings_contain_snapshot_per_finding(self):
        ceiling = _make_test_ceiling(
            mode="HALT", time_budget_seconds=0.05,
            poll_interval_seconds=0.01,
            kill_on_breach=False,  # graceful path so fn completes
        )

        @autoclaw_protect(ceiling)
        def slow():
            time.sleep(0.3)

        with self.assertRaises(ResourceCeilingExceeded) as ctx:
            slow()
        for f in ctx.exception.findings:
            self.assertIsInstance(f, ResourceFinding)
            self.assertIsInstance(f.snapshot, ResourceSnapshot)

    def test_kill_on_breach_default_false(self):
        c = ResourceCeiling()
        self.assertFalse(c.kill_on_breach)

    def test_kill_on_breach_settable(self):
        c = _make_test_ceiling(kill_on_breach=True)
        self.assertTrue(c.kill_on_breach)


# ════════════════════════════════════════════════════════════════
# DECORATOR — SANDBOX ASSERTED AT DECORATION TIME
# ════════════════════════════════════════════════════════════════

class TestSandboxPathsAtDecoration(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aclaw_decoration_test_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

    def test_decoration_with_sandbox_paths_inside_root_passes(self):
        ceiling = _make_test_ceiling(sandbox_root=self.tmp)
        target = str(self.tmp / "subdir/file.txt")  # absolute path inside root

        @autoclaw_protect(ceiling, sandbox_paths=[target])
        def write():
            return "ok"

        self.assertEqual(write(), "ok")

    def test_decoration_with_sandbox_paths_outside_root_fails(self):
        ceiling = _make_test_ceiling(sandbox_root=Path("/tmp"))
        with self.assertRaises(SandboxViolation):
            @autoclaw_protect(ceiling, sandbox_paths=["/etc/passwd"])
            def write():
                return "blocked"


# ════════════════════════════════════════════════════════════════
# TOKEN BUDGET
# ════════════════════════════════════════════════════════════════

class TestTokenBudget(unittest.TestCase):
    def test_token_budget_breach_raises_in_halt(self):
        ceiling = _make_test_ceiling(
            mode="HALT",
            token_budget=5,
            poll_interval_seconds=0.01,
            kill_on_breach=False,
        )
        counter = {"n": 100}

        @autoclaw_protect(ceiling, tokens_supplier=lambda: counter["n"])
        def work():
            # Counter is already over the budget; the preflight
            # should raise before we even enter the function body.
            pass

        with self.assertRaises(ResourceCeilingExceeded) as ctx:
            work()
        codes = [f.code for f in ctx.exception.findings]
        self.assertIn("TOKEN_BUDGET_EXCEEDED", codes)


# ════════════════════════════════════════════════════════════════
# WIRE-IN: crash_log integration is best-effort (no hard requirement)
# ════════════════════════════════════════════════════════════════

class TestCrashLogBestEffort(unittest.TestCase):
    def test_emit_to_crash_log_returns_bool(self):
        # Without sibling crash_log.py importable we get False;
        # if it IS importable we get True. Either is acceptable —
        # the call is best-effort either way.
        result = _try_emit_to_crash_log("test.event", {"k": 1})
        self.assertIsInstance(result, bool)


# ════════════════════════════════════════════════════════════════
# SMOKE / SAINT SIGILS
# ════════════════════════════════════════════════════════════════

class TestEasterConstants(unittest.TestCase):
    def test_easter_bloom_set(self):
        self.assertEqual(EASTER_BLOOM, "🪨")
        self.assertIn("Hard ceilings", SAYING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
