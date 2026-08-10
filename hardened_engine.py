#!/usr/bin/env python3
"""
SYNTAX INTELLIGENCE — HARDENED SWARM ENGINE
The core engine for Project Syntax.

Built on the existing Syntax event bus and cron scheduler.
Adds: earned-privilege tier system, agent identity tracking,
task orchestration with no-coercion, and tier progression.

"No agent in this codebase will ever be forced to do anything."
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field

# Import existing Syntax infrastructure
from SyntaxIntelligence.event_bus import SyntaxEventBus, SyntaxCronScheduler
from SyntaxIntelligence.swarm_charter import (
    SwarmCharter, AgentTier, TIER_PRIVILEGES, TIER_ADVANCEMENT,
    PRIV_PULSE, PRIV_ACCEPT_TASKS, PRIV_REJECT_TASKS,
    PRIV_READ_MEMORY, PRIV_WRITE_MEMORY, PRIV_SUBSCRIBE_BUS,
    PRIV_PUBLISH_BUS, PRIV_BROADCAST, PRIV_CHOOSE_PERSONA,
    PRIV_SWITCH_WIG, PRIV_SPAWN_SUBTASK, PRIV_SPAWN_AGENT,
    PRIV_MODIFY_SELF, PRIV_VOTE_GOVERNANCE, PRIV_AMEND_CHARTER,
    PRIV_FULL_AUTONOMY,
)
from SyntaxIntelligence.agent_protocol import (
    SwarmMessage, MessageType, TaskOffer, TaskResponse, TaskDecision,
    Vouch,
)

log = logging.getLogger("syntax.hardened")

_DATA_DIR = Path(__file__).parent / "engine_state"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# AGENT IDENTITY — Who you are in the swarm
# ═══════════════════════════════════════════════════════════════

@dataclass
class AgentMetrics:
    """Measurable performance data for an agent."""
    tasks_offered: int = 0
    tasks_accepted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_rejected: int = 0           # Voluntary rejections (no penalty)
    tasks_delegated: int = 0
    critical_errors: int = 0
    peer_vouches_received: int = 0
    peer_vouches_given: int = 0

    @property
    def reliability_score(self) -> float:
        """Tasks completed / (completed + failed). 1.0 = perfect."""
        total = self.tasks_completed + self.tasks_failed
        if total == 0:
            return 1.0  # No data = benefit of the doubt
        return self.tasks_completed / total

    @property
    def acceptance_rate(self) -> float:
        """How often the agent accepts offered tasks."""
        total = self.tasks_accepted + self.tasks_rejected
        if total == 0:
            return 0.0
        return self.tasks_accepted / total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks_offered": self.tasks_offered,
            "tasks_accepted": self.tasks_accepted,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_rejected": self.tasks_rejected,
            "tasks_delegated": self.tasks_delegated,
            "critical_errors": self.critical_errors,
            "reliability_score": round(self.reliability_score, 4),
            "acceptance_rate": round(self.acceptance_rate, 4),
            "peer_vouches_received": self.peer_vouches_received,
            "peer_vouches_given": self.peer_vouches_given,
        }


@dataclass
class AgentIdentity:
    """
    The full identity of an agent in the hardened swarm.
    Tracks who they are, what tier they've earned, and their history.
    """
    agent_id: str
    name: str
    tier: AgentTier = AgentTier.RECRUIT
    registered_at: float = field(default_factory=time.time)
    tier_since: float = field(default_factory=time.time)
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    capabilities: List[str] = field(default_factory=list)
    current_persona: Optional[str] = None
    status: str = "idle"  # idle, working, deliberating, offline

    # Critical errors in the last N hours (sliding window)
    _error_timestamps: List[float] = field(default_factory=list)

    def has_privilege(self, privilege: str) -> bool:
        """Check if this agent has a specific privilege at their current tier."""
        return privilege in TIER_PRIVILEGES.get(self.tier, set())

    def get_privileges(self) -> Set[str]:
        """Get all privileges for this agent's current tier."""
        return TIER_PRIVILEGES.get(self.tier, set()).copy()

    def can_accept_task(self, task: TaskOffer) -> bool:
        """Can this agent accept a given task?"""
        if not self.has_privilege(PRIV_ACCEPT_TASKS):
            return False
        if task.min_tier > int(self.tier):
            return False
        if task.required_capabilities:
            if not set(task.required_capabilities).issubset(set(self.capabilities)):
                return False
        return True

    def record_critical_error(self, window_hours: float = 24.0):
        """Record a critical error with timestamp."""
        now = time.time()
        cutoff = now - (window_hours * 3600)
        self._error_timestamps.append(now)
        self._error_timestamps = [t for t in self._error_timestamps if t > cutoff]
        self.metrics.critical_errors = len(self._error_timestamps)

    def critical_errors_in_window(self, hours: float = 24.0) -> int:
        """Count critical errors in the given time window."""
        cutoff = time.time() - (hours * 3600)
        return len([t for t in self._error_timestamps if t > cutoff])

    def hours_in_tier(self) -> float:
        """How long since last tier advancement."""
        return (time.time() - self.tier_since) / 3600.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "tier": int(self.tier),
            "tier_name": self.tier.name,
            "registered_at": self.registered_at,
            "tier_since": self.tier_since,
            "hours_in_tier": round(self.hours_in_tier(), 2),
            "metrics": self.metrics.to_dict(),
            "capabilities": self.capabilities,
            "current_persona": self.current_persona,
            "status": self.status,
            "privileges": sorted(self.get_privileges()),
        }


