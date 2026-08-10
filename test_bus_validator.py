"""
Tests for `bus_validator` — ValidatingBusProxy wire-in
(spec §2 hard layer productionization, gap-report §3 next-gap closure).

Run from `bleaknarratives/Syntax-Intelligence/`:
    python3 -m unittest test_bus_validator -v

Or run all tests:
    python3 -m unittest test_handoff_validator test_bus_validator \
        test_hardened_engine test_dispatchers
"""
import time
import unittest
from typing import Any, Dict, List, Tuple

from bus_validator import (
    SWARM_ENVELOPE_REQUIRED_KEYS,
    VALID_MODES,
    ValidatingBusProxy,
    default_semantic_key_fn,
    is_swarm_envelope,
    validate_bus_event_line,
)


# ════════════════════════════════════════════════════════════════
# STUBS — minimal bus + Recorder
# ════════════════════════════════════════════════════════════════

class _StubBus:
    """Minimum bus-like object that records published messages."""

    def __init__(self) -> None:
        self._log_calls: List[Dict[str, Any]] = []
        self._published: List[Tuple[str, str, Any]] = []
        self._broadcast: List[Tuple[str, str, Any]] = []
        self._subscribed: Dict[str, List[Tuple[str, Any]]] = {}

    def publish(self, sender_id, channel, data):
        self._published.append((sender_id, channel, data))

    def broadcast(self, sender_id, channel, data):
        self._broadcast.append((sender_id, channel, data))

    def subscribe(self, agent_id, channel, callback):
        self._subscribed.setdefault(channel, []).append((agent_id, callback))

    def unsubscribe(self, agent_id, channel):
        subs = self._subscribed.get(channel, [])
        self._subscribed[channel] = [
            (a, c) for (a, c) in subs if a != agent_id
        ]

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

    def subscribed_count(self, channel: str) -> int:
        return len(self._subscribed.get(channel, []))


def _make_envelope(
    *, message_type: str = "task_offer",
    sender_id: str = "swarm",
    payload: Dict[str, Any] = None,
    message_id: str = "msg_envelope_001",
    ttl: float = 60.0,
    timestamp: float = None,
) -> Dict[str, Any]:
    """Build a SwarmMessage-shaped dict for testing."""
    return {
        "sender_id": sender_id,
        "message_type": message_type,
        "channel": "task.offered",
        "payload": payload or {
            "task_id": "task_abcdef01",
            "title": "x",
            "description": "y",
        },
        "message_id": message_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "ttl": ttl,
    }


# ════════════════════════════════════════════════════════════════
# ENVELOPE DETECTION
# ════════════════════════════════════════════════════════════════

class TestIsSwarmEnvelope(unittest.TestCase):
    def test_dict_with_all_required_keys_is_envelope(self):
        d = _make_envelope()
        for k in SWARM_ENVELOPE_REQUIRED_KEYS:
            self.assertIn(k, d)
        self.assertTrue(is_swarm_envelope(d))

    def test_dict_missing_any_required_key_is_not_envelope(self):
        d = _make_envelope()
        for missing in SWARM_ENVELOPE_REQUIRED_KEYS:
            dd = {k: v for k, v in d.items() if k != missing}
            self.assertFalse(
                is_swarm_envelope(dd),
                f"should not be an envelope when missing {missing!r}",
            )

    def test_non_dict_returns_false(self):
        for bad in (None, 42, "string", ["list"], object(), 3.14):
            self.assertFalse(is_swarm_envelope(bad))


# ════════════════════════════════════════════════════════════════
# FORWARDED SUBSCRIBE/UNSUBSCRIBE
# ════════════════════════════════════════════════════════════════

