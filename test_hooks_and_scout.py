#!/usr/bin/env python3
"""
Tests for hooks/triggers/automations engine, Commerce Scout, and WebSocket broadcaster.

NOTE: hooks_engine.py and websocket_events.py were deleted during the engine merge.
Hook/trigger/automation tests are skipped; CommerceScout tests remain active.
"""
import sys
import os
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from SyntaxIntelligence.hardened_engine import HardenedSwarm

# hooks_engine was deleted — skip all dependent classes
try:
    from SyntaxIntelligence.hooks_engine import (
        HooksEngine, HookEngine, TriggerEngine, AutomationEngine,
        HookPriority, TriggerOperator, AutomationStep,
    )
    _HOOKS_AVAILABLE = True
except ImportError:
    _HOOKS_AVAILABLE = False

from SyntaxIntelligence.commerce_scout import CommerceScout, build_daily_commerce_report


# ═══════════════════════════════════════════════════════════════
# HOOK ENGINE TESTS
# ═══════════════════════════════════════════════════════════════

@unittest.skipUnless(_HOOKS_AVAILABLE, "hooks_engine.py deleted during engine merge")
class TestHookEngine(unittest.TestCase):

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.engine = HooksEngine(self.swarm.event_bus)

    def test_register_hook(self):
        """Should register a hook on a channel."""
        fired = []
        self.engine.hooks.register("test_hook", "task.offered", lambda aid, ch, d: fired.append(d))
        hooks = self.engine.hooks.list_hooks()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["hook_id"], "test_hook")

    def test_hook_fires_on_event(self):
        """Hook should fire when matching event occurs."""
        fired = []
        self.engine.hooks.register("fire_hook", "task.offered", lambda aid, ch, d: fired.append(d))

        # Publish to the channel
        self.swarm.event_bus.publish("test", "task.offered", {"task_id": "t1"})

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["task_id"], "t1")

    def test_hook_with_condition(self):
        """Hook should only fire when condition is met."""
        fired = []
        condition = lambda data: data.get("priority", 0) > 5

        self.engine.hooks.register("cond_hook", "task.offered",
                                   lambda aid, ch, d: fired.append(d),
                                   condition=condition)

        # Low priority — should not fire
        self.swarm.event_bus.publish("test", "task.offered", {"priority": 3})
        self.assertEqual(len(fired), 0)

        # High priority — should fire
        self.swarm.event_bus.publish("test", "task.offered", {"priority": 10})
        self.assertEqual(len(fired), 1)

    def test_hook_max_fires(self):
        """Hook should disable after max_fires."""
        fired = []
        self.engine.hooks.register("max_hook", "task.offered",
                                   lambda aid, ch, d: fired.append(d),
                                   max_fires=2)

        self.swarm.event_bus.publish("test", "task.offered", {"x": 1})
        self.swarm.event_bus.publish("test", "task.offered", {"x": 2})
        self.swarm.event_bus.publish("test", "task.offered", {"x": 3})

        self.assertEqual(len(fired), 2)

    def test_unregister_hook(self):
        """Should unregister a hook."""
        self.engine.hooks.register("del_hook", "task.offered", lambda a, c, d: None)
        self.assertTrue(self.engine.hooks.unregister("del_hook"))
        self.assertEqual(len(self.engine.hooks.list_hooks()), 0)

    def test_hook_log(self):
        """Should log hook firings."""
        self.engine.hooks.register("log_hook", "task.offered", lambda a, c, d: None)
        self.swarm.event_bus.publish("test", "task.offered", {"x": 1})
        log = self.engine.hooks.get_log()
        self.assertGreater(len(log), 0)


# ═══════════════════════════════════════════════════════════════
# TRIGGER ENGINE TESTS
# ═══════════════════════════════════════════════════════════════

