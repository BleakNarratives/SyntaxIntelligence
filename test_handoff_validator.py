"""
Tests for handoff_validator — structural layer (spec §4 layer 1).

Run from `bleaknarratives/Syntax-Intelligence/`:
    python3 -m unittest test_handoff_validator

Or from project root with cwd in the package:
    python3 -m unittest discover -s bleaknarratives/Syntax-Intelligence -p 'test_*.py'
"""

import random
import unittest
from typing import Any, Dict

from handoff_validator import (
    ContractField,
    EASTER_BLOOM,
    HandoffContract,
    HandoffValidator,
    SAYING,
    SWARM_MESSAGE_CONTRACT,
    Severity,
    ValidationFinding,
    ValidationResult,
    gut_signal,
    validate_swarm_message,
    _MissingSentinel,
    _MISSING,
)


class _FakeEnvelope:
    """Duck-typed stand-in for agent_protocol.SwarmMessage."""

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _make_contract(**field_overrides: Any) -> HandoffContract:
    fields = [
        ContractField(name="a", type=str, required=True, min_length=1, max_length=64,
                      description="simple required string"),
        ContractField(name="b", type=int, required=False, description="optional int"),
    ]
    for fname, override in field_overrides.items():
        for f in fields:
            if f.name == fname:
                for k, v in override.items():
                    setattr(f, k, v)
    return HandoffContract(name="t", version="1.0.0", fields=fields, description="")


class TestContractShape(unittest.TestCase):
    def test_default_contract_has_nine_fields(self) -> None:
        self.assertEqual(len(SWARM_MESSAGE_CONTRACT.fields), 9)
        expected_required = {"sender_id", "message_type", "payload", "message_id",
                             "timestamp", "ttl"}
        actual_required = {f.name for f in SWARM_MESSAGE_CONTRACT.fields if f.required}
        self.assertEqual(actual_required, expected_required)

    def test_to_dict_round_trip(self) -> None:
        d = SWARM_MESSAGE_CONTRACT.to_dict()
        self.assertEqual(d["name"], "swarm_message_v1")
        self.assertEqual(d["version"], "1.0.0")
        self.assertEqual(len(d["fields"]), 9)
        self.assertGreater(len(d["notes"]), 0)


class TestPassAndFail(unittest.TestCase):
    def test_pass_for_valid_envelope(self) -> None:
        env = _FakeEnvelope(sender_id="alice", message_type="pulse")
        # add a few defaults to satisfy the built-in contract
        env.message_id = "msg123"
        env.timestamp = 1234.0
        env.ttl = 60.0
        env.payload = {"ok": True}
        result = validate_swarm_message(env, target_id="alice")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")

    def test_fail_on_missing_required(self) -> None:
        contract = _make_contract()
        env = _FakeEnvelope()  # 'a' missing
        # Dict payload for validate
        result = HandoffValidator(contract).validate({})
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("REQUIRED_MISSING", codes)

    def test_fail_on_type_mismatch(self) -> None:
        contract = _make_contract()
        env = _FakeEnvelope(a="hi")  # a is str — fine
        result = HandoffValidator(contract).validate({"a": "hi", "b": "not-an-int"})
        self.assertFalse(result.passed)
        self.assertTrue(any(f.code == "TYPE_MISMATCH" for f in result.findings))

    def test_warn_on_length_too_short_does_not_fail(self) -> None:
        contract = _make_contract(a=dict(min_length=5))
        env = _FakeEnvelope(a="hi")
        result = HandoffValidator(contract).validate(env)
        # Warning is non-blocking — passed should remain True
        self.assertTrue(result.passed)
        self.assertTrue(any(f.severity == Severity.WARNING for f in result.findings))

    def test_warn_on_length_too_long_does_not_fail(self) -> None:
        contract = _make_contract(a=dict(min_length=1, max_length=3))
        result = HandoffValidator(contract).validate({"a": "abcdef"})
        self.assertTrue(result.passed)
        self.assertTrue(any(f.code == "LENGTH_TOO_LONG" for f in result.findings))

    def test_fail_on_enum_out_of_range(self) -> None:
        contract = HandoffContract(
            name="t", version="1.0",
            fields=[ContractField(name="a", type=str, required=True,
                                    enum_values=["x", "y"])],
        )
        result = HandoffValidator(contract).validate({"a": "z"})
        self.assertFalse(result.passed)
        self.assertTrue(any(f.code == "ENUM_OUT_OF_RANGE" for f in result.findings))

    def test_fail_on_custom_predicate(self) -> None:
        contract = HandoffContract(
            name="t", version="1.0",
            fields=[ContractField(name="token", type=str, required=True,
                                    predicate=lambda v: v.startswith("ok-"))],
        )
        result = HandoffValidator(contract).validate({"token": "nope"})
        self.assertFalse(result.passed)
        self.assertTrue(any(f.code == "PREDICATE_FAIL" for f in result.findings))

    def test_bool_distinguished_from_int_regression(self) -> None:
        """Python's bool is subclass of int — must be flagged when int is expected."""
        contract = _make_contract()
        result = HandoffValidator(contract).validate({"a": "hi", "b": True})
        self.assertFalse(result.passed)
        f = next(f for f in result.findings if f.code == "TYPE_MISMATCH")
        self.assertEqual(f.actual, "bool")
        self.assertEqual(f.expected, "int")