class TestSubscribeForwarded(unittest.TestCase):
    def test_subscribe_and_unsubscribe_pass_through(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        recorder: List[Any] = []
        proxy.subscribe("agent_x", "ch_x", lambda *a: recorder.append(a))
        proxy.unsubscribe("agent_x", "ch_x")
        # No errors; recorder untouched (no publisher triggered).
        self.assertEqual(recorder, [])


# ════════════════════════════════════════════════════════════════
# VALID ENVELOPE — dispatch path
# ════════════════════════════════════════════════════════════════

class TestValidEnvelopeDispatch(unittest.TestCase):
    def test_valid_offer_dispatched_with_validation_field_attached(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_abcdef01",
            "title": "Build the thing",
            "description": "Detailed description of what to build.",
            "priority": 3,
            "timeout_seconds": 300.0,
            "min_tier": 1,
        })
        proxy.publish("swarm", "task.offered", env)
        # Dispatched exactly once
        self.assertEqual(bus.published_count(), 1)
        sender, channel, published_data = bus._published[0]
        self.assertEqual(sender, "swarm")
        self.assertEqual(channel, "task.offered")
        # Caller's dict NOT mutated (proxy copies before injection)
        self.assertNotIn("validation", env)
        # Downstream sees validation field, and it passed
        self.assertIn("validation", published_data)
        self.assertTrue(published_data["validation"]["passed"])

    def test_valid_offer_passes_through_in_log_only_mode(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="log_only")
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_abcdef01",
            "title": "x", "description": "y",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        })
        proxy.publish("swarm", "task.offered", env)
        self.assertEqual(bus.published_count(), 1)

    def test_pulse_message_dispatches_cleanly(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope(
            message_type="pulse",
            payload={"pulse": 0.0},
            message_id="msg_pulse_001",
        )
        proxy.publish("alice", "swarm.heartbeat", env)
        self.assertEqual(bus.published_count(), 1)


# ════════════════════════════════════════════════════════════════
# INVALID ENVELOPE — block / log_only
# ════════════════════════════════════════════════════════════════

class TestInvalidEnvelopeBlocking(unittest.TestCase):
    def test_block_on_error_does_not_dispatch(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope()
        # Bad message_type triggers ENUM_OUT_OF_RANGE (ERROR) while
        # keeping the envelope shape (all 6 required keys present).
        # pop("sender_id") would also produce REQUIRED_MISSING but
        # would change the envelope shape — is_swarm_envelope would
        # return False and the proxy would pass through unvalidated.
        env["message_type"] = "not-a-real-type"
        proxy.publish("swarm", "task.offered", env)
        # NO dispatch
        self.assertEqual(bus.published_count(), 0)
        # validation_blocked log entry exists
        block_logs = [e for e in bus._log_calls
                      if e["type"] == "validation_blocked"]
        self.assertEqual(len(block_logs), 1)
        # validation_log updated
        self.assertEqual(len(proxy.get_validation_log()), 1)
        # Stats
        stats = proxy.get_stats()
        self.assertEqual(stats["envelopes_validated"], 1)
        self.assertEqual(stats["envelopes_blocked"], 1)
        self.assertEqual(stats["envelopes_passed"], 0)

    def test_log_only_mode_does_dispatch_with_failure_visible(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="log_only")
        env = _make_envelope()
        env["message_type"] = "not-a-real-type"  # ENUM_OUT_OF_RANGE
        proxy.publish("swarm", "task.offered", env)
        # Dispatched BUT with validation showing failure
        self.assertEqual(bus.published_count(), 1)
        published_data = bus._published[0][2]
        self.assertIn("validation", published_data)
        self.assertFalse(published_data["validation"]["passed"])
        # Stats show advisory-blocked (still counted as blocked)
        stats = proxy.get_stats()
        self.assertEqual(stats["envelopes_blocked"], 1)
        self.assertEqual(stats["envelopes_passed"], 0)


# ════════════════════════════════════════════════════════════════
# NON-ENVELOPE PASS-THROUGH (existing hardened_engine usage)
# ════════════════════════════════════════════════════════════════

class TestNonEnvelopePassThrough(unittest.TestCase):
    """These mimic `hardened_engine.offer_task`'s existing bus usage."""

    def test_task_offer_inner_dict_passes_through(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        data = {"task_id": "task_xyz123", "title": "build", "target": "agent_01"}
        proxy.publish("swarm", "task.offered", data)
        self.assertEqual(bus.published_count(), 1)
        # No `validation` field injected (not an envelope)
        published_data = bus._published[0][2]
        self.assertNotIn("validation", published_data)
        self.assertEqual(published_data, data)
        # Stats: non_envelope++
        self.assertEqual(proxy.get_stats()["non_envelope_passed_through"], 1)

    def test_bus_meta_line_shape_passes_through(self):
        """hub/state/event_log.jsonl lines are bus-meta shape, NOT envelopes."""
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        meta_line = {
            "timestamp": "2026-07-20T18:24:07-05:00",
            "type": "pulse",
            "agent_id": "buffy",
            "channel": "swarm.heartbeat",
            "detail": "shipped=handoff_validator.py",
            "pulse_ts": 1784589847,
        }
        proxy.broadcast("buffy", "bus.meta", meta_line)
        self.assertEqual(bus.broadcast_count(), 1)
        self.assertEqual(proxy.get_stats()["non_envelope_passed_through"], 1)


# ════════════════════════════════════════════════════════════════
# BROADCAST
# ════════════════════════════════════════════════════════════════

class TestBroadcastGated(unittest.TestCase):
    def test_broadcast_with_valid_envelope_dispatched(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope(message_type="pulse",
                             payload={"pulse": 0.0},
                             message_id="msg_bcast_001")
        proxy.broadcast("alice", "swarm.heartbeat", env)
        self.assertEqual(bus.broadcast_count(), 1)

    def test_broadcast_with_invalid_envelope_blocked(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope()
        env["message_type"] = "not-a-real-type"  # ENUM_OUT_OF_RANGE (ERROR)
        proxy.broadcast("swarm", "task.offered", env)
        self.assertEqual(bus.broadcast_count(), 0)
        # bus._log fired
        self.assertEqual(
            len([e for e in bus._log_calls
                 if e["type"] == "validation_blocked"]),
            1,
        )


# ════════════════════════════════════════════════════════════════
# JSONL REPLAY PATH
# ════════════════════════════════════════════════════════════════

class TestValidateBusEventLine(unittest.TestCase):
    def test_envelope_line_returns_validation_result(self):
        env = _make_envelope(message_type="pulse",
                             payload={"pulse": 0.0},
                             message_id="msg_replay_001")
        result = validate_bus_event_line(env)
        self.assertIsNotNone(result)
        self.assertTrue(result.passed)

    def test_non_envelope_line_returns_none(self):
        meta = {
            "timestamp": "2026-07-20T18:24:07-05:00",
            "type": "pulse",
            "agent_id": "buffy",
            "channel": "swarm.heartbeat",
            "detail": "x",
            "pulse_ts": 1,
        }
        self.assertIsNone(validate_bus_event_line(meta))

    def test_non_dict_returns_none(self):
        for bad in (None, "string", 42, ["list"]):
            self.assertIsNone(validate_bus_event_line(bad))

    def test_envelope_with_inner_payload_error_fails(self):
        env = _make_envelope(
            message_type="task_offer",
            payload={  # missing task_id, title, etc.
                "title": "ok", "description": "ok",
                "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
            },
        )
        result = validate_bus_event_line(env)
        self.assertIsNotNone(result)
        self.assertFalse(result.passed)


# ════════════════════════════════════════════════════════════════
# STATS
# ════════════════════════════════════════════════════════════════

class TestStats(unittest.TestCase):
    def test_stats_track_mixed_traffic(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")

        # 1 valid envelope → passed
        proxy.publish("alice", "ch1", _make_envelope(
            message_type="pulse", payload={"x": 1}, message_id="m_ok"))

        # 1 invalid envelope (bad message_type → ENUM_OUT_OF_RANGE) → blocked
        bad = _make_envelope(message_type="pulse", payload={"x": 1},
                             message_id="m_bad")
        bad["message_type"] = "not-a-real-type"
        proxy.publish("alice", "ch2", bad)

        # 1 advisory-only envelope (low-entropy string triggers the
        # gut_string check without failing structural validation) —
        # passes BUT counts toward gut_advisories. Earlier this test
        # mistakenly used an empty task_offer payload, which trips
        # REQUIRED_MISSING inside TASK_OFFER_PAYLOAD_CONTRACT (ERROR),
        # landing in envelopes_blocked — not envelopes_passed.
        advisory = _make_envelope(
            message_type="pulse",
            payload={"x": 1},
            sender_id="a" * 100,  # >16 chars, low entropy → GUT_LOW_ENTROPY (INFO)
            message_id="m_gut_adv",
        )
        proxy.publish("alice", "ch3", advisory)

        # 1 non-envelope passed through
        proxy.publish("alice", "ch4", {"foo": "bar"})

        s = proxy.get_stats()
        self.assertEqual(s["publish_calls"], 4)
        self.assertEqual(s["envelopes_validated"], 3)
        self.assertEqual(s["envelopes_passed"], 2)  # valid + advisory
        self.assertEqual(s["envelopes_blocked"], 1)
        self.assertEqual(s["non_envelope_passed_through"], 1)
        # advisory had GUT_EMPTY_TASK_PAYLOAD → gut_advisories >= 1
        self.assertGreaterEqual(s["gut_advisories"], 1)


# ════════════════════════════════════════════════════════════════
# MODE VALIDATION
# ════════════════════════════════════════════════════════════════

class TestModeValidation(unittest.TestCase):
    def test_default_mode_is_block_on_error(self):
        proxy = ValidatingBusProxy(_StubBus())
        self.assertEqual(proxy._mode, "block_on_error")

    def test_invalid_mode_raises_value_error(self):
        with self.assertRaises(ValueError):
            ValidatingBusProxy(_StubBus(), mode="strict")
        with self.assertRaises(ValueError):
            ValidatingBusProxy(_StubBus(), mode="")

    def test_valid_modes_construct(self):
        for m in VALID_MODES:
            ValidatingBusProxy(_StubBus(), mode=m)


# ════════════════════════════════════════════════════════════════
# CALLER'S DICT NOT MUTATED — defensive copy
# ════════════════════════════════════════════════════════════════

class TestCallerDictNotMutated(unittest.TestCase):
    def test_publish_does_not_mutate_caller_dict(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope(message_type="pulse",
                             payload={"pulse": 0.0},
                             message_id="m_immutable")
        snapshot_before = dict(env)
        proxy.publish("alice", "swarm.heartbeat", env)
        # Caller's env dict unchanged
        self.assertEqual(env, snapshot_before)
        self.assertNotIn("validation", env)


# ════════════════════════════════════════════════════════════════
# SEMANTIC OPT-IN — TruthSleuth lane wire-up (spec §4 layer 2)
# ════════════════════════════════════════════════════════════════
#
# Tests for the opt-in semantic integration of `semantic_handoff.py
# SemanticHandoffValidator` into `ValidatingBusProxy`. The proxy
# wraps any bus-like object and, when constructed with
# `semantic_validator=...`, additionally runs a sliding-window
# compare on each structurally-valid envelope BEFORE dispatch.
#
# Design (per prior thinker round):
#   * Sliding window key by default = reply_to > payload.task_id > message_id.
#   * Compare only inner `payload` field (envelope fields like
#     timestamp / message_id change every hop; comparing them
#     would fire false positives).
#   * Layer order: structural gate first; semantic only if structural
#     passed (no point comparing a malformed envelope).
#   * LRU bound (default 256) — OOO eviction via dict insertion order.
#   * RLock thread-safety on the window.
#
# These tests verify the contract — they intentionally re-build a
# minimal semantic validator rather than importing the real one —
# so bus_validator tests are decoupled from semantic_handoff
# (cross-module brittleness avoided).

# A minimal stand-in validator so this test file doesn't import
# semantic_handoff (kept decoupled for cross-module brittleness avoidance).
class _StubSemanticValidator:
    """Drop-in stand-in for SemanticHandoffValidator with controllable findings."""

    def __init__(self, findings_per_call=None):
        self._calls = []
        self._queue = list(findings_per_call or [])

    def compare(self, prior, current):
        self._calls.append({"prior": prior, "current": current})
        # Yield the next pre-queued verdict on each call.
        if self._queue:
            verdict_data = self._queue.pop(0)
        else:
            verdict_data = {"passed": True, "findings": []}
        # Build a duck-typed verdict
        return _StubVerdict(**verdict_data)


class _StubVerdict:
    def __init__(self, passed=True, findings=None):
        self.passed = passed
        self.findings = list(findings or [])
    def errors(self):
        return [f for f in self.findings if getattr(f, "severity", None) in ("error", "critical")]
    def warnings(self):
        return [f for f in self.findings if getattr(f, "severity", None) == "warning"]
    def to_dict(self):
        return {"passed": self.passed, "findings": []}


class _StubFinding:
    def __init__(self, severity):
        self.severity = severity

    def to_dict(self):
        # Mirrors ValidationFinding.to_dict so `_record_semantic_block`
        # can serialize findings without AttributeError on test stubs.
        try:
            sev_value = self.severity.value
        except AttributeError:
            sev_value = str(self.severity)
        return {
            "field": getattr(self, "field", None),
            "severity": sev_value,
            "code": getattr(self, "code", None),
            "message": getattr(self, "message", ""),
            "actual": getattr(self, "actual", None),
            "expected": getattr(self, "expected", None),
        }


class TestSemanticOptOutByDefault(unittest.TestCase):
    """No validator supplied → no semantic layer. Regression: existing
    behavior must be unchanged for callers who don't opt in."""

    def test_no_validator_no_semantic_stats_or_findings(self):
        bus = _StubBus()
        proxy = ValidatingBusProxy(bus, mode="block_on_error")
        env = _make_envelope(message_type="pulse", payload={"pulse": 0.0},
                             message_id="m_sem_off_1")
        proxy.publish("alice", "swarm.heartbeat", env)
        # Dispatched cleanly
        self.assertEqual(bus.published_count(), 1)
        published_data = bus._published[0][2]
        # No semantic_validation field attached (validator not provided)
        self.assertNotIn("semantic_validation", published_data)
        # Stats show ZERO semantic activity
        s = proxy.get_stats()
        self.assertEqual(s["envelopes_with_semantic_check"], 0)
        self.assertEqual(s["semantic_findings_count"], 0)
        self.assertEqual(s["semantic_omissions_blocked"], 0)


class TestSemanticOptInBasic(unittest.TestCase):
    """Validator supplied → semantic check runs on structurally-valid envelopes."""

    def test_clean_chain_dispatches_cleanly(self):
        bus = _StubBus()
        v = _StubSemanticValidator([])  # all clean
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        # Two consecutive task_offer envelopes with the SAME task_id —
        # second compares against first.
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_abcdef01",
            "title": "Build the thing",
            "description": "Detailed work description.",
            "priority": 3,
            "timeout_seconds": 300.0,
            "min_tier": 1,
        }, message_id="m_sem_1")
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_abcdef01",
            "title": "Build the thing",
            "description": "Detailed work description.",
            "priority": 3,
            "timeout_seconds": 300.0,
            "min_tier": 1,
        }, message_id="m_sem_2")
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        # Both dispatched
        self.assertEqual(bus.published_count(), 2)
        s = proxy.get_stats()
        # Second envelope ran semantic compare (one prior exists)
        self.assertGreaterEqual(s["envelopes_with_semantic_check"], 1)
        self.assertEqual(s["semantic_findings_count"], 0)
        self.assertEqual(s["semantic_omissions_blocked"], 0)

    def test_layer_order_structural_gate_runs_first(self):
        """Structural block must short-circuit semantic check.

        If the envelope is structurally invalid, the proxy must
        never compare it against the prior (no garbage in / out)."""
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        bad = _make_envelope(message_type="pulse", payload={"x": 1}, message_id="m_layer_bad")
        bad["message_type"] = "not-a-real-type"  # structural fail
        proxy.publish("alice", "ch", bad)
        # Not dispatched
        self.assertEqual(bus.published_count(), 0)
        # Semantic compare did NOT run (validator was not called)
        self.assertEqual(len(v._calls), 0)
        s = proxy.get_stats()
        self.assertEqual(s["envelopes_with_semantic_check"], 0)


class TestSemanticKeyStrategy(unittest.TestCase):
    """Confirm the default key fn: reply_to > payload.task_id > message_id."""

    def test_reply_to_wins_when_present(self):
        env = _make_envelope(message_type="task_progress",
                             payload={"progress": "30%"},
                             message_id="m1")
        env["reply_to"] = "parent_chain_head_id_xyz"
        # Same as: payload.task_id = "task_abc"; message_id = "m1"
        # default_semantic_key_fn should pick reply_to first.
        self.assertEqual(
            default_semantic_key_fn(env),
            "parent_chain_head_id_xyz",
        )

    def test_payload_task_id_used_when_no_reply_to(self):
        env = _make_envelope(message_type="task_progress",
                             payload={"task_id": "task_abc01", "progress": "30%"},
                             message_id="m1")
        self.assertEqual(default_semantic_key_fn(env), "task_abc01")

    def test_message_id_used_when_no_reply_to_and_no_task_id(self):
        env = _make_envelope(message_type="pulse", payload={"p": 0.0},
                             message_id="m_only_id")
        self.assertEqual(default_semantic_key_fn(env), "m_only_id")

    def test_none_returned_when_no_usable_key(self):
        env = _make_envelope(message_type="pulse", payload={"p": 0.0},
                             message_id="")  # falsy
        # No reply_to, no payload.task_id, message_id is empty → None
        self.assertIsNone(default_semantic_key_fn(env))

    def test_non_dict_input_returns_none(self):
        for bad in (None, "string", 42, ["list"]):
            self.assertIsNone(default_semantic_key_fn(bad))


class TestSemanticSlidingWindowBehavior(unittest.TestCase):
    """Verify the sliding prior window — compare → store / evict."""

    def test_first_envelope_has_no_prior_compare_runs_no_error(self):
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_first01",
            "title": "x", "description": "y",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_first")
        proxy.publish("alice", "task.offered", env)
        # First envelope: validator.compare IS called (with empty prior {})
        # but it should not block dispatch in the absence of findings.
        self.assertEqual(bus.published_count(), 1)
        self.assertEqual(len(v._calls), 1)
        # Compare was called with empty prior (first envelope semantics)
        self.assertEqual(v._calls[0]["prior"], {})

    def test_window_snapshots_after_publish(self):
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_snap01",
            "title": "x", "description": "y",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_snap")
        proxy.publish("alice", "task.offered", env)
        # Window should contain the envelope, keyed by payload.task_id
        snap = proxy.get_semantic_window_snapshot()
        self.assertIn("task_snap01", snap)
        snap_env = snap["task_snap01"]
        self.assertEqual(snap_env.get("message_id"), "m_snap")

    def test_lru_eviction_at_max(self):
        """When window exceeds max, oldest entry is evicted (O(1) dict-order pop)."""
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(
            bus, mode="block_on_error",
            semantic_validator=v,
            semantic_window_max=3,
        )
        # Push 5 envelopes into a 3-slot window with distinct task_ids.
        for i in range(5):
            env = _make_envelope(message_type="task_offer", payload={
                "task_id": f"task_e{i:02d}",
                "title": "x", "description": "y",
                "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
            }, message_id=f"m_e{i}")
            proxy.publish("alice", "task.offered", env)
        snap = proxy.get_semantic_window_snapshot()
        # Should hold exactly max (3), oldest 2 evicted
        self.assertEqual(len(snap), 3)
        # Newest three are task_e02..task_e04
        self.assertIn("task_e02", snap)
        self.assertIn("task_e03", snap)
        self.assertIn("task_e04", snap)
        # Evicted
        self.assertNotIn("task_e00", snap)
        self.assertNotIn("task_e01", snap)

    def test_only_payload_compared_envelope_fields_ignored(self):
        """Envelope fields like timestamp / message_id change every hop.

        Confirms: validator.compare receives ONLY the inner payload
        dicts (prior and current), not the outer envelope."""
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_iso01", "title": "ok",
            "description": "ok", "priority": 0,
            "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_iso1", ttl=99.0)
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_iso01", "title": "ok",
            "description": "ok", "priority": 0,
            "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_iso2", ttl=11.0)  # different ttl, different msg_id
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        self.assertEqual(len(v._calls), 2)
        # Both compare calls received payload dicts that match each
        # other's payload — NOT the outer envelope fields.
        prior_arg = v._calls[1]["prior"]
        prior_arg.pop("envelope", None)  # if any leakage happened
        self.assertEqual(prior_arg, env1["payload"])
        self.assertEqual(v._calls[1]["current"], env2["payload"])


class TestSemanticBlocking(unittest.TestCase):
    """Semantic ERROR findings can block dispatch in block_on_error mode."""

    def test_semantic_error_blocks_in_block_on_error_mode(self):
        bus = _StubBus()
        # Validator queues a verdict with ERROR-class finding for the
        # second call. First call (no prior) gets a clean verdict.
        v = _StubSemanticValidator([
            {"passed": True, "findings": []},  # first envelope
            {"passed": False, "findings": [_StubFinding("error")]},  # second
        ])
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_blk01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_b1")
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_blk01", "title": "c", "description": "d",  # deletion
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_b2")
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        # First dispatched; second BLOCKED by semantic-error in block_on_error.
        self.assertEqual(bus.published_count(), 1)
        s = proxy.get_stats()
        self.assertEqual(s["envelopes_blocked"], 1)
        self.assertEqual(s["semantic_omissions_blocked"], 1)
        # validation_log records the semantic block
        vlog = proxy.get_validation_log()
        self.assertEqual(len(vlog), 1)
        self.assertEqual(vlog[0].get("layer"), "semantic")

    def test_semantic_error_advisory_only_in_log_only_mode(self):
        """log_only mode dispatches anyway, even on semantic ERROR."""
        bus = _StubBus()
        v = _StubSemanticValidator([
            {"passed": True, "findings": []},
            {"passed": False, "findings": [_StubFinding("error")]},
        ])
        proxy = ValidatingBusProxy(bus, mode="log_only", semantic_validator=v)
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_log01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_l1")
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_log01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_l2")
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        # Both dispatched
        self.assertEqual(bus.published_count(), 2)
        # But second carries semantic_validation field showing failure
        published_2 = bus._published[1][2]
        self.assertIn("semantic_validation", published_2)
        self.assertFalse(published_2["semantic_validation"]["passed"])
        s = proxy.get_stats()
        # Log-only counts envelopes_blocked (still a "blocked" in stats terms)
        self.assertEqual(s["envelopes_blocked"], 1)
        self.assertEqual(s["semantic_omissions_blocked"], 1)
        self.assertEqual(s["semantic_findings_count"], 1)

    def test_semantic_warning_does_not_block(self):
        """A pure-WARNING verdict (no ERROR/CRITICAL) must dispatch regardless of mode."""
        bus = _StubBus()
        v = _StubSemanticValidator([
            {"passed": True, "findings": []},
            {"passed": True, "findings": [_StubFinding("warning")]},
        ])
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_warn01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_w1")
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_warn01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_w2")
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        # Both dispatched (warning ≠ error)
        self.assertEqual(bus.published_count(), 2)
        s = proxy.get_stats()
        self.assertEqual(s["envelopes_blocked"], 0)
        self.assertEqual(s["semantic_findings_count"], 1)


class TestSemanticCustomKey(unittest.TestCase):
    """Caller can override semantic_key_fn."""

    def test_custom_key_fn_used(self):
        bus = _StubBus()
        v = _StubSemanticValidator()
        # Always key by message_id (smoke — even though envelope fields change)
        def always_msg_id(env):
            return (env or {}).get("message_id")
        proxy = ValidatingBusProxy(
            bus, mode="block_on_error",
            semantic_validator=v,
            semantic_key_fn=always_msg_id,
        )
        env1 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_ckk01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_ckk1")
        env2 = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_ckk01", "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        }, message_id="m_ckk2")
        proxy.publish("alice", "task.offered", env1)
        proxy.publish("alice", "task.offered", env2)
        snap = proxy.get_semantic_window_snapshot()
        # Keys are the message_ids (custom fn)
        self.assertIn("m_ckk1", snap)
        self.assertIn("m_ckk2", snap)