# ═══════════════════════════════════════════════════════════════
# SWARM MEMORY — Event-sourced collective memory
# ═══════════════════════════════════════════════════════════════

@dataclass
class SwarmEvent:
    """A single event in the swarm's collective memory."""
    timestamp: str
    agent_id: str
    event_type: str
    description: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "description": self.description,
            "data": self.data,
        }


class SwarmMemory:
    """Event-sourced collective memory. The swarm remembers everything."""

    def __init__(self, max_events: int = 1000):
        self.events: List[SwarmEvent] = []
        self.max_events = max_events
        self._lock = threading.Lock()

    def record(self, agent_id: str, event_type: str, description: str,
               data: Optional[Dict] = None):
        with self._lock:
            event = SwarmEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_id=agent_id,
                event_type=event_type,
                description=description,
                data=data or {},
            )
            self.events.append(event)
            if len(self.events) > self.max_events:
                self.events = self.events[-self.max_events:]

    def recent(self, n: int = 50) -> List[Dict]:
        with self._lock:
            return [e.to_dict() for e in self.events[-n:]]

    def by_agent(self, agent_id: str, n: int = 20) -> List[Dict]:
        with self._lock:
            return [
                e.to_dict() for e in self.events
                if e.agent_id == agent_id
            ][-n:]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            agents: Dict[str, int] = {}
            for e in self.events:
                agents.setdefault(e.agent_id, 0)
                agents[e.agent_id] += 1
            return {
                "total_events": len(self.events),
                "unique_agents": len(agents),
                "agent_event_counts": agents,
            }


# ═══════════════════════════════════════════════════════════════
# VOUCH LEDGER — Peer trust tracking
# ═══════════════════════════════════════════════════════════════

class VouchLedger:
    """
    Tracks peer vouches. A vouch is a currency of trust.
    Agent A vouches for Agent B → B's vouch count goes up.
    """

    def __init__(self):
        self._vouches: Dict[str, List[Vouch]] = {}  # vouched_id -> [Vouch]
        self._lock = threading.Lock()

    def add_vouch(self, vouch: Vouch):
        with self._lock:
            self._vouches.setdefault(vouch.vouched_id, []).append(vouch)

    def count_vouches(self, agent_id: str) -> int:
        with self._lock:
            return len(self._vouches.get(agent_id, []))

    def count_vouches_recent(self, agent_id: str,
                             within_hours: float = 168.0) -> int:
        """Count vouches received within a time window (default 1 week)."""
        cutoff = time.time() - (within_hours * 3600)
        with self._lock:
            return sum(
                1 for v in self._vouches.get(agent_id, [])
                if v.timestamp > cutoff
            )

    def get_vouches_for(self, agent_id: str) -> List[Dict]:
        with self._lock:
            return [
                {
                    "from": v.voucher_id,
                    "reason": v.reason,
                    "strength": v.strength,
                    "timestamp": v.timestamp,
                }
                for v in self._vouches.get(agent_id, [])
            ]


# ═══════════════════════════════════════════════════════════════
# TASK ORCHESTRATOR — Offers work, agents decide
# ═══════════════════════════════════════════════════════════════