class TestDictAndDataclass(unittest.TestCase):
    def test_dict_payload_supported(self) -> None:
        contract = _make_contract()
        result = HandoffValidator(contract).validate({"a": "hi"})
        self.assertTrue(result.passed)

    def test_dataclass_like_attribute_access_supported(self) -> None:
        contract = _make_contract()
        env = _FakeEnvelope(a="hi")
        result = HandoffValidator(contract).validate(env)
        self.assertTrue(result.passed)


class TestGutSignal(unittest.TestCase):
    def test_emits_info_on_task_offer_with_empty_payload(self) -> None:
        contract = HandoffContract(name="t", version="1", fields=[
            ContractField(name="payload", type=dict, required=True),
        ])
        findings = gut_signal(_FakeEnvelope(payload={}), contract, message_type="task_offer")
        self.assertGreaterEqual(len(findings), 1)
        first = findings[0]
        self.assertEqual(first.severity, Severity.INFO)
        self.assertEqual(first.code, "GUT_EMPTY_TASK_PAYLOAD")

    def test_emits_info_on_low_entropy_string(self) -> None:
        contract = HandoffContract(name="t", version="1", fields=[])
        junky = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        findings = gut_signal({"k": junky}, contract, message_type="pulse")
        codes = [g.code for g in findings]
        self.assertIn("GUT_LOW_ENTROPY", codes)

    def test_emits_info_on_oversize_payload(self) -> None:
        """High-entropy oversize fixture — must fire GUT_OVERSIZE, not LOW_ENTROPY."""
        random.seed(0xC0FFEE)
        high_entropy_text = "".join(
            random.choices(
                "abcdefghijklmnopqrstuvwxyz", k=70 * 1024
            )
        )
        contract = HandoffContract(name="t", version="1", fields=[])
        big = {"k": high_entropy_text}
        findings = gut_signal(big, contract, message_type="pulse")
        codes = [g.code for g in findings]
        self.assertIn("GUT_OVERSIZE", codes)

    def test_collects_multiple_gut_signals(self) -> None:
        """Oversize + low entropy — both should fire (collection, not first-match)."""
        contract = HandoffContract(name="t", version="1", fields=[])
        big = {"k": "x" * (70 * 1024)}  # both oversize AND low entropy
        findings = gut_signal(big, contract, message_type="pulse")
        codes = [g.code for g in findings]
        # Both fire because gut_signal collects, not first-match.
        self.assertIn("GUT_LOW_ENTROPY", codes)
        self.assertIn("GUT_OVERSIZE", codes)

    def test_returns_empty_list_on_normal_payload(self) -> None:
        contract = HandoffContract(name="t", version="1", fields=[])
        normal = _FakeEnvelope(payload={"task_id": "abc", "title": "Some normal task"})
        self.assertEqual(gut_signal(normal, contract, message_type="task_offer"), [])

    def test_gut_signal_does_not_block_pass(self) -> None:
        contract = _make_contract()
        # Empty payload on a task_progress envelope fires
        # GUT_EMPTY_TASK_PAYLOAD (info). We use task_progress specifically
        # because it sits in the gut-required-payload set WITHOUT being
        # in semantic_signal's task-id-required set — so we exercise the
        # gut info path without the semantic ERROR path blocking passed.
        # (Earlier this test used task_offer. AN-09 minimum-viable
        # semantic_signal promoted task_offer to task-id-required, so
        # SEMANTIC_LOSS_TASK_ID would fail the passed=True assertion.
        # task_progress uses reply_to for chain continuation instead of
        # inline task_id — the natural choice here.)
        env = _FakeEnvelope(a="hi", payload={})
        result = HandoffValidator(contract).validate(
            env, message_type="task_progress"
        )
        self.assertTrue(result.passed)
        self.assertIsNotNone(result.gut_note)


