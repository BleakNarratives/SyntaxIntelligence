#!/usr/bin/env python3
"""
SEMANTIC HANDOFF VALIDATOR — TruthSleuth's lane (spec §4 layer 2 / AN-11).

================================================================================
PURPOSE
================================================================================
The structural validator (`handoff_validator.py`) confirms that an envelope has
the right fields, right types, and right enum values. The semantic layer is the
OTHER half of fidelity: even when the shape is correct, a summary hop can
distort, omit, or spin the meaning. This module implements the minimum-viable
heuristics for that lane so spec §4 layer 2 has its first concrete shape.

================================================================================
LANE BOUNDARY (CRITICAL — DO NOT CONFUSE)
================================================================================
* The dispatcher named "TruthSleuth" in `Syntax-Intelligence/dispatchers.py`
  (a BaseDispatcher subclass doing regex-based code audit on `code` payloads)
  is a DIFFERENT thing. It is the audit persona in the swarm — what it
  inspects is the code submitted to a task, not the SwarmMessage envelopes
  passing between agents.

* This `semantic_handoff` is the spec §4 layer-2 fidelity check on
  SwarmMessages — what spec calls "TruthSleuth's lane" abstractly,
  a name that the original spec author used as a role-label rather than a
  class-name. The two are intentionally not coupled. We do NOT shift the
  TruthSleuth dispatcher behavior per the gap-report's non-goal list
  (`hub/state/SYNTAX_GAP_REPORT.md` §4).

================================================================================
HEURISTIC FAMILIES (minimum-viable)
================================================================================
Four detection families. Each is independently testable and opt-in.

1. OMISSION       — a key that existed in `prior` is absent (or empty) in
                    `current`. Severity: ERROR. Code: `SEMANTIC_OMISSION`.

2. NUMERIC-DISTORTION — a numeric field that existed in `prior` is present
                    in `current` but its magnitude differs (smaller or
                    larger by ≥10x or by absolute delta > 50%). Severity:
                    WARNING for shrinks that could be re-summary; ERROR
                    for ≥1000x deltas that look like deliberate
                    under-statement. Code: `SEMANTIC_NUMERIC_DISTORTION`.

3. CLASS-FLIP     — a field's runtime class changed (str → list, dict → str,
                    etc.). Always suspicious on a fidelity hop. Severity:
                    ERROR. Code: `SEMANTIC_CLASS_FLIP`.

4. POLARITY-SHIFT — a `reason`, `description`, or `title` text field's
                    polarity flipped — was strongly cautionary ("urgent",
                    "critical", "blocking", "broken") and is now neutral
                    or positive ("manageable", "later", "minor"). Severity:
                    WARNING. Code: `SEMANTIC_POLARITY_SHIFT`.

These four cover the spec's layer-2 vocabulary: spin (polarity), omission
(omission + class-flip), misrepresentation (numeric-distortion). They are
deliberately simple — easy to test, easy to audit, easy to extend. The spec
open items (The TruthSleuth full lane in `hub/state/SYNTAX_GAP_REPORT.md`
§3 + AN-11) leave room for richer heuristics (semantic embeddings, LLM-as-
judge, etc.) but those need operator sign-off on the model + cost.

================================================================================
USAGE
================================================================================
    from semantic_handoff import SemanticHandoffValidator, compare_handoffs

    # Symmetric API — both prior and current must be dicts (honey)
    findings = compare_handoffs(prior=prior_payload, current=current_payload)

    # Class API — wraps compare_handoffs with opt-in heuristics
    v = SemanticHandoffValidator(enabled_heuristics={"omission", "numeric", "class"})
    findings = v.compare(prior_payload, current_payload)

    # Single-payload mode (for audit-on-bus-log) — checks internal consistency
    # only (no prior available). Currently a thin wrapper — most fidelity
    # checks need before/after, so single-payload returns empty unless
    # flag-worthy asymmetry is detectable from context.
    findings = v.audit_single(current_payload)

================================================================================
NON-GOALS
================================================================================
- Pure stdlib + zero new dependencies.
- Does NOT modify the TruthSleuth dispatcher behavior.
- Does NOT modify `swarm_charter.py` (open B is operator's call).
- Does NOT modify the structural validator; this is a peer module.
- Does NOT touch `hub/state/event_log.jsonl` historicals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


# ═══════════════════════════════════════════════════════════════
# SEVERITY (mirrors handoff_validator.Severity conventions)
# ═══════════════════════════════════════════════════════════════

class Severity(Enum):
    """
    Mirrors handoff_validator.Severity — duplicated locally so this
    module stays stdlib-only with no import dependency on the structural
    validator. Operators who re-unify these should pick one and update
    the other; for now both enum sets are minefields-coupled.
    """
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ═══════════════════════════════════════════════════════════════
# FINDING + RESULT surfaces
# ═══════════════════════════════════════════════════════════════

@dataclass
class SemanticFinding:
    """
    One issue surfaced by a semantic-handoff check.

    Severity defaults:
    - ERROR:    block-shaped (omission, class-flip, gross numeric distortion).
    - WARNING:  advisory (modest numeric distortion, polarity shift).
    - CRITICAL: not produced by minimum-viable — reserved for future
                "absolute distortion" cases (e.g., text-swap-lookalike).
    """
    field: Optional[str]
    severity: Severity
    code: str
    message: str
    original: Any = None
    current: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """
        JSON-friendly form. `original` and `current` use _safe_str
        which renders None as the literal string "null" (JSON-friendly)
        and bounds to 200 chars so audit log dumps stay sane on long
        string fields. Use this for downstream audit-log consumers.
        """
        return {
            "field": self.field,
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "original": _safe_str(self.original),
            "current": _safe_str(self.current),
        }


def _safe_str(v: Any) -> str:
    """
    Coerce any value to a string for the JSON-friendly finding dict.
    Bounds to 200 chars so audit logs don't blow up on long fields.

    Special-cases:
      * None          → "null" (JSON-friendly; downstream grep stays clean)
      * bytes/bytearray → "<bytes>" (binary payloads are noise in audit JSON)
    """
    if v is None:
        return "null"
    if isinstance(v, (bytes, bytearray)):
        return "<bytes>"
    try:
        s = str(v)
    except Exception:
        return "<unrepresentable>"
    if len(s) > 200:
        s = s[:197] + "..."
    return s


@dataclass
class SemanticVerdict:
    """
    The combined verdict for one compare() call.
    Field intentionally NOT called `ValidationResult` — it lives in
    a different module and operator may decide later whether to
    unify.
    """
    passed: bool
    findings: List[SemanticFinding] = field(default_factory=list)

    def errors(self) -> List[SemanticFinding]:
        return [f for f in self.findings if f.severity in (Severity.ERROR, Severity.CRITICAL)]

    def warnings(self) -> List[SemanticFinding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
        }


# ═══════════════════════════════════════════════════════════════
# HEURISTIC 1 — OMISSION
# ═══════════════════════════════════════════════════════════════
#
# Rule: a key present-with-nonzero-value in `prior` is gone, or empty,
# in `current`. Keys absent in `prior` don't count (we don't penalize
# additions).
#
# Strictness: only flag fields whose prior value was "substantive"
# (length > 0 for strings, > 0 for lists/dicts, != 0 for numerics).
# Suppress noise on empty-string priors (those don't carry fidelity loss).
#
# Why ERROR severity: a downstream agent reading `current` has lost
# information it had a hop ago. Every further decision it makes is
# guesswork. Block-level appropriate.

def detect_omission(prior: Dict[str, Any], current: Dict[str, Any]) -> List[SemanticFinding]:
    """Field-level omission detector.

    Two passes:
      1. Common keys (in BOTH prior and current): value went from
         substantive to non-substantive on hop.
      2. Prior-only keys (in prior but absent from current): entire
         displacement of a substantive prior field. This is the
         canonical "omission" case — the prior value evaporated on hop.
    Both passes fire SEMANTIC_OMISSION at ERROR severity.
    """
    findings: List[SemanticFinding] = []

    # Pass 1: common keys whose value went empty.
    common_keys = set(prior.keys()) & set(current.keys())
    for key in common_keys:
        p_val = prior.get(key)
        c_val = current.get(key)
        if _substantive(p_val) and not _substantive(c_val):
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.ERROR,
                code="SEMANTIC_OMISSION",
                message=(
                    f"Semantic fidelity: field '{key}' was substantive in prior "
                    f"({_safe_str(p_val)[:80]}) but is empty/missing in current "
                    f"({_safe_str(c_val)[:80]}). Possible omission on hop."
                ),
                original=p_val,
                current=c_val,
            ))

    # Pass 2: prior-only keys — the field was substantive in prior and
    # is no longer present at all in current. This is the more
    # egregious omission case (the value evaporated).
    prior_only_keys = set(prior.keys()) - set(current.keys())
    for key in prior_only_keys:
        p_val = prior.get(key)
        if _substantive(p_val):
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.ERROR,
                code="SEMANTIC_OMISSION",
                message=(
                    f"Semantic fidelity: field '{key}' was substantive in prior "
                    f"({_safe_str(p_val)[:80]}) but is absent from current "
                    f"entirely. Possible omission on hop."
                ),
                original=p_val,
                current=None,
            ))

    return findings


def _substantive(v: Any) -> bool:
    """
    A "substantive" value carries information. Empty strings, empty
    containers, zero, None, False — all NOT substantive. Strings with
    any chars ARE substantive. Lists/dicts with any elements are.
    """
    if v is None:
        return False
    if isinstance(v, (str, list, tuple, dict, set)):
        return len(v) > 0
    if isinstance(v, bool):
        # Booleans are technically "substantive" but tend to create
        # noise. EXCLUDE them from fidelity tracking unless paired with
        # an explicit flag in the contract.
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return True


# ═══════════════════════════════════════════════════════════════
# HEURISTIC 2 — NUMERIC DISTORTION
# ═══════════════════════════════════════════════════════════════
#
# Rule: a numeric field's magnitude changed on hop.
#
# Two threshold classes:
# - "Significant": smaller by ≥ 10x OR larger by ≥ 10x — WARNING.
# - "Severe":      smaller by ≥ 1000x OR larger by ≥ 1000x — ERROR.
#
# Why two classes: a 10x shrink (e.g., timeout_seconds 300 → 30) is
# possible-but-suspicious re-summarization, but a 1000x shrink (300 → 0.3)
# looks like deliberate under-statement. The ERROR class blocks gate at
# the bus_validator level; WARNING rides into the data["validation"]
# field as a non-blocking advisory per existing pattern.
#
# Out-of-scope: tracking which scaler (timeout_seconds, priority, etc.)
# had the change against its expected units. Spec §2 layer 1 hard
# does this; spec §4 layer 2 leaves it to contract type checking.

def detect_numeric_distortion(
    prior: Dict[str, Any],
    current: Dict[str, Any],
) -> List[SemanticFinding]:
    """Numeric magnitude-distortion detector."""
    findings: List[SemanticFinding] = []
    common_keys = set(prior.keys()) & set(current.keys())

    for key in common_keys:
        p_val = prior.get(key)
        c_val = current.get(key)
        if not isinstance(p_val, (int, float)) or not isinstance(c_val, (int, float)):
            continue
        if isinstance(p_val, bool) or isinstance(c_val, bool):
            # Skip booleans — they are technically int subclasses
            # but tracking their magnitude is nonsense.
            continue
        if p_val == 0 or c_val == 0:
            # Avoid division-by-zero; zero crossings are not
            # "distortions" in the sense we mean — call them out
            # separately if needed.
            continue

        ratio = c_val / p_val

        # Severe class — ≥ 1000x shift.
        if ratio >= 1000 or ratio <= 1 / 1000:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.ERROR,
                code="SEMANTIC_NUMERIC_DISTORTION",
                message=(
                    f"Semantic fidelity: numeric field '{key}' shifted severely "
                    f"({_safe_str(p_val)} → {_safe_str(c_val)}, ratio={ratio:.1f}). "
                    f"Likely deliberate under/over-statement on hop."
                ),
                original=p_val,
                current=c_val,
            ))
            continue

        # Significant class — 10x to 1000x shift.
        if ratio >= 10 or ratio <= 1 / 10:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.WARNING,
                code="SEMANTIC_NUMERIC_DISTORTION",
                message=(
                    f"Semantic fidelity: numeric field '{key}' shifted significantly "
                    f"({_safe_str(p_val)} → {_safe_str(c_val)}, ratio={ratio:.1f}). "
                    f"Possible re-summarization — verify."
                ),
                original=p_val,
                current=c_val,
            ))

    return findings


# ═══════════════════════════════════════════════════════════════
# HEURISTIC 3 — CLASS FLIP
# ═══════════════════════════════════════════════════════════════
#
# Rule: a field's runtime class on the receiving side differs from
# what's on the sending side. e.g., `tags: "alpha,beta"` prior and
# `tags: ["alpha","beta"]` current. Layout semantics change.
#
# Why ERROR: a downstream consumer either (a) stringifies and
# double-quotes, (b) indexes the list and crashes. Either way it's
# a fidelity problem.
#
# Excluded: prior=None vs current=dict (intentional missing→present
# transitions). The omission check catches the other direction.

def detect_class_flip(prior: Dict[str, Any], current: Dict[str, Any]) -> List[SemanticFinding]:
    """Detect class-type mismatch between prior and current payload fields."""
    findings: List[SemanticFinding] = []
    common_keys = set(prior.keys()) & set(current.keys())

    for key in common_keys:
        p_val = prior.get(key)
        c_val = current.get(key)
        # Skip None pairs — those are omission territory.
        if p_val is None or c_val is None:
            continue
        p_class = _semantic_class(p_val)
        c_class = _semantic_class(c_val)
        if p_class != c_class:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.ERROR,
                code="SEMANTIC_CLASS_FLIP",
                message=(
                    f"Semantic fidelity: field '{key}' class changed on hop "
                    f"({p_class} → {c_class}). Downstream consumer may "
                    f"mis-handle."
                ),
                original=p_val,
                current=c_val,
            ))

    return findings


def _semantic_class(v: Any) -> str:
    """
    Map a Python value to a class label for flip detection.

    Note: bool→'bool' before int→'int' (bool is int subclass but we
    want booleans to register distinctly). Used in module-local only;
    no import from handoff_validator (per zero-deps constraint).
    """
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, tuple):
        return "tuple"
    if isinstance(v, set):
        return "set"
    return type(v).__name__


# ═══════════════════════════════════════════════════════════════
# HEURISTIC 4 — POLARITY SHIFT
# ═══════════════════════════════════════════════════════════════
#
# Rule: a text field whose polarity words shift from cautionary to
# neutral/positive (or vice versa) carries fidelity risk — typically
# the writer shifted framing to make a problem sound smaller, or to
# amplify concern. Both are "spin", per spec §4 layer 2 vocabulary.
#
# Targeted text fields: reason, description, title. Operators can extend
# later via the `text_fields=` constructor argument.
#
# Polarity lexicon (deliberately small — easy to audit):
# CAUTIONARY: urgent, critical, blocking, broken, severe, must, mandatory,
#             failing, fails, fail, error, blocked, dead, urgent-now
# NEUTRAL:    ok, noted, fine, acknowledged, progressing, progressing-as-
#             expected, normal, baseline, standard
# POSITIVE:    great, easy, simple, trivial, manageable, fixed, working,
#             done, success, excellent, clean, perfect

_POLARITY_CAUTIONARY = frozenset({
    "urgent", "critical", "blocking", "blocked", "broken", "severe",
    "must", "mandatory", "failing", "fails", "fail", "error", "dead",
    "urgent-now", "asap", "broken-now",
})
_POLARITY_NEUTRAL = frozenset({
    "ok", "okay", "noted", "fine", "acknowledged", "progressing",
    "normal", "baseline", "standard", "ongoing", "tracked",
})
_POLARITY_POSITIVE = frozenset({
    "great", "easy", "simple", "trivial", "manageable", "fixed",
    "working", "done", "success", "successful", "excellent", "clean",
    "perfect", "minor", "no-issue", "no-issue-found",
})


def _polarity_score(text: str) -> Dict[str, int]:
    """
    Count polarity matches per category in a text. Case-insensitive.
    Word-boundary regex to avoid partial matches.
    """
    if not text:
        return {"cautionary": 0, "neutral": 0, "positive": 0}
    lower = text.lower()
    counts = {"cautionary": 0, "neutral": 0, "positive": 0}
    for word in _POLARITY_CAUTIONARY:
        counts["cautionary"] += len(re.findall(r"\b" + re.escape(word) + r"\b", lower))
    for word in _POLARITY_NEUTRAL:
        counts["neutral"] += len(re.findall(r"\b" + re.escape(word) + r"\b", lower))
    for word in _POLARITY_POSITIVE:
        counts["positive"] += len(re.findall(r"\b" + re.escape(word) + r"\b", lower))
    return counts


def detect_polarity_shift(
    prior: Dict[str, Any],
    current: Dict[str, Any],
    text_fields: Set[str] = frozenset({"reason", "description", "title"}),
) -> List[SemanticFinding]:
    """Detect polarity flips on text fields (cautionary → neutral/positive or vice versa)."""
    findings: List[SemanticFinding] = []
    common_keys = set(prior.keys()) & set(current.keys())
    fields_to_check = common_keys & text_fields

    for key in fields_to_check:
        p_val = prior.get(key)
        c_val = current.get(key)
        # Both must be readable strings.
        if not isinstance(p_val, str) or not isinstance(c_val, str):
            continue
        if not p_val.strip() or not c_val.strip():
            # Empty strings are caught by omission detector.
            continue

        p_scores = _polarity_score(p_val)
        c_scores = _polarity_score(c_val)

        # Heuristic: cautionary-to-positive (or vice versa) without neutral
        # stepping stones is a spin risk.
        p_cautionary = p_scores["cautionary"] > 0
        c_cautionary = c_scores["cautionary"] > 0
        p_positive = p_scores["positive"] > 0
        c_positive = c_scores["positive"] > 0

        # Direction 1: prior cautionary, current positive (minimization spin).
        if p_cautionary and c_positive and not (p_scores["neutral"] or c_scores["neutral"]):
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.WARNING,
                code="SEMANTIC_POLARITY_SHIFT",
                message=(
                    f"Semantic fidelity: polarity shifted on '{key}' "
                    f"(cautionary → positive without neutral intermediation). "
                    f"Possible minimization spin on hop."
                ),
                original=_safe_str(p_val)[:120],
                current=_safe_str(c_val)[:120],
            ))
            continue

        # Direction 2: prior positive, current cautionary (alarm spin).
        if p_positive and c_cautionary and not (p_scores["neutral"] or c_scores["neutral"]):
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.WARNING,
                code="SEMANTIC_POLARITY_SHIFT",
                message=(
                    f"Semantic fidelity: polarity shifted on '{key}' "
                    f"(positive → cautionary without neutral intermediation). "
                    f"Possible alarmism spin on hop."
                ),
                original=_safe_str(p_val)[:120],
                current=_safe_str(c_val)[:120],
            ))
            continue

    return findings


# ═══════════════════════════════════════════════════════════════
# VALIDATOR CLASS
# ═══════════════════════════════════════════════════════════════

class SemanticHandoffValidator:
    """
    Wrapper around the four heuristics. Encapsulates enabled-set
    and provides a single `compare()` API. Stateless across calls —
    safe to share, thread-friendly.

    Constructor args:
        enabled_heuristics: str set, any of {"omission", "numeric",
                          "class", "polarity"}. Default = all four.
                          Pass an empty set to disable everything
                          (useful when an operator wants to verify
                          "no findings at all" as a smoke test).

    Methods:
        compare(prior, current) -> SemanticVerdict
            Both args must be dicts. Returns verdict with combined
            findings (Union across all enabled heuristics). passed=True
            only if no Error/Critical findings.

        audit_single(payload) -> SemanticVerdict
            Single-payload mode for bus-log audit scripts that don't
            have a prior to compare against. Currently delegates to
            trivial self-consistency (a payload that contradicts itself
            is suspicious but not blockable). Returns SemanticVerdict
            with passed=True unless something's clearly self-contradictory.
    """

    VALID_HEURISTICS = frozenset({"omission", "numeric", "class", "polarity", "proper"})

    def __init__(
        self,
        enabled_heuristics: Optional[Set[str]] = None,
        model_path: Optional[str] = None,
        proper_warning_threshold: float = 0.95,
        proper_error_threshold: float = 0.80,
    ):
        """Construct a semantic validator.

        Args:
            enabled_heuristics: subset of VALID_HEURISTICS. Default = all 5.
            model_path: path to the pre-staged sentence-transformers model
                directory (e.g., ~/.cache/torch/sentence_transformers/
                models--sentence-transformers--all-MiniLM-L6-v2/snapshots/<hash>/).
                When None (default) OR model loading fails, the `proper`
                heuristic gracefully degrades to no-op.

        Try/except isolation: sentence-transformers + numpy are imported
        inside try/except so the module remains importable on stdlib-only
        runners. The `proper` heuristic silently degrades when the
        dependency is missing or the model is not cached.
        """
        if enabled_heuristics is None:
            self.enabled: Set[str] = set(self.VALID_HEURISTICS)
        else:
            bad = enabled_heuristics - self.VALID_HEURISTICS
            if bad:
                raise ValueError(
                    f"SemanticHandoffValidator: unknown heuristics {bad!r}. "
                    f"Valid set: {sorted(self.VALID_HEURISTICS)}"
                )
            self.enabled = set(enabled_heuristics)

        # Threshold validation: 0.5 <= error <= warning <= 1.0.
        # Lower bound 0.5 (vs. 0.0) avoids degenerate "always block" policies
        # where cosine=0.0 (orthogonal/uncorrelated) fires ERROR for every pair.
        # Per code-review 2026-07-21: cos=0 is nonsense data, not a real semantic judgment.
        if not (0.5 <= proper_error_threshold <= proper_warning_threshold <= 1.0):
            raise ValueError(
                f"proper thresholds must satisfy: 0.5 <= error ({proper_error_threshold}) "
                f"<= warning ({proper_warning_threshold}) <= 1.0"
            )
        self._proper_warning_threshold: float = float(proper_warning_threshold)
        self._proper_error_threshold: float = float(proper_error_threshold)
        # Emit-once INFO gate so missing model does NOT spam audit log.
        self._emitted_unavailable_finding: bool = False

        # PROPER: lazy-load the sentence-transformers model inside try/except.
        # All state below is initialized regardless of load success so the
        # other 4 heuristics remain functional.
        self._proper_model: Any = None
        self._proper_available: bool = False
        self._proper_unavailable_reason: Optional[str] = None
        if model_path is not None and "proper" in self.enabled:
            try:
                from sentence_transformers import SentenceTransformer  # noqa: F401
                self._proper_model = SentenceTransformer(
                    model_path, local_files_only=True,
                )
                self._proper_available = True
            except ImportError as e:
                self._proper_unavailable_reason = f"ImportError: {e!r}"
            except Exception as e:
                self._proper_unavailable_reason = f"{type(e).__name__}: {e!r}"

    def compare(self, prior: Dict[str, Any], current: Dict[str, Any]) -> SemanticVerdict:
        """Run all enabled heuristics on the prior→current transition."""
        findings: List[SemanticFinding] = []

        if "omission" in self.enabled and isinstance(prior, dict) and isinstance(current, dict):
            findings.extend(detect_omission(prior, current))
        if "numeric" in self.enabled and isinstance(prior, dict) and isinstance(current, dict):
            findings.extend(detect_numeric_distortion(prior, current))
        if "class" in self.enabled and isinstance(prior, dict) and isinstance(current, dict):
            findings.extend(detect_class_flip(prior, current))
        if "polarity" in self.enabled and isinstance(prior, dict) and isinstance(current, dict):
            findings.extend(detect_polarity_shift(prior, current))
        if (
            "proper" in self.enabled
            and self._proper_available
            and isinstance(prior, dict)
            and isinstance(current, dict)
        ):
            findings.extend(detect_proper_semantic_drift(
                prior, current, self._proper_model,
                warning_threshold=self._proper_warning_threshold,
                error_threshold=self._proper_error_threshold,
            ))
        elif (
            "proper" in self.enabled
            and not self._proper_available
            and not self._emitted_unavailable_finding
        ):
            # Emit-once gate: ONE informational finding per validator instance,
            # not per compare() call. Keeps audit logs clean when the operator
            # enabled `proper` but never pre-staged the model.
            self._emitted_unavailable_finding = True
            if self._proper_unavailable_reason is not None:
                unavailable_msg = (
                    f"the sentence-transformers model failed to load "
                    f"({self._proper_unavailable_reason}). "
                    "Verify cache path or run scripts/fetch_minilm.py."
                )
            else:
                unavailable_msg = (
                    "no model_path was passed at construction time. "
                    "Pass model_path=... to enable proper_semantic."
                )
            findings.append(SemanticFinding(
                field=None,
                severity=Severity.INFO,
                code="SEMANTIC_PROPER_UNAVAILABLE",
                message=(
                    "Semantic fidelity: proper_semantic heuristic was enabled but "
                    + unavailable_msg
                ),
            ))

        passed = not any(f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings)
        return SemanticVerdict(passed=passed, findings=findings)

    def audit_single(self, payload: Dict[str, Any]) -> SemanticVerdict:
        """
        Single-payload audit (no prior available). Used by the bus-log
        audit script when comparing against a missing prior. Currently
        returns an empty verdict — minimum-viable doesn't ship a
        self-contradiction detector. Operators can extend later
        (e.g., typed-claim vs actual-class detectors).
        """
        if not isinstance(payload, dict):
            return SemanticVerdict(passed=True)
        return SemanticVerdict(passed=True)


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE
# ═══════════════════════════════════════════════════════════════

def compare_handoffs(
    prior: Dict[str, Any],
    current: Dict[str, Any],
) -> SemanticVerdict:
    """
    Functional API mirroring StructuralValidator.validate() ergonomics.
    Returns SemanticVerdict for the cross-handoff comparison with all
    four heuristics enabled.
    """
    return SemanticHandoffValidator().compare(prior, current)


# ═══════════════════════════════════════════════════════════════
# SMOKE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("semantic_handoff module loaded (TruthSleuth lane, AN-11 minimum-viable).")
    print(f"  heuristics: {sorted(SemanticHandoffValidator.VALID_HEURISTICS)}")
    print(f"  proper_semantic (PROPER) requires sentence-transformers + a pre-staged model.")
    print(f"  When model is absent, 'proper' heuristic degrades to no-op + INFO finding.")
    print(f"  severity levels: {[s.value for s in Severity]}")
    print(f"  polarity lexicon: "
          f"cautionary={len(_POLARITY_CAUTIONARY)}, "
          f"neutral={len(_POLARITY_NEUTRAL)}, "
          f"positive={len(_POLARITY_POSITIVE)}")
    print(f"  usage: see compare_handoffs(prior, current) → SemanticVerdict")


# ═══════════════════════════════════════════════════════════════════
# HEURISTIC 5 — PROPER SEMANTIC DRIFT (layer-2 PROPER extension)
# ═══════════════════════════════════════════════════════════════════
#
# Rule: compute text-embedding cosine similarity on `reason`, `description`,
# `title` fields. Bands (per AN-11 PROPER prep brief, 2026-07-21):
#
#   sim >= warning_threshold                    = no finding (synonym-clean)
#   error_threshold <= sim < warning_threshold  = SEMANTIC_DRIFT_WARNING
#   sim < error_threshold                       = SEMANTIC_DRIFT_ERROR
#
# Dependencies: sentence-transformers (>= 2.2) + the all-MiniLM-L6-v2 model
# pre-staged via scripts/fetch_minilm.py. Loaded with local_files_only=True.
# When the model is unavailable, proper_semantic degrades to no-op +
# emits ONE INFO finding per validator instance (not per compare()).
# Other heuristics remain fully functional — PROPER is purely additive.

def detect_proper_semantic_drift(
    prior: Dict[str, Any],
    current: Dict[str, Any],
    model: Any,
    warning_threshold: float = 0.95,
    error_threshold: float = 0.80,
    text_fields: Set[str] = frozenset({"reason", "description", "title"}),
) -> List[SemanticFinding]:
    """Compute text-embedding cosine similarity on reason/description/title.

    Severity:
      - sim < error_threshold                        -> SEMANTIC_DRIFT_ERROR
      - error_threshold <= sim < warning_threshold  -> SEMANTIC_DRIFT_WARNING
      - sim >= warning_threshold                     -> no finding
    """
    findings: List[SemanticFinding] = []
    common_keys = (set(prior.keys()) & set(current.keys())) & text_fields
    import numpy as np  # local import keeps stdlib-only path alive
    for key in common_keys:
        p_val = prior.get(key)
        c_val = current.get(key)
        if not isinstance(p_val, str) or not isinstance(c_val, str):
            continue
        if not p_val.strip() or not c_val.strip():
            continue
        try:
            emb_p = model.encode([p_val], normalize_embeddings=True)[0]
            emb_c = model.encode([c_val], normalize_embeddings=True)[0]
            sim = float(np.dot(emb_p, emb_c))
        except Exception as e:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.WARNING,
                code="SEMANTIC_EMBEDDING_FAILED",
                message=(
                    f"Semantic fidelity: embedding compute failed on '{key}': "
                    f"{type(e).__name__}: {e!r}. Skipping proper_semantic on this field."
                ),
                original=p_val[:120],
                current=c_val[:120],
            ))
            continue
        if sim < error_threshold:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.ERROR,
                code="SEMANTIC_DRIFT_ERROR",
                message=(
                    f"Semantic fidelity: cosine similarity on '{key}' is {sim:.3f} "
                    f"(< {error_threshold:.2f}). Likely semantic spin / distortion on hop."
                ),
                original=p_val[:120],
                current=c_val[:120],
            ))
        elif sim < warning_threshold:
            findings.append(SemanticFinding(
                field=key,
                severity=Severity.WARNING,
                code="SEMANTIC_DRIFT_WARNING",
                message=(
                    f"Semantic fidelity: cosine similarity on '{key}' is {sim:.3f} "
                    f"({error_threshold:.2f}-{warning_threshold:.2f}). Possible re-summarization on hop."
                ),
                original=p_val[:120],
                current=c_val[:120],
            ))
        # sim >= warning_threshold -> no finding (synonym-clean)
    return findings