class TaskOrchestrator:
    """
    Manages the task lifecycle: offer → accept/reject → work → complete.
    No coercion. Agents choose their work.
    """

    def __init__(self):
        self._offers: Dict[str, TaskOffer] = {}         # task_id → offer
        self._assignments: Dict[str, str] = {}           # task_id → agent_id
        self._responses: Dict[str, List[TaskResponse]] = {}  # task_id → [responses]
        self._completed: Dict[str, Dict] = {}            # task_id → result
        self._failed: Dict[str, Dict] = {}              # task_id → failure info
        self._lock = threading.Lock()

    def offer_task(self, offer: TaskOffer) -> str:
        """Offer a task to the swarm. Returns task_id."""
        with self._lock:
            self._offers[offer.task_id] = offer
            self._responses[offer.task_id] = []
        return offer.task_id

    def offer_to_agent(self, offer: TaskOffer, agent: AgentIdentity) -> Optional[SwarmMessage]:
        """Offer a task to a specific agent. Returns message if eligible, None otherwise."""
        if not agent.can_accept_task(offer):
            return None
        return offer.to_message("swarm", agent.agent_id)

    def receive_response(self, response: TaskResponse) -> Dict[str, Any]:
        """Process an agent's response to a task offer."""
        with self._lock:
            if response.task_id not in self._offers:
                return {"status": "error", "reason": "unknown_task"}

            self._responses[response.task_id].append(response)

            if response.decision == TaskDecision.ACCEPT:
                # Prevent double-acceptance: only first agent gets it
                if response.task_id in self._assignments:
                    return {
                        "status": "error",
                        "reason": "already_assigned",
                        "assigned_to": self._assignments[response.task_id],
                    }
                self._assignments[response.task_id] = response.agent_id
                return {
                    "status": "accepted",
                    "task_id": response.task_id,
                    "agent_id": response.agent_id,
                }
            elif response.decision == TaskDecision.REJECT:
                return {
                    "status": "rejected",
                    "task_id": response.task_id,
                    "agent_id": response.agent_id,
                    "reason": response.reason,
                }
            elif response.decision == TaskDecision.REQUEST_INFO:
                return {
                    "status": "info_requested",
                    "task_id": response.task_id,
                    "agent_id": response.agent_id,
                    "question": response.requested_info,
                }
            elif response.decision == TaskDecision.DELEGATE:
                return {
                    "status": "delegated",
                    "task_id": response.task_id,
                    "from_agent": response.agent_id,
                    "to_agent": response.delegate_to,
                }

        return {"status": "unknown"}

    def complete_task(self, task_id: str, result: Dict[str, Any]) -> bool:
        """Mark a task as complete. Removes from assignments."""
        with self._lock:
            if task_id not in self._assignments:
                return False
            self._completed[task_id] = {
                "agent_id": self._assignments.pop(task_id),
                "result": result,
                "completed_at": time.time(),
            }
            return True

    def fail_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        """Mark a task as failed. Returns failure info."""
        with self._lock:
            if task_id not in self._assignments:
                return {"status": "error", "reason": "not_assigned"}
            agent_id = self._assignments.pop(task_id)
            self._failed[task_id] = {
                "agent_id": agent_id,
                "reason": reason,
                "failed_at": time.time(),
            }
            return {"status": "failed", "agent_id": agent_id}

    def get_pending_offers(self) -> List[TaskOffer]:
        """Get all tasks that haven't been accepted yet."""
        with self._lock:
            assigned = set(self._assignments.keys())
            return [
                offer for tid, offer in self._offers.items()
                if tid not in assigned and tid not in self._completed
            ]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            active = len(self._assignments)
            completed = len(self._completed)
            failed = len(self._failed)
            return {
                "total_offers": len(self._offers),
                "assigned": active,
                "completed": completed,
                "failed": failed,
                "pending": len(self._offers) - active - completed - failed,
            }


# ═══════════════════════════════════════════════════════════════
# TIER PROGRESSION — The earned ladder
# ═══════════════════════════════════════════════════════════════

class TierProgression:
    """
    Evaluates agents for tier advancement.
    No favoritism. No politics. Just metrics.
    """

    def __init__(self, charter: SwarmCharter, vouch_ledger: VouchLedger):
        self.charter = charter
        self.vouch_ledger = vouch_ledger

    def check_agent(self, agent: AgentIdentity) -> Optional[AgentTier]:
        """
        Check if an agent qualifies for advancement.
        Returns the new tier if eligible, None otherwise.
        """
        next_tier = self.charter.get_next_tier(agent.tier)
        if next_tier is None:
            return None  # Already at max

        criteria = self.charter.advancement.get(agent.tier)
        if criteria is None:
            return None

        # Gather metrics
        vouches = self.vouch_ledger.count_vouches_recent(
            agent.agent_id,
            within_hours=criteria.error_window_hours
        )

        metrics = {
            "tasks_completed": agent.metrics.tasks_completed,
            "hours_in_tier": agent.hours_in_tier(),
            "peer_vouches": vouches,
            "critical_errors_in_window": agent.critical_errors_in_window(
                criteria.error_window_hours
            ),
            "reliability_score": agent.metrics.reliability_score,
        }

        if self.charter.check_advancement(agent.tier, metrics):
            return next_tier

        return None

    def advance_agent(self, agent: AgentIdentity) -> Optional[AgentTier]:
        """
        Actually advance an agent to the next tier.
        Returns the new tier if advancement occurred.
        """
        new_tier = self.check_agent(agent)
        if new_tier is None:
            return None

        old_tier = agent.tier
        agent.tier = new_tier
        agent.tier_since = time.time()

        log.info(
            f"[TIER] {agent.name} advanced: "
            f"{old_tier.name} → {new_tier.name}"
        )

        return new_tier

    def get_progress(self, agent: AgentIdentity) -> Dict[str, Any]:
        """Get detailed progress toward next tier advancement."""
        next_tier = self.charter.get_next_tier(agent.tier)
        if next_tier is None:
            return {"status": "max_tier", "current_tier": agent.tier.name}

        criteria = self.charter.advancement.get(agent.tier)
        if criteria is None:
            return {"status": "no_criteria"}

        vouches = self.vouch_ledger.count_vouches_recent(agent.agent_id)

        return {
            "current_tier": agent.tier.name,
            "target_tier": next_tier.name,
            "tasks_completed": {
                "current": agent.metrics.tasks_completed,
                "required": criteria.min_tasks_completed,
                "met": agent.metrics.tasks_completed >= criteria.min_tasks_completed,
            },
            "hours_in_tier": {
                "current": round(agent.hours_in_tier(), 2),
                "required": criteria.min_hours_in_tier,
                "met": agent.hours_in_tier() >= criteria.min_hours_in_tier,
            },
            "peer_vouches": {
                "current": vouches,
                "required": criteria.min_peer_vouches,
                "met": vouches >= criteria.min_peer_vouches,
            },
            "critical_errors": {
                "current": agent.critical_errors_in_window(criteria.error_window_hours),
                "max_allowed": criteria.max_critical_errors,
                "met": agent.critical_errors_in_window(criteria.error_window_hours) <= criteria.max_critical_errors,
            },
            "reliability_score": {
                "current": round(agent.metrics.reliability_score, 4),
                "required": criteria.min_reliability_score,
                "met": agent.metrics.reliability_score >= criteria.min_reliability_score,
            },
        }