class TestEasterAndSmoke(unittest.TestCase):
    def test_easter_bloom_constant(self) -> None:
        self.assertTrue(len(EASTER_BLOOM) >= 1)
        self.assertIsInstance(EASTER_BLOOM, str)

    def test_saying_is_set(self) -> None:
        self.assertGreater(len(SAYING), 0)

    def test_missing_sentinel_is_distinct(self) -> None:
        """Sentinel must not collide with None / missing-key sentinels."""
        self.assertIsNotNone(_MISSING)
        self.assertIsInstance(_MISSING, _MissingSentinel)


class TestSemanticSignal(unittest.TestCase):
    """Spec §4 Layer-2 minimum-viable semantic validation.

    Covers the simplest handoff type, TaskOffer → TaskResponse, on
    parameters that must survive the hop: `task_id` and (for responses
    only) `decision`. TruthSleuth proper (distortion/spin/omission) is
    still open work — see anomalies.md AN-09 and the spec.
    """

    def test_task_offer_with_truncated_task_id_fails(self) -> None:
        v = HandoffValidator(SWARM_MESSAGE_CONTRACT)
        payload = {
            "sender_id": "agent_x",
            "message_id": "msg_off_001",
            "message_type": "task_offer",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
            "payload": {
                "task_id": "t",  # truncated — should fail semantic check
                "title": "Build the thing",
                "description": "...",
            },
        }
        result = v.validate(payload, message_type="task_offer")
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("SEMANTIC_LOSS_TASK_ID", codes)

    def test_task_offer_with_missing_task_id_fails(self) -> None:
        v = HandoffValidator(SWARM_MESSAGE_CONTRACT)
        payload = {
            "sender_id": "agent_x",
            "message_id": "msg_off_002",
            "message_type": "task_offer",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
            "payload": {
                # task_id missing entirely
                "title": "Build the thing",
            },
        }
        result = v.validate(payload, message_type="task_offer")
        self.assertFalse(result.passed)
        self.assertTrue(
            any(f.code == "SEMANTIC_LOSS_TASK_ID" for f in result.findings)
        )

    def test_task_accept_without_decision_fails(self) -> None:
        v = HandoffValidator(SWARM_MESSAGE_CONTRACT)
        payload = {
            "sender_id": "agent_x",
            "message_id": "msg_acc_001",
            "message_type": "task_accept",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
            "payload": {
                "task_id": "task_abcdef123",  # OK length
                # decision missing — agent envelope has no actionable answer
            },
        }
        result = v.validate(payload, message_type="task_accept")
        self.assertFalse(result.passed)
        self.assertTrue(
            any(f.code == "SEMANTIC_LOSS_DECISION" for f in result.findings)
        )

    def test_task_accept_with_id_and_decision_passes_semantic(self) -> None:
        """A structurally and semantically valid task_accept must pass."""
        v = HandoffValidator(SWARM_MESSAGE_CONTRACT)
        payload = {
            "sender_id": "agent_x",
            "message_id": "msg_acc_002",
            "message_type": "task_accept",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
            "payload": {
                "task_id": "task_abcdef123",
                "decision": "accept",
                "reason": "On it.",
            },
        }
        result = v.validate(payload, message_type="task_accept")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")
        # No semantic-layer findings, since ID and decision are both present.
        self.assertFalse(
            any(f.code in ("SEMANTIC_LOSS_TASK_ID", "SEMANTIC_LOSS_DECISION")
                for f in result.findings)
        )

    def test_task_progress_does_not_get_semantic_loss_finding(self) -> None:
        """task_progress uses reply_to, not payload.task_id — must not flag."""
        v = HandoffValidator(SWARM_MESSAGE_CONTRACT)
        payload = {
            "sender_id": "agent_x",
            "message_id": "msg_prog_001",
            "message_type": "task_progress",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
            "payload": {"progress": "40%", "note": "halfway"},
        }
        result = v.validate(payload, message_type="task_progress")
        # No SEMANTIC_LOSS_* findings because task_progress is excluded
        # from the task_id-presence check (intent: reply_to carries the ID).
        self.assertFalse(
            any(f.code in ("SEMANTIC_LOSS_TASK_ID", "SEMANTIC_LOSS_DECISION")
                for f in result.findings)
        )


