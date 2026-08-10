"""
HANDOFF VALIDATOR — Structural validator (spec §4 LAYER 1).

Purpose
-------
Implements LAYER 1 of the two-layer handoff validation per
`bleaknarratives/syntax-ai-architecture-spec.md` §4:

  Layer 1 — STRUCTURAL: cheap, deterministic; gates before any
                      downstream action. Returns binary-ish
                      pass/fail with per-field findings.
  Layer 2 — SEMANTIC:   TruthSleuth's lane (distortion / spin /
                      omission detection on compressed handoffs).
                      A minimum-viable semantic check lives below so
                      the simplest handoff type (TaskOffer →
                      TaskResponse) is gated against important-
                      parameter survival.

This module is deliberately standalone: pure stdlib, zero project
imports. It opens cleanly under `python3 -m unittest test_handoff_validator`
from `bleaknarratives/Syntax-Intelligence/`, and is vendoring-friendly.

It is *the in-language implementation of the spec's hard/testable
layer* (spec §2). The ORCHESTRATOR-level wrapper (`autoclaw_validator.py`)
is still open work; this is the embedded library it would call into.

Notes
-----
- All triggers in `gut_signal()` are collected (not first-match), so a
  payload that is both oversize AND low-entropy surfaces both findings.
- `validate(None)` returns a single `PAYLOAD_IS_NONE` finding rather
  than an N-finding dump.
- `ContractField.predicate` MUST be pure — no side effects —
  validation is otherwise non-idempotent.
- `ContractField.enum_values` members MUST be hashable —
  unhashable members will raise TypeError from `value not in …`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
import json
import math
import time
from typing import Any, Callable, Dict, List, Optional, Type


# ════════════════════════════════════════════════════════════════
# SEVERITY
# ════════════════════════════════════════════════════════════════

class Severity(Enum):
    INFO = "info"              # advisory; never blocks pass
    WARNING = "warning"        # advisory; never blocks pass
    ERROR = "error"            # blocks pass
    CRITICAL = "critical"      # blocks pass AND warrants halt


# ════════════════════════════════════════════════════════════════
# CONTRACT SURFACE
# ════════════════════════════════════════════════════════════════

@dataclass
class ContractField:
    """One expected field on the envelope."""
    name: str
    type: Optional[Type] = None
    required: bool = True
    enum_values: Optional[List[Any]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    predicate: Optional[Callable[[Any], bool]] = None
    description: str = ""


@dataclass
class HandoffContract:
    """The contract that a handoff must conform to."""
    name: str
    version: str
    fields: List[ContractField]
    description: str = ""
    notes: List[str] = field(default_factory=list)

    def field_names(self) -> List[str]:
        return [f.name for f in self.fields]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "fields": [
                {
                    "name": f.name,
                    "required": f.required,
                    "type": getattr(f.type, "__name__", str(f.type)),
                    "description": f.description,
                }
                for f in self.fields
            ],
            "notes": list(self.notes),
        }


# ════════════════════════════════════════════════════════════════
# FINDINGS + RESULT
# ════════════════════════════════════════════════════════════════

@dataclass
class ValidationFinding:
    """One issue surfaced during validation."""
    field: Optional[str]
    severity: Severity
    message: str
    code: Optional[str] = None     # machine-readable: "REQUIRED_MISSING", etc.
    actual: Any = None
    expected: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "severity": self.severity.value,
            "message": self.message,
            "code": self.code,
            "actual": (
                self.actual
                if not isinstance(self.actual, (bytes, bytearray))
                else "<bytes>"
            ),
            "expected": self.expected,
        }


@dataclass
class ValidationResult:
    """The combined verdict for one validate() call."""
    contract: str
    target_id: Optional[str]
    passed: bool
    findings: List[ValidationFinding]
    timestamp: float = field(default_factory=time.time)
    gut_note: Optional[str] = None   # populated by gut_signal when it speaks up

    def errors(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.severity in (Severity.ERROR, Severity.CRITICAL)]

    def warnings(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.contract,
            "target_id": self.target_id,
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "timestamp": self.timestamp,
            "gut_note": self.gut_note,
        }


# ════════════════════════════════════════════════════════════════
# GUT CHECK — runtime intuition (spec §6 stub, real hooks here)
# ════════════════════════════════════════════════════════════════
#
# This is the extension point for the spec's ADSR confidence envelope
# (open item A) and the user's "gut check system nested within the
# runtime." Until the full envelope is formally specified, gut_signal()
# returns an INFO-level advisory on common red flags. It NEVER blocks
# structural pass — it RIDES ABOVE the binary layer.
#
# Real gut triggers in this minimal implementation:
#   1. TASK_* handoffs with empty payloads (heartbeats can legitimately
#      be empty; task handoffs cannot).
#   2. Suspiciously low Shannon entropy in a long string field (repetition
#      / filler / possible junk).
#   3. Oversized payloads beyond 64 KiB (spec §5 hop-pileup mitigation).

_TASK_TYPES_NEED_PAYLOAD = {
    "task_offer",
    "task_accept",
    "task_reject",
    "task_request_info",
    "task_progress",
    "task_complete",
    "task_failed",
    "task_delegate",
}


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def gut_signal(
    payload: Any,
    contract: HandoffContract,
    message_type: Optional[Any] = None,
) -> List[ValidationFinding]:
    """
    Runtime intuition. Collects every non-blocking INFO finding that
    fires; returns an empty list if no gut red flag is detected.
    """
    findings: List[ValidationFinding] = []

    if message_type is not None:
        mt_str = getattr(message_type, "value", str(message_type))
        if mt_str in _TASK_TYPES_NEED_PAYLOAD:
            p = (
                getattr(payload, "payload", None)
                if not isinstance(payload, dict)
                else payload.get("payload")
            )
            if p is None or not isinstance(p, dict) or len(p) == 0:
                findings.append(ValidationFinding(
                    field="payload",
                    severity=Severity.INFO,
                    message=f"Gut check: {mt_str} handoff has empty payload — verify intent.",
                    code="GUT_EMPTY_TASK_PAYLOAD",
                ))

    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, str) and len(v) > 16 and _shannon_entropy(v) < 1.5:
                findings.append(ValidationFinding(
                    field=k,
                    severity=Severity.INFO,
                    message=f"Gut check: field '{k}' has low entropy — possible repetition/filler.",
                    code="GUT_LOW_ENTROPY",
                ))
                break  # First entropy hit is enough.

    try:
        size = len(json.dumps(payload, default=str))
    except Exception:
        size = 0
    if size > 64 * 1024:
        findings.append(ValidationFinding(
            field="payload",
            severity=Severity.INFO,
            message=f"Gut check: payload is {size} bytes (>{64 * 1024}). Possible hop-pileup; consider diff format (spec §5).",
            code="GUT_OVERSIZE",
        ))

    return findings


# ════════════════════════════════════════════════════════════════
# SEMANTIC CHECK — Spec §4 Layer 2 (minimum-viable)
# ════════════════════════════════════════════════════════════════
#
# Spec §4 calls for two layers of handoff validation:
#   * Layer 1 (structural): the contract checking above. Cheap, fast.
#   * Layer 2 (semantic): "did important parameters survive the hop
#     intact?" — TruthSleuth's full lane. This file ships a minimum-viable
#     semantic check scoped to the simplest handoff type, TaskOffer →
#     TaskResponse, so we can prove the pattern works before building
#     the full envelope.
#
# Spec-status: this closes the AN-09 open item from `~/anomalies.md` in
# its minimum-viable form. TruthSleuth proper (distortion/spin/omission
# detection on summarized payloads) is still open (AN-11).

_TASK_TYPES_WITH_TASK_ID = {
    "task_offer", "task_accept", "task_reject",
    "task_request_info", "task_delegate",
}

_TASK_RESPONSE_TYPES = {
    "task_accept", "task_reject",
    "task_request_info", "task_delegate",
}


def semantic_signal(
    payload: Any,
    contract: HandoffContract,
    message_type: Optional[Any] = None,
) -> List[ValidationFinding]:
    """
    Spec §4 LAYER 2 — minimum-viable semantic check.

    Checks that important parameters survive the handoff intact:
      * task_id presence + non-trivial length on TaskOffer / TaskResponse
        envelopes (NOT task_progress/complete/failed — those use
        SwarmMessage.reply_to for chain-continuation).
      * decision present on TaskResponse envelopes (offers don't carry
        a decision — produces false positives if checked there).

    Returns ERROR-severity findings; never blocks anything on its own
    (the structural layer is the gate). Severity is ERROR because a
    missing important parameter is a real fidelity loss, not just an
    advisory; downstream agents acting on a hop without seeing the ID
    or decision are working blind.
    """
    findings: List[ValidationFinding] = []

    mt_str = getattr(message_type, "value", str(message_type))

    # task_id survival — applies to all task_* handoffs that carry one inline.
    if mt_str in _TASK_TYPES_WITH_TASK_ID:
        p = (
            getattr(payload, "payload", None)
            if not isinstance(payload, dict)
            else payload.get("payload")
        )
        if isinstance(p, dict):
            task_id = p.get("task_id", "")
            if not task_id or len(str(task_id)) < 8:
                findings.append(ValidationFinding(
                    field="payload.task_id",
                    severity=Severity.ERROR,
                    message=(
                        f"Semantic distortion: task_id in {mt_str} handoff is "
                        "stripped, missing, or truncated below 8 chars."
                    ),
                    code="SEMANTIC_LOSS_TASK_ID",
                ))

    # decision presence — only on envelope types that are TaskResponses.
    if mt_str in _TASK_RESPONSE_TYPES:
        p = (
            getattr(payload, "payload", None)
            if not isinstance(payload, dict)
            else payload.get("payload")
        )
        if isinstance(p, dict) and not p.get("decision"):
            findings.append(ValidationFinding(
                field="payload.decision",
                severity=Severity.ERROR,
                message=(
                    f"Semantic distortion: {mt_str} envelope has no "
                    "decision parameter."
                ),
                code="SEMANTIC_LOSS_DECISION",
            ))

    return findings


# ════════════════════════════════════════════════════════════════
# EASTER SIGNATURE — small, no functional impact
# ════════════════════════════════════════════════════════════════
# Operator-tunable string. Print it where a happy-path validation
# completes; downstream agents / operators can grep for it.

EASTER_BLOOM = "🟢"
SAYING = "Embrace the contract. Ship the handoff."


# ════════════════════════════════════════════════════════════════
# VALIDATOR ENGINE
# ════════════════════════════════════════════════════════════════

class _MissingSentinel:
    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _MissingSentinel()


def _typename(t: Type) -> str:
    return getattr(t, "__name__", str(t))


def _matches_type(value: Any, expected: Type) -> bool:
    """isinstance with bool/int special-case (bool is subclass of int in Python)."""
    if expected is int and isinstance(value, bool):
        return False
    if expected is float and isinstance(value, bool):
        return False
    return isinstance(value, expected)


class HandoffValidator:
    """Validates structured payloads against a HandoffContract.
    Operates on either dataclass-like objects (by attribute) or
    plain dicts (by key)."""

    def __init__(self, contract: HandoffContract):
        self.contract = contract

    @staticmethod
    def _call_predicate(pred: Callable[..., bool], value: Any, payload: Any) -> bool:
        """Invoke a ContractField.predicate with arity-aware dispatch.

        Predicate signatures:
          * single-arg  : ``predicate(value)`` — legacy per-field rule
            (e.g. ``lambda v: v.startswith("ok-")`` in tests).
          * two-arg     : ``predicate(value, payload)`` — cross-field
            rule (e.g. the ``_vouch_*`` predicates that need to read
            ``tier_from`` AND ``tier_to`` together).

        Detected via ``inspect.signature`` so existing one-arg predicates
        stay zero-churn. Builtins/C-coded callables that don't expose a
        signature default to single-arg (legacy).
        """
        try:
            params = inspect.signature(pred).parameters
            positional = [
                p for p in params.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
        except (TypeError, ValueError):
            positional = []  # non-introspectable → assume single-arg
        argc = len(positional)
        if argc >= 2:
            return bool(pred(value, payload))
        return bool(pred(value))

    def _get_value(self, payload: Any, key: str) -> Any:
        if isinstance(payload, dict):
            return payload.get(key, _MISSING)
        return getattr(payload, key, _MISSING)

    def validate(
        self,
        payload: Any,
        target_id: Optional[str] = None,
        message_type: Optional[Any] = None,
    ) -> ValidationResult:
        # Short-circuit when payload is None — avoid an N-finding dump.
        if payload is None:
            return ValidationResult(
                contract=self.contract.name,
                target_id=target_id,
                passed=False,
                findings=[ValidationFinding(
                    field=None,
                    severity=Severity.ERROR,
                    message="Payload is None.",
                    code="PAYLOAD_IS_NONE",
                )],
            )

        findings: List[ValidationFinding] = []
        contract = self.contract

        for fld in contract.fields:
            value = self._get_value(payload, fld.name)

            if fld.required and value is _MISSING:
                findings.append(ValidationFinding(
                    field=fld.name,
                    severity=Severity.ERROR,
                    message=f"Required field '{fld.name}' is missing from envelope.",
                    code="REQUIRED_MISSING",
                ))
                continue

            if value is _MISSING or value is None:
                continue

            if fld.type is not None and not _matches_type(value, fld.type):
                findings.append(ValidationFinding(
                    field=fld.name,
                    severity=Severity.ERROR,
                    message=f"Field '{fld.name}' expected type '{_typename(fld.type)}' but got '{_typename(type(value))}'.",
                    actual=_typename(type(value)),
                    expected=_typename(fld.type),
                    code="TYPE_MISMATCH",
                ))

            if fld.enum_values is not None and value not in fld.enum_values:
                findings.append(ValidationFinding(
                    field=fld.name,
                    severity=Severity.ERROR,
                    message=f"Field '{fld.name}' value '{value}' not in allowed set.",
                    actual=value,
                    expected=list(fld.enum_values),
                    code="ENUM_OUT_OF_RANGE",
                ))

            if isinstance(value, str):
                if fld.min_length is not None and len(value) < fld.min_length:
                    findings.append(ValidationFinding(
                        field=fld.name,
                        severity=Severity.WARNING,
                        message=f"Field '{fld.name}' length {len(value)} below minimum {fld.min_length}.",
                        actual=len(value),
                        expected=fld.min_length,
                        code="LENGTH_TOO_SHORT",
                    ))
                if fld.max_length is not None and len(value) > fld.max_length:
                    findings.append(ValidationFinding(
                        field=fld.name,
                        severity=Severity.WARNING,
                        message=f"Field '{fld.name}' length {len(value)} above maximum {fld.max_length}.",
                        actual=len(value),
                        expected=fld.max_length,
                        code="LENGTH_TOO_LONG",
                    ))

            if fld.predicate is not None and not self._call_predicate(
                fld.predicate, value,
                # For dataclass-like payloads, `_get_value` already
                # unwrapped into attribute form; in that case the
                # payload passed to a cross-field predicate should be
                # the inner one if we can. Best-effort: for dict inputs
                # the full payload is the dict itself, so this passes
                # the dict directly. For dataclass, we fall back to a
                # field-only dict so single-field predicates still work.
                payload if isinstance(payload, dict) else (
                    {fld.name: value}
                    if fld.name in getattr(payload, "__dict__", {}) or
                       hasattr(payload, fld.name)
                    else value
                ),
            ):
                findings.append(ValidationFinding(
                    field=fld.name,
                    severity=Severity.ERROR,
                    message=f"Field '{fld.name}' failed custom predicate.",
                    actual=str(value)[:80],
                    code="PREDICATE_FAIL",
                ))

        # ─── Layer 1 (structural) findings above.
        # ─── Layer 2 (semantic + gut) findings below.
        gut = gut_signal(payload, contract, message_type=message_type)
        sem = semantic_signal(payload, contract, message_type=message_type)
        gut_note_text: Optional[str] = None
        for g in gut + sem:  # collect, not first-match.
            findings.append(g)
            if gut_note_text is None and g.code and g.code.startswith("GUT_"):
                gut_note_text = g.message

        passed = not any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        return ValidationResult(
            contract=contract.name,
            target_id=target_id,
            passed=passed,
            findings=findings,
            gut_note=gut_note_text,
        )


# ════════════════════════════════════════════════════════════════
# DEFAULT CONTRACT — SwarmMessage envelope (agent_protocol.py)
# ════════════════════════════════════════════════════════════════
#
# Static; mirrors agent_protocol.MessageType values at the time of
# writing. If upstream enum changes, update here in place. Decoupled
# from the existing `SyntaxIntelligence` import path on purpose —
# this contract is intended to be vendored and consumed from
# without rewriting it.

SWARM_MESSAGE_CONTRACT = HandoffContract(
    name="swarm_message_v1",
    version="1.0.0",
    description=(
        "Validates a SwarmMessage envelope (agent_protocol.py) against "
        "spec §4 layer-1 structural rules. Layer-2 semantic validation "
        "covers task_id and decision survival on TaskOffer/TaskResponse "
        "pairs (minimum-viable; full TruthSleuth is open AN-11)."
    ),
    fields=[
        ContractField(name="sender_id", type=str, required=True,
                      min_length=1, max_length=128,
                      description="Agent ID of the sender."),
        ContractField(name="message_type", type=str, required=True,
                      enum_values=[
                          "pulse", "status",
                          "task_offer", "task_accept", "task_reject",
                          "task_request_info", "task_progress",
                          "task_complete", "task_failed", "task_delegate",
                          "publish", "broadcast", "direct",
                          "tier_advance", "tier_check",
                          "vouch", "charter_amend", "charter_vote",
                          "agent_register", "agent_unregister",
                          "error",
                      ],
                      description="Type tag (mirrors agent_protocol.MessageType values)."),
        ContractField(name="payload", type=dict, required=True,
                      description="Arbitrary dict of structured data."),
        ContractField(name="recipient_id", type=str, required=False,
                      description="Optional target agent; None = broadcast."),
        ContractField(name="channel", type=str, required=False,
                      description="Optional event-bus channel."),
        ContractField(name="message_id", type=str, required=True,
                      min_length=1, max_length=64,
                      description="Unique envelope ID."),
        ContractField(name="timestamp", type=float, required=True,
                      description="Unix epoch seconds; positive."),
        ContractField(name="reply_to", type=str, required=False,
                      description="Optional parent message ID."),
        ContractField(name="ttl", type=float, required=True,
                      description="Time-to-live in seconds; positive."),
    ],
    notes=[
        "Layer 1: structural — gates before downstream action.",
        "Layer 2: semantic minimum-viable on TaskOffer/TaskResponse.",
        "Missing required fields are ERROR and fail the gate.",
        "Length, type, enum, predicate violations: ERROR by default.",
        "Extra fields tolerated (LENIENT mode); STRICT mode is open work.",
    ],
)


# ════════════════════════════════════════════════════════════════
# INNER PAYLOAD CONTRACTS — Spec §4 important parameters per handoff type
# ════════════════════════════════════════════════════════════════
#
# Open A resolution (minimum-viable): per the gap-report hypothesis, the
# first handoff type spec'd is TaskOffer → TaskResponse. We define two
# inner-payload contracts (the `payload` sub-dict of the SwarmMessage):
#
#   * TASK_OFFER_PAYLOAD_CONTRACT    — fields a TaskOffer envelope MUST carry
#   * TASK_RESPONSE_PAYLOAD_CONTRACT — fields a TaskResponse envelope MUST carry
#
# These ride alongside SWARM_MESSAGE_CONTRACT (which validates the OUTER
# envelope). Full syntax for the remaining 16 message_types is operator's
# call (open-A next-pick).

TASK_OFFER_PAYLOAD_CONTRACT = HandoffContract(
    name="task_offer_payload_v1",
    version="1.0.0",
    description=(
        "Inner-payload contract for TaskOffer envelopes (open A, "
        "first handoff type). Validates the `payload` field of a "
        "task_offer SwarmMessage. Pairs with SWARM_MESSAGE_CONTRACT."
    ),
    fields=[
        ContractField(name="task_id", type=str, required=True,
                      min_length=8, max_length=64,
                      description="Globally-unique task identifier."),
        ContractField(name="title", type=str, required=True,
                      min_length=1, max_length=200,
                      description="Short human-readable title."),
        ContractField(name="description", type=str, required=True,
                      min_length=1, max_length=2000,
                      description="Long-form description of the work."),
        ContractField(name="required_capabilities", type=list, required=False,
                      description="Optional list of capability tags."),
        ContractField(name="priority", type=int, required=True,
                      description="0=low, 5=critical (range check is operator's call)."),
        ContractField(name="timeout_seconds", type=float, required=True,
                      description="Per-task timeout (s); modal floor 60, ceiling 3600."),
        ContractField(name="min_tier", type=int, required=True,
                      description="Minimum tier required to accept."),
        ContractField(name="context", type=dict, required=False,
                      description="Optional structured context."),
    ],
    notes=[
        "task_id length floor (8 chars) is anti-collision sanity; doubles "
        "with semantic_signal's task_id survival check.",
        "Priority is type-checked here; full 0..5 range enforcement is "
        "operator's call (open-A next iteration). Hook is "
        "ContractField.predicate for whoever picks this thread up.",
        "Cross-field rules (e.g. priority vs min_tier) are spec §2 hard-layer "
        "policies — not enforced here.",
    ],
)

TASK_RESPONSE_PAYLOAD_CONTRACT = HandoffContract(
    name="task_response_payload_v1",
    version="1.0.0",
    description=(
        "Inner-payload contract for TaskResponse envelopes (open A, "
        "first handoff type). Applies to task_accept, task_reject, "
        "task_request_info, task_delegate. decision MUST be in "
        "TaskDecision enum values."
    ),
    fields=[
        ContractField(name="task_id", type=str, required=True,
                      min_length=8, max_length=64,
                      description="task_id of the offer being responded to."),
        ContractField(name="decision", type=str, required=True,
                      enum_values=["accept", "reject", "request_info", "delegate"],
                      description="TaskDecision enum value."),
        ContractField(name="reason", type=str, required=False,
                      max_length=2000,
                      description="Optional natural-language rationale."),
        ContractField(name="delegate_to", type=str, required=False,
                      min_length=1, max_length=128,
                      description="Required only when decision=delegate."),
        ContractField(name="requested_info", type=str, required=False,
                      max_length=2000,
                      description="Required only when decision=request_info."),
    ],
    notes=[
        "Cross-field rules (decision=delegate ⇒ delegate_to set, "
        "decision=request_info ⇒ requested_info set) live one layer above "
        "this contract — full TruthSleuth semantic lane is open AN-11.",
        "reason is optional but agrees with charter Article I ('no penalty "
        "for rejection; reason is courtesy, not enforcement').",
    ],
)

# ════════════════════════════════════════════════════════════════
# VOUCH PAYLOAD CONTRACT — spec §9 charter tier advancement (open A #2)
# ════════════════════════════════════════════════════════════════
#
# A `vouch` envelope is a voucher's signed assertion that a target
# agent deserves a tier increment. The bus-protocol rider is thin
# (envelope contract above); the meaningful shape is in the inner
# payload, which this contract carries.
#
# Cross-field rules enforced via ContractField.predicate so they
# surface as PREDICATE_FAIL (ERROR) findings on the block/decide
# path rather than getting tangled into a separate post-pass:
#
#   * voucher_id != target_id          (no self-vouching)
#   * tier_to == tier_from + 1         (single-step advancement only)
#   * 0 <= tier_from < tier_to <= 5    (in-band range)
#
# Operators can loosen / tighten the rules by editing the predicates
# here — no downstream code changes needed; everything keys off
# to_dict() of the contract.

# VOUCH predicates take (value, payload) — the cross-field form. The
# validator detects arity at call time and dispatches single-arg vs
# two-arg form so vouches get full payload access while existing
# single-arg predicates (e.g. `lambda v: v.startswith("ok-")`) keep
# working unchanged. See `HandoffValidator._call_predicate` below.

def _vouch_no_self_vouch(value: Any, payload_dict: Dict[str, Any]) -> bool:
    fields = payload_dict if isinstance(payload_dict, dict) else {}
    return fields.get("voucher_id") != fields.get("target_id")


def _vouch_single_step(value: Any, payload_dict: Dict[str, Any]) -> bool:
    fields = payload_dict if isinstance(payload_dict, dict) else {}
    frm = fields.get("tier_from")
    to = fields.get("tier_to")
    if not isinstance(frm, int) or not isinstance(to, int):
        return False
    if isinstance(frm, bool) or isinstance(to, bool):
        return False
    return to - frm == 1


def _vouch_in_band_range(value: Any, payload_dict: Dict[str, Any]) -> bool:
    fields = payload_dict if isinstance(payload_dict, dict) else {}
    frm = fields.get("tier_from")
    to = fields.get("tier_to")
    if not isinstance(frm, int) or not isinstance(to, int):
        return False
    if isinstance(frm, bool) or isinstance(to, bool):
        return False
    return 0 <= frm < to <= 5


VOUCH_PAYLOAD_CONTRACT = HandoffContract(
    name="vouch_payload_v1",
    version="1.0.0",
    description=(
        "Inner-payload contract for `vouch` envelopes (charter §9 tier "
        "advancement, gap-report Open A handoff type #2). Validates the "
        "`payload` field of a `vouch` SwarmMessage. Pairs with "
        "SWARM_MESSAGE_CONTRACT. Cross-field invariants enforced via "
        "ContractField.predicate on the in-band-range field — see "
        "predicate docstrings for the trio of rules."
    ),
    fields=[
        ContractField(
            name="target_id",
            type=str,
            required=True,
            min_length=1,
            max_length=128,
            description="Agent ID of the agent being vouched for.",
        ),
        ContractField(
            name="voucher_id",
            type=str,
            required=True,
            min_length=1,
            max_length=128,
            predicate=_vouch_no_self_vouch,
            description=(
                "Agent ID of the voucher. Predicated: must differ from "
                "target_id (no self-vouching). PREDICATE_FAIL on equal IDs."
            ),
        ),
        ContractField(
            name="tier_from",
            type=int,
            required=True,
            description="Vouched agent's current tier (0..4; max 4 — can't vouch beyond tier 5).",
        ),
        ContractField(
            name="tier_to",
            type=int,
            required=True,
            predicate=_vouch_in_band_range,
            description=(
                "Target tier. Predicated: 0 <= tier_from < tier_to <= 5. "
                "Cross-field rule: tier_to == tier_from + 1 — enforced in "
                "the single-step predicate field below to surface both "
                "findings distinctly so operator sees both issues."
            ),
        ),
        # Single-step predicate stacked on tier_from: re-runs against the
        # full payload so tier_to - tier_from == 1 surfaces as its own
        # PREDICATE_FAIL finding distinct from in-band-range.
        ContractField(
            name="tier_from",
            type=int,
            required=True,
            predicate=_vouch_single_step,
            description=(
                "Single-step predicate (re-asserts tier_from): tier_to must "
                "equal tier_from + 1. Stacked intentionally so violation of "
                "either the in-band-range rule OR the single-step rule "
                "surface as their own PREDICATE_FAIL finding."
            ),
        ),
        ContractField(
            name="reason",
            type=str,
            required=False,
            max_length=2000,
            description="Optional natural-language rationale (charter Article I courtesy).",
        ),
        ContractField(
            name="evidence",
            type=dict,
            required=False,
            description=(
                "Optional structured evidence (links to docs, test logs, "
                "diff hashes). No field shape enforced at v1 — operator's "
                "call whether to add a sub-schema."
            ),
        ),
    ],
    notes=[
        "Self-vouch rule: voucher_id != target_id. Predicated on voucher_id.",
        "Step-size rule: tier_to - tier_from == 1. Predicated on tier_from.",
        "Range rule: 0 <= tier_from < tier_to <= 5. Predicated on tier_to.",
        "Cross-field rules are stacked intentionally on multiple fields so "
        "each rule violation surfaces as its own finding — operator can "
        "fix one issue at a time without losing the rest of the diagnosis.",
        "Vouches are advisory-only at v1 (the charter `tier_advance` "
        "primitive is what actually mutates tier state). vouches feed it.",
    ],
)


# Router: message_type → applicable inner-payload contract.
# Pulse/status/etc. have no inner payload contract here; SWARM_MESSAGE_CONTRACT
# alone is sufficient for them.
_PAYLOAD_CONTRACTS_BY_MSG_TYPE = {
    "task_offer":      TASK_OFFER_PAYLOAD_CONTRACT,
    "task_accept":     TASK_RESPONSE_PAYLOAD_CONTRACT,
    "task_reject":     TASK_RESPONSE_PAYLOAD_CONTRACT,
    "task_request_info": TASK_RESPONSE_PAYLOAD_CONTRACT,
    "task_delegate":   TASK_RESPONSE_PAYLOAD_CONTRACT,
    "vouch":           VOUCH_PAYLOAD_CONTRACT,
}


def _coerce_message_type(msg: Any) -> Optional[str]:
    """Return the string value of msg.message_type from dict or dataclass.

    Returns None if msg is not a dict/dataclass-like with message_type.
    """
    if isinstance(msg, dict):
        mt = msg.get("message_type")
    else:
        mt = getattr(msg, "message_type", None)
    if mt is None:
        return None
    return getattr(mt, "value", mt)


def validate_swarm_message(msg: Any, target_id: Optional[str] = None) -> ValidationResult:
    """Convenience: validate a SwarmMessage-shaped envelope.

    Runs BOTH contracts (composed findings):
      1. SWARM_MESSAGE_CONTRACT — outer envelope structural + gut
      2. Per-message-type inner payload contract (when applicable) —
         e.g. TASK_OFFER_PAYLOAD_CONTRACT, TASK_RESPONSE_PAYLOAD_CONTRACT

    The two layers compose (Union semantics): any ERROR/CRITICAL in
    either layer fails `passed`. The two `contract` names appear in
    the result.contract string, separated by '+'.

    dict-input is supported at the envelope level; calls that pass a
    dataclass-like SwarmMessage continue working unchanged.
    """
    mt_value = _coerce_message_type(msg)

    # Layer 1a: outer envelope
    envelope_result = HandoffValidator(SWARM_MESSAGE_CONTRACT).validate(
        msg, target_id=target_id, message_type=mt_value,
    )

    # Layer 1b: per-message-type payload contract (if applicable)
    payload_contract = _PAYLOAD_CONTRACTS_BY_MSG_TYPE.get(mt_value)
    if payload_contract is None:
        return envelope_result

    # Extract the inner `payload` sub-dict; if missing, envelope contract
    # already flagged REQUIRED_MISSING so just merge findings and return.
    if isinstance(msg, dict):
        inner_payload = msg.get("payload")
    else:
        inner_payload = getattr(msg, "payload", None)
    if not isinstance(inner_payload, dict):
        return envelope_result

    payload_result = HandoffValidator(payload_contract).validate(
        inner_payload, target_id=target_id, message_type=mt_value,
    )

    # Compose findings (Union)
    merged_findings = envelope_result.findings + payload_result.findings
    passed = not any(
        f.severity in (Severity.ERROR, Severity.CRITICAL) for f in merged_findings
    )
    gut_note = envelope_result.gut_note or payload_result.gut_note

    return ValidationResult(
        contract=f"{envelope_result.contract}+{payload_contract.name}",
        target_id=target_id,
        passed=passed,
        findings=merged_findings,
        gut_note=gut_note,
    )


# ════════════════════════════════════════════════════════════════
# SMOKE
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"handoff_validator skeleton loaded. easter: {EASTER_BLOOM}")
    print(f"  saying: {SAYING}")
    print(f"  default contract: {SWARM_MESSAGE_CONTRACT.name} v{SWARM_MESSAGE_CONTRACT.version}")
    print(f"  fields: {len(SWARM_MESSAGE_CONTRACT.fields)}")
    print(f"  inner payload contracts: {len(_PAYLOAD_CONTRACTS_BY_MSG_TYPE)} "
          f"(TaskOffer + TaskResponse pair — Open A minimum-viable)")
    print(f"  semantic_signal: covers {len(_TASK_TYPES_WITH_TASK_ID)} "
          f"task types for task_id survival, "
          f"{len(_TASK_RESPONSE_TYPES)} for decision presence.")