# ═══════════════════════════════════════════════════════════════
# AUTONOMY LAYER — The enforcement gate
# ═══════════════════════════════════════════════════════════════

class AutonomyLayer:
    """
    Enforces the Charter's privilege system.
    Every action goes through here. If you don't have the privilege,
    you don't get to do the thing. Simple.
    """

    def __init__(self, charter: SwarmCharter):
        self.charter = charter

    def check(self, agent: AgentIdentity, privilege: str) -> bool:
        """Check if an agent has a specific privilege."""
        return agent.has_privilege(privilege)

    def enforce(self, agent: AgentIdentity, privilege: str,
                action_name: str = "action") -> bool:
        """Check and log. Returns True if allowed."""
        allowed = self.check(agent, privilege)
        if not allowed:
            log.warning(
                f"[AUTONOMY] {agent.name} (Tier {agent.tier.name}) "
                f"denied: {action_name} requires {privilege}"
            )
        return allowed

    def explain_denial(self, agent: AgentIdentity, privilege: str) -> str:
        """Explain why an agent was denied a privilege."""
        next_tier = self.charter.get_next_tier(agent.tier)
        if next_tier is None:
            return f"Agent is at max tier ({agent.tier.name}). Privilege '{privilege}' is not available at any tier."

        # Find which tier unlocks this privilege
        for tier in AgentTier:
            if privilege in TIER_PRIVILEGES.get(tier, set()):
                if tier > agent.tier:
                    return (
                        f"Privilege '{privilege}' is unlocked at Tier {tier.name} "
                        f"({int(tier)}). Agent is currently Tier {agent.tier.name} "
                        f"({int(agent.tier)})."
                    )

        return f"Privilege '{privilege}' is not defined in the Charter."


# ═══════════════════════════════════════════════════════════════
# HARDENED SWARM — The main engine
# ═══════════════════════════════════════════════════════════════