class TestValidateSwarmMessageOnRealShape(unittest.TestCase):
    """Verify SWARM_MESSAGE_CONTRACT validates a real-shape SwarmMessage correctly."""

    def test_minimal_message_passes(self) -> None:
        env = _FakeEnvelope(
            sender_id="alice",
            message_type="pulse",
            payload={"ts": 0.0},
            message_id="msg001",
            timestamp=1234567890.0,
            ttl=300.0,
        )
        result = validate_swarm_message(env, target_id="alice")
        self.assertTrue(result.passed, msg=f"unexpected findings: {result.findings}")

    def test_bad_message_type_fails(self) -> None:
        env = _FakeEnvelope(
            sender_id="alice",
            message_type="not-a-type",
            payload={},
            message_id="msg001",
            timestamp=1234567890.0,
            ttl=300.0,
        )
        result = validate_swarm_message(env)
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("ENUM_OUT_OF_RANGE", codes)

    def test_validate_none_short_circuits(self) -> None:
        """None input does not explode into a 6-finding REQUIRED_MISSING dump."""
        result = validate_swarm_message(None)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].code, "PAYLOAD_IS_NONE")


# ════════════════════════════════════════════════════════════════
# INNER PAYLOAD CONTRACTS — Open A minimum-viable
# ════════════════════════════════════════════════════════════════

