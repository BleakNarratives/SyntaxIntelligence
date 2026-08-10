"""
Tests for the autoclaw_validator wire-in to bus_validator.ValidatingBusProxy.

Run from `bleaknarratives/Syntax-Intelligence/`:
    CRASH_LOG_SKIP_LOAD_MARKER=1 python3 -m unittest test_bus_validator_autoclaw -v

Or with the rest of the suite:
    python3 -m unittest discover -s bleaknarratives/Syntax-Intelligence -p 'test_*.py'

Wire-in summary (see `bus_validator.py` docstring):
  * `autoclaw_ceiling=None` (default): no-op, zero overhead.
  * `autoclaw_ceiling=ResourceCeiling(mode="HALT", rss_block_bytes=...)`:
        preflight RSS check BEFORE validation+dispatch; BLOCK aborts
        publish cleanly. Postcall: time-budget + RSS observation only
        (cannot un-dispatch — log + stats).
  * `autoclaw_ceiling=...mode="TRACE"`: emit findings, never block.
  * `autoclaw_ceiling=...mode="OFF"`: skip entirely (same as None effectively).

Tests use a 1-byte RSS ceiling to deterministically trigger preflight
BLOCK without monkeypatching (real /proc/self/status reads).
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from typing import Any, Dict, List, Tuple
from unittest import mock

# Opt out of /tmp pollution BEFORE module imports
os.environ["CRASH_LOG_SKIP_LOAD_MARKER"] = "1"

from bus_validator import ValidatingBusProxy, is_swarm_envelope
from autoclaw_validator import ResourceCeiling, Severity


# ════════════════════════════════════════════════════════════════
# STUBS — minimal bus + Recorder (shared with test_bus_validator style)
# ════════════════════════════════════════════════════════════════

class _StubBus:
    """Minimum bus-like object that records published messages."""

    def __init__(self) -> None:
        self._log_calls: List[Dict[str, Any]] = []
        self._published: List[Tuple[str, str, Any]] = []
        self._broadcast: List[Tuple[str, str, Any]] = []

    def publish(self, sender_id, channel, data):
        self._published.append((sender_id, channel, data))

    def broadcast(self, sender_id, channel, data):
        self._broadcast.append((sender_id, channel, data))

    def subscribe(self, agent_id, channel, callback):
        pass

    def unsubscribe(self, agent_id, channel):
        pass

    def _log(self, msg_type, agent_id, channel, detail, **kw):
        self._log_calls.append({
            "type": msg_type,
            "agent_id": agent_id,
            "channel": channel,
            "detail": detail,
            **kw,
        })

    def published_count(self) -> int:
        return len(self._published)

    def broadcast_count(self) -> int:
        return len(self._broadcast)


def _make_envelope(
    *,
    message_type: str = "pulse",
    sender_id: str = "alice",
    payload: Dict[str, Any] = None,
    message_id: str = "m_autoclaw_test_001",
    ttl: float = 60.0,
    timestamp: float = None,
) -> Dict[str, Any]:
    """Build a SwarmMessage-shaped dict."""
    return {
        "sender_id": sender_id,
        "message_type": message_type,
        "channel": "swarm.heartbeat",
        "payload": payload or {"pulse": 0.0},
        "message_id": message_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "ttl": ttl,
    }


def _tiny_ceiling(mode: str = "HALT") -> ResourceCeiling:
    """Return a ResourceCeiling that ALWAYS fires preflight BLOCK.

    rss_block_bytes=1 → any process with measurable RSS exceeds it.
    Tests that need preflight to PASS instead use a much larger ceiling.
    """
    return ResourceCeiling(
        rss_block_bytes=1,
        rss_warn_pct=0.0001,
        swap_block_bytes=1,
        time_budget_seconds=10.0,  # large enough that postcall time check doesn't fire spuriously
        poll_interval_seconds=0.05,
        mode=mode,
        kill_on_breach=False,
    )


def _generous_ceiling(mode: str = "HALT") -> ResourceCeiling:
    """Return a ceiling set comfortably above any real-test-process RSS.

    Used when the test wants preflight to PASS and exercise the
    post-call observation path (which checks time-budget + RSS-warn).
    1 GiB ceiling is well above Python's ~25 MiB test baseline.
    """
    large = 1_073_741_824  # 1 GiB
    return ResourceCeiling(
        rss_block_bytes=large,
        rss_warn_pct=0.99,
        swap_block_bytes=large,
        time_budget_seconds=10.0,
        poll_interval_seconds=0.05,
        mode=mode,
        kill_on_breach=False,
    )


# ════════════════════════════════════════════════════════════════
# OPT-IN SEMANTICS
# ════════════════════════════════════════════════════════════════

class TestAutoclawOptIn(unittest.TestCase):
    """No ceiling → no autoclaw activity at all (zero overhead)."""

    def test_no_ceiling_no_autoclaw_observations(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus)  # no autoclaw_ceiling
        env = _make_envelope(message_id="m_opt_off_1")
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched normally
        self.assertEqual(bus.published_count(), 1)
        # Zero autoclaw activity
        s = proxy.get_stats()
        self.assertEqual(s["autoclaw_observations"], 0)
        self.assertEqual(s["autoclaw_preflight_findings"], 0)
        self.assertEqual(s["autoclaw_postcall_findings"], 0)
        self.assertEqual(s["autoclaw_blocks_preflight"], 0)
        self.assertEqual(s["autoclaw_blocks_postcall"], 0)
        self.assertEqual(s["autoclaw_findings_total"], 0)

    def test_no_ceiling_no_autoclaw_log_lines(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus)
        env = _make_envelope(message_id="m_opt_off_2")
        proxy.publish("alice", "swarm.heartbeat", env)
        # No autoclaw-related log entries
        autoclaw_logs = [e for e in bus._log_calls if e["type"] == "autoclaw_blocked"]
        self.assertEqual(len(autoclaw_logs), 0)

    def test_explicit_off_mode_no_autoclaw_observations(self):
        """autoclaw_ceiling with mode='OFF' behaves like None."""
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="OFF")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_opt_off_3")
        proxy.publish("alice", "swarm.heartbeat", env)
        self.assertEqual(bus.published_count(), 1)
        # OFF mode is the "skip" path — no observations counted
        s = proxy.get_stats()
        self.assertEqual(s["autoclaw_observations"], 0)

    def test_no_ceiling_non_envelope_also_unprotected(self):
        """Non-envelope path is unchanged when autoclaw=None."""
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus)
        proxy.publish("alice", "ch", {"foo": "bar"})
        self.assertEqual(bus.published_count(), 1)
        self.assertNotIn("resource_validation", bus._published[0][2])


# ════════════════════════════════════════════════════════════════
# HALT MODE — preflight BLOCK halts publish
# ════════════════════════════════════════════════════════════════

class TestAutoclawHaltPreflight(unittest.TestCase):

    def test_tiny_rss_block_halts_before_validation(self):
        """With rss_block_bytes=1, preflight detects RSS over ceiling and aborts."""
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="HALT")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_halt01", "title": "x", "description": "y",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_halt_1")

        # Spy on the underlying validate_swarm_message to confirm it does NOT run
        with mock.patch("handoff_validator.validate_swarm_message") as mock_validate:
            proxy.publish("alice", "task.offered", env)
            # validate_swarm_message was not called — preflight short-circuited
            mock_validate.assert_not_called()

        # Not dispatched
        self.assertEqual(bus.published_count(), 0)
        # Stats
        s = proxy.get_stats()
        self.assertEqual(s["autoclaw_observations"], 1)
        self.assertEqual(s["autoclaw_blocks_preflight"], 1)
        self.assertGreaterEqual(s["autoclaw_preflight_findings"], 1)
        # validation_log contains autoclaw entry
        vlog = proxy.get_validation_log()
        autoclaw_entries = [e for e in vlog if e.get("layer") == "autoclaw"]
        self.assertEqual(len(autoclaw_entries), 1)
        e = autoclaw_entries[0]
        self.assertEqual(e["phase"], "preflight")
        # bus._log fired
        autoclaw_logs = [e for e in bus._log_calls if e["type"] == "autoclaw_blocked"]
        self.assertEqual(len(autoclaw_logs), 1)


# ════════════════════════════════════════════════════════════════
# TRACE MODE — emit findings, never block
# ════════════════════════════════════════════════════════════════

class TestAutoclawTraceMode(unittest.TestCase):

    def test_trace_does_not_block_dispatch(self):
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="TRACE")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_trace_1")
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched despite preflight findings (TRACE observes only)
        self.assertEqual(bus.published_count(), 1)
        s = proxy.get_stats()
        # Findings emitted, but no block
        self.assertGreaterEqual(s["autoclaw_preflight_findings"], 1)
        self.assertEqual(s["autoclaw_blocks_preflight"], 0)
        # Zero autoclaw_blocked log entries (we did NOT block)
        autoclaw_logs = [e for e in bus._log_calls if e["type"] == "autoclaw_blocked"]
        self.assertEqual(len(autoclaw_logs), 0)


# ════════════════════════════════════════════════════════════════
# POST-CALL OBSERVATION
# ════════════════════════════════════════════════════════════════

class TestAutoclawPostcallObservation(unittest.TestCase):
    """Generous ceiling → preflight passes → postcall observation runs."""

    def test_postcall_observations_counted(self):
        bus = _StubBus()
        ceiling = _generous_ceiling(mode="TRACE")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_post_1")
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched normally
        self.assertEqual(bus.published_count(), 1)
        s = proxy.get_stats()
        # observation ran (preflight + postcall both observations)
        self.assertEqual(s["autoclaw_observations"], 1)
        # postcall count is >= 0 (depends on whether RSS approaches warn
        # threshold; time check passes since wallclock is well under 10s)
        self.assertGreaterEqual(s["autoclaw_postcall_findings"], 0)
        # Total = preflight + postcall
        self.assertEqual(
            s["autoclaw_findings_total"],
            s["autoclaw_preflight_findings"] + s["autoclaw_postcall_findings"],
        )

    def test_postcall_time_breach_detected_via_synthesized_snap(self):
        """Force wallclock > time_budget via minor delay in publish path."""
        bus = _StubBus()
        ceiling = _generous_ceiling(mode="TRACE")
        # Tighten time budget to fire on any non-trivial path
        ceiling = ResourceCeiling(
            rss_block_bytes=ceiling.rss_block_bytes,
            rss_warn_pct=ceiling.rss_warn_pct,
            swap_block_bytes=ceiling.swap_block_bytes,
            time_budget_seconds=1e-9,
            poll_interval_seconds=0.01,
            mode="TRACE",
        )
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_post_time_1")
        # Tiny sleep before publish so wallclock ≥ 0.001s
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched (TRACE observes)
        self.assertEqual(bus.published_count(), 1)
        s = proxy.get_stats()
        # postcall fired time-budget finding
        self.assertGreaterEqual(s["autoclaw_postcall_findings"], 1)

    def test_postcall_block_after_dispatch_logs_but_keeps_delivered(self):
        """HALT-mode postcall block: already dispatched, observation only.

        Cannot un-dispatch — bus._log fired for visibility but the
        envelope already reached the underlying bus.
        """
        bus = _StubBus()
        ceiling = _generous_ceiling(mode="HALT")
        ceiling = ResourceCeiling(
            rss_block_bytes=ceiling.rss_block_bytes,
            swap_block_bytes=ceiling.swap_block_bytes,
            time_budget_seconds=1e-9,
            poll_interval_seconds=0.01,
            mode="HALT",
            kill_on_breach=False,
        )
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_post_halt_1")
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched (postcall cannot un-dispatch)
        self.assertEqual(bus.published_count(), 1)
        s = proxy.get_stats()
        # autoclaw_blocks_postcall incremented (HALT-mode post-call finding)
        self.assertGreaterEqual(s["autoclaw_blocks_postcall"], 1)
        # envelope was already dispatched — no validation_blocked log
        # for autoclaw (we don't un-dispatch)
        autoclaw_logs = [e for e in bus._log_calls if e["type"] == "autoclaw_blocked"]
        self.assertGreaterEqual(len(autoclaw_logs), 1)


# ════════════════════════════════════════════════════════════════
# NON-ENVELOPE PATH ALSO FENCED
# ════════════════════════════════════════════════════════════════

class TestAutoclawNonEnvelopePath(unittest.TestCase):

    def test_preflight_blocks_non_envelope_publish(self):
        """Non-envelope path is also fenced (not just envelope path)."""
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="HALT")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        # hardened_engine publishes a task_offer inner dict (not envelope)
        data = {"task_id": "task_id_x", "title": "build", "target": "agent_01"}
        proxy.publish("swarm", "task.offered", data)
        # BLOCKED by autoclaw preflight
        self.assertEqual(bus.published_count(), 0)
        s = proxy.get_stats()
        self.assertEqual(s["autoclaw_blocks_preflight"], 1)
        # non_envelope_passed_through NOT incremented (block happened first)
        self.assertEqual(s["non_envelope_passed_through"], 0)

    def test_broadcast_path_preflight_blocks(self):
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="HALT")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_bcast_ac_1")
        proxy.broadcast("alice", "swarm.bcast", env)
        self.assertEqual(bus.broadcast_count(), 0)


# ════════════════════════════════════════════════════════════════
# STRUCTURAL-FIRST ORDERING PRESERVED
# ════════════════════════════════════════════════════════════════

class TestAutoclawOrdering(unittest.TestCase):
    """Both gates fire on envelope path; autoclaw preflight runs BEFORE
    structural validation so RSS-blocked envelopes don't burn CPU on
    pydantic-style validation."""

    def test_preflight_block_short_circuits_before_structural(self):
        bus = _StubBus()
        ceiling = _tiny_ceiling(mode="HALT")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)

        # Spy on the structural validator path
        with mock.patch("handoff_validator.validate_swarm_message") as mock_validate:
            env = _make_envelope(message_type="task_offer", payload={
                "task_id": "task_ord01", "title": "x", "description": "y",
                "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
            }, message_id="m_ord_1")
            proxy.publish("alice", "task.offered", env)
            mock_validate.assert_not_called()

    def test_generous_ceiling_lets_structural_run_normally(self):
        """Generous ceiling + bad envelope message_type → existing
        structural gate still catches errors as before."""
        bus = _StubBus()
        ceiling = _generous_ceiling(mode="HALT")
        proxy = ValidatingBusProxy(bus, autoclaw_ceiling=ceiling)
        env = _make_envelope(message_id="m_ord_2")
        env["message_type"] = "not-a-real-type"  # structural ENUM_OUT_OF_RANGE
        proxy.publish("alice", "swarm.heartbeat", env)
        # Blocked by STRUCTURAL (autoclaw generous — passes)
        self.assertEqual(bus.published_count(), 0)
        s = proxy.get_stats()
        # Both layers recorded the event
        self.assertEqual(s["envelopes_blocked"], 1)
        self.assertEqual(s["autoclaw_observations"], 1)
        # Pre-flight findings low (RSS well below generous ceiling)
        # Post-call findings low (wallclock well below 10s)
        # → no autoclaw_blocks
        self.assertEqual(s["autoclaw_blocks_preflight"], 0)
        self.assertEqual(s["autoclaw_blocks_postcall"], 0)


# ════════════════════════════════════════════════════════════════
# SANITY — stats additions DON'T break existing keys
# ════════════════════════════════════════════════════════════════

class TestAutoclawNonBreakingStats(unittest.TestCase):
    """Existing stats keys remain present (backwards compatibility)."""

    def test_all_existing_stats_keys_present_with_ceiling(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(
            bus, autoclaw_ceiling=_generous_ceiling(mode="TRACE")
        )
        s = proxy.get_stats()
        for key in (
            "publish_calls", "broadcast_calls", "envelopes_validated",
            "envelopes_passed", "envelopes_blocked", "gut_advisories",
            "non_envelope_passed_through",
            "envelopes_with_semantic_check", "semantic_findings_count",
            "semantic_omissions_blocked",
        ):
            self.assertIn(key, s, f"missing existing stats key: {key}")
        # New autoclaw keys also present
        for key in (
            "autoclaw_observations", "autoclaw_preflight_findings",
            "autoclaw_postcall_findings", "autoclaw_blocks_preflight",
            "autoclaw_blocks_postcall", "autoclaw_findings_total",
        ):
            self.assertIn(key, s, f"missing new autoclaw stats key: {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