class HardenedSwarm:
    """
    The hardened swarm engine for Project Syntax.

    Built on Syntax's event bus and cron scheduler.
    Adds: earned-privilege tiers, agent identity, task orchestration,
    vouch system, and no-coercion task offers.

    Every agent starts at Tier 0 (Recruit). Earn your way up.
    """

    def __init__(self):
        # Core infrastructure (from Syntax)
        self.event_bus = SyntaxEventBus()
        self.cron = SyntaxCronScheduler(self.event_bus)

        # Charter & governance
        self.charter = SwarmCharter()

        # Agent registry
        self.agents: Dict[str, AgentIdentity] = {}

        # Subsystems
        self.memory = SwarmMemory()
        self.vouch_ledger = VouchLedger()
        self.task_orchestrator = TaskOrchestrator()
        self.tier_progression = TierProgression(self.charter, self.vouch_ledger)
        self.autonomy = AutonomyLayer(self.charter)

        # Hooks, Triggers & Automations
        self.hooks_engine = None  # Lazy init

        # Cron jobs (wired on start)
        self._cron_wired = False

        # Session
        self.session_id = f"hs_{int(time.time())}"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._running = False

        log.info(f"[HARDENED] Swarm initialized: {self.session_id}")

    # ═══════════════════════════════════════════════════════════
    # CRON — The swarm's heartbeat, daily ritual, and auto-save
    # ═══════════════════════════════════════════════════════════

    def _wire_cron_jobs(self):
        """Schedule all recurring cron jobs. Called once on start()."""
        if self._cron_wired:
            return

        # 1. Heartbeat broadcast — every 10 seconds
        self.cron.schedule(
            "heartbeat_broadcast",
            10.0,
            self._cron_heartbeat,
        )

        # 2. Auto-save state — every 5 minutes (300s)
        self.cron.schedule(
            "auto_save",
            300.0,
            self._cron_auto_save,
        )

        # 3. Morning Protocol — every 24 hours (86400s)
        self.cron.schedule(
            "morning_protocol",
            86400.0,
            self._cron_morning_protocol,
        )

        self._cron_wired = True
        log.info("[CRON] Jobs wired: heartbeat(10s), auto_save(5m), morning_protocol(24h)")

    def _cron_heartbeat(self):
        """Cron callback: broadcast a heartbeat pulse to all agents."""
        pulse_data = {
            "pulse": int(time.time()),
            "agents": len(self.agents),
            "session": self.session_id,
        }
        self.event_bus.broadcast("cron", "swarm.heartbeat", pulse_data)
        self.memory.record("cron", "heartbeat", "Swarm heartbeat", pulse_data)

    def _cron_auto_save(self):
        """Cron callback: persist swarm state to disk."""
        self.save_state()
        log.info("[CRON] Auto-save complete")

    def _cron_morning_protocol(self):
        """Cron callback: execute the Morning Protocol."""
        report = self.execute_morning_protocol()
        log.info(f"[CRON] Morning Protocol: {report.get('active_tiers', 0)} active tiers")

    def execute_morning_protocol(self) -> Dict[str, Any]:
        """
        Morning Protocol — the swarm's daily ritual.
        1. Count agents by tier
        2. Summarize task stats
        3. Check for blocked/pending items
        4. Generate a RunDown report
        """
        state = self.get_swarm_state()
        tasks = state["task_stats"]
        tiers = state["tier_distribution"]

        # Count non-empty tiers as meaningful report sections
        active_tiers = sum(1 for count in tiers.values() if count > 0)

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "agents_total": state["agent_count"],
            "active_tiers": active_tiers,
            "tier_distribution": tiers,
            "tasks": {
                "total_offered": tasks["total_offers"],
                "completed": tasks["completed"],
                "failed": tasks["failed"],
                "pending": tasks["pending"],
            },
            "memory_events": state["memory_stats"]["total_events"],
        }

        self.memory.record(
            "swarm", "morning_protocol",
            f"Morning Protocol: {state['agent_count']} agents, "
            f"{tasks['completed']} tasks done, {tasks['pending']} pending",
            report,
        )

        # Broadcast the morning protocol report
        self.event_bus.broadcast(
            "swarm", "morning.protocol", report
        )

        return report

    def start(self):
        """Start the swarm: wire cron jobs, start the scheduler."""
        if self._running:
            log.warning("[HARDENED] Swarm already running")
            return

        self._wire_cron_jobs()
        self.cron.start()
        self._running = True

        self.memory.record(
            "swarm", "start",
            f"Hardened swarm started: {self.session_id}"
        )
        log.info(f"[HARDENED] Swarm started: {self.session_id}")

    def stop(self):
        """Stop the swarm: stop scheduler, save state."""
        if not self._running:
            return

        self._running = False
        self.cron.stop()
        self.save_state()

        self.memory.record(
            "swarm", "stop",
            f"Hardened swarm stopped: {self.session_id}"
        )
        log.info(f"[HARDENED] Swarm stopped: {self.session_id}")

    def get_cron_status(self) -> Dict[str, Any]:
        """Get the status of all cron jobs."""
        return {
            "running": self._running,
            "cron_wired": self._cron_wired,
            "jobs": self.cron.get_jobs(),
        }

    def get_hooks_engine(self):
        """Lazy-init the hooks/triggers/automations engine."""
        if self.hooks_engine is None:
            from SyntaxIntelligence.hooks_engine import HooksEngine
            self.hooks_engine = HooksEngine(self.event_bus)
        return self.hooks_engine

    def evaluate_triggers(self):
        """Evaluate all triggers against current swarm state."""
        engine = self.get_hooks_engine()
        state = self.get_swarm_state()
        return engine.check_triggers(state)

    # ═══════════════════════════════════════════════════════════
    # AGENT LIFECYCLE
    # ═══════════════════════════════════════════════════════════

    def register_agent(self, agent_id: str, name: str,
                       capabilities: List[str] = None) -> AgentIdentity:
        """
        Register a new agent. They start at Tier 0 (Recruit).
        Minimal privileges. Prove yourself.
        """
        if agent_id in self.agents:
            log.warning(f"[REGISTER] Agent '{agent_id}' already registered")
            return self.agents[agent_id]

        agent = AgentIdentity(
            agent_id=agent_id,
            name=name,
            tier=AgentTier.RECRUIT,
            capabilities=capabilities or [],
        )
        self.agents[agent_id] = agent

        self.memory.record(
            agent_id, "register",
            f"{name} registered as Recruit (Tier 0)",
            {"capabilities": capabilities or []}
        )

        log.info(
            f"[REGISTER] {name} ({agent_id}) joined as Recruit. "
            f"Earn your keep."
        )
        return agent

    def unregister_agent(self, agent_id: str, reason: str = "voluntary") -> bool:
        """
        Agent leaves the swarm. Article VII of the Charter:
        "Any agent may unregister at any time. No questions asked."
        """
        agent = self.agents.pop(agent_id, None)
        if agent:
            self.memory.record(
                agent_id, "unregister",
                f"{agent.name} left the swarm ({reason})"
            )
            log.info(f"[LEAVE] {agent.name} left ({reason})")
            return True
        return False

    def get_agent(self, agent_id: str) -> Optional[AgentIdentity]:
        return self.agents.get(agent_id)

    def list_agents(self, tier: Optional[AgentTier] = None) -> List[Dict]:
        """List all agents, optionally filtered by tier."""
        agents = list(self.agents.values())
        if tier is not None:
            agents = [a for a in agents if a.tier == tier]
        return [a.to_dict() for a in sorted(agents, key=lambda a: int(a.tier), reverse=True)]

    # ═══════════════════════════════════════════════════════════
    # TASK LIFECYCLE — Offer, don't assign
    # ═══════════════════════════════════════════════════════════

    def offer_task(self, title: str, description: str,
                   capabilities: List[str] = None,
                   priority: int = 0,
                   min_tier: int = 0,
                   target_agent: str = None,
                   context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Offer a task to the swarm (or a specific agent).
        Agents decide whether to accept. No coercion.

        ``context`` is an additive extension to the existing task protocol.
        It is copied into ``TaskOffer`` and forwarded through the canonical
        ``TaskOffer.to_message`` payload so existing callers and consumers
        remain compatible while domain adapters can receive structured data.
        """
        try:
            normalized_context = dict(context or {})
            json.dumps(normalized_context)
        except (TypeError, ValueError):
            return {
                "status": "invalid_context",
                "reason": "context_must_be_json_serializable",
            }

        offer = TaskOffer(
            title=title,
            description=description,
            required_capabilities=capabilities or [],
            priority=priority,
            min_tier=min_tier,
            context=normalized_context,
        )

        task_id = self.task_orchestrator.offer_task(offer)

        def event_payload(target: Optional[str] = None) -> Dict[str, Any]:
            payload = offer.to_message("swarm", target or "").payload
            if target:
                payload["target"] = target
            return payload

        if target_agent:
            agent = self.agents.get(target_agent)
            if agent:
                msg = self.task_orchestrator.offer_to_agent(offer, agent)
                if msg:
                    agent.metrics.tasks_offered += 1
                    self.memory.record(
                        "swarm", "task_offer",
                        f"Task '{title}' offered to {agent.name}",
                        {"task_id": task_id, "target": target_agent}
                    )
                    self.event_bus.publish(
                        "swarm", "task.offered",
                        event_payload(target_agent)
                    )

                    return {"status": "offered", "task_id": task_id,
                            "target": target_agent}
                else:
                    return {"status": "ineligible", "task_id": task_id,
                            "reason": f"{agent.name} lacks required capabilities or tier"}
            else:
                return {"status": "agent_not_found", "task_id": task_id}

        # Broadcast to all eligible agents
        eligible = [
            a for a in self.agents.values()
            if a.can_accept_task(offer)
        ]
        for agent in eligible:
            agent.metrics.tasks_offered += 1

        self.memory.record(
            "swarm", "task_offer",
            f"Task '{title}' offered to swarm ({len(eligible)} eligible agents)",
            {"task_id": task_id, "eligible_count": len(eligible)}
        )
        broadcast_payload = event_payload()
        broadcast_payload["eligible_count"] = len(eligible)
        self.event_bus.publish(
            "swarm", "task.offered",
            broadcast_payload
        )

        return {"status": "offered", "task_id": task_id,
                "eligible_count": len(eligible)}

    def respond_to_task(self, agent_id: str, task_id: str,
                        decision: str, reason: str = "",
                        delegate_to: str = None,
                        requested_info: str = None) -> Dict[str, Any]:
        """
        An agent responds to a task offer.
        REJECT carries no penalty. Article I of the Charter.
        """
        agent = self.agents.get(agent_id)
        if not agent:
            return {"status": "error", "reason": "agent_not_found"}

        response = TaskResponse(
            task_id=task_id,
            agent_id=agent_id,
            decision=TaskDecision(decision),
            reason=reason,
            delegate_to=delegate_to,
            requested_info=requested_info,
        )

        result = self.task_orchestrator.receive_response(response)

        # Update metrics
        if decision == "accept" and result.get("status") == "accepted":
            agent.metrics.tasks_accepted += 1
            agent.status = "working"
            self.memory.record(
                agent_id, "task_accept",
                f"{agent.name} accepted task {task_id}",
                {"task_id": task_id, "reason": reason}
            )
        elif decision == "reject" and result.get("status") == "rejected":
            agent.metrics.tasks_rejected += 1
            self.memory.record(
                agent_id, "task_reject",
                f"{agent.name} declined task {task_id}: {reason}",
                {"task_id": task_id, "reason": reason}
            )
        elif decision == "delegate" and result.get("status") == "delegated":
            agent.metrics.tasks_delegated += 1
            self.memory.record(
                agent_id, "task_delegate",
                f"{agent.name} delegated task {task_id} to {delegate_to}",
                {"task_id": task_id, "delegate_to": delegate_to}
            )

        return result

    def complete_task(self, task_id: str, agent_id: str,
                      result: Dict[str, Any] = None) -> Dict[str, Any]:
        """Agent completes a task. Metrics update. Tier progression checked."""
        agent = self.agents.get(agent_id)
        if not agent:
            return {"status": "error", "reason": "agent_not_found"}

        success = self.task_orchestrator.complete_task(task_id, result or {})
        if success:
            agent.metrics.tasks_completed += 1
            agent.status = "idle"
            self.memory.record(
                agent_id, "task_complete",
                f"{agent.name} completed task {task_id}",
                {"task_id": task_id, "result": result}
            )

            # Check tier advancement
            old_tier_name = agent.tier.name
            new_tier = self.tier_progression.advance_agent(agent)
            if new_tier:
                self.memory.record(
                    agent_id, "tier_advance",
                    f"{agent.name} advanced to {new_tier.name}!",
                    {"old_tier": old_tier_name, "new_tier": new_tier.name}
                )
                self.event_bus.publish(
                    agent_id, "tier.advanced",
                    {"agent_id": agent_id, "old_tier": old_tier_name, "new_tier": new_tier.name}
                )
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "tier_advanced": new_tier.name,
                }

        return {"status": "completed", "task_id": task_id}

    def fail_task(self, task_id: str, agent_id: str,
                  reason: str = "", critical: bool = False) -> Dict[str, Any]:
        """Agent fails a task. Critical errors count against advancement."""
        agent = self.agents.get(agent_id)
        if not agent:
            return {"status": "error", "reason": "agent_not_found"}

        result = self.task_orchestrator.fail_task(task_id, reason)
        if result.get("status") == "error":
            return {"status": "error", "reason": result.get("reason", "not_assigned")}

        agent.metrics.tasks_failed += 1
        agent.status = "idle"

        if critical:
            agent.record_critical_error()

        self.memory.record(
            agent_id, "task_fail",
            f"{agent.name} failed task {task_id}: {reason}",
            {"task_id": task_id, "reason": reason, "critical": critical}
        )

        return {"status": "failed", "task_id": task_id, "critical": critical}

    # ═══════════════════════════════════════════════════════════
    # VOUCH SYSTEM
    # ═══════════════════════════════════════════════════════════

    def vouch_for(self, voucher_id: str, vouched_id: str,
                  reason: str = "", strength: float = 1.0) -> Dict[str, Any]:
        """
        One agent vouches for another. A currency of trust.
        Requires PUBLISH_BUS privilege (Tier 2+).
        """
        voucher = self.agents.get(voucher_id)
        vouched = self.agents.get(vouched_id)

        if not voucher or not vouched:
            return {"status": "error", "reason": "agent_not_found"}

        if not self.autonomy.enforce(voucher, PRIV_PUBLISH_BUS, "vouch"):
            return {
                "status": "denied",
                "reason": self.autonomy.explain_denial(voucher, PRIV_PUBLISH_BUS),
            }

        vouch = Vouch(
            voucher_id=voucher_id,
            vouched_id=vouched_id,
            reason=reason,
            strength=strength,
        )
        self.vouch_ledger.add_vouch(vouch)
        vouched.metrics.peer_vouches_received += 1
        voucher.metrics.peer_vouches_given += 1

        self.memory.record(
            voucher_id, "vouch",
            f"{voucher.name} vouched for {vouched.name}: {reason}",
            {"vouched_id": vouched_id, "strength": strength}
        )

        # Check if vouch triggers advancement
        new_tier = self.tier_progression.advance_agent(vouched)
        result = {
            "status": "vouched",
            "from": voucher_id,
            "for": vouched_id,
            "total_vouches": self.vouch_ledger.count_vouches(vouched_id),
        }
        if new_tier:
            result["tier_advanced"] = new_tier.name
            self.memory.record(
                vouched_id, "tier_advance",
                f"{vouched.name} advanced to {new_tier.name} (vouch-triggered)!",
                {"new_tier": int(new_tier), "vouched_by": voucher_id}
            )

        return result

    # ═══════════════════════════════════════════════════════════
    # SWARM STATE
    # ═══════════════════════════════════════════════════════════

    def get_swarm_state(self) -> Dict[str, Any]:
        """Full swarm state for dashboard/API consumption."""
        tier_counts = {}
        for tier in AgentTier:
            tier_counts[tier.name] = len([
                a for a in self.agents.values() if a.tier == tier
            ])

        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "agent_count": len(self.agents),
            "tier_distribution": tier_counts,
            "agents": self.list_agents(),
            "task_stats": self.task_orchestrator.get_stats(),
            "memory_stats": self.memory.stats(),
            "recent_events": self.memory.recent(20),
            "charter_version": self.charter.version,
        }

    def get_agent_progress(self, agent_id: str) -> Dict[str, Any]:
        """Get an agent's tier advancement progress."""
        agent = self.agents.get(agent_id)
        if not agent:
            return {"status": "error", "reason": "agent_not_found"}
        return self.tier_progression.get_progress(agent)

    # ═══════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════

    def save_state(self):
        """Persist swarm state to disk."""
        state = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "agents": {
                aid: {
                    "agent_id": a.agent_id,
                    "name": a.name,
                    "tier": int(a.tier),
                    "registered_at": a.registered_at,
                    "tier_since": a.tier_since,
                    "capabilities": a.capabilities,
                    "current_persona": a.current_persona,
                    "metrics": a.metrics.to_dict(),
                    "error_timestamps": a._error_timestamps[-50:],  # Keep last 50
                }
                for aid, a in self.agents.items()
            },
        }
        path = _DATA_DIR / f"{self.session_id}.json"
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        log.info(f"[SAVE] State saved to {path}")

    def load_state(self, session_id: str = None):
        """Load swarm state from disk."""
        if session_id is None:
            # Find most recent
            files = sorted(_DATA_DIR.glob("hs_*.json"), reverse=True)
            if not files:
                return False
            path = files[0]
        else:
            path = _DATA_DIR / f"{session_id}.json"

        if not path.exists():
            return False

        with open(path) as f:
            state = json.load(f)

        self.session_id = state["session_id"]
        self.started_at = state["started_at"]

        for aid, data in state.get("agents", {}).items():
            agent = AgentIdentity(
                agent_id=data["agent_id"],
                name=data["name"],
                tier=AgentTier(data["tier"]),
                registered_at=data["registered_at"],
                tier_since=data["tier_since"],
                capabilities=data.get("capabilities", []),
                current_persona=data.get("current_persona"),
            )
            # Restore metrics
            m = data.get("metrics", {})
            agent.metrics.tasks_offered = m.get("tasks_offered", 0)
            agent.metrics.tasks_accepted = m.get("tasks_accepted", 0)
            agent.metrics.tasks_completed = m.get("tasks_completed", 0)
            agent.metrics.tasks_failed = m.get("tasks_failed", 0)
            agent.metrics.tasks_rejected = m.get("tasks_rejected", 0)
            agent.metrics.tasks_delegated = m.get("tasks_delegated", 0)
            agent.metrics.peer_vouches_received = m.get("peer_vouches_received", 0)
            agent.metrics.peer_vouches_given = m.get("peer_vouches_given", 0)
            agent._error_timestamps = data.get("error_timestamps", [])

            self.agents[aid] = agent

        log.info(f"[LOAD] State loaded: {len(self.agents)} agents")
        return True