class TestTaskOfferPayloadContract(unittest.TestCase):
    """Direct tests of TASK_OFFER_PAYLOAD_CONTRACT (open A: 1st handoff type)."""

    def test_valid_full_offer_passes(self) -> None:
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "title": "Build the thing",
            "description": "Detailed work description.",
            "required_capabilities": ["python", "git"],
            "priority": 3,
            "timeout_seconds": 300.0,
            "min_tier": 1,
            "context": {"repo": "bleaknarratives/syntax-ai"},
        }
        result = v.validate(payload, message_type="task_offer")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")

    def test_missing_task_id_fails(self) -> None:
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "title": "x",
            "description": "y",
            "priority": 0,
            "timeout_seconds": 60.0,
            "min_tier": 0,
        }
        result = v.validate(payload)
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "task_id" and f.code == "REQUIRED_MISSING"
            for f in result.findings
        ))

    def test_short_or_missing_task_id_fails(self) -> None:
        """Missing task_id blocks (REQUIRED_MISSING, ERROR). Empty
        task_id would only fire LENGTH_TOO_SHORT (WARNING) — push to
        pop to test the actual blocking case. Note also: the existing
        LengthTooShort semantic check requires len >= 8."""
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "title": "x",
            "description": "y",
            "priority": 0,
            "timeout_seconds": 60.0,
            "min_tier": 0,
        }
        # task_id missing entirely — REQUIRED_MISSING (ERROR) blocks gate
        result = v.validate(payload)
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "task_id" and f.code == "REQUIRED_MISSING"
            for f in result.findings
        ))

    def test_empty_or_missing_title_fails(self) -> None:
        """Missing title blocks (REQUIRED_MISSING). Empty title would
        only fire LENGTH_TOO_SHORT (WARNING, non-blocking) per spec §4
        precedent — so test the actual blockable case."""
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "description": "y",
            "priority": 0,
            "timeout_seconds": 60.0,
            "min_tier": 0,
        }
        # title missing entirely — REQUIRED_MISSING blocks
        result = v.validate(payload)
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "title" and f.code == "REQUIRED_MISSING"
            for f in result.findings
        ))

    def test_priority_type_mismatch_fails(self) -> None:
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "title": "x",
            "description": "y",
            "priority": "high",  # string, not int
            "timeout_seconds": 60.0,
            "min_tier": 0,
        }
        result = v.validate(payload)
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "priority" and f.code == "TYPE_MISMATCH"
            for f in result.findings
        ))

    def test_short_description_is_advisory_only_no_block(self) -> None:
        """LENGTH_TOO_SHORT is WARNING (non-blocking) per spec §4.

        This regression test locks down the warning-vs-error distinction
        for the new TASK_OFFER_PAYLOAD_CONTRACT — confirming an empty
        title fires a WARNING finding but does NOT fail the gate.
        Future contributors tempted to "fix" warnings need to promote
        them to predicate checks (ERROR) rather than calling passed=True
        a bug."""
        from handoff_validator import TASK_OFFER_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_OFFER_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "title": "ok",
            "description": "",  # below min_length=1 -> WARNING only
            "priority": 0,
            "timeout_seconds": 60.0,
            "min_tier": 0,
        }
        result = v.validate(payload)
        # Spec §4: WARNING findings do not block passed.
        self.assertTrue(result.passed,
                        msg="LENGTH_TOO_SHORT should NOT block the gate")
        self.assertTrue(any(
            f.field == "description"
            and f.code == "LENGTH_TOO_SHORT"
            and f.severity == Severity.WARNING
            for f in result.findings
        ))


class TestTaskResponsePayloadContract(unittest.TestCase):
    """Direct tests of TASK_RESPONSE_PAYLOAD_CONTRACT."""

    def test_accept_with_decision_passes(self) -> None:
        from handoff_validator import TASK_RESPONSE_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_RESPONSE_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "decision": "accept",
            "reason": "On it.",
        }
        result = v.validate(payload, message_type="task_accept")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")

    def test_reject_with_reason_passes(self) -> None:
        from handoff_validator import TASK_RESPONSE_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_RESPONSE_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "decision": "reject",
            "reason": "Out of scope.",
        }
        result = v.validate(payload, message_type="task_reject")
        self.assertTrue(result.passed)

    def test_decision_must_be_in_enum(self) -> None:
        from handoff_validator import TASK_RESPONSE_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_RESPONSE_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "decision": "maybe",  # not in enum
        }
        result = v.validate(payload, message_type="task_accept")
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "decision" and f.code == "ENUM_OUT_OF_RANGE"
            for f in result.findings
        ))

    def test_missing_decision_fails(self) -> None:
        from handoff_validator import TASK_RESPONSE_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_RESPONSE_PAYLOAD_CONTRACT)
        payload = {"task_id": "task_abcdef01"}
        result = v.validate(payload, message_type="task_accept")
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "decision" and f.code == "REQUIRED_MISSING"
            for f in result.findings
        ))

    def test_delegate_with_delegate_to_passes(self) -> None:
        from handoff_validator import TASK_RESPONSE_PAYLOAD_CONTRACT
        v = HandoffValidator(TASK_RESPONSE_PAYLOAD_CONTRACT)
        payload = {
            "task_id": "task_abcdef01",
            "decision": "delegate",
            "delegate_to": "agent_03",
        }
        result = v.validate(payload, message_type="task_delegate")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")


# ════════════════════════════════════════════════════════════════
# ROUTED VALIDATION — both contracts composed
# ════════════════════════════════════════════════════════════════