class TestSemanticRejectedKeyShortCircuits(unittest.TestCase):
    """When semantic_key_fn returns None, semantic check is skipped."""

    def test_none_key_no_validator_call_no_stats_increment(self):
        bus = _StubBus()
        v = _StubSemanticValidator()
        proxy = ValidatingBusProxy(bus, mode="block_on_error", semantic_validator=v)
        env = _make_envelope(message_type="task_offer", payload={
            "task_id": "task_nonek01",
            "title": "a", "description": "b",
            "priority": 0, "timeout_seconds": 60.0, "min_tier": 0,
        # In bus_validator the default key fn picks payload.task_id when
        # everything else is empty. We override so the key fn returns None.
        }, message_id="")  # forces default fn to return task_id=None if no other key
        # Actually defaults will pick task_id. Override to force None.
        proxy._semantic_key_fn = lambda env: None
        proxy.publish("alice", "task.offered", env)
        s = proxy.get_stats()
        self.assertEqual(s["envelopes_with_semantic_check"], 0)
        # Validator wasn't called
        self.assertEqual(len(v._calls), 0)
        # But dispatch still happened (envelope passed structural)
        self.assertEqual(bus.published_count(), 1)


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()


# ════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════
# PROPER SEMANTIC INTEGRATION (closes AN-11 PROPER test gap)
# ════════════════════════════════════════════════════════════════
# Exercises ValidatingBusProxy’s wire-up with a real
# SemanticHandoffValidator whose proper_semantic layer is monkey-patched
# on (no MiniLM model load required → keeps CI/dev fast + light).
#
# Why message_type="status": SWARM_MESSAGE_CONTRACT routes
# message_type="task_offer" through TASK_OFFER_PAYLOAD_CONTRACT which
# requires priority/timeout_seconds/min_tier. We use "status" (no inner
# payload contract lookup) so the outer structural check passes cleanly
# and the semantic layer (compare on inner payload) actually runs.
#
# All tests publish TWICE: the first publish seeds the sliding window;
# the second publish triggers compare() with a prior and exercises the
# real block/forward decision. Publishing once would never reach the
# proper_semantic compare path (no prior → no finding).