@unittest.skipUnless(_HOOKS_AVAILABLE, "hooks_engine.py deleted during engine merge")
class TestTriggerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TriggerEngine()

    def test_trigger_fires_when_threshold_met(self):
        """Trigger should fire when metric crosses threshold."""
        fired = []
        self.engine.register("t1", "task_stats.completed", TriggerOperator.GTE, 5,
                             lambda tid, val, thresh: fired.append((tid, val)))

        state = {"task_stats": {"completed": 10}}
        result = self.engine.evaluate(state)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0], "t1")

    def test_trigger_does_not_fire_below_threshold(self):
        """Trigger should not fire below threshold."""
        fired = []
        self.engine.register("t2", "task_stats.completed", TriggerOperator.GT, 100,
                             lambda tid, val, thresh: fired.append(tid))

        state = {"task_stats": {"completed": 50}}
        result = self.engine.evaluate(state)
        self.assertEqual(len(fired), 0)

    def test_trigger_cooldown(self):
        """Trigger should respect cooldown period."""
        fired = []
        self.engine.register("t3", "agent_count", TriggerOperator.GTE, 1,
                             lambda tid, val, thresh: fired.append(tid),
                             cooldown_seconds=999)

        state = {"agent_count": 5}
        self.engine.evaluate(state)
        self.engine.evaluate(state)  # Should not fire again due to cooldown

        self.assertEqual(len(fired), 1)

    def test_trigger_log(self):
        """Should log trigger firings."""
        self.engine.register("t_log", "agent_count", TriggerOperator.GT, 0,
                             lambda tid, val, thresh: None)
        self.engine.evaluate({"agent_count": 5})
        log = self.engine.get_log()
        self.assertGreater(len(log), 0)


# ═══════════════════════════════════════════════════════════════
# AUTOMATION ENGINE TESTS
# ═══════════════════════════════════════════════════════════════

@unittest.skipUnless(_HOOKS_AVAILABLE, "hooks_engine.py deleted during engine merge")
class TestAutomationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = AutomationEngine()

    def test_register_and_execute(self):
        """Should register and execute an automation."""
        results = []
        self.engine.register_action("log_msg", lambda params, ctx: results.append(params["msg"]))

        steps = [
            AutomationStep(step_id="s1", action="log_msg", params={"msg": "hello"}),
            AutomationStep(step_id="s2", action="log_msg", params={"msg": "world"}),
        ]

        self.engine.register("auto1", "Test Auto", "A test automation", steps=steps)
        result = self.engine.execute("auto1")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps_completed"], 2)
        self.assertEqual(results, ["hello", "world"])

    def test_automation_with_dependencies(self):
        """Should skip steps with unmet dependencies."""
        results = []
        self.engine.register_action("run", lambda params, ctx: results.append(params["v"]))

        steps = [
            AutomationStep(step_id="s1", action="run", params={"v": 1}),
            AutomationStep(step_id="s2", action="run", params={"v": 2}, depends_on=["s1"]),
            AutomationStep(step_id="s3", action="run", params={"v": 3}, depends_on=["missing"]),
        ]

        self.engine.register("auto2", "Dep Test", "Tests dependencies", steps=steps)
        result = self.engine.execute("auto2")

        self.assertEqual(result["steps_completed"], 2)
        self.assertEqual(results, [1, 2])

    def test_automation_list(self):
        """Should list registered automations."""
        self.engine.register("a1", "Auto 1", "First")
        self.engine.register("a2", "Auto 2", "Second")
        autos = self.engine.list_automations()
        self.assertEqual(len(autos), 2)


# ═══════════════════════════════════════════════════════════════
# WEBSOCKET BROADCASTER TESTS
# ═══════════════════════════════════════════════════════════════

@unittest.skip("websocket_events module removed during engine merge")
class TestLiveEventBroadcaster(unittest.TestCase):

    def setUp(self):
        self.broadcaster = LiveEventBroadcaster()

    def test_status(self):
        """Should report status correctly."""
        status = self.broadcaster.get_status()
        self.assertEqual(status["connected_clients"], 0)

    def test_event_log(self):
        """Should log broadcast events."""
        # Simulate logging
        self.broadcaster._event_log.append({
            "channel": "test",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {"msg": "test"},
        })
        log = self.broadcaster.get_event_log()
        self.assertEqual(len(log), 1)

    def test_bridge_init(self):
        """Bridge should initialize without errors."""
        from SyntaxIntelligence.event_bus import SyntaxEventBus
        bus = SyntaxEventBus()
        bridge = EventBusWebSocketBridge(bus, self.broadcaster)
        self.assertFalse(bridge._active)


