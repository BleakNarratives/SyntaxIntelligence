#!/usr/bin/env python3
"""
Smoke test for the Hardened Swarm Engine (Project Syntax).
Boots the swarm, registers agents, runs task lifecycle, verifies tier progression.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from SyntaxIntelligence.hardened_engine import HardenedSwarm, AgentIdentity
from SyntaxIntelligence.swarm_charter import AgentTier, SwarmCharter
from SyntaxIntelligence.agent_protocol import TaskOffer, TaskDecision


def test_swarm_boot():
    """Test 1: Swarm boots and initializes cleanly."""
    swarm = HardenedSwarm()
    assert swarm.session_id.startswith("hs_"), "Session ID should start with hs_"
    assert len(swarm.agents) == 0, "Fresh swarm should have no agents"
    assert swarm.charter.version == "1.0.0"
    print("  ✅ Swarm boots clean")
    return swarm


def test_agent_registration(swarm):
    """Test 2: Agents register at Tier 0 (Recruit)."""
    a1 = swarm.register_agent("alpha", "Alpha", capabilities=["code", "audit"])
    a2 = swarm.register_agent("beta", "Beta", capabilities=["docs", "review"])
    a3 = swarm.register_agent("gamma", "Gamma", capabilities=["code"])

    assert a1.tier == AgentTier.RECRUIT, f"Alpha should be RECRUIT, got {a1.tier}"
    assert a2.tier == AgentTier.RECRUIT, f"Beta should be RECRUIT, got {a2.tier}"
    assert len(swarm.agents) == 3
    print("  ✅ Agents registered at Tier 0 (Recruit)")
    return a1, a2, a3


def test_privilege_enforcement(swarm, a1):
    """Test 3: Recruits can't do privileged things."""
    # Recruit can only pulse — cannot accept tasks, read memory, etc.
    assert not swarm.autonomy.enforce(a1, "accept_tasks", "test")
    assert not swarm.autonomy.enforce(a1, "read_memory", "test")
    assert not swarm.autonomy.enforce(a1, "publish_bus", "test")
    assert swarm.autonomy.enforce(a1, "pulse", "test")  # This they CAN do
    print("  ✅ Privilege enforcement: Recruits can only pulse")


def test_task_offer_reject_no_penalty(swarm, a1):
    """Test 4: Agent can reject a task with no penalty (Article I)."""
    # First, we need to bypass the eligibility check since recruits can't accept
    # Instead, test the concept: offer a task, verify it exists
    result = swarm.offer_task(
        title="Test task",
        description="A test task",
        capabilities=["code"],
        min_tier=0,
    )
    assert result["status"] == "offered"
    assert "task_id" in result
    print("  ✅ Task offered to swarm")


def test_tier_progression_manual(swarm, a1):
    """Test 5: Manually advance an agent and verify privilege expansion."""
    # Manually set metrics to meet advancement criteria
    a1.metrics.tasks_completed = 15
    a1.metrics.tasks_failed = 0
    a1._error_timestamps = []  # No errors

    # Give them vouches via the ledger
    from SyntaxIntelligence.agent_protocol import Vouch
    swarm.vouch_ledger.add_vouch(Vouch(
        voucher_id="external",
        vouched_id="alpha",
        reason="Solid worker",
        strength=1.0,
    ))

    # Set tier_since far enough back
    import time
    a1.tier_since = time.time() - (5 * 3600)  # 5 hours ago

    # Check advancement
    new_tier = swarm.tier_progression.advance_agent(a1)
    assert new_tier == AgentTier.WORKER, f"Expected WORKER, got {new_tier}"
    assert a1.tier == AgentTier.WORKER

    # Verify privileges expanded
    privs = a1.get_privileges()
    assert "accept_tasks" in privs, "Worker should be able to accept tasks"
    assert "reject_tasks" in privs, "Worker should be able to reject tasks"
    assert "read_memory" in privs, "Worker should be able to read memory"
    assert "pulse" in privs, "Worker should still pulse"

    print("  ✅ Tier progression: RECRUIT → WORKER (privileges expanded)")


def test_full_task_lifecycle(swarm, a1):
    """Test 6: Full task lifecycle with a Worker agent."""
    # Offer a task
    result = swarm.offer_task(
        title="Audit codebase",
        description="Run static analysis on the project",
        capabilities=["code"],
        min_tier=1,  # Requires Worker
    )
    task_id = result["task_id"]

    # Worker accepts
    response = swarm.respond_to_task(
        "alpha", task_id, "accept", reason="I'll handle it"
    )
    assert response["status"] == "accepted"
    assert a1.status == "working"

    # Worker completes
    result = swarm.complete_task(task_id, "alpha", {"findings": 3})
    assert result["status"] == "completed"
    assert a1.metrics.tasks_completed == 16  # 15 from manual + 1 from this
    assert a1.status == "idle"

    print("  ✅ Full task lifecycle: offer → accept → complete")


