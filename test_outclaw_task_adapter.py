#!/usr/bin/env python3
"""Focused tests for the Syntax -> OutClaw task adapter boundary."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any, Dict

from SyntaxIntelligence.agent_protocol import TaskOffer
from SyntaxIntelligence.hardened_engine import HardenedSwarm
from SyntaxIntelligence.outclaw_task_adapter import OutClawTaskAdapter
from SyntaxIntelligence.swarm_charter import AgentTier


@dataclass
class FakeReport:
    text: str
    findings: list[dict[str, Any]]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "findings": self.findings,
            "summary": self.summary,
        }


class FakeOutClawBus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish_findings(self, report: dict[str, Any]) -> dict[str, Any]:
        digest = {
            "audit_id": "audit-test-1",
            "severity_counts": report["summary"]["severity_counts"],
            "safe_to_draft": report["summary"]["safe_to_draft"],
            "high_count": 1,
            "high_findings": [{"rule": "EXISTENCE", "excerpt": "REDACTED"}],
        }
        self.published.append(digest)
        return digest


class TestTaskOfferContextCompatibility(unittest.TestCase):
    def test_context_round_trips_and_legacy_defaults(self) -> None:
        context = {"operation": "outclaw.audit_text", "nested": {"use_llm": False}}
        offer = TaskOffer(title="audit", description="run", context=context)
        restored = TaskOffer.from_message(offer.to_message("syntax", "auditor"))
        self.assertEqual(restored.context, context)

        legacy = TaskOffer.from_message(
            type("LegacyMessage", (), {"sender_id": "legacy", "timestamp": 1.0, "payload": {"title": "old"}})()
        )
        self.assertEqual(legacy.context, {})

    def test_hardened_swarm_forwards_context_in_event_payload(self) -> None:
        swarm = HardenedSwarm()
        received: list[dict[str, Any]] = []
        swarm.event_bus.subscribe("capture", "task.offered", lambda _a, _c, data: received.append(data))

        result = swarm.offer_task(
            "audit",
            "run audit",
            capabilities=["legal_audit"],
            context={"operation": "outclaw.audit_text", "audit_text": "text"},
        )

        self.assertEqual(result["status"], "offered")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["context"]["operation"], "outclaw.audit_text")
        self.assertEqual(received[0]["task_id"], result["task_id"])

    def test_legacy_positional_offer_task_call_still_works(self) -> None:
        swarm = HardenedSwarm()
        result = swarm.offer_task("old", "legacy call", None, 0, 0, None)
        self.assertEqual(result["status"], "offered")

    def test_non_json_context_is_rejected_before_event_publication(self) -> None:
        swarm = HardenedSwarm()
        events: list[dict[str, Any]] = []
        swarm.event_bus.subscribe("capture", "task.offered", lambda _a, _c, data: events.append(data))
        result = swarm.offer_task("bad", "bad context", context={"bad": object()})
        self.assertEqual(result["status"], "invalid_context")
        self.assertEqual(events, [])


class TestOutClawTaskAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.swarm = HardenedSwarm()
        agent = self.swarm.register_agent(
            "outclaw-auditor",
            "OutClaw auditor",
            capabilities=["legal_audit"],
        )
        agent.tier = AgentTier.WORKER
        self.bus = FakeOutClawBus()
        self.audit_calls: list[tuple[str, bool]] = []

        def audit(text: str, *, use_llm: bool = False) -> FakeReport:
            self.audit_calls.append((text, use_llm))
            return FakeReport(
                text=text,
                findings=[{"severity": "HIGH", "sentence": "secret raw text"}],
                summary={"severity_counts": {"HIGH": 1}, "safe_to_draft": False},
            )

        self.adapter = OutClawTaskAdapter(
            self.swarm,
            self.bus,
            audit_fn=audit,
            max_text_chars=100,
        )
        self.adapter.attach()

    def _offer(self, **context: Any) -> dict[str, Any]:
        return self.swarm.offer_task(
            "Audit legal text",
            "Run deterministic citation audit",
            capabilities=["legal_audit"],
            target_agent="outclaw-auditor",
            context={
                "operation": "outclaw.audit_text",
                "audit_text": "Under 42 U.S.C. § 1983, claim.",
                "result_channel": "outclaw.findings",
                **context,
            },
        )

    def test_successful_audit_accepts_completes_and_publishes_once(self) -> None:
        result = self._offer(use_llm=False)

        self.assertEqual(result["status"], "offered")
        self.assertEqual(len(self.audit_calls), 1)
        self.assertEqual(len(self.bus.published), 1)
        self.assertEqual(self.adapter.last_result.status, "completed")
        self.assertEqual(self.adapter.last_result.digest["audit_id"], "audit-test-1")
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["completed"], 1)
        completion = self.swarm.task_orchestrator._completed[result["task_id"]]["result"]
        self.assertNotIn("text", completion)
        self.assertNotIn("secret raw text", str(completion))

    def test_duplicate_offer_is_idempotent(self) -> None:
        result = self._offer()
        task = self.swarm.task_orchestrator._offers[result["task_id"]]
        payload = task.to_message("swarm", "outclaw-auditor").payload
        self.swarm.event_bus.publish("retry", "task.offered", payload)

        self.assertEqual(len(self.audit_calls), 1)
        self.assertEqual(len(self.bus.published), 1)
        self.assertEqual(self.adapter.last_result.status, "duplicate")

    def test_malformed_context_requests_information_without_failure(self) -> None:
        offer = TaskOffer(
            title="Audit",
            description="Needs context",
            required_capabilities=["legal_audit"],
            context={},
        )
        self.swarm.task_orchestrator.offer_task(offer)
        result = self.adapter.handle_offer(offer.to_message("swarm", "outclaw-auditor").payload)

        self.assertEqual(result.status, "request_info")
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["failed"], 0)
        self.assertEqual(self.swarm.get_agent("outclaw-auditor").metrics.tasks_rejected, 0)

    def test_unsupported_operation_is_rejected_without_audit(self) -> None:
        result = self._offer(operation="other.operation")

        self.assertEqual(result["status"], "offered")
        self.assertEqual(self.adapter.last_result.status, "rejected")
        self.assertEqual(self.audit_calls, [])
        self.assertEqual(self.swarm.get_agent("outclaw-auditor").metrics.tasks_rejected, 1)

    def test_oversized_text_is_rejected_before_acceptance(self) -> None:
        result = self._offer(audit_text="x" * 101)

        self.assertEqual(result["status"], "offered")
        self.assertEqual(self.adapter.last_result.reason, "audit_text_too_large")
        self.assertEqual(self.audit_calls, [])
        self.assertEqual(self.swarm.get_agent("outclaw-auditor").metrics.tasks_rejected, 1)

    def test_audit_exception_fails_noncritically(self) -> None:
        self.adapter.audit_fn = lambda _text, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        result = self._offer()

        self.assertEqual(result["status"], "offered")
        self.assertEqual(self.adapter.last_result.status, "failed")
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["failed"], 1)
        self.assertEqual(self.swarm.get_agent("outclaw-auditor").metrics.critical_errors, 0)

    def test_wrong_target_and_nonlegal_tasks_are_ignored(self) -> None:
        result = self._offer()
        calls_before = len(self.audit_calls)
        payload = self.swarm.task_orchestrator._offers[result["task_id"]].to_message(
            "swarm", "other-agent"
        ).payload
        payload["target"] = "other-agent"
        ignored = self.adapter.handle_offer(payload)
        self.assertEqual(ignored.status, "ignored")

        nonlegal = dict(payload)
        nonlegal["target"] = "outclaw-auditor"
        nonlegal["required_capabilities"] = ["code_review"]
        ignored_nonlegal = self.adapter.handle_offer(nonlegal)
        self.assertEqual(ignored_nonlegal.status, "ignored")
        self.assertEqual(len(self.audit_calls), calls_before)

    def test_invalid_options_request_info_or_reject_without_audit(self) -> None:
        invalid_llm = self._offer(use_llm="no")
        self.assertEqual(self.adapter.last_result.status, "request_info")
        self.assertEqual(invalid_llm["status"], "offered")

        invalid_channel = self._offer(result_channel="other.channel")
        self.assertEqual(self.adapter.last_result.status, "rejected")
        self.assertEqual(invalid_channel["status"], "offered")
        self.assertEqual(self.audit_calls, [])

    def test_request_info_does_not_poison_corrected_retry(self) -> None:
        offer = TaskOffer(
            title="Audit",
            description="Needs context",
            required_capabilities=["legal_audit"],
            context={},
        )
        self.swarm.task_orchestrator.offer_task(offer)
        first = self.adapter.handle_offer(offer.to_message("swarm", "outclaw-auditor").payload)
        self.assertEqual(first.status, "request_info")

        corrected = offer.to_message("swarm", "outclaw-auditor").payload
        corrected["context"] = {
            "operation": "outclaw.audit_text",
            "audit_text": "corrected text",
            "result_channel": "outclaw.findings",
        }
        second = self.adapter.handle_offer(corrected)
        self.assertEqual(second.status, "completed")
        self.assertEqual(len(self.audit_calls), 1)

    def test_publication_failure_fails_noncritically(self) -> None:
        class FailingBus:
            def publish_findings(self, _report: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("publication unavailable")

        self.adapter.outclaw_bus = FailingBus()
        result = self._offer()
        self.assertEqual(result["status"], "offered")
        self.assertEqual(self.adapter.last_result.status, "failed")
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["failed"], 1)
        self.assertEqual(self.swarm.get_agent("outclaw-auditor").metrics.critical_errors, 0)

    def test_completion_failure_does_not_republish(self) -> None:
        original_complete = self.swarm.complete_task
        self.swarm.complete_task = lambda *_args, **_kwargs: {"status": "error", "reason": "test"}
        result = self._offer()
        self.assertEqual(result["status"], "offered")
        self.assertEqual(self.adapter.last_result.status, "completion_failed")
        self.assertEqual(len(self.bus.published), 1)

        duplicate = self.adapter.handle_offer(
            self.swarm.task_orchestrator._offers[result["task_id"]].to_message(
                "retry", "outclaw-auditor"
            ).payload
        )
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(len(self.bus.published), 1)
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["assigned"], 0)
        self.assertEqual(self.swarm.task_orchestrator.get_stats()["failed"], 1)
        self.swarm.complete_task = original_complete

    def test_completion_exception_after_publication_does_not_republish(self) -> None:
        original_complete = self.swarm.complete_task

        def raise_on_completion(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("completion store unavailable")

        self.swarm.complete_task = raise_on_completion
        try:
            result = self._offer()
            self.assertEqual(result["status"], "offered")
            self.assertEqual(self.adapter.last_result.status, "completion_failed")
            self.assertEqual(
                self.adapter.last_result.reason,
                "task_completion_raised_after_publication",
            )
            self.assertEqual(len(self.bus.published), 1)

            duplicate = self.adapter.handle_offer(
                self.swarm.task_orchestrator._offers[result["task_id"]].to_message(
                    "retry", "outclaw-auditor"
                ).payload
            )
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(len(self.bus.published), 1)
            self.assertEqual(self.swarm.task_orchestrator.get_stats()["assigned"], 0)
            self.assertEqual(self.swarm.task_orchestrator.get_stats()["failed"], 1)
        finally:
            self.swarm.complete_task = original_complete


if __name__ == "__main__":
    unittest.main()