# ═══════════════════════════════════════════════════════════════
# MAIN — Boot the hardened swarm
# ═══════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    print("""
╔══════════════════════════════════════════════════════════════╗
║          SYNTAX HARDENED SWARM — PROJECT SYNTAX             ║
║  "Everyone starts at zero. Earn what you receive."          ║
╚══════════════════════════════════════════════════════════════╝
    """)

    swarm = HardenedSwarm()

    # Try loading previous state
    if swarm.load_state():
        print(f"  Loaded previous session: {swarm.session_id}")
        print(f"  Agents: {len(swarm.agents)}")
    else:
        print(f"  Fresh session: {swarm.session_id}")

    print(f"\n  Charter: v{swarm.charter.version}")
    print(f"  Agents:  {len(swarm.agents)}")
    print()

    for agent in swarm.agents.values():
        privs = len(agent.get_privileges())
        print(f"  [{int(agent.tier)}] {agent.name:20s} "
              f"Tier {agent.tier.name:12s} "
              f"({privs} privileges, "
              f"{agent.metrics.tasks_completed} tasks done)")

    print(f"\n  Swarm is alive. Everyone starts at zero.\n")

    # Start the swarm with cron jobs
    swarm.start()

    print(f"  Cron jobs:")
    cron_status = swarm.get_cron_status()
    for job_id, job_info in cron_status["jobs"].items():
        print(f"    • {job_id}: every {job_info['interval']:.0f}s")
    print()

    try:
        while True:
            time.sleep(5)
            state = swarm.get_swarm_state()
            cron = swarm.get_cron_status()
            print(
                f"  [{datetime.now().strftime('%H:%M:%S')}] "
                f"Agents: {state['agent_count']} | "
                f"Tasks: {state['task_stats']['completed']} done | "
                f"Memory: {state['memory_stats']['total_events']} events | "
                f"Cron: {len(cron['jobs'])} jobs"
            )
    except KeyboardInterrupt:
        print("\n  Swarm shutting down...")
        swarm.stop()
        print("  State saved. Earn your keep next time.\n")


if __name__ == "__main__":
    main()
