#!/usr/bin/env python3
"""
Tests for the Hardened Swarm Engine cron scheduler.
Verifies heartbeat, auto-save, morning protocol, start/stop lifecycle.
"""

import sys
import os
import time
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from SyntaxIntelligence.hardened_engine import HardenedSwarm, _DATA_DIR
from SyntaxIntelligence.swarm_charter import AgentTier


def test_cron_not_wired_by_default():
    """Test 1: Cron jobs are not wired until start() is called."""
    swarm = HardenedSwarm()
    assert not swarm._cron_wired, "Cron should not be wired before start()"
    assert not swarm._running, "Swarm should not be running before start()"
    cron_status = swarm.get_cron_status()
    assert cron_status["running"] is False
    assert cron_status["cron_wired"] is False
    assert len(cron_status["jobs"]) == 0
    print("  ✅ Cron not wired by default")


def test_start_wires_cron_jobs():
    """Test 2: start() wires all 3 cron jobs and starts the scheduler."""
    swarm = HardenedSwarm()
    swarm.start()
    assert swarm._running, "Swarm should be running after start()"
    assert swarm._cron_wired, "Cron should be wired after start()"

    cron_status = swarm.get_cron_status()
    assert cron_status["running"] is True
    assert cron_status["cron_wired"] is True

    jobs = cron_status["jobs"]
    assert "heartbeat_broadcast" in jobs, "heartbeat_broadcast job should exist"
    assert "auto_save" in jobs, "auto_save job should exist"
    assert "morning_protocol" in jobs, "morning_protocol job should exist"
    assert jobs["heartbeat_broadcast"]["interval"] == 10.0
    assert jobs["auto_save"]["interval"] == 300.0
    assert jobs["morning_protocol"]["interval"] == 86400.0

    swarm.stop()
    print("  ✅ start() wires 3 cron jobs and starts scheduler")


def test_start_is_idempotent():
    """Test 3: Calling start() twice doesn't double-wire cron jobs."""
    swarm = HardenedSwarm()
    swarm.start()
    swarm.start()  # Should not error or double-wire
    cron_status = swarm.get_cron_status()
    jobs = cron_status["jobs"]
    assert "heartbeat_broadcast" in jobs
    assert len(jobs) == 3
    swarm.stop()
    print("  ✅ start() is idempotent")


def test_stop_saves_state():
    """Test 4: stop() stops the scheduler and saves state."""
    swarm = HardenedSwarm()
    swarm.register_agent("cron-test", "CronTest", capabilities=["test"])
    swarm.start()

    # Let at least one heartbeat fire (10s interval, but cron loop checks every 1s)
    time.sleep(2)

    swarm.stop()
    assert not swarm._running, "Swarm should not be running after stop()"

    # Verify state was saved using the specific session file
    state_file = _DATA_DIR / f"{swarm.session_id}.json"
    assert state_file.exists(), f"State file {state_file} should exist after stop()"

    # Verify the saved state contains our agent
    with open(state_file) as f:
        saved = json.load(f)
    assert "cron-test" in saved["agents"]
    print("  ✅ stop() stops scheduler and saves state")


def test_heartbeat_broadcasts():
    """Test 5: Heartbeat fires and records events."""
    swarm = HardenedSwarm()
    events_before = len(swarm.memory.events)
    swarm.start()

    # The cron scheduler fires callbacks when interval passes.
    # Since we can't wait 10s, manually trigger the callback.
    swarm._cron_heartbeat()

    events_after = len(swarm.memory.events)
    assert events_after > events_before, "Heartbeat should record events"

    # Verify the event bus got a message
    bus_stats = swarm.event_bus.get_stats()
    assert bus_stats["total_messages"] > 0, "Event bus should have messages"

    swarm.stop()
    print("  ✅ Heartbeat broadcasts and records events")


def test_morning_protocol_generates_report():
    """Test 6: Morning Protocol generates a structured report."""
    swarm = HardenedSwarm()
    swarm.register_agent("mp-alpha", "Alpha", capabilities=["code"])
    swarm.register_agent("mp-beta", "Beta", capabilities=["review"])

    report = swarm.execute_morning_protocol()

    assert report["session_id"] == swarm.session_id
    assert report["agents_total"] == 2
    assert "tier_distribution" in report
    assert "tasks" in report
    assert "memory_events" in report
    assert report["tasks"]["completed"] == 0
    assert report["tasks"]["pending"] == 0

    # Verify the event was recorded
    recent = swarm.memory.recent(5)
    mp_events = [e for e in recent if e["event_type"] == "morning_protocol"]
    assert len(mp_events) > 0, "Morning Protocol should be recorded in memory"

    swarm.stop()
    print("  ✅ Morning Protocol generates structured report")


def test_auto_save_persists_correctly():
    """Test 7: Auto-save callback persists state correctly."""
    swarm = HardenedSwarm()
    swarm.register_agent("save-test", "SaveTest", capabilities=["test"])
    swarm.start()

    # Manually trigger auto-save
    swarm._cron_auto_save()

    # Verify state file exists using the specific session file
    state_file = _DATA_DIR / f"{swarm.session_id}.json"
    assert state_file.exists(), f"State file {state_file} should exist after auto-save"

    # Load and verify
    swarm2 = HardenedSwarm()
    loaded = swarm2.load_state(swarm.session_id)
    assert loaded
    assert "save-test" in swarm2.agents
    assert swarm2.agents["save-test"].name == "SaveTest"

    swarm.stop()
    print("  ✅ Auto-save persists and reloads correctly")


def test_cron_status_api():
    """Test 8: get_cron_status returns well-formed data."""
    swarm = HardenedSwarm()
    status = swarm.get_cron_status()
    assert "running" in status
    assert "cron_wired" in status
    assert "jobs" in status
    assert isinstance(status["jobs"], dict)
    # No start/stop needed — test the API on a fresh swarm

    # Load and verify
    swarm2 = HardenedSwarm()
    loaded = swarm2.load_state(swarm.session_id)
    assert loaded
    assert "save-test" in swarm2.agents
    assert swarm2.agents["save-test"].name == "SaveTest"

    swarm.stop()
    print("  ✅ Auto-save persists and reloads correctly")


def test_cron_status_api():
    """Test 8: get_cron_status returns well-formed data."""
    swarm = HardenedSwarm()
    status = swarm.get_cron_status()
    assert "running" in status
    assert "cron_wired" in status
    assert "jobs" in status
    assert isinstance(status["jobs"], dict)
    # No start/stop needed — test the API on a fresh swarm
    print("  ✅ Cron status API returns well-formed data")


def main():
    print("\n" + "═" * 60)
    print("  CRON SCHEDULER — SMOKE TEST")
    print("═" * 60 + "\n")

    test_cron_not_wired_by_default()
    test_start_wires_cron_jobs()
    test_start_is_idempotent()
    test_stop_saves_state()
    test_heartbeat_broadcasts()
    test_morning_protocol_generates_report()
    test_auto_save_persists_correctly()
    test_cron_status_api()

    print("\n" + "═" * 60)
    print("  ALL 8 CRON TESTS PASSED ✅")
    print("  The swarm's heartbeat is live.")
    print("  Morning Protocol runs at dawn.")
    print("  State saves itself on schedule.")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
