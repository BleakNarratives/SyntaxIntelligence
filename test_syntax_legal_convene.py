#!/usr/bin/env python3
"""Focused tests for SyntaxSwarm.convene_legal (OutClaw Legal Mind convene path)."""

from __future__ import annotations

import unittest
from typing import Any

from SyntaxIntelligence.outclaw_task_adapter import OutClawTaskAdapter
from SyntaxIntelligence.syntax_core import SyntaxSwarm
from SyntaxIntelligence.swarm_charter import AgentTier


class FakeReport:
    text: str
    findings: list[dict[str, Any]]
    summary: dict[str, Any]

    def __init__(self, text: str, findings: list[dict[str, Any]],
                 summary: dict[str, Any]) -> None:
        self.text = text
        self.findings = findings
        self.summary = summary

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "findings": self.findings,
                "summary": self.summary}


class FakeOutClawBus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish_findings(self, report: dict[str, Any]) -> dict[str, Any]:
        digest = {
            "audit_id": "audit-cli-1",
            "severity_counts": report["summary"]["severity_counts"],
            "safe_to_draft": report["summary"]["safe_to_draft"],
            "high_count": 1,
            "high_findings": [{"rule": "EXISTENCE", "citation_fp": "abc123",
                               "excerpt": "REDACTED"}],
        }
        self.published.append(digest)
        return digest


class TestConveneLegal(unittest.TestCase):
    def setUp(self) -> None:
        self.swarm = SyntaxSwarm(auto_assemble=False)
        self.audit_calls: list[tuple[str, bool]] = []

        def audit(text: str, *, use_llm: bool = False) -> FakeReport:
            self.audit_calls.append((text, use_llm))
            return FakeReport(
                text=text,
                findings=[{"severity": "HIGH", "sentence": "secret raw text",
                           "citation": "99 U.S.C. § 9999"}],
                summary={"severity_counts": {"HIGH": 1}, "safe_to_draft": False},
            )

        # Register the auditor exactly like convene_legal does, so the
        # injected adapter can be constructed with the real swarm. Tier must
        # be WORKER+ (RECRUIT lacks PRIV_ACCEPT_TASKS — the accept would be
        # rejected and the audit would never run). Matches the canonical
        # adapter test setUp.
        if "outclaw-auditor" not in self.swarm._hardened.agents:
            agent = self.swarm._hardened.register_agent(
                "outclaw-auditor", "OutClaw Legal Mind", capabilities=["legal_audit"]
            )
            agent.tier = AgentTier.WORKER
        self.bus = FakeOutClawBus()
        self.adapter = OutClawTaskAdapter(
            self.swarm._hardened, self.bus, audit_fn=audit,
            agent_id="outclaw-auditor",
        )
        self.adapter.attach()

    def test_convenes_and_publishes_once(self) -> None:
        outcome = self.swarm.convene_legal(
            "Under 42 U.S.C. § 1983, claim.", adapter=self.adapter
        )

        self.assertEqual(outcome["offered"]["status"], "offered")
        result = outcome["adapter_result"]
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digest"]["audit_id"], "audit-cli-1")
        self.assertEqual(len(self.audit_calls), 1)
        self.assertEqual(len(self.bus.published), 1)
        # Legal text/findings stay out of the swarm-facing result.
        self.assertNotIn("text", result)
        self.assertNotIn("secret raw text", str(result))

    def test_llm_flag_passthrough(self) -> None:
        self.swarm.convene_legal("text", use_llm=True, adapter=self.adapter)
        self.assertEqual(self.audit_calls, [("text", True)])

    def test_empty_text_requests_info_not_failure(self) -> None:
        outcome = self.swarm.convene_legal("", adapter=self.adapter)
        result = outcome["adapter_result"]
        self.assertEqual(result["status"], "request_info")
        self.assertEqual(self.audit_calls, [])
        self.assertEqual(self.bus.published, [])

    def test_registers_auditor_agent_when_missing(self) -> None:
        swarm = SyntaxSwarm(auto_assemble=False)
        self.assertNotIn("outclaw-auditor", swarm._hardened.agents)
        # Inject a prebuilt adapter bound to the fresh swarm so no real
        # OutClaw import happens during the test.
        if "outclaw-auditor" not in swarm._hardened.agents:
            agent = swarm._hardened.register_agent(
                "outclaw-auditor", "OutClaw Legal Mind", capabilities=["legal_audit"]
            )
            agent.tier = AgentTier.WORKER
        bus = FakeOutClawBus()
        adapter = OutClawTaskAdapter(
            swarm._hardened, bus, audit_fn=lambda text, *, use_llm=False: FakeReport(
                text=text,
                findings=[],
                summary={"severity_counts": {}, "safe_to_draft": True},
            ),
            agent_id="outclaw-auditor",
        )
        adapter.attach()
        outcome = swarm.convene_legal("42 U.S.C. § 1983", adapter=adapter)
        self.assertEqual(outcome["adapter_result"]["status"], "completed")
        # Agent is now tracked in governance and can take future tasks.
        self.assertIn("outclaw-auditor", swarm._hardened.agents)


if __name__ == "__main__":
    unittest.main()
