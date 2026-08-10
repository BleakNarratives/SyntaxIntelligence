"""
BUS VALIDATOR — wire `handoff_validator.validate_swarm_message()` +
optionally `autoclaw_validator.ResourceCeiling` into the event-bus pipeline
(spec §2 hard layer + spec §4 layer-1 productionization, plus the
resource ceiling wire-up requested 2026-07-21).

Goal
----
Wrap any bus-like object that exposes `publish(sender_id, channel, data)` and
`broadcast(sender_id, channel, data)` so that messages shaped like
SwarmMessage envelopes are gated by `validate_swarm_message()` BEFORE
downstream dispatch. Failure modes are surfaced via bus._log('validation_blocked').

When constructed with an `autoclaw_ceiling=ResourceCeiling(...)`, the
proxy additionally runs a SPEC §2 hard-layer resource preflight BEFORE
validation+dispatch (RSS-over-block halts the publish cleanly) and a
post-call observation afterward (time-budget breach / RSS-warn logged to
crash_log, no un-dispatch). The preflight is the actual OOM defense
because it stops NEW envelopes once the process is already in the
danger zone (the failure pattern that hit 2026-07-21, see AN-12).

Non-goals (per gap-report §4 — "what this report does NOT propose")
-------------------------------------------------------------------
- Does NOT modify `SyntaxEventBus` source.
- Does NOT shift TruthSleuth lane (open AN-11).
- Does NOT modify `swarm_charter.py` (open B).
- Does NOT touch `hub/state/event_log.jsonl` historicals.
- Does NOT interpret non-SwarmMessage event payloads (event-meta lines in
  the JSONL bus stay as-is; validated separately via `validate_bus_event_line`).

Modes
-----
- "block_on_error" (default) — any ERROR/CRITICAL structural/semantic finding
  aborts dispatch; logged to bus._log and `proxy._validation_log`.
- "log_only" — dispatch anyway; findings attached to data['validation'] for
  downstream visibility; counted in stats but NO block.

JSONL replay
------------
`validate_bus_event_line(line_dict)` validates a single event-log line if
and only if it has the SwarmMessage envelope shape. Returns ValidationResult
or None.

Spec status
-----------
Closes the gap-report §3 next-gap: 'wire the validator into the live
event-bus pipeline so SwarmMessage handoffs are gated before downstream
action' (Layer 1+2). Also closes the in-flight follow-up "every SwarmMessage
envelope pays the resource ceiling cost on top of the structural + semantic
validation" — autoclaw gate hooks in preflight + postcall.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# Heuristic envelope detection: a dict carrying all six required envelope
# keys (sender_id, message_type, payload, message_id, timestamp, ttl).
# Existing hardened_engine publishes (`{"task_id": ..., "title": ...}`)
# do NOT carry these — they're passed through unchanged.
SWARM_ENVELOPE_REQUIRED_KEYS = frozenset({
    "sender_id", "message_type", "payload",
    "message_id", "timestamp", "ttl",
})

VALID_MODES = ("block_on_error", "log_only")


# ════════════════════════════════════════════════════════════════
# SEMANTIC KEY (spec §4 layer 2 — sliding prior window)
# ════════════════════════════════════════════════════════════════
#
# Why a key function rather than a static (sender, type) tuple:
#   Two distinct task_progress messages from the same sender are
#   DIFFERENT conversations; comparing them semantically produces
#   false positives. The window should key by thread-of-correspondence.
#
# Default strategy: prefer reply_to (the parent's message_id) so a
# chain reply is compared against the chain head, else
# payload.task_id (so progress #1 vs #2 of the same task compare),
# else message_id (single-message self-bound; never overlaps).
#
# Operators can override by passing a `semantic_key_fn` to the proxy.
# The function receives an envelope dict and returns a string key,
# or None to disable semantic comparison for that envelope (e.g.,
# a fire-and-forget status publish where there's no chain).

def default_semantic_key_fn(envelope: Dict[str, Any]) -> Optional[str]:
    """Pick a comparison key for the sliding prior window.

    Falls through:
      1. envelope.reply_to       — chain reply against parent
      2. envelope.payload.task_id — progress against task thread
      3. envelope.message_id      — self-bound; never overlaps
      4. None                     — disable semantic compare for this msg
    """
    if not isinstance(envelope, dict):
        return None
    reply_to = envelope.get("reply_to")
    if isinstance(reply_to, str) and reply_to:
        return reply_to
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
    msg_id = envelope.get("message_id")
    if isinstance(msg_id, str) and msg_id:
        return msg_id
    return None


def is_swarm_envelope(data: Any) -> bool:
    """True if `data` is a dict shaped like a SwarmMessage envelope."""
    if not isinstance(data, dict):
        return False
    return SWARM_ENVELOPE_REQUIRED_KEYS.issubset(data.keys())


class ValidatingBusProxy:
    """Wrap a bus-like object so SwarmMessage-shaped messages are gated.

    Construction
    ------------
        bus = SyntaxEventBus()                              # any bus
        proxy = ValidatingBusProxy(bus)                     # default mode
        proxy = ValidatingBusProxy(bus, mode="log_only")    # advisory only

    Use
    ---
        Same API as the underlying bus — `subscribe/unsubscribe/publish/broadcast`
        are forwarded. The proxy intercepts `publish/broadcast` and inserts a
        validation gate on SwarmMessage-shaped data; non-envelope data is
        passed through untouched.

    Optional `bus._log` hook
    ------------------------
        Some bus implementations expose `_log(...)` for internal events.
        The proxy duck-calls it (swallowing `AttributeError`) so it works
        on the live `SyntaxEventBus` and on test stubs alike.

    Optional SEMANTIC layer (spec §4 layer 2)
    -----------------------------------------
        Pass `semantic_validator=...` to enable TruthSleuth's sliding
        prior-window fidelity check on top of the structural layer.
        The proxy keys the sliding window by default via
        `default_semantic_key_fn` (reply_to > payload.task_id > message_id)
        so a progress chain compares against the chain head, not against
        an unrelated message from the same sender.

        Constructor kwargs:
          semantic_validator:    SemanticHandoffValidator instance (or None).
                                 If None (default), no semantic check runs.
          semantic_key_fn:       callable(envelope) -> Optional[str].
                                 Default: default_semantic_key_fn.
          semantic_window_max:   sliding-window bound. Default 256.
                                 When exceeded, oldest entry is dropped
                                 (plain dict insert-order pop is O(1)).

        Thread safety: a module-level `_lock` guards the sliding window.
        Two threads publishing for the same key can corrupt the prior
        without it. The lock is acquired only during semantic comparison
        + window update — structural validation remains lock-free.

        Semantic stats (new — additive, never breaks existing stats):
          envelopes_with_semantic_check — envelopes that ran semantic_compare
          semantic_findings_count        — total findings (incl. WARNINGs)
          semantic_omissions_blocked     — ERROR-class omissions that blocked
    """

    def __init__(
        self,
        bus: Any,
        mode: str = "block_on_error",
        semantic_validator: Optional[Any] = None,
        semantic_key_fn: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
        semantic_window_max: int = 256,
        autoclaw_ceiling: Optional[Any] = None,
        long_lived_monitor: bool = False,
    ):
        if mode not in VALID_MODES:
            raise ValueError(
                f"ValidatingBusProxy mode must be one of {VALID_MODES}; "
                f"got {mode!r}"
            )
        self._bus = bus
        self._mode = mode
        self._semantic_validator = semantic_validator
        self._semantic_key_fn = semantic_key_fn or default_semantic_key_fn
        self._semantic_window_max = max(2, semantic_window_max)
        # Spec §2 hard layer — opt-in. `None` disables the gate entirely
        # (zero overhead, baseline behavior preserved for callers that
        # don't opt in). When set, ResourceCeiling.mode controls whether
        # the gate actually blocks (HALT) or observes only (TRACE/OFF).
        self._autoclaw_ceiling = autoclaw_ceiling
        self._prior_envelopes: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._validation_log: List[Dict[str, Any]] = []
        self._stats: Dict[str, Union[int, float]] = {        "publish_calls": 0,
        "broadcast_calls": 0,
        "envelopes_validated": 0,
        "envelopes_passed": 0,
        "envelopes_blocked": 0,
        "gut_advisories": 0,
        "non_envelope_passed_through": 0,
        # semantic layer (inert when validator not provided)
        "envelopes_with_semantic_check": 0,
        "semantic_findings_count": 0,
        "semantic_omissions_blocked": 0,
        # autoclaw layer (spec §2 hard layer; inert when ceiling=None)
        "autoclaw_observations": 0,        # calls where autoclaw ran (any branch)
        "autoclaw_preflight_findings": 0,  # ResourceFinding count at preflight
        "autoclaw_postcall_findings": 0,   # ResourceFinding count at postcall
        "autoclaw_blocks_preflight": 0,    # HALT-mode preflight blocks (publish aborted)
        "autoclaw_blocks_postcall": 0,     # HALT-mode post-call blocks (observation only)
        "autoclaw_findings_total": 0,      # preflight + postcall (sanity)
        # long-lived monitor layer (spec §2 hard layer; INERT unless
        # long_lived_monitor=True was passed to the constructor).
        # These stats reflect the lifetime of the proxy, not a single
        # dispatch. observer-only — never blocks a publish.
        "monitor_findings_total": 0,        # lifetime findings drained from queue
        "monitor_ticks_total": 0,           # lifetime poll iterations
        "monitor_last_poll_at": 0.0,        # epoch seconds; 0.0 = never polled
        "monitor_observed_during_dispatch": 0,  # findings drained after postcall
        # NOTE: monitor_last_poll_at carries an epoch-seconds float, so the
        # annotation on `self._stats` (above) is Dict[str, Union[int, float]]
        # rather than strict int. NOTE 2: get_stats() returns the same Union.
    }
        self._log = logging.getLogger("syntax.bus_validator")
        self._lazy_validate: Optional[Any] = None  # bound on first call
        self._lazy_autoclaw: Optional[Any] = None  # bound on first autoclaw_call
        self._dispatch_started_at: float = 0.0    # set/used when autoclaw active

        # Long-lived monitor (spec §2 hard layer, opt-in). Closes the
        # in-flight-RSS-spike blind spot that per-call preflight+postcall
        # leaves open. Default False → zero thread overhead; baseline
        # behavior unchanged. Use ``with ValidatingBusProxy(...,
        # long_lived_monitor=True) as proxy:`` for the longer-lived
        # path; the ContextManager starts the monitor on enter and
        # stops on exit (see __enter__ / __exit__ below).
        self._long_lived_monitor: bool = long_lived_monitor
        self._monitor_queue: Optional[Any] = None
        self._monitor: Optional[Any] = None  # autoclaw_validator.ResourceMonitor
        if self._long_lived_monitor:
            import queue as _queue
            self._monitor_queue = _queue.Queue()

    # ─── FORWARDED BUS INTERFACE ───────────────────────────────────
    def subscribe(self, agent_id: str, channel: str, callback):
        return self._bus.subscribe(agent_id, channel, callback)

    def unsubscribe(self, agent_id: str, channel: str):
        return self._bus.unsubscribe(agent_id, channel)

    # ─── GATED INTERFACE ───────────────────────────────────────────
    def publish(self, sender_id: str, channel: str, data: Dict[str, Any]):
        self._stats["publish_calls"] += 1
        return self._dispatch(sender_id, channel, data, kind="publish")

    def broadcast(self, sender_id: str, channel: str, data: Dict[str, Any]):
        self._stats["broadcast_calls"] += 1
        return self._dispatch(sender_id, channel, data, kind="broadcast")

    # ─── INTERNAL ──────────────────────────────────────────────────
    def _bound_validate(self):
        """Lazy-import `validate_swarm_message` from handoff_validator.

        Imported here (not at module top) so `import bus_validator`
        never pulls in handoff_validator — keeps each module
        independently testable and break-cycle-safe.
        """
        if self._lazy_validate is None:
            from handoff_validator import validate_swarm_message
            self._lazy_validate = validate_swarm_message
        return self._lazy_validate

    def _dispatch(self, sender_id: str, channel: str, data: Any, kind: str):
        # ── SPEC §2 HARD LAYER — preflight resource gate. Runs BEFORE
        # any validation because if RSS is already over the block
        # ceiling, doing pydantic-validation work is wasted CPU. HALT
        # mode aborts the publish; TRACE/OFF observe only.
        autoclaw_active = (
            self._autoclaw_ceiling is not None
            and getattr(self._autoclaw_ceiling, "mode", "OFF") != "OFF"
        )
        if autoclaw_active:
            self._stats["autoclaw_observations"] += 1
            # Long-lived monitor: drain any findings the bg thread
            # emitted since the last dispatch entry. Defense-in-depth
            # element: catches in-flight RSS spikes between dispatches.
            self._drain_monitor_queue(phase="preflight")
            self._dispatch_started_at = time.monotonic()
            if self._autoclaw_preflight_check(sender_id, channel, data):
                # Preflight BLOCK — do NOT enter validation+dispatch.
                # `return` early; stats already incremented.
                return

        # Non-SwarmMessage-shaped data → pass through untouched.
        if not is_swarm_envelope(data):
            self._stats["non_envelope_passed_through"] += 1
            dispatched = self._proxy_dispatch(sender_id, channel, data, kind)
            if autoclaw_active:
                self._autoclaw_postcall_check(sender_id, channel, data, kind)
                self._drain_monitor_queue(phase="postcall")
            return dispatched

        self._stats["envelopes_validated"] += 1
        validate = self._bound_validate()
        result = validate(data, target_id=sender_id)

        # ── LAYER 2 (semantic) — run ONLY AFTER structural pass on
        # outer envelope so we never feed a malformed shape into the
        # sliding-window machinery. Comparison is on inner `payload`
        # only (per design — envelope fields like timestamp/message_id
        # change every hop and would fire false-positive findings).
        semantic_verdict: Optional[Any] = None
        semantic_blocking = False
        if (
            self._semantic_validator is not None
            and not bool(result.errors())
        ):
            semantic_verdict, semantic_blocking = self._run_semantic(data, result)

        # Inject findings into a COPY so the caller's dict is not
        # mutated. Downstream subscribers see data['validation'].
        # `is_swarm_envelope(data)` is True here, so `data` is a dict.
        data_with_validation = dict(data)
        data_with_validation["validation"] = result.to_dict()
        if semantic_verdict is not None:
            data_with_validation["semantic_validation"] = semantic_verdict.to_dict()

        errors_present = bool(result.errors())
        gut_present = bool(result.gut_note)

        # Stats measure VALIDATION RESULT (mutually exclusive):
        #   envelopes_blocked = validator reported errors
        #   envelopes_passed  = validator reported no errors
        # Dispatch decisions (block_on_error aborts; log_only delivers
        # the failed-validation envelope anyway) are tracked separately
        # by the underlying bus's publish/broadcast counters — NOT here.
        if errors_present:
            self._stats["envelopes_blocked"] += 1
            self._record_structural_block(sender_id, channel, result)
            if self._mode == "block_on_error":
                if autoclaw_active:
                    self._autoclaw_postcall_check(sender_id, channel, data, kind)
                    self._drain_monitor_queue(phase="postcall")
                return  # do NOT dispatch — structural gate wins
        elif semantic_blocking:
            # Semantic-only block: structural gate was clean, but the
            # sliding window reports an ERROR-class fidelity loss (e.g.,
            # an important field evaporated on hop). Counts as blocked
            # for the proxy's stats (the operator sees it didn't make
            # it downstream) but the underlying pub/sub counter is NOT
            # incremented because we never called it.
            self._stats["envelopes_blocked"] += 1
            self._stats["semantic_omissions_blocked"] += 1
            self._record_semantic_block(sender_id, channel, semantic_verdict)
            if self._mode == "block_on_error":
                if autoclaw_active:
                    self._autoclaw_postcall_check(sender_id, channel, data, kind)
                    self._drain_monitor_queue(phase="postcall")
                return  # do NOT dispatch
        else:
            self._stats["envelopes_passed"] += 1

        if gut_present:
            self._stats["gut_advisories"] += 1
        dispatched = self._proxy_dispatch(sender_id, channel, data_with_validation, kind)
        if autoclaw_active:
            self._autoclaw_postcall_check(sender_id, channel, data, kind)
            self._drain_monitor_queue(phase="postcall")
        return dispatched

    def _run_semantic(
        self,
        data: Dict[str, Any],
        structural_result: Any,
    ) -> tuple:
        """
        Sliding-window semantic compare. Returns (verdict, blocking_bool).

        Sequence inside the lock:
          1. Compute key via self._semantic_key_fn(data).
          2. If key is None → skip (verdict=None, blocking=False).
          3. Look up prior envelope by key; if found, compare inner
             payloads only (envelope fields like timestamp/message_id
             are unstable hop-to-hop).
          4. Stash current envelope in the window. If window is over
             the bound, pop the oldest (dict insert-order is O(1)).
          5. Return verdict; blocking=True iff verdict has any
             severity ERROR / CRITICAL AND mode is block_on_error.

        Sanity: structural_result must have no errors before we run;
        callers gate us with that check. Inner `data['payload']` must
        be a dict (envelope contract already enforced it). Both are
        asserted defensively because one of these assertions firing
        means we have a logic bug to fix, not a runtime noisy-warning
        case to ignore.
        """
        with self._lock:
            key = self._semantic_key_fn(data)
            if key is None:
                return None, False

            inner_payload = data.get("payload")
            if not isinstance(inner_payload, dict):
                # Structural gate already enforced payload=dict; this is
                # belt-and-suspenders for any future caller path.
                return None, False

            prior_env = self._prior_envelopes.get(key)
            verdict = self._semantic_validator.compare(
                prior_env.get("payload", {}) if prior_env else {},
                inner_payload,
            )
            # Track the window update INSIDE the lock so a parallel
            # dispatch can't read-then-stale-write the prior.
            self._prior_envelopes[key] = data
            if len(self._prior_envelopes) > self._semantic_window_max:
                # O(1) OOO eviction: dicts preserve insertion order;
                # popping next(iter(...)) removes the oldest.
                self._prior_envelopes.pop(next(iter(self._prior_envelopes)))

            self._stats["envelopes_with_semantic_check"] += 1
            self._stats["semantic_findings_count"] += len(verdict.findings)

            # `blocking` here is a VIOLATION marker, not a dispatch gate.
            # Disambiguate: the `envelopes_blocked` stat increments for ANY
            # ERROR-class semantic finding regardless of mode (operator
            # wants visibility into fidelity loss). The `_dispatch` caller
            # uses `self._mode == "block_on_error"` to decide whether to
            # actually skip the dispatch. Previously these were conflated
            # into one AND'd expression, which made `envelopes_blocked`
            # miss the log_only case in tests.
            blocking = bool(verdict.errors())
            return verdict, blocking

    def get_semantic_window_snapshot(self) -> Dict[str, Any]:
        """
        Inspection helper — returns a shallow copy of the sliding window
        keyed by current key. Mostly useful in tests; nowhere on the
        hot path.
        """
        with self._lock:
            return {k: dict(v) for k, v in self._prior_envelopes.items()}

    def _proxy_dispatch(self, sender_id: str, channel: str, data: Any, kind: str):
        if kind == "publish":
            return self._bus.publish(sender_id, channel, data)
        return self._bus.broadcast(sender_id, channel, data)

    def _record_block(self, sender_id: str, channel: str, result: Any):
        """Backwards-compat shim. New code routes through _record_structural_block."""
        return self._record_structural_block(sender_id, channel, result)

    def _record_structural_block(self, sender_id: str, channel: str, result: Any):
        """Append a structural-layer block entry to the validation log."""
        entry: Dict[str, Any] = {
            "timestamp": getattr(result, "timestamp", None),
            "agent_id": sender_id,
            "channel": channel,
            "contract": getattr(result, "contract", None),
            "passed": getattr(result, "passed", None),
            "errors": [f.to_dict() for f in result.errors()],
            "mode": self._mode,
        }
        self._validation_log.append(entry)

        # Bus._log if available — duck-call, swallow AttributeError so
        # the proxy works against bare-stub test buses too.
        try:
            self._bus._log(  # type: ignore[attr-defined]
                "validation_blocked", sender_id, channel,
                f"contract={entry['contract']} passed={entry['passed']} "
                f"errors={len(entry['errors'])} mode={self._mode}",
            )
        except AttributeError:
            pass

        self._log.warning(
            "VALIDATION_BLOCKED agent=%s channel=%s contract=%s errors=%s",
            sender_id, channel, entry["contract"],
            [f.get("code") for f in entry["errors"]],
        )

    def _record_semantic_block(
        self,
        sender_id: str,
        channel: str,
        semantic_verdict: Any,
    ):
        """
        Append a semantic-layer block entry. Distinct from structural
        so an operator can tell at a glance which layer caught the
        failure. Same bus._log duck-call pattern.
        """
        entry: Dict[str, Any] = {
            "timestamp": getattr(semantic_verdict, "timestamp", None),
            "agent_id": sender_id,
            "channel": channel,
            "layer": "semantic",
            "passed": getattr(semantic_verdict, "passed", None),
            "findings": [f.to_dict() for f in semantic_verdict.findings],
            "mode": self._mode,
        }
        self._validation_log.append(entry)

        try:
            self._bus._log(  # type: ignore[attr-defined]
                "semantic_validation_blocked", sender_id, channel,
                f"passed={entry['passed']} findings={len(entry['findings'])} mode={self._mode}",
            )
        except AttributeError:
            pass

        # Pull short codes for the log line so operator grep stays clean.
        codes = [f.get("code") for f in entry["findings"]]
        self._log.warning(
            "SEMANTIC_BLOCKED agent=%s channel=%s findings=%s",
            sender_id, channel, codes,
        )

    # ─── INSPECTION ────────────────────────────────────────────────
    def get_validation_log(self) -> List[Dict[str, Any]]:
        return list(self._validation_log)

    def get_stats(self) -> Dict[str, Union[int, float]]:
        return dict(self._stats)

    # ─── AUTOCLAW GATE (spec §2 hard layer wire-up) ───────────
    def _bound_autoclaw(self) -> Tuple[Any, Any, Any]:
        """Lazy-import autoclaw_validator symbols. Same pattern as
        `_bound_validate` — keeps bus_validator import-independent of
        autoclaw_validator (each module independently testable)."""
        if self._lazy_autoclaw is None:
            from autoclaw_validator import (
                take_snapshot as _take_snapshot,
                ResourceFinding as _ResourceFinding,
                Severity as _Severity,
            )
            self._lazy_autoclaw = (_take_snapshot, _ResourceFinding, _Severity)
        return self._lazy_autoclaw

    def _autoclaw_active(self) -> bool:
        """True iff autoclaw_ceiling is set AND its mode is not OFF."""
        c = self._autoclaw_ceiling
        return c is not None and getattr(c, "mode", "OFF") != "OFF"

    def _autoclaw_preflight_check(
        self, sender_id: str, channel: str, data: Any
    ) -> bool:
        """Take a snapshot, evaluate against ceiling, act on findings.

        Returns True iff HALT-mode BLOCK fired (caller must abort the
        publish). TRACE-mode findings are emitted but never block.
        """
        ceiling = self._autoclaw_ceiling
        take_snapshot, ResourceFinding, Severity = self._bound_autoclaw()
        snap = take_snapshot(wallclock=0.0)
        findings = self._autoclaw_evaluate(snap, ceiling, ResourceFinding, Severity)
        blocking = [
            f for f in findings
            if f.severity in (Severity.BLOCK, Severity.CRITICAL)
        ]
        if not findings:
            return False
        self._stats["autoclaw_preflight_findings"] += len(findings)
        self._stats["autoclaw_findings_total"] += len(findings)
        self._emit_autoclaw_findings(sender_id, channel, findings, phase="preflight")
        if blocking and ceiling.mode == "HALT":
            self._stats["autoclaw_blocks_preflight"] += 1
            self._record_autoclaw_block(sender_id, channel, findings, phase="preflight")
            return True
        return False

    def _autoclaw_postcall_check(
        self, sender_id: str, channel: str, data: Any, kind: str
    ) -> None:
        """Post-call observation. Cannot un-dispatch — record only."""
        ceiling = self._autoclaw_ceiling
        take_snapshot, ResourceFinding, Severity = self._bound_autoclaw()
        elapsed = time.monotonic() - self._dispatch_started_at
        snap = take_snapshot(wallclock=elapsed)
        # Postcall-check uses a threshold variant that also fires on
        # TIME_BUDGET EXCEEDED (because elapsed is real wall-clock).
        findings = self._autoclaw_evaluate(snap, ceiling, ResourceFinding, Severity)
        if elapsed >= ceiling.time_budget_seconds:
            findings.append(ResourceFinding(
                severity=Severity.CRITICAL,
                code="RESOURCE_TIME_BUDGET_EXCEEDED",
                message=(
                    f"wallclock {elapsed:.3f}s >= time budget "
                    f"{ceiling.time_budget_seconds:.3f}s"
                ),
                snapshot=snap,
                detail={
                    "ceiling_time_budget_seconds": ceiling.time_budget_seconds,
                    "observed_wallclock_seconds": elapsed,
                },
            ))
        if not findings:
            return
        self._stats["autoclaw_postcall_findings"] += len(findings)
        self._stats["autoclaw_findings_total"] += len(findings)
        self._emit_autoclaw_findings(sender_id, channel, findings, phase="postcall")
        blocking = [
            f for f in findings
            if f.severity in (Severity.BLOCK, Severity.CRITICAL)
        ]
        if blocking and ceiling.mode == "HALT":
            self._stats["autoclaw_blocks_postcall"] += 1
            self._record_autoclaw_block(sender_id, channel, findings, phase="postcall")

    def _autoclaw_evaluate(
        self,
        snap: Any,
        ceiling: Any,
        ResourceFinding: Any,
        Severity: Any,
    ) -> List[Any]:
        """Compare snapshot against ceiling; return ResourceFinding list.

        Notes:
          * RSS check only (the agent process IS what's being protected).
          * Swap check fires when the agent-process swap use crosses
            the ceiling. Both criticals map to CRITICAL severity so the
            blocking path treats them as halt-worthy.
          * Time-budget check is run from the postcall path (where
            elapsed is meaningful) — not here, where wallclock=0.
          * Token check skipped at the bus level (the bus doesn't
            consume model tokens directly).
        """
        findings: List[Any] = []
        if snap.rss_bytes >= ceiling.rss_block_bytes:
            findings.append(ResourceFinding(
                severity=Severity.CRITICAL,
                code="RESOURCE_RSS_BLOCK_EXCEEDED",
                message=(
                    f"RSS {snap.rss_bytes} >= block ceiling "
                    f"{ceiling.rss_block_bytes} bytes"
                ),
                snapshot=snap,
                detail={
                    "ceiling_rss_block_bytes": ceiling.rss_block_bytes,
                    "observed_rss_bytes": snap.rss_bytes,
                },
            ))
        elif snap.rss_bytes >= ceiling.rss_warn_bytes:
            findings.append(ResourceFinding(
                severity=Severity.WARNING,
                code="RESOURCE_RSS_WARN_APPROACHING",
                message=(
                    f"RSS {snap.rss_bytes} approaching block ceiling "
                    f"({ceiling.rss_warn_bytes} bytes)"
                ),
                snapshot=snap,
            ))
        if (
            getattr(snap, "swap_bytes", 0) > 0
            and snap.swap_bytes >= ceiling.swap_block_bytes
        ):
            findings.append(ResourceFinding(
                severity=Severity.CRITICAL,
                code="RESOURCE_SWAP_BLOCK_EXCEEDED",
                message=(
                    f"swap {snap.swap_bytes} >= block ceiling "
                    f"{ceiling.swap_block_bytes} bytes"
                ),
                snapshot=snap,
            ))
        return findings

    def _emit_autoclaw_findings(
        self,
        sender_id: str,
        channel: str,
        findings: List[Any],
        phase: str,
    ) -> None:
        """Best-effort crash_log emit for each finding."""
        try:
            import crash_log  # type: ignore
            for f in findings:
                crash_log.step(
                    "bus.autoclaw.finding",
                    phase=phase,
                    sender_id=sender_id,
                    channel=channel,
                    code=f.code,
                    severity=f.severity.value,
                    message=f.message,
                    snapshot_rss_bytes=getattr(
                        getattr(f, "snapshot", None), "rss_bytes", None
                    ),
                )
        except ImportError:
            pass
        except Exception:
            # crash_log NEVER raises; but be belt-and-suspenders.
            pass

    def _record_autoclaw_block(
        self,
        sender_id: str,
        channel: str,
        findings: List[Any],
        phase: str,
    ) -> None:
        """Append autoclaw-block entry to validation_log + bus._log hook."""
        entry: Dict[str, Any] = {
            "timestamp": time.time(),
            "agent_id": sender_id,
            "channel": channel,
            "layer": "autoclaw",
            "phase": phase,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity.value,
                    "message": f.message,
                }
                for f in findings
            ],
        }
        self._validation_log.append(entry)
        summary = (
            f"phase={phase} findings={len(findings)} "
            f"codes={[f.code for f in findings]}"
        )
        try:
            self._bus._log(  # type: ignore[attr-defined]
                "autoclaw_blocked", sender_id, channel, summary,
            )
        except AttributeError:
            pass
        self._log.warning(
            "AUTOCLAW_BLOCKED agent=%s channel=%s %s",
            sender_id, channel, summary,
        )


    # ─── LONG-LIVED RESOURCE MONITOR (spec §2 — opt-in) ─────
    def __enter__(self) -> "ValidatingBusProxy":
        """ContextManager entry: if long_lived_monitor=True AND an
        autoclaw_ceiling is configured, start the bg-thread monitor so
        it ticks between dispatch events.

        Use:
            with ValidatingBusProxy(
                bus, autoclaw_ceiling=ceiling, long_lived_monitor=True,
            ) as proxy:
                proxy.publish(...)
        """
        if (
            self._long_lived_monitor
            and self._autoclaw_ceiling is not None
        ):
            self._start_long_lived_monitor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """ContextManager exit: stop the bg-thread monitor cleanly.
        Returns False so caller exceptions still propagate."""
        if self._monitor is not None:
            self._monitor.stop()
        # Final drain so last-second findings show up in stats.
        self._drain_monitor_queue(phase="finalize")
        return False

    def _start_long_lived_monitor(self) -> None:
        """Instantiate a ResourceMonitor fed into _monitor_queue.
        Idempotent — second call is a no-op."""
        if self._monitor is not None:
            return
        from autoclaw_validator import ResourceMonitor

        def _on_findings(findings: List[Any]) -> None:
            """Best-effort crash_log emission per monitor poll.
            Queue push is handled by ResourceMonitor itself when
            out_queue= is supplied alongside on_findings=.
            """
            try:
                import crash_log  # type: ignore
            except ImportError:
                return
            for f in findings:
                try:
                    crash_log.step(
                        "bus.autoclaw.monitor",
                        code=getattr(f, "code", None),
                        severity=getattr(getattr(f, "severity", None), "value", None),
                        message=getattr(f, "message", None),
                    )
                except Exception:
                    pass  # monitor callback must NOT raise

        self._monitor = ResourceMonitor(
            ceiling=self._autoclaw_ceiling,
            on_findings=_on_findings,
            out_queue=self._monitor_queue,
        )
        self._monitor.start()

    def _drain_monitor_queue(self, phase: str = "preflight") -> int:
        """Pop every queued ResourceFinding and tally to stats.

        The monitor thread emits findings directly to crash_log via
        its on_findings callback; this drain only counts them here.
        Findings are PROCESS-level events, not per-message; pinning
        them to a specific envelope would be noise.

        Phase values:
          * "preflight" — drain at dispatch entry. Findings emitted
            between calls show up here.
          * "postcall"  — drain after dispatch. Counted in
            monitor_observed_during_dispatch (proves the long-lived
            path caught something the per-call fence missed).
          * "finalize"  — drain on __exit__. Final accounting.
        """
        if not self._long_lived_monitor or self._monitor_queue is None:
            return 0
        count = 0
        while True:
            try:
                self._monitor_queue.get_nowait()
            except Exception:
                break
            count += 1
        self._stats["monitor_findings_total"] += count
        if phase == "postcall":
            self._stats["monitor_observed_during_dispatch"] += count
        if self._monitor is not None:
            # Mirror the monitor's own counters so get_stats() is the
            # single inspection point. Ticks_total / last_poll_at
            # are intrinsically the monitor's, not ours.
            self._stats["monitor_ticks_total"] = self._monitor.ticks_total
            self._stats["monitor_last_poll_at"] = self._monitor.last_poll_at
        return count


def validate_bus_event_line(line: Any) -> Optional[Any]:
    """Validate one JSONL bus event line if it's a SwarmMessage envelope.

    Returns
    -------
    ValidationResult if `line` is a dict that looks like a SwarmMessage;
    None if not.

    Use from a JSONL replay tool:
        for line in open('hub/state/event_log.jsonl'):
            d = json.loads(line)
            result = validate_bus_event_line(d)
            if result is not None and not result.passed:
                print(f"OH NO: {result.contract} failed: {result.findings}")
    """
    if not is_swarm_envelope(line):
        return None
    from handoff_validator import validate_swarm_message
    return validate_swarm_message(line)


# ════════════════════════════════════════════════════════════════
# SMOKE
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("bus_validator skeleton loaded.")
    print(f"  modes: {VALID_MODES}")
    print(f"  envelope-required keys: {sorted(SWARM_ENVELOPE_REQUIRED_KEYS)}")
    print(
        "  class: ValidatingBusProxy (subscribe/unsubscribe forwarded, "
        "publish/broadcast gated)"
    )
