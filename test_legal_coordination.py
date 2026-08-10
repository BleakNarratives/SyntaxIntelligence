#!/usr/bin/env python3
"""Tests for Syntax Legal Mind swarm/boardroom coordination."""

from __future__ import annotations

import json
import sys
import unittest
from typing import Any
from unittest.mock import patch

from SyntaxIntelligence.event_bus import SyntaxEventBus
from SyntaxIntelligence.legal_coordination import (
    BOARDROOM_GUIDANCE_CHANNEL,
    BOARDROOM_REQUEST_CHANNEL,
    MAX_PACKET_TEXT,
    OVERSIGHT_REQUEST_CHANNEL,
    SyntaxLegalCoordinator,
    _safe_digest,
)
from SyntaxIntelligence.outclaw_task_adapter import OutClawTaskAdapter
from SyntaxIntelligence.syntax_core import SyntaxSwarm
from SyntaxIntelligence.swarm_charter import AgentTier


class FakeReport:
    def __init__(self, text: str) -> None:
        self.text = text

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "findings": [{
                "severity": "HIGH",
                "rule": "EXISTENCE",
                "citation": "99 U.S.C. § 9999",
                "sentence": "Jane Doe SSN 123-45-6789 secret legal text",
            }],
            "summary": {
                "severity_counts": {"HIGH": 1},
                "safe_to_draft": False,
            },
        }