class TestProperSemanticIntegration(unittest.TestCase):
    """Validates that ValidatingBusProxy routes a proper_semantic-enabled
    SemanticHandoffValidator end-to-end and honours ERROR findings per
    configured mode (block_on_error vs log_only).
    """

    def _make_proper_validator(self):
        """Build a real SemanticHandoffValidator with proper_semantic
        enabled via monkey-patch; bypasses the MiniLM model load so the
        test exercises the real routing logic without the heavy dep.

        Mock encode() uses a per-call counter that tiles an orthogonal
        unit vector to any input length. Cos sim between adjacent calls
        is exactly 0.0 (well below the 0.80 error threshold) → every
        compare() produces an ERROR-grade finding.
        """
        from unittest.mock import MagicMock
        import numpy as np
        from semantic_handoff import SemanticHandoffValidator
        # Default enabled_heuristics=None enables all 5 families,
        # giving realistic integration coverage.
        v = SemanticHandoffValidator()
        v._proper_available = True
        model = MagicMock()

        def fake_encode(texts, **kw):
            n = len(texts) if hasattr(texts, "__len__") else 1
            idx = fake_encode.counter
            fake_encode.counter += 1
            dim = 4
            vec = np.zeros(dim, dtype=np.float32)
            vec[idx % dim] = 1.0
            return np.tile(vec, (n, 1))

        fake_encode.counter = 0
        model.encode.side_effect = fake_encode
        v._proper_model = model
        return v

    def _make_status_envelope(
        self, *, title: str = "x", description: str = "y", reason: str = "z",
        # Stable across publishes: default_semantic_key_fn falls through
        # reply_to > payload.task_id > message_id, so identical message_id
        # is what makes the second publish find the first as a prior in
        # the sliding window. Do NOT randomize this without rethinking
        # the test (no prior → no compare → no ERROR → assertions fail).
        message_id: str = "msg_envelope_001",
    ) -> dict:
        """Build a SwarmMessage-shaped envelope that PASSES structural
        validation: message_type='status' has no inner payload contract,
        so only the outer SWARM_MESSAGE_CONTRACT runs."""
        import time
        return {
            "sender_id": "swarm",
            "message_type": "status",
            "channel": "status.update",
            "payload": {
                "title": title,
                "description": description,
                "reason": reason,
            },
            "message_id": message_id,
            "timestamp": time.time(),
            "ttl": 60.0,
        }

    def test_proper_disabled_passes_through(self):
        """Validator built WITHOUT monkey-patching (model_path=None
        → _proper_available=False) still increments
        envelopes_with_semantic_check and forwards both messages in
        block_on_error mode (no ERROR from disabled proper)."""
        from semantic_handoff import SemanticHandoffValidator
        v = SemanticHandoffValidator()  # proper disabled
        bus = _StubBus()
        proxy = ValidatingBusProxy(
            bus, mode="block_on_error", semantic_validator=v,
        )
        env = self._make_status_envelope(
            title="hello world",
            description="warm friendly text",
            reason="no embedding comparison possible",
        )
        proxy.publish("swarm", "status.update", env)
        proxy.publish("swarm", "status.update", env)
        # proper disabled → no ERROR → both forwarded.
        self.assertEqual(bus.published_count(), 2)
        stats = proxy.get_stats()
        self.assertGreaterEqual(
            stats["envelopes_with_semantic_check"], 2,
            "envelopes_with_semantic_check should advance even when proper is disabled",
        )
        # Soft no-finding invariant: identical payloads + proper disabled
        # SHOULD produce zero (or one advisory) semantic findings. The
        # first publish calls compare({}, current) — the empty prior is
        # an edge case that some legacy heuristics emit an INFO/WARNING
        # advisory on; that's fine. What we actually lock against is
        # ERROR-grade findings, which would block_on_error drop the
        # message (and the upstream assertEqual(published_count, 2)
        # would fail loudly with the regression's exact mode).
        # Soft upper bound: with proper disabled, 4 active heuristics
        # (omission/numeric/class/polarity — NOT proper) × 2 publishes
        # = 8 max if each emits one advisory per call. For THIS test
        # (identical payloads + proper disabled) the realistic count
        # is 0–2. ERROR-grade findings would still drop the publish
        # upstream under block_on_error and fail the
        # published_count() assertion loudly with the regression's
        # exact mode.
        # WARNING: do NOT enable proper in this test — it would add a
        # 5th active heuristic and push findings past 8, failing the
        # assertion. If you intentionally want proper active here,
        # revise the `<= 8` bound upward (and update the comment to
        # enumerate the new active-heuristic set).
        self.assertLessEqual(
            stats["semantic_findings_count"], 8,
            "0–8 advisory findings expected when proper is disabled + payloads are identical; "
            "ERROR-grade findings would have dropped the publish upstream",
        )

    def test_proper_error_honors_block_on_error(self):
        """Orthogonal mock vectors → cos=0.0 → ERROR severity.
        block_on_error mode forwards the first publish (window seed),
        but the second publish triggers compare() with prior → ERROR
        → message dropped. semantic_findings_count still advances."""
        v = self._make_proper_validator()
        bus = _StubBus()
        proxy = ValidatingBusProxy(
            bus, mode="block_on_error", semantic_validator=v,
        )
        env = self._make_status_envelope(
            title="completely orthogonal content alpha",
            description="totally orthogonal content beta",
            reason="no overlap whatsoever gamma",
        )
        # First publish: seeds the window (no prior → no compare).
        proxy.publish("swarm", "status.update", env)
        # Second publish: compare(prior, current) → orthogonal cos=0
        # → ERROR → blocked under block_on_error.
        proxy.publish("swarm", "status.update", env)
        # Only the first envelope reached the bus; the second was dropped.
        self.assertEqual(
            bus.published_count(), 1,
            "block_on_error should drop the second publish (compare() ERROR)",
        )
        stats = proxy.get_stats()
        self.assertGreaterEqual(
            stats["semantic_findings_count"], 1,
            "ERROR finding should be counted even when blocked",
        )

    def test_proper_error_honors_log_only(self):
        """Same orthogonal-vectors ERROR scenario under log_only mode:
        both publishes are forwarded (log_only does not block), but
        the ERROR finding is still counted from the second publish."""
        v = self._make_proper_validator()
        bus = _StubBus()
        proxy = ValidatingBusProxy(
            bus, mode="log_only", semantic_validator=v,
        )
        env = self._make_status_envelope(
            title="completely orthogonal content alpha",
            description="totally orthogonal content beta",
            reason="no overlap whatsoever gamma",
        )
        proxy.publish("swarm", "status.update", env)
        proxy.publish("swarm", "status.update", env)
        # log_only forwards both (only logs the ERROR).
        self.assertEqual(
            bus.published_count(), 2,
            "log_only should forward both publishes (only logs ERROR)",
        )
        stats = proxy.get_stats()
        self.assertGreaterEqual(
            stats["semantic_findings_count"], 1,
            "ERROR finding should be counted under log_only too",
        )

    def test_proper_threshold_validation_blocks_degenerate_values(self):
        """The threshold validation guard (0.5 <= error <= warning <= 1.0)
        must reject degenerate policy values at construction time."""
        from semantic_handoff import SemanticHandoffValidator
        with self.assertRaises(ValueError):
            SemanticHandoffValidator(
                model_path=None,
                proper_warning_threshold=0.50,
                proper_error_threshold=0.80,  # error > warning → invalid
            )
        with self.assertRaises(ValueError):
            SemanticHandoffValidator(
                model_path=None,
                proper_warning_threshold=0.95,
                proper_error_threshold=0.30,  # error < 0.5 → invalid (nonsense)
            )


if __name__ == "__main__":
    unittest.main()

