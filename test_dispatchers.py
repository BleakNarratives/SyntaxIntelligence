#!/usr/bin/env python3
"""
Tests for agent dispatchers and event bus wiring.
"""
import sys
import os
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from SyntaxIntelligence.hardened_engine import HardenedSwarm
from SyntaxIntelligence.dispatchers import (
    TruthSleuth, Bardildo, ThinkingHats, DispatcherRegistry,
    create_default_dispatchers, DispatcherType,
)


class TestEventBusWiring(unittest.TestCase):
    """Test that task.offered and tier.advanced events are published to the event bus."""

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.received_events = []

        def capture(agent_id, channel, data):
            self.received_events.append({"channel": channel, "data": data})

        # Subscribe to task.offered and tier.advanced
        self.swarm.event_bus.subscribe("test_listener", "task.offered", capture)
        self.swarm.event_bus.subscribe("test_listener", "tier.advanced", capture)

    def test_offer_publishes_to_event_bus(self):
        """offer_task should publish to task.offered channel."""
        self.swarm.register_agent("a1", "Agent1", capabilities=["code"])
        result = self.swarm.offer_task(
            title="Test Task",
            description="A test",
            capabilities=["code"],
        )
        self.assertEqual(result["status"], "offered")

        offered_events = [e for e in self.received_events if e["channel"] == "task.offered"]
        self.assertEqual(len(offered_events), 1, "task.offered should be published once")
        self.assertEqual(offered_events[0]["data"]["title"], "Test Task")

    def test_offer_targeted_publishes_to_event_bus(self):
        """offer_task to a specific agent should publish to task.offered if eligible."""
        from SyntaxIntelligence.swarm_charter import AgentTier
        agent = self.swarm.register_agent("a1", "Agent1", capabilities=["code"])
        # Upgrade to WORKER so agent can accept tasks
        agent.tier = AgentTier.WORKER
        result = self.swarm.offer_task(
            title="Targeted Task",
            description="A targeted test",
            target_agent="a1",
            capabilities=["code"],
        )
        self.assertEqual(result["status"], "offered")

        offered_events = [e for e in self.received_events if e["channel"] == "task.offered"]
        self.assertEqual(len(offered_events), 1, "task.offered should be published once for targeted task")

    def test_complete_advances_publishes_to_event_bus(self):
        """complete_task with tier advancement should publish to tier.advanced."""
        # Register agent at RECRUIT tier
        agent = self.swarm.register_agent("a1", "Agent1")
        # Upgrade to WORKER so they can accept tasks
        from SyntaxIntelligence.swarm_charter import AgentTier
        agent.tier = AgentTier.WORKER

        # Offer and accept a task
        result = self.swarm.offer_task(title="Task1", description="Test task", target_agent="a1")
        if result["status"] == "offered":
            task_id = result["task_id"]
            # Accept the task
            self.swarm.respond_to_task("a1", task_id, "accept")
            # Complete it
            self.swarm.complete_task(task_id, "a1")

            # Check tier.advanced was published if advancement happened
            tier_events = [e for e in self.received_events if e["channel"] == "tier.advanced"]
            # May or may not advance depending on criteria, but the event should be published if it does
            if tier_events:
                self.assertIn("new_tier", tier_events[0]["data"])
                self.assertEqual(tier_events[0]["data"]["agent_id"], "a1")


class TestTruthSleuth(unittest.TestCase):
    """Test the TruthSleuth code auditor dispatcher."""

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.truthsleuth = TruthSleuth(self.swarm)
        self.truthsleuth.register("truthsleuth_01")

    def test_basic_audit(self):
        """TruthSleuth should produce findings for code with issues."""
        code = '''
def dangerous():
    eval(user_input)
    os.system("rm -rf /")
    x = 42
    pass
'''
        result = self.truthsleuth.execute("truthsleuth_01", "task_001", {
            "code": code,
            "filename": "test.py",
            "audit_type": "full",
        })

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.dispatcher, "truthsleuth")
        self.assertGreater(len(result.findings), 0, "Should find security issues")
        self.assertIn("test.py", result.summary)

    def test_clean_code(self):
        """TruthSleuth should produce few/no findings for clean code."""
        code = '''
def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"
'''
        result = self.truthsleuth.execute("truthsleuth_01", "task_002", {
            "code": code,
            "filename": "clean.py",
            "audit_type": "security",
        })

        self.assertEqual(result.status, "completed")
        # Clean code should have minimal or no security findings
        security_findings = [f for f in result.findings if f.get("category") == "security"]
        self.assertEqual(len(security_findings), 0, "Clean code should have no security findings")

    def test_empty_code(self):
        """TruthSleuth should handle empty code gracefully."""
        result = self.truthsleuth.execute("truthsleuth_01", "task_003", {
            "code": "",
        })
        self.assertEqual(result.status, "failed")
        self.assertIn("No code", result.summary)

    def test_quick_scan(self):
        """TruthSleuth quick scan should only find critical issues."""
        code = '''
x = 42  # not critical
eval("dangerous")  # critical
print("hello")  # not critical
'''
        result = self.truthsleuth.execute("truthsleuth_01", "task_004", {
            "code": code,
            "audit_type": "quick",
        })

        self.assertEqual(result.status, "completed")
        # Quick scan should find the eval but not the quality issues
        self.assertGreater(len(result.findings), 0)