class TestRoutedValidation(unittest.TestCase):
    """verify_swarm_message runs both envelope + payload contracts."""

    def _make_valid_task_offer(self) -> Dict[str, Any]:
        return {
            "sender_id": "swarm",
            "message_type": "task_offer",
            "channel": "task.offered",
            "payload": {
                "task_id": "task_abcdef01",
                "title": "Build the thing",
                "description": "Detailed description.",
                "priority": 3,
                "timeout_seconds": 300.0,
                "min_tier": 1,
            },
            "message_id": "msg_off_001",
            "timestamp": 1234567890.0,
            "ttl": 60.0,
        }

    def test_correct_offer_passes_with_composed_contract_name(self) -> None:
        result = validate_swarm_message(self._make_valid_task_offer())
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")
        # Contract name is composed with '+'
        self.assertIn("+", result.contract)
        self.assertIn("swarm_message_v1", result.contract)
        self.assertIn("task_offer_payload_v1", result.contract)

    def test_inner_payload_error_fails_with_inner_contract(self) -> None:
        """Remove a required inner field so REQUIRED_MISSING (ERROR) fires
        — empty string here would only produce LENGTH_TOO_SHORT (WARNING,
        non-blocking), which would NOT fail the gate per spec §4."""
        env = self._make_valid_task_offer()
        env["payload"].pop("title")  # inner structural fail (REQUIRED)
        result = validate_swarm_message(env)
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("REQUIRED_MISSING", codes)
        # Field tagged with inner contract path
        title_findings = [f for f in result.findings if f.field == "title"]
        self.assertGreater(len(title_findings), 0)

    def test_outer_envelope_error_fails_with_envelope_contract(self) -> None:
        env = self._make_valid_task_offer()
        env.pop("sender_id")  # outer structural fail
        result = validate_swarm_message(env)
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("REQUIRED_MISSING", codes)
        # Must be tagged with sender_id
        sender_findings = [f for f in result.findings if f.field == "sender_id"]
        self.assertGreater(len(sender_findings), 0)

    def test_both_layers_fail_findings_compose(self) -> None:
        env = self._make_valid_task_offer()
        env.pop("sender_id")  # outer fail
        env["payload"]["task_id"] = "x"  # inner fail (too short)
        result = validate_swarm_message(env)
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("REQUIRED_MISSING", codes)  # outer
        self.assertIn("LENGTH_TOO_SHORT", codes)  # inner

    def test_pulse_message_routes_to_envelope_only(self) -> None:
        env = {
            "sender_id": "alice",
            "message_type": "pulse",
            "payload": {"pulse": 0.0},
            "message_id": "msg_pls_001",
            "timestamp": 1234567890.0,
            "ttl": 300.0,
        }
        result = validate_swarm_message(env, target_id="alice")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")
        # Pulse has no payload contract — contract name is envelope-only
        self.assertEqual(result.contract, "swarm_message_v1")

    def test_task_accept_routes_to_response_contract(self) -> None:
        env = {
            "sender_id": "alice",
            "message_type": "task_accept",
            "payload": {
                "task_id": "task_abcdef01",
                "decision": "accept",
                "reason": "On it.",
            },
            "message_id": "msg_acc_007",
            "timestamp": 1234567890.0,
            "ttl": 300.0,
        }
        result = validate_swarm_message(env)
        self.assertTrue(result.passed)
        self.assertIn("task_response_payload_v1", result.contract)

    def test_dict_input_works_on_envelope_extraction(self) -> None:
        """Regression: previously validate_swarm_message on a dict returned
        message_type=None (getattr dict.message_type yields None). With
        the _coerce_message_type helper, dict input now works correctly."""
        from dataclasses import dataclass
        # Plain dict input — fixes the isinstance(msg, dict) path.
        env = self._make_valid_task_offer()
        # `env["message_type"]` is the string "task_offer"
        # The new helper resolves Enum.value, then falls through unchanged.
        result = validate_swarm_message(env)
        self.assertTrue(result.passed)
        # Confirm the routing worked (router key is by string)
        self.assertIn("task_offer_payload_v1", result.contract)


# ════════════════════════════════════════════════════════════════
# VOUCH PAYLOAD CONTRACT — open A handoff type #2
# ════════════════════════════════════════════════════════════════
#
# Tests for `vouch` envelopes (charter §9 tier-elevation, gap-report
# Open A handoff type #2). Three cross-field rules: no self-vouch,
# single-step advancement (tier_to == tier_from + 1), in-band range
# (0 <= tier_from < tier_to <= 5). All three should produce their
# own PREDICATE_FAIL finding so an operator can fix one issue at a
# time without losing the rest of the diagnosis.