class TestLegalCoordinator(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = SyntaxEventBus()
        self.shared_events: list[tuple[str, str, dict[str, Any]]] = []
        self.coordinator = SyntaxLegalCoordinator(
            self.bus,
            shared_emit=lambda source, event_type, payload: self.shared_events.append(
                (source, event_type, payload)
            ),
        )
        self.coordinator.attach()

    def test_findings_publish_redacted_oversight_packet(self) -> None:
        observed: list[dict[str, Any]] = []
        self.bus.subscribe(
            "observer", OVERSIGHT_REQUEST_CHANNEL,
            lambda _a, _c, payload: observed.append(payload),
        )
        packet = self.coordinator.handle_findings({
            "audit_id": "audit-1",
            "severity_counts": {"HIGH": 1},
            "safe_to_draft": False,
            "high_count": 1,
            "high_findings": [{
                "rule": "EXISTENCE",
                "citation_fp": "abc123",
                "excerpt": "Jane Doe SSN 123-45-6789",
            }],
            "raw_text": "SECRET RAW LEGAL TEXT",
        })

        self.assertEqual(observed[0], packet)
        self.assertTrue(packet["oversight_required"])
        self.assertTrue(packet["human_approval_required"])
        self.assertFalse(packet["execution_allowed"])
        self.assertNotIn("SECRET RAW LEGAL TEXT", str(packet))
        self.assertNotIn("Jane Doe", str(packet))
        self.assertNotIn("123-45-6789", str(packet))
        self.assertIn("REDACTED", str(packet))
        self.assertEqual(self.shared_events[0][1], "syntax_legal_oversight_requested")

    def test_duplicate_audit_is_not_republished(self) -> None:
        packet = {"audit_id": "audit-dup", "safe_to_draft": True}
        first = self.coordinator.handle_findings(packet)
        second = self.coordinator.handle_findings(packet)
        self.assertEqual(first, second)
        self.assertEqual(len(self.shared_events), 1)

    def test_boardroom_guidance_is_explicit_and_non_executable(self) -> None:
        requests: list[dict[str, Any]] = []
        guidance_events: list[dict[str, Any]] = []
        self.bus.subscribe(
            "request-capture", BOARDROOM_REQUEST_CHANNEL,
            lambda _a, _c, payload: requests.append(payload),
        )
        self.bus.subscribe(
            "guidance-capture", BOARDROOM_GUIDANCE_CHANNEL,
            lambda _a, _c, payload: guidance_events.append(payload),
        )

        def boardroom(context: dict[str, Any]) -> dict[str, Any]:
            self.assertIsNone(context["raw"])
            content = context["data"]["content"]
            self.assertTrue(content)
            self.assertIn("severity_counts", content)
            self.assertLessEqual(len(content), MAX_PACKET_TEXT)
            self.assertIsInstance(json.loads(content), dict)
            self.assertNotIn("SECRET", str(context))
            return {"synthesis": {
                "verdict": "conditional",
                "chairman_statement": "Hold for human citation review.",
                "primary_risk": "Unverified authority",
            }}

        packet = self.coordinator.handle_findings({
            "audit_id": "audit-board",
            "safe_to_draft": False,
            "high_count": 1,
            "high_findings": [{"rule": "EXISTENCE", "excerpt": "REDACTED"}],
        })
        guidance = self.coordinator.request_guidance(packet, boardroom_runner=boardroom)

        self.assertEqual(requests[0]["context"]["type"], "legal_oversight")
        self.assertEqual(guidance_events[0], guidance)
        self.assertEqual(guidance["verdict"], "conditional")
        self.assertTrue(guidance["human_approval_required"])
        self.assertFalse(guidance["execution_allowed"])
        self.assertIn("syntax_boardroom_guidance", [event[1] for event in self.shared_events])

    def test_boardroom_failure_is_reported_without_raising(self) -> None:
        guidance = self.coordinator.request_guidance(
            {"audit_id": "audit-fail", "safe_to_draft": False},
            boardroom_runner=lambda _context: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        self.assertEqual(guidance["status"], "failed")
        self.assertNotIn("boom", guidance["guidance"])
        self.assertFalse(guidance["execution_allowed"])

    def test_cross_device_routes_requests_and_guidance_to_distinct_channels(self) -> None:
        published: list[tuple[str, dict[str, Any]]] = []

        class Bridge:
            def publish_local(self, channel: str, payload: dict[str, Any], **_kwargs: Any) -> None:
                published.append((channel, payload))

        coordinator = SyntaxLegalCoordinator(SyntaxEventBus(), sync_bridge=Bridge())
        oversight = coordinator.handle_findings({
            "audit_id": "audit-routes",
            "safe_to_draft": False,
            "high_count": 1,
        })
        coordinator.request_guidance(
            oversight,
            boardroom_runner=lambda _context: {"synthesis": {"verdict": "conditional"}},
        )
        self.assertEqual(
            [channel for channel, _payload in published],
            [OVERSIGHT_REQUEST_CHANNEL, BOARDROOM_REQUEST_CHANNEL, BOARDROOM_GUIDANCE_CHANNEL],
        )

    def test_safe_digest_drops_unapproved_fields_and_bounds_text(self) -> None:
        safe = _safe_digest({
            "audit_id": "a",
            "raw_text": "SECRET",
            "severity_counts": {"HIGH": "bad", "LOW": 2},
            "high_count": "nan",
            "high_findings": [{
                "rule": "R",
                "excerpt": "Jane Doe 21-CV-12345 " + "x" * 1000,
            }],
        })
        self.assertNotIn("raw_text", safe)
        self.assertNotIn("Jane Doe", str(safe))
        self.assertNotIn("21-CV-12345", str(safe))
        self.assertIn("REDACTED", str(safe))
        self.assertEqual(safe["severity_counts"]["HIGH"], 0)
        self.assertEqual(safe["high_count"], 0)
        self.assertLessEqual(len(safe["high_findings"][0]["excerpt"]), MAX_PACKET_TEXT)
        self.assertEqual(_safe_digest(None)["high_count"], 0)  # type: ignore[arg-type]

    def test_fallback_redactor_handles_offline_outclaw(self) -> None:
        payload = {
            "audit_id": "fallback",
            "high_count": 1,
            "high_findings": [{
                "rule": "Jane Doe",
                "excerpt": "Jane Doe SSN 123-45-6789 docket 21-CV-12345",
            }],
        }
        with patch.dict(sys.modules, {"OutClaw.outclaw_bus": None}):
            safe = _safe_digest(payload)
        rendered = str(safe)
        self.assertNotIn("Jane Doe", rendered)
        self.assertNotIn("123-45-6789", rendered)
        self.assertNotIn("21-CV-12345", rendered)
        self.assertIn("REDACTED", rendered)

    def test_formal_oversight_queue_is_opt_in_and_fail_closed(self) -> None:
        proposed: list[tuple[str, str, str, str]] = []
        coordinator = SyntaxLegalCoordinator(
            SyntaxEventBus(),
            oversight_proposer=lambda *args: proposed.append(args),
        )
        packet = coordinator.handle_findings({
            "audit_id": "audit-formal",
            "safe_to_draft": False,
            "high_count": 1,
        })
        self.assertTrue(packet["formal_oversight_queued"])
        self.assertEqual(proposed[0][1:], (
            "legal_review",
            "audit-formal",
            "Human review required before drafting or relying on this audit.",
        ))

        failing = SyntaxLegalCoordinator(
            SyntaxEventBus(),
            oversight_proposer=lambda *_args: (_ for _ in ()).throw(RuntimeError("no")),
        )
        failed_packet = failing.handle_findings({
            "audit_id": "audit-formal-fail",
            "safe_to_draft": False,
            "high_count": 1,
        })
        self.assertFalse(failed_packet["formal_oversight_queued"])
        self.assertTrue(failed_packet["human_approval_required"])
        self.assertFalse(failed_packet["execution_allowed"])


class TestSyntaxSwarmCoordination(unittest.TestCase):
    def test_convene_legal_returns_oversight_and_optional_guidance(self) -> None:
        swarm = SyntaxSwarm(auto_assemble=False)
        agent = swarm._hardened.register_agent(
            "outclaw-auditor", "Legal Mind", capabilities=["legal_audit"]
        )
        agent.tier = AgentTier.WORKER
        adapter = OutClawTaskAdapter(
            swarm._hardened,
            type("Bus", (), {"publish_findings": lambda _self, _report: {
                "audit_id": "audit-swarm",
                "severity_counts": {"HIGH": 1},
                "safe_to_draft": False,
                "high_count": 1,
                "high_findings": [{"rule": "EXISTENCE", "excerpt": "REDACTED"}],
            }})(),
            audit_fn=lambda text, *, use_llm=False: FakeReport(text),
        )
        adapter.attach()
        runner_calls: list[dict[str, Any]] = []

        def runner(context: dict[str, Any]) -> dict[str, Any]:
            runner_calls.append(context)
            return {"synthesis": {"verdict": "conditional", "chairman_statement": "Review."}}

        swarm.legal_coordinator.boardroom_runner = runner
        outcome = swarm.convene_legal(
            "SECRET RAW TEXT", adapter=adapter, request_boardroom=True,
        )
        self.assertEqual(outcome["adapter_result"]["status"], "completed")
        self.assertIn("oversight", outcome["coordination"])
        self.assertEqual(outcome["coordination"]["guidance"]["verdict"], "conditional")
        self.assertEqual(len(runner_calls), 1)
        self.assertNotIn("SECRET RAW TEXT", str(runner_calls[0]))

    def test_owned_sync_bridge_is_stopped_without_stopping_injected_bridge(self) -> None:
        class Bridge:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        owned = Bridge()
        with patch(
            "SyntaxIntelligence.syntax_core._load_optional_sync_bridge",
            return_value=owned,
        ):
            swarm = SyntaxSwarm(auto_assemble=False)
        swarm.stop()
        self.assertTrue(owned.stopped)

    def test_assembled_swarm_receives_oversight_channel(self) -> None:
        swarm = SyntaxSwarm(auto_assemble=True)
        try:
            self.assertTrue(swarm.agents)
            subscribed_agents = [
                agent_id
                for agent_id in swarm.agents
                if agent_id in swarm.event_bus._subscriptions.get(
                    "legal.oversight.request", {}
                )
            ]
            self.assertTrue(subscribed_agents)
            before = len(swarm.memory.by_agent(subscribed_agents[0]))
            swarm.legal_coordinator.handle_findings({
                "audit_id": "audit-delivery",
                "safe_to_draft": False,
                "high_count": 1,
            })
            after_events = swarm.memory.by_agent(subscribed_agents[0])
            self.assertGreater(len(after_events), before)
            self.assertTrue(any(
                event["event_type"] == "action"
                and "legal.oversight.request" in event["description"]
                for event in after_events
            ))
        finally:
            swarm.stop()


if __name__ == "__main__":
    unittest.main()