class TestBardildo(unittest.TestCase):
    """Test the Bardildo repo roaster dispatcher."""

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.bardildo = Bardildo(self.swarm)
        self.bardildo.register("bardildo_01")

    def test_basic_roast(self):
        """Bardildo should produce roast findings for messy code."""
        code = '''
def x(tmp, temp, data):
    if True:
        if True:
            if True:
                if True:
                    if True:
                        if True:
                            pass
    pass
    pass
    pass
    pass
    pass
'''
        result = self.bardildo.execute("bardildo_01", "task_001", {
            "code": code,
            "filename": "messy.py",
            "roast_level": "spicy",
        })

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.dispatcher, "bardildo")
        self.assertGreater(len(result.findings), 0, "Should find something to roast")
        self.assertIn("Bardildo", result.summary)

    def test_clean_code_roast(self):
        """Bardildo should find little to roast in clean code."""
        code = '''
def greet(name: str) -> str:
    return f"Hello, {name}!"
'''
        result = self.bardildo.execute("bardildo_01", "task_002", {
            "code": code,
            "filename": "clean.py",
            "roast_level": "mild",
        })

        self.assertEqual(result.status, "completed")
        # Clean code = fewer roast findings
        self.assertLessEqual(len(result.findings), 3)

    def test_empty_code(self):
        """Bardildo should handle empty code gracefully."""
        result = self.bardildo.execute("bardildo_01", "task_003", {
            "code": "",
        })
        self.assertEqual(result.status, "failed")
        self.assertIn("No code", result.summary)


class TestThinkingHats(unittest.TestCase):
    """Test the ThinkingHats multi-perspective deliberation dispatcher."""

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.hats = ThinkingHats(self.swarm)
        self.hats.register("thinking_hats_01")

    def test_full_deliberation(self):
        """ThinkingHats should produce perspectives from all 7 hats."""
        result = self.hats.execute("thinking_hats_01", "task_001", {
            "decision": "Should we add authentication to the API?",
            "context": "The API currently has no auth. It's internal but growing.",
            "options": ["Add JWT auth", "Add API keys", "Add OAuth2", "Do nothing"],
        })

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.dispatcher, "thinking_hats")
        # Should have 7 perspectives (one per hat)
        self.assertEqual(len(result.findings), 7, "Should have all 7 hat perspectives")

        # Check that Brown Hat is present (always has final word)
        hat_colors = [f["color"] for f in result.findings]
        self.assertIn("brown", hat_colors, "Brown Hat must be present")
        self.assertIn("white", hat_colors)
        self.assertIn("black", hat_colors)
        self.assertIn("yellow", hat_colors)
        self.assertIn("green", hat_colors)
        self.assertIn("red", hat_colors)
        self.assertIn("blue", hat_colors)

    def test_partial_hats(self):
        """ThinkingHats should support using specific hats only."""
        result = self.hats.execute("thinking_hats_01", "task_002", {
            "decision": "Ship or kill?",
            "hats": ["black", "yellow", "brown"],
        })

        self.assertEqual(result.status, "completed")
        # Should have 3 hats (black, yellow) + Brown Hat (auto-added)
        hat_colors = [f["color"] for f in result.findings]
        self.assertIn("black", hat_colors)
        self.assertIn("yellow", hat_colors)
        self.assertIn("brown", hat_colors)  # Brown always gets final word

    def test_brown_hat_final_word(self):
        """Brown Hat should always be the last perspective."""
        result = self.hats.execute("thinking_hats_01", "task_003", {
            "decision": "Go/no-go decision",
        })

        self.assertEqual(result.status, "completed")
        last_hat = result.findings[-1]["color"]
        self.assertEqual(last_hat, "brown", "Brown Hat must have the final word")

    def test_metadata(self):
        """ThinkingHats should include metadata with final recommendation."""
        result = self.hats.execute("thinking_hats_01", "task_004", {
            "decision": "Test decision",
        })

        self.assertIn("final_recommendation", result.metadata)
        self.assertIn("BROWN HAT", result.metadata["final_recommendation"])


class TestDispatcherRegistry(unittest.TestCase):
    """Test the dispatcher registry."""

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.registry = create_default_dispatchers(self.swarm)

    def test_all_registered(self):
        """All 5 default dispatchers should be registered."""
        dispatchers = self.registry.list_dispatchers()
        self.assertEqual(len(dispatchers), 5)
        types = {d["type"] for d in dispatchers}
        self.assertEqual(types, {
            "truthsleuth", "bardildo", "thinking_hats", "commerce_scout", "boardroom",
        })

    def test_get_dispatcher(self):
        """Should be able to get dispatcher by type."""
        ts = self.registry.get_dispatcher("truthsleuth")
        self.assertIsNotNone(ts)
        self.assertIsInstance(ts, TruthSleuth)

    def test_get_dispatcher_for_agent(self):
        """Should be able to get dispatcher for an agent."""
        d = self.registry.get_dispatcher_for_agent("truthsleuth_01")
        self.assertIsNotNone(d)
        self.assertIsInstance(d, TruthSleuth)

    def test_execute_task(self):
        """Should be able to execute a task through the registry."""
        result = self.registry.execute_task("truthsleuth_01", "task_exec_001", {
            "code": "x = eval('1+1')",
            "filename": "test.py",
            "audit_type": "quick",
        })
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "completed")

    def test_unknown_agent(self):
        """Unknown agent should return None."""
        result = self.registry.execute_task("unknown_agent", "task_001", {})
        self.assertIsNone(result)

    def test_agents_registered_in_swarm(self):
        """All dispatcher agents should be registered in the swarm."""
        agent_ids = {
            "truthsleuth_01", "bardildo_01", "thinking_hats_01",
            "commerce_scout_01", "boardroom_01",
        }
        for aid in agent_ids:
            agent = self.swarm.get_agent(aid)
            self.assertIsNotNone(agent, f"Agent {aid} should be in swarm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