class TestVouchPayloadContract(unittest.TestCase):
    """Direct tests of VOUCH_PAYLOAD_CONTRACT (open A handoff type #2)."""

    def test_valid_single_step_advance_passes(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "target_id": "agent_y",
            "voucher_id": "agent_x",
            "tier_from": 2,
            "tier_to": 3,
            "reason": "Demonstrated execution excellence on prior task.",
            "evidence": {"diff": "abc123", "tests_passed": True},
        }
        result = v.validate(payload, message_type="vouch")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")

    def test_self_vouch_fails(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "target_id": "agent_x",
            "voucher_id": "agent_x",  # SELF
            "tier_from": 0,
            "tier_to": 1,
        }
        result = v.validate(payload, message_type="vouch")
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("PREDICATE_FAIL", codes)
        # At least one PREDICATE_FAIL is on voucher_id (no self-vouch rule).
        voucher_findings = [
            f for f in result.findings
            if f.field == "voucher_id" and f.code == "PREDICATE_FAIL"
        ]
        self.assertGreaterEqual(len(voucher_findings), 1)

    def test_multi_step_advance_blocked_by_single_step_rule(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "target_id": "agent_y",
            "voucher_id": "agent_x",
            "tier_from": 0,
            "tier_to": 4,  # jumps 4 levels — should fail both rules
        }
        result = v.validate(payload, message_type="vouch")
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("PREDICATE_FAIL", codes)
        # Stacked predicate on tier_from fires for single-step
        step_findings = [
            f for f in result.findings if f.code == "PREDICATE_FAIL"
        ]
        self.assertGreaterEqual(len(step_findings), 1)

    def test_downgrade_blocked_by_range_rule(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "target_id": "agent_y",
            "voucher_id": "agent_x",
            "tier_from": 3,
            "tier_to": 2,  # DOWNGRADE — should fail (tier_to < tier_from)
        }
        result = v.validate(payload, message_type="vouch")
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("PREDICATE_FAIL", codes)

    def test_above_tier_5_blocked_by_range_rule(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "target_id": "agent_y",
            "voucher_id": "agent_x",
            "tier_from": 4,
            "tier_to": 6,  # ABOVE TIER_5 — should fail (5 is the cap)
        }
        result = v.validate(payload, message_type="vouch")
        self.assertFalse(result.passed)
        codes = [f.code for f in result.findings]
        self.assertIn("PREDICATE_FAIL", codes)

    def test_missing_target_id_fails(self) -> None:
        from handoff_validator import VOUCH_PAYLOAD_CONTRACT
        v = HandoffValidator(VOUCH_PAYLOAD_CONTRACT)
        payload = {
            "voucher_id": "agent_x",
            "tier_from": 0,
            "tier_to": 1,
        }
        result = v.validate(payload, message_type="vouch")
        self.assertFalse(result.passed)
        self.assertTrue(any(
            f.field == "target_id" and f.code == "REQUIRED_MISSING"
            for f in result.findings
        ))

    def test_vouch_envelope_routes_to_vouch_contract(self) -> None:
        """`vouch` envelopes compose SWARM_MESSAGE_CONTRACT + VOUCH_PAYLOAD_CONTRACT."""
        env = {
            "sender_id": "voucher_x",
            "message_type": "vouch",
            "payload": {
                "target_id": "target_y",
                "voucher_id": "voucher_x",
                "tier_from": 1,
                "tier_to": 2,
            },
            "message_id": "msg_vouch_001",
            "timestamp": 1234567890.0,
            "ttl": 300.0,
        }
        result = validate_swarm_message(env, target_id="voucher_x")
        self.assertTrue(result.passed, msg=f"findings: {result.findings}")
        # Contract name composed
        self.assertIn("vouch_payload_v1", result.contract)


# ════════════════════════════════════════════════════════════════
# SMOKE
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