# ═══════════════════════════════════════════════════════════════
# COMMERCE SCOUT TESTS
# ═══════════════════════════════════════════════════════════════

class TestCommerceScout(unittest.TestCase):

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.scout = CommerceScout(self.swarm)

    def test_daily_report(self):
        """Should generate a daily commerce report."""
        result = self.scout.execute("scout_01", "task_001", {"report_type": "daily"})

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.dispatcher, "commerce_scout")
        self.assertGreater(len(result.findings), 0)
        self.assertIn("bottlenecks", result.metadata)
        self.assertIn("solutions", result.metadata)
        self.assertIn("risk_score", result.metadata)
        self.assertGreater(result.metadata["risk_score"], 0)

    def test_persona_focus(self):
        """Should focus on a single persona."""
        result = self.scout.execute("scout_01", "task_002", {
            "report_type": "persona_focus",
            "persona": "forge",
        })

        self.assertEqual(result.status, "completed")
        # All findings should be from forge
        for f in result.findings:
            self.assertEqual(f["persona"], "forge")

    def test_deep_dive(self):
        """Should run deep dive on specific area."""
        result = self.scout.execute("scout_01", "task_003", {
            "report_type": "deep_dive",
            "area": "payment_processing",
        })

        self.assertEqual(result.status, "completed")
        self.assertGreater(len(result.findings), 0)

    def test_all_personas(self):
        """Should run all 6 personas in daily report."""
        result = self.scout.execute("scout_01", "task_004", {"report_type": "daily"})

        personas_found = set(f["persona"] for f in result.findings)
        self.assertEqual(len(personas_found), 6, f"Expected 6 personas, got: {personas_found}")

    def test_bottleneck_identification(self):
        """Should identify bottlenecks from findings."""
        result = self.scout.execute("scout_01", "task_005", {"report_type": "daily"})

        bottlenecks = result.metadata["bottlenecks"]
        self.assertGreater(len(bottlenecks), 0)
        for b in bottlenecks:
            self.assertIn("area", b)
            self.assertIn("severity", b)
            self.assertIn("titles", b)

    def test_solutions_proposed(self):
        """Should propose solutions for bottlenecks."""
        result = self.scout.execute("scout_01", "task_006", {"report_type": "daily"})

        solutions = result.metadata["solutions"]
        self.assertGreater(len(solutions), 0)
        for s in solutions:
            self.assertIn("solution", s)
            self.assertIn("priority", s)
            self.assertIn("timeline", s)

    def test_risk_score(self):
        """Should calculate risk score between 0 and 100."""
        result = self.scout.execute("scout_01", "task_007", {"report_type": "daily"})

        score = result.metadata["risk_score"]
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_build_daily_report_function(self):
        """Should build report via helper function."""
        report = build_daily_commerce_report(self.scout)
        self.assertEqual(report["status"], "completed")
        self.assertIn("metadata", report)
        self.assertIn("risk_score", report["metadata"])


# ═══════════════════════════════════════════════════════════════
# HOOKS ENGINE INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════

@unittest.skipUnless(_HOOKS_AVAILABLE, "hooks_engine.py deleted during engine merge")
class TestHooksEngineIntegration(unittest.TestCase):

    def setUp(self):
        self.swarm = HardenedSwarm()
        self.engine = HooksEngine(self.swarm.event_bus)

    def test_get_status(self):
        """Should report unified status."""
        status = self.engine.get_status()
        self.assertIn("hooks", status)
        self.assertIn("triggers", status)
        self.assertIn("automations", status)

    def test_trigger_evaluation(self):
        """Should evaluate triggers against swarm state."""
        fired = []
        self.engine.triggers.register("test_t", "agent_count", TriggerOperator.GTE, 1,
                                      lambda tid, val, thresh: fired.append(tid))

        state = {"agent_count": 5}
        result = self.engine.check_triggers(state)
        self.assertEqual(len(fired), 1)

    def test_get_log(self):
        """Should return logs from all components."""
        logs = self.engine.get_log()
        self.assertIn("hooks", logs)
        self.assertIn("triggers", logs)
        self.assertIn("automations", logs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