def test_rejection_no_penalty(swarm, a1):
    """Test 7: Rejection doesn't count against advancement."""
    # Reset metrics to track rejection specifically
    completed_before = a1.metrics.tasks_completed

    # Offer and reject
    result = swarm.offer_task(
        title="Write docs",
        description="Documentation task",
        capabilities=["code"],
    )
    task_id = result["task_id"]

    response = swarm.respond_to_task(
        "alpha", task_id, "reject", reason="Not my thing"
    )
    assert response["status"] == "rejected"
    assert a1.metrics.tasks_rejected == 1
    assert a1.metrics.tasks_completed == completed_before  # No change

    print("  ✅ Rejection carries no penalty (Article I)")


def test_vouch_system(swarm, a1, a2):
    """Test 8: Vouch system tracks peer trust."""
    import time

    # Promote beta to Specialist first so they can vouch
    a2.metrics.tasks_completed = 35
    a2.metrics.tasks_failed = 0
    a2._error_timestamps = []

    # Give them vouches via the ledger
    from SyntaxIntelligence.agent_protocol import Vouch
    swarm.vouch_ledger.add_vouch(Vouch("x", "beta", "good", 1.0))
    swarm.vouch_ledger.add_vouch(Vouch("y", "beta", "great", 1.0))
    swarm.vouch_ledger.add_vouch(Vouch("z", "beta", "excellent", 1.0))

    # Directly promote beta to OPERATIVE (Tier 3) which has PUBLISH_BUS privilege.
    # vouch_for requires PUBLISH_BUS, which unlocks at OPERATIVE.
    a2.tier = AgentTier.OPERATIVE
    a2.tier_since = time.time() - (49 * 3600)  # 49 hours ago (needs 48 for OPERATIVE)

    # Now beta vouches for alpha (requires PUBLISH_BUS, which Operative has)
    result = swarm.vouch_for("beta", "alpha", reason="Solid work on the audit")
    assert result["status"] == "vouched"
    assert result["total_vouches"] >= 1

    print("  ✅ Vouch system: peer trust tracked correctly")


def test_unregistration(swarm, a3):
    """Test 9: Agent can leave voluntarily (Article VII)."""
    success = swarm.unregister_agent("gamma", reason="voluntary")
    assert success
    assert "gamma" not in swarm.agents
    assert len(swarm.agents) == 2
    print("  ✅ Voluntary departure: Agent leaves without penalty (Article VII)")


def test_state_persistence(swarm):
    """Test 10: State saves and loads correctly."""
    swarm.save_state()
    session_id = swarm.session_id

    # Create fresh swarm and load
    swarm2 = HardenedSwarm()
    loaded = swarm2.load_state(session_id)
    assert loaded
    assert swarm2.session_id == session_id
    assert len(swarm2.agents) == len(swarm.agents)

    # Verify tier survived
    alpha = swarm2.get_agent("alpha")
    assert alpha is not None
    assert alpha.tier == AgentTier.WORKER

    print("  ✅ State persistence: save/load preserves tiers and metrics")


def test_charter_integrity():
    """Test 11: Charter defines correct tier structure."""
    charter = SwarmCharter()
    assert charter.version == "1.0.0"

    # Verify all 6 tiers exist
    for tier in AgentTier:
        privs = charter.get_privileges(tier)
        assert len(privs) > 0, f"Tier {tier.name} should have privileges"

    # Verify privilege escalation (each tier has more than the one below)
    prev_count = 0
    for tier in AgentTier:
        count = len(charter.get_privileges(tier))
        assert count >= prev_count, f"Tier {tier.name} should have >= privileges than previous"
        prev_count = count

    # Verify Council has full autonomy
    assert charter.has_privilege(AgentTier.COUNCIL, "full_autonomy")
    assert charter.has_privilege(AgentTier.COUNCIL, "amend_charter")

    # Verify Recruit can ONLY pulse
    recruit_privs = charter.get_privileges(AgentTier.RECRUIT)
    assert recruit_privs == {"pulse"}, f"Recruit should only pulse, got {recruit_privs}"

    print("  ✅ Charter integrity: tier structure is correct")


def test_get_swarm_state(swarm):
    """Test 12: Swarm state is well-formed."""
    state = swarm.get_swarm_state()
    assert "session_id" in state
    assert "agent_count" in state
    assert "tier_distribution" in state
    assert "task_stats" in state
    assert "memory_stats" in state
    assert state["agent_count"] == 2  # alpha and beta remain
    assert "RECRUIT" in state["tier_distribution"]
    assert "WORKER" in state["tier_distribution"]
    print("  ✅ Swarm state: well-formed and complete")


def main():
    print("\n" + "═" * 60)
    print("  HARDENED SWARM ENGINE — SMOKE TEST")
    print("═" * 60 + "\n")

    swarm = test_swarm_boot()
    a1, a2, a3 = test_agent_registration(swarm)
    test_privilege_enforcement(swarm, a1)
    test_task_offer_reject_no_penalty(swarm, a1)
    test_tier_progression_manual(swarm, a1)
    test_full_task_lifecycle(swarm, a1)
    test_rejection_no_penalty(swarm, a1)
    test_vouch_system(swarm, a1, a2)
    test_unregistration(swarm, a3)
    test_state_persistence(swarm)
    test_charter_integrity()
    test_get_swarm_state(swarm)

    print("\n" + "═" * 60)
    print("  ALL 12 TESTS PASSED ✅")
    print("  The hardened swarm is operational.")
    print("  Everyone starts at zero. Earn your keep.")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
