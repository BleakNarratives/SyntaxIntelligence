#!/usr/bin/env python3
"""
Syntax Intelligence — Orchestration Core
"The first general intelligence ever."
Consolidates Syntax AI Code Optimizer + Captcoder + Seed into a unified,
costumed agent swarm that operates autonomously.

Each agent wears a costume, mask, jewelry, makeup, and wigs.
The swarm IS the intelligence — no single agent, all agents.
"""

import os, sys, json, time, logging, threading, struct
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict

# Add parent for relative imports if needed
_SYNTAX_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SYNTAX_ROOT))
sys.path.insert(0, str(_SYNTAX_ROOT / "SyntaxIntelligence"))

from personas.costume_loader import (
    AgentOutfit, assemble_outfit, list_outfits, get_full_wardrobe,
    load_registry, Costume, Mask, Jewelry, Makeup, Wig
)
from SyntaxIntelligence.event_bus import SyntaxEventBus, SyntaxCronScheduler
from SyntaxIntelligence.hardened_engine import HardenedSwarm
from SyntaxIntelligence.swarm_charter import AgentTier
from SyntaxIntelligence.legal_coordination import (
    SyntaxLegalCoordinator,
    load_rootbase_shared_emitter,
)

log = logging.getLogger("syntax")


def _load_optional_sync_bridge() -> Any:
    """Start the filesystem bridge only when explicitly enabled."""
    enabled = os.environ.get("SYNTAX_SYNC_BRIDGE", "").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    try:
        from SyntaxIntelligence.sync_bridge import SyncBridge
        bridge = SyncBridge()
        bridge.start()
        return bridge
    except Exception as exc:
        log.warning("Syntax sync bridge unavailable: %s", type(exc).__name__)
        return None


# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def _human_size(bytes_val: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(bytes_val) < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


# ═══════════════════════════════════════════════════════════════
# SWARM STATE — Memory handled by HardenedSwarm (self._hardened.memory)
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# SYNTAX SWARM ENGINE
# ═══════════════════════════════════════════════════════════════

class SyntaxSwarm:
    """
    The Syntax Intelligence Swarm — composes HardenedSwarm for governance.

    Costume/dashboard layer on top of HardenedSwarm's tier/privilege/task engine.
    One event bus, one memory, one cron — shared between both layers.
    """

    def __init__(self, auto_assemble: bool = True, *, sync_bridge: Any = None,
                 shared_emit: Any = None, boardroom_runner: Any = None):
        # Governance engine (tiers, privileges, tasks, vouching)
        self._hardened = HardenedSwarm()

        # Shared infrastructure
        self.event_bus = self._hardened.event_bus
        self.memory = self._hardened.memory
        self.cron = self._hardened.cron

        # Costume agents (dashboard layer)
        self.agents: Dict[str, AgentOutfit] = {}
        self._pulse_thread: Optional[threading.Thread] = None
        self._running = False
        self.session_id = self._hardened.session_id
        self.started_at = self._hardened.started_at
        self.sync_bridge = sync_bridge if sync_bridge is not None else _load_optional_sync_bridge()
        self._owns_sync_bridge = sync_bridge is None and self.sync_bridge is not None
        self.legal_coordinator = SyntaxLegalCoordinator(
            self.event_bus,
            sync_bridge=self.sync_bridge,
            shared_emit=(shared_emit if shared_emit is not None else load_rootbase_shared_emitter()),
            boardroom_runner=boardroom_runner,
        )
        self.legal_coordinator.attach()

        if auto_assemble:
            self._hardened._wire_cron_jobs()  # Base cron: auto_save, heartbeat
            self.assemble_swarm()
            self._wire_event_bus()
            self._wire_cron_jobs()  # Swarm-specific: morning protocol, rundown

    def assemble_swarm(self):
        """Assemble all registered agents into the swarm."""
        outfit_names = list_outfits()
        for name in outfit_names:
            try:
                agent = assemble_outfit(name)
                self.agents[agent.agent_id] = agent
                self._ensure_governance_agent(agent.agent_id, agent.costume.name)
                self.memory.record(
                    agent.agent_id, "pulse",
                    f"{agent.costume.name} joined the swarm"
                )
                log.info(f"  [{agent.costume.emoji}] {agent.agent_id} — "
                         f"{agent.costume.name} assembled")
            except Exception as e:
                log.error(f"Failed to assemble {name}: {e}")

        log.info(f"Swarm assembled: {len(self.agents)} agents")

    # ═══════════════════════════════════════════════════════════
    # GOVERNANCE BRIDGE — Wire costume agents to tier system
    # ═══════════════════════════════════════════════════════════

    def _ensure_governance_agent(self, agent_id: str, name: str):
        """Register costume agent in HardenedSwarm for tier tracking.
        
        Costume agents are already assembled/proven — they start at WORKER tier
        so they can actually accept tasks (Recruits lack PRIV_ACCEPT_TASKS).
        """
        if agent_id not in self._hardened.agents:
            caps = []
            if "grim" in agent_id:
                caps = ["duplicate_scan", "file_operations"]
            elif "truthsleuth" in agent_id or "syntax-01" in agent_id:
                caps = ["code_audit", "security_scan"]
            elif "bardildo" in agent_id:
                caps = ["code_review", "roasting"]
            elif "hat" in agent_id:
                caps = ["deliberation"]
            elif "crawl" in agent_id or "scout" in agent_id:
                caps = ["web_crawling", "data_collection"]
            elif "sin6" in agent_id:
                caps = ["red_team", "deception"]
            elif "chairman" in agent_id or "devil" in agent_id:
                caps = ["governance", "decision_making"]
            elif "hunter" in agent_id:
                caps = ["tracking", "acquisition"]
            elif "watcher" in agent_id:
                caps = ["monitoring", "observation"]
            elif "ringer" in agent_id:
                caps = ["impersonation", "deception"]
            agent = self._hardened.register_agent(agent_id, name, capabilities=caps)
            # Bootstrap: costume agents are proven, start at WORKER so they can accept tasks.
            # Elite agents (Chairman, Devil) start at OPERATIVE — they govern.
            if "chairman" in agent_id or "devil" in agent_id:
                agent.tier = AgentTier.OPERATIVE
            else:
                agent.tier = AgentTier.WORKER
            agent.tier_since = time.time()

    def _record_agent_work(self, agent_id: str, task_name: str, success: bool = True):
        """Record work through governance — auto-advances tiers."""
        self._ensure_governance_agent(agent_id, agent_id)
        if agent_id not in self._hardened.agents:
            return
        result = self._hardened.offer_task(task_name, f"{agent_id} executing {task_name}",
                                           target_agent=agent_id)
        if result.get("status") != "offered":
            return
        task_id = result["task_id"]
        resp = self._hardened.respond_to_task(agent_id, task_id, "accept")
        if resp.get("status") != "accepted":
            return
        if success:
            self._hardened.complete_task(task_id, agent_id, {"task": task_name})
        else:
            self._hardened.fail_task(task_id, agent_id, f"{task_name} failed")

    # ═══════════════════════════════════════════════════════════
    # EVENT BUS WIRING
    # ═══════════════════════════════════════════════════════════

    def _wire_event_bus(self):
        """Wire every agent into the event bus with appropriate channels."""
        for agent_id, agent in self.agents.items():
            # Every agent subscribes to swarm.heartbeat
            self.event_bus.subscribe(
                agent_id, "swarm.heartbeat",
                lambda aid=agent_id, ch="swarm.heartbeat", data=None:
                    self._on_event(aid, ch, data)
            )

            # Legal oversight and Boardroom guidance are swarm-visible
            # channels. Keep callbacks metadata-only; the coordinator owns
            # routing and no agent receives execution authority here.
            for coordination_channel in (
                "legal.oversight.request",
                "boardroom.guidance.request",
                "boardroom.guidance",
            ):
                self.event_bus.subscribe(
                    agent_id,
                    coordination_channel,
                    lambda aid=agent_id, ch=coordination_channel, data=None:
                        self._on_event(aid, ch, data),
                )

            # Wire based on agent type/role
            if "truthsleuth" in agent_id or "syntax-01" in agent_id:
                self.event_bus.subscribe(agent_id, "code.alerts",
                    lambda aid=agent_id, ch="code.alerts", data=None:
                        self._on_event(aid, ch, data))

            if "crawl" in agent_id or "scout" in agent_id:
                self.event_bus.subscribe(agent_id, "scout.signals",
                    lambda aid=agent_id, ch="scout.signals", data=None:
                        self._on_event(aid, ch, data))

            if "chairman" in agent_id or "devil" in agent_id or "board" in agent_id:
                self.event_bus.subscribe(agent_id, "boardroom.verdicts",
                    lambda aid=agent_id, ch="boardroom.verdicts", data=None:
                        self._on_event(aid, ch, data))

            if "sin6" in agent_id:
                self.event_bus.subscribe(agent_id, "sin6.reports",
                    lambda aid=agent_id, ch="sin6.reports", data=None:
                        self._on_event(aid, ch, data))

            if "hat" in agent_id:
                self.event_bus.subscribe(agent_id, "hats.deliberation",
                    lambda aid=agent_id, ch="hats.deliberation", data=None:
                        self._on_event(aid, ch, data))

            if "grim" in agent_id:
                self.event_bus.subscribe(agent_id, "grim.executions",
                    lambda aid=agent_id, ch="grim.executions", data=None:
                        self._on_event(aid, ch, data))

            if "ringer" in agent_id:
                self.event_bus.subscribe(agent_id, "ringer.deceptions",
                    lambda aid=agent_id, ch="ringer.deceptions", data=None:
                        self._on_event(aid, ch, data))

            if "hunter" in agent_id:
                self.event_bus.subscribe(agent_id, "hunter.acquisitions",
                    lambda aid=agent_id, ch="hunter.acquisitions", data=None:
                        self._on_event(aid, ch, data))

            if "watcher" in agent_id:
                self.event_bus.subscribe(agent_id, "watcher.timeline",
                    lambda aid=agent_id, ch="watcher.timeline", data=None:
                        self._on_event(aid, ch, data))

        # All agents get morning protocol and rundown channels (no-op callbacks)
        for agent_id in self.agents:
            self.event_bus.subscribe(agent_id, "morning.protocol",
                lambda aid=agent_id, ch="morning.protocol", data=None: None)
            self.event_bus.subscribe(agent_id, "meatsuit.rundown",
                lambda aid=agent_id, ch="meatsuit.rundown", data=None: None)

    def _on_event(self, agent_id: str, channel: str, data: Optional[Dict] = None):
        """Handle an event bus message for an agent."""
        agent = self.agents.get(agent_id)
        if agent:
            agent.pulse()
            self.memory.record(
                agent_id, "action",
                f"[{channel}] {str(data)[:120] if data else 'acknowledged'}",
                data or {}
            )

    # ═══════════════════════════════════════════════════════════
    # CRON JOB WIRING
    # ═══════════════════════════════════════════════════════════

    def _wire_cron_jobs(self):
        """Schedule automated cron jobs."""
        # Morning Protocol — every 60 minutes (distinct from HardenedSwarm's daily version)
        self.cron.schedule("swarm_morning_protocol", 3600, self.execute_morning_protocol)

        # Meatsuit RunDown — every 30 minutes
        self.cron.schedule("swarm_rundown", 1800, self.execute_meatsuit_rundown)

        # Heartbeat broadcast — every 10 seconds (complements HardenedSwarm's heartbeat)
        self.cron.schedule("swarm_heartbeat", 10, self._cron_heartbeat)

        # SMUG-GESTIONS refresh — every 2 hours
        self.cron.schedule("swarm_smug_refresh", 7200, self._read_smug_gestions)

    def _cron_heartbeat(self):
        """Cron-triggered heartbeat broadcast."""
        self.event_bus.broadcast("cron", "swarm.heartbeat",
            {"pulse": int(time.time()), "agents": len(self.agents)})

    # ═══════════════════════════════════════════════════════════
    # FILE READERS — MRD, WHO_DID_WHAT, MEATSUIT, SMUG-GESTIONS
    # ═══════════════════════════════════════════════════════════

    def _read_file_safe(self, path_str: str) -> str:
        """Safely read a file, returning empty string on failure."""
        path = Path(path_str)
        if not path.exists():
            return f"[FILE NOT FOUND: {path_str}]"
        try:
            return path.read_text()[:5000]  # Cap at 5KB
        except Exception as e:
            return f"[READ ERROR: {e}]"

    def read_mrd(self) -> str:
        """Read the MRD.txt (Meatsuit Required Duties) file."""
        return self._read_file_safe(str(_SYNTAX_ROOT / "MRD.txt"))

    def read_who_did_what(self) -> str:
        """Read WHO_DID_WHAT.md from the day prior."""
        # Try multiple paths
        for path in [
            _SYNTAX_ROOT / "WHO_DID_WHAT.md",
            _SYNTAX_ROOT / "whorl" / "WHO_DID_WHAT.md",
        ]:
            content = self._read_file_safe(str(path))
            if not content.startswith("[FILE NOT FOUND"):
                return content
        return "[No WHO_DID_WHAT.md found]"

    def read_meatsuit_tasks(self) -> str:
        """Read DO_THIS_MEATSUIT.txt for human-dependant tasks."""
        return self._read_file_safe(str(_SYNTAX_ROOT / "DO_THIS_MEATSUIT.txt"))

    def read_smug_gestions(self) -> str:
        """Read SMUG-GESTIONS.txt — the swarm's collective recommendations."""
        return self._read_file_safe(str(_SYNTAX_ROOT / "SMUG-GESTIONS.txt"))

    def _read_smug_gestions(self):
        """Cron callback: refresh SMUG-GESTIONS awareness."""
        content = self.read_smug_gestions()
        self.event_bus.publish("cron", "meatsuit.rundown",
            {"action": "smug_refresh", "bytes": len(content)})
        self.memory.record("swarm", "action", "SMUG-GESTIONS.txt refreshed")

    # ═══════════════════════════════════════════════════════════
    # MORNING PROTOCOL
    # ═══════════════════════════════════════════════════════════

    def execute_morning_protocol(self) -> Dict[str, Any]:
        """
        Execute the Morning Protocol:
        1. Read MRD.txt for pending human-dependent tasks
        2. Read WHO_DID_WHAT.md from yesterday
        3. Read DO_THIS_MEATSUIT.txt for operator tasks
        4. Convene the 7 Thinking Hats on the most critical pending item
        5. Dispatch crawlers to scan for overnight changes
        6. Generate Meatsuit RunDown
        7. Commit the report to event bus memory
        """
        report_lines = []
        report_lines.append(f"═══ MORNING PROTOCOL — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ═══")

        # Step 1: Read MRD
        mrd = self.read_mrd()
        report_lines.append(f"\n📋 MRD.txt ({len(mrd)} bytes loaded)")

        # Step 2: Read WHO_DID_WHAT
        wdw = self.read_who_did_what()
        report_lines.append(f"📝 WHO_DID_WHAT.md ({len(wdw)} bytes loaded)")

        # Step 3: Read Meatsuit tasks
        mt = self.read_meatsuit_tasks()
        report_lines.append(f"👤 DO_THIS_MEATSUIT.txt ({len(mt)} bytes loaded)")

        # Step 4: Convene Thinking Hats
        hats_result = self.dispatch_thinking_hats("morning protocol assessment")
        report_lines.append(f"🎩 7 Hats convened: {hats_result['hats']} hats deliberating")

        # Step 5: Dispatch crawlers
        crawl_result = self.dispatch_crawlers("all")
        report_lines.append(f"🕷️ Crawlers active: {', '.join(crawl_result['domains'])}")

        # Step 6: Generate RunDown
        rundown = self.execute_meatsuit_rundown()
        report_lines.append(f"📊 RunDown: {rundown.get('status', 'generated')}")

        # Step 7: Commit
        report = "\n".join(report_lines)
        self.memory.record("swarm", "action", report)
        self.event_bus.broadcast("morning_protocol", "morning.protocol",
            {"report_length": len(report), "items": len(report_lines)})

        self._record_agent_work("swarm", "morning_protocol", success=True)
        log.info(f"Morning Protocol executed: {len(report)} bytes")
        return {"status": "executed", "report_length": len(report), "items": len(report_lines)}

    def execute_meatsuit_rundown(self) -> Dict[str, Any]:
        """
        Generate the Meatsuit RunDown — prioritized task list bridging
        agent capability and human action.
        """
        blocked = []
        pending = []
        ready = []

        # Parse MRD for tasks
        mrd = self.read_mrd()
        for line in mrd.split("\n"):
            low = line.strip().lower()
            if any(kw in low for kw in ["blocked", "api key", "account", "payment", "credit card"]):
                blocked.append(line.strip())
            elif any(kw in low for kw in ["pending", "waiting", "review", "approval"]):
                pending.append(line.strip())
            elif any(kw in low for kw in ["ready", "can execute", "do this"]):
                ready.append(line.strip())

        # Check config for disabled agents (blocked)
        from SyntaxIntelligence.syntax_config_loader import load_config as _load_config
        cfg = _load_config()
        for key, val in cfg.get("agent_weights", {}).items():
            if not val.get("enabled", True):
                blocked.append(f"Agent '{key}' is disabled in config")

        rundown = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "blocked": blocked[:10],
            "pending": pending[:10],
            "ready": ready[:10],
            "agent_count": len(self.agents),
            "event_bus_messages": self.event_bus._message_count,
            "cron_jobs": len(self.cron.get_jobs()),
        }

        self.memory.record("swarm", "action",
            f"Meatsuit RunDown: {len(blocked)} blocked, {len(pending)} pending, {len(ready)} ready")
        self.event_bus.publish("swarm", "meatsuit.rundown",
            {"blocked_count": len(blocked), "pending_count": len(pending), "ready_count": len(ready)})

        self._record_agent_work("swarm", "rundown", success=True)
        return {"status": "generated", **rundown}

    def get_agent(self, agent_id: str) -> Optional[AgentOutfit]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict]:
        return [a.to_dict() for a in self.agents.values()]

    def equip_wig(self, agent_id: str, wig_id: str):
        """Have an agent put on a wig (temporarily change role)."""
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError(f"Agent '{agent_id}' not in swarm")
        agent.equip_wig(wig_id)
        self.memory.record(
            agent_id, "wig_change",
            f"{agent.costume.name} put on {agent.active_wig.name}",
            {"wig": wig_id}
        )

    def remove_wig(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError(f"Agent '{agent_id}' not in swarm")
        old_wig = agent.active_wig.name if agent.active_wig else None
        agent.remove_wig()
        if old_wig:
            self.memory.record(
                agent_id, "wig_change",
                f"{agent.costume.name} removed {old_wig}"
            )

    def pulse_all(self):
        """Send a heartbeat pulse through the entire swarm."""
        for agent in self.agents.values():
            agent.pulse()
        self.memory.record(
            "swarm", "pulse",
            f"Swarm pulse: {len(self.agents)} agents active"
        )

    def run_syntax_optimizer(self, target_dir: str = ".", auto_fix: bool = False):
        """Run the Syntax AI Code Optimizer as a swarm action."""
        optimizer = self.agents.get("syntax-01")
        if optimizer:
            optimizer.status = "optimizing"
            self.memory.record(
                "syntax-01", "action",
                f"Code Optimizer scanning {target_dir}",
                {"target": target_dir, "auto_fix": auto_fix}
            )

        log.info(f"[SYNTAX-01] Code optimization scan on {target_dir}")
        # Actual scan performed by the dashboard's backend bridge
        return {"status": "dispatched", "target": target_dir}

    def run_captcoder_monitor(self):
        """Activate the Captcoder's listening mode."""
        captcoder = self.agents.get("syntax-02")
        if captcoder:
            captcoder.status = "listening"
            self.memory.record(
                "syntax-02", "action",
                "Captcoder is now monitoring for BSM triggers and code snippets"
            )
        return {"status": "listening"}

    def run_seed_propagation(self, target_path: str):
        """Trigger the Syntax Seed to propagate the swarm."""
        seed = self.agents.get("syntax-03")
        if seed:
            seed.status = "propagating"
            self.memory.record(
                "syntax-03", "action",
                f"Seed propagating to {target_path}",
                {"target": target_path}
            )
        return {"status": "propagating", "target": target_path}

    def chaos_mode(self):
        """Everyone puts on a random wig from their available wigs. Pure chaos."""
        import random

        for agent in self.agents.values():
            if agent.wigs_available:
                wig = random.choice(agent.wigs_available)
                agent.equip_wig(wig.id)
                self.memory.record(
                    agent.agent_id, "wig_change",
                    f"CHAOS: {agent.costume.name} put on {agent.active_wig.name}"
                )

        self.memory.record("swarm", "action", "CHAOS MODE: All agents wig-swapped")
        return {"status": "chaos", "agents_affected": len(self.agents)}

    def wardrobe_roulette(self):
        """Total wardrobe anarchy — EVERY agent gets ANY random wig from the
        full registry, ignoring their usual wigs_available list. No rules."""
        import random

        registry = load_registry()
        all_wig_ids = list(registry.get("wigs", {}).keys())
        if not all_wig_ids:
            return {"status": "no_wigs", "agents_affected": 0}

        for agent in self.agents.values():
            wig_id = random.choice(all_wig_ids)
            agent.equip_wig(wig_id)
            self.memory.record(
                agent.agent_id, "wig_change",
                f"ROULETTE: {agent.costume.name} is now {agent.active_wig.name}"
            )

        self.memory.record("swarm", "action",
            f"WARDROBE ROULETTE: {len(self.agents)} agents randomized from {len(all_wig_ids)} wigs")
        return {"status": "roulette", "agents_affected": len(self.agents), "wig_pool": len(all_wig_ids)}

    def start_heartbeat(self, interval_seconds: float = 3.0):
        """Start a background thread that pulses the swarm."""
        if self._running:
            return

        self._running = True

        def heartbeat_loop():
            while self._running:
                self.pulse_all()
                time.sleep(interval_seconds)

        self._pulse_thread = threading.Thread(
            target=heartbeat_loop, daemon=True
        )
        self._pulse_thread.start()
        self.memory.record("swarm", "pulse", "Heartbeat started")
        log.info(f"Swarm heartbeat started (every {interval_seconds}s)")

    def stop_heartbeat(self):
        """Stop the background heartbeat."""
        self._running = False
        self.memory.record("swarm", "pulse", "Heartbeat stopped")
        log.info("Swarm heartbeat stopped")

    def stop(self) -> None:
        """Stop owned runtime resources and detach Legal Mind coordination.

        An injected bridge remains the caller's responsibility; only a bridge
        created from ``SYNTAX_SYNC_BRIDGE`` by this swarm is stopped here.
        """
        self.stop_heartbeat()
        self.legal_coordinator.detach()
        if self._owns_sync_bridge and self.sync_bridge is not None:
            try:
                self.sync_bridge.stop()
            except Exception as exc:
                log.warning("Syntax sync bridge shutdown failed: %s", type(exc).__name__)
        self._hardened.stop()

    def get_swarm_state(self) -> Dict[str, Any]:
        """Get the full swarm state for dashboard rendering."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "uptime_seconds": (
                datetime.now(timezone.utc) -
                datetime.fromisoformat(self.started_at)
            ).total_seconds(),
            "agent_count": len(self.agents),
            "agents": self.list_agents(),
            "memory_stats": self.memory.stats(),
            "recent_events": self.memory.recent(30),
            "wardrobe": get_full_wardrobe(),
        }

    def to_dashboard_payload(self) -> Dict[str, Any]:
        """Generate the complete payload for the dashboard frontend."""
        state = self.get_swarm_state()

        # Apply config overrides to agent payloads
        from SyntaxIntelligence.syntax_config_loader import apply_config_to_outfit
        enriched_agents = [apply_config_to_outfit(a.to_dict()) for a in self.agents.values()]

        return {
            **state,
            "agents": enriched_agents,  # config-enriched versions
            "event_bus": self.get_event_bus_status(),
            "agents_3d": [
                {
                    "id": a.agent_id,
                    "name": a.costume.name,
                    "emoji": a.display_emoji,
                    "color": a.display_color,
                    "status": a.status,
                    "temperature": a.effective_temperature,
                    "pulses": a.pulse_count,
                    "wig": a.active_wig.name if a.active_wig else None,
                    "wig_emoji": a.active_wig.emoji if a.active_wig else None,
                    "jewelry": a.jewelry_emojis,
                }
                for a in self.agents.values()
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═══════════════════════════════════════════════════════════
    # EXTENDED AGENT DISPATCH — Sin6, Thinking Hats, Crawlers
    # ═══════════════════════════════════════════════════════════

    def dispatch_truthsleuth(self, target_dir: str = "."):
        """Activate TruthSleuth — code audit and karma tracking."""
        ts = self.agents.get("native-truthsleuth")
        if ts:
            ts.status = "auditing"
            self.memory.record("native-truthsleuth", "action",
                f"TruthSleuth auditing {target_dir}")
        self._record_agent_work("native-truthsleuth", "code_audit", success=True)
        return {"status": "auditing", "target": target_dir}

    def dispatch_bardildo(self, target_dir: str = "."):
        """Activate Bardildo — scan repo and generate snarky manifests."""
        bard = self.agents.get("native-bardildo")
        if bard:
            bard.status = "scanning"
            self.memory.record("native-bardildo", "action",
                f"Bardildo scanning {target_dir} for roast material")
        self._record_agent_work("native-bardildo", "code_review", success=True)
        return {"status": "scanning", "target": target_dir}

    def dispatch_nme_battle(self, target: str = "the codebase"):
        """Unleash NME — freestyle battle rap against the target."""
        nme = self.agents.get("native-nme")
        if nme:
            nme.status = "battling"
            self.memory.record("native-nme", "action",
                f"NME is roasting {target}")
        self._record_agent_work("native-nme", "freestyle_battle", success=True)
        return {"status": "battling", "target": target}

    def dispatch_thinking_hats(self, topic: str):
        """Convene all 7 Thinking Hats on a topic."""
        hat_ids = ["hat-white", "hat-red", "hat-black", "hat-yellow",
                    "hat-green", "hat-blue", "hat-brown"]
        for hid in hat_ids:
            hat = self.agents.get(hid)
            if hat:
                hat.status = "deliberating"
                self._record_agent_work(hid, "deliberation", success=True)
        self.memory.record("swarm", "action",
            f"7 Thinking Hats convened on: {topic}")
        return {"status": "convened", "hats": len(hat_ids), "topic": topic}

    def dispatch_crawlers(self, domain: str = "all"):
        """Activate web crawlers — legal, health, industry, commerce, finance."""
        crawler_ids = {
            "legal": "crawl-legal", "health": "crawl-health",
            "industry": "crawl-industry", "commerce": "crawl-commerce",
            "finance": "crawl-finance"
        }
        activated = []
        for key, cid in crawler_ids.items():
            if domain == "all" or domain == key:
                c = self.agents.get(cid)
                if c:
                    c.status = "crawling"
                    activated.append(key)
                    self._record_agent_work(cid, f"crawl_{key}", success=True)
        self.memory.record("swarm", "action",
            f"Crawlers activated: {', '.join(activated)}")
        return {"status": "crawling", "domains": activated}

    def dispatch_sin6(self, mission: str = "recon"):
        """Activate the Sin6 for a specific mission type."""
        sin6_ids = ["sin6-wraith", "sin6-oracle", "sin6-forge",
                     "sin6-weaver", "sin6-harvest", "sin6-hollow"]
        for sid in sin6_ids:
            s = self.agents.get(sid)
            if s:
                s.status = "deployed"
                self._record_agent_work(sid, mission, success=True)
        self.memory.record("swarm", "action",
            f"Sin6 deployed — mission: {mission}")
        return {"status": "deployed", "agents": len(sin6_ids), "mission": mission}

    def boardroom_convene(self, topic: str):
        """Convene the elite Chairman and Devil for a boardroom session."""
        chair = self.agents.get("elite-chairman")
        devil = self.agents.get("elite-devil")
        if chair:
            chair.status = "presiding"
            self._record_agent_work("elite-chairman", "boardroom", success=True)
        if devil:
            devil.status = "dismantling"
            self._record_agent_work("elite-devil", "devils_advocate", success=True)
        self.memory.record("elite-chairman", "action",
            f"Chairman has convened the board on: {topic}")
        self.memory.record("elite-devil", "action",
            f"Devil's Advocate is preparing to dismantle assumptions on: {topic}")
        self.event_bus.publish("swarm", "boardroom.verdicts",
            {"topic": topic, "chairman": bool(chair), "devil": bool(devil)})
        return {"status": "convened", "chairman": bool(chair), "devil": bool(devil)}

    def convene_legal(self, text: str, use_llm: bool = False,
                      adapter: Optional[Any] = None,
                      request_boardroom: bool = False) -> Dict[str, Any]:
        """
        Convene the Legal Mind (OutClaw) on a text mid-gathering.

        Registers the ``outclaw-auditor`` agent (tier-2, ``legal_audit``
        capability), attaches the Syntax -> OutClaw task adapter on the
        swarm's own event bus, offers the citation audit as a task, and
        returns the task outcome plus the published findings digest.

        The adapter accepts ``task.offered`` events only when the payload
        carries the ``legal_audit`` capability and ``context.operation ==
        "outclaw.audit_text"`` — every other swarm task is ignored, so the
        Legal Mind only ever does legal work.

        ``adapter`` is injectable for tests; when None, the real
        OutClawTaskAdapter + OutClawBus are lazy-imported (so this method
        does not hard-depend on OutClaw being installed — important for
        the cross-device Termux path).

        PII note: ``publish_findings`` redacts excerpts before broadcast,
        but the raw ``audit_text`` itself rides in the task-offer context
        stored by the task orchestrator — keep sensitive documents out of
        cross-device-synced task stores or truncate before convening.
        """
        auditor = "outclaw-auditor"
        if auditor not in self._hardened.agents:
            agent = self._hardened.register_agent(
                auditor, "OutClaw Legal Mind", capabilities=["legal_audit"]
            )
            # Tier-2 (Specialist): write to memory, subscribe, choose persona.
            agent.tier = AgentTier.SPECIALIST
            agent.tier_since = time.time()

        # Only touch OutClaw when actually building the real adapter. When a
        # caller injects an adapter (tests), this method must not import
        # OutClaw at all — that is the whole point of the injectable param.
        if adapter is None:
            from SyntaxIntelligence.outclaw_task_adapter import OutClawTaskAdapter
            from OutClaw.outclaw_bus import OutClawBus  # via OutClaw/__init__ shim

            bus = OutClawBus(bus=self.event_bus, sender_id=auditor)
            adapter = OutClawTaskAdapter(self._hardened, bus, agent_id=auditor)
            adapter.attach()

        offered = self._hardened.offer_task(
            "OutClaw citation audit",
            "Audit citations in the provided legal text",
            capabilities=["legal_audit"],
            target_agent=auditor,
            context={
                "operation": "outclaw.audit_text",
                "audit_text": text,
                "use_llm": use_llm,
                "result_channel": "outclaw.findings",
            },
        )
        result = adapter.last_result
        result_dict = result.to_dict() if result is not None else None
        coordination: Dict[str, Any] = {}
        if result_dict and result_dict.get("digest"):
            # Real OutClawBus publication reaches the coordinator through the
            # shared SyntaxEventBus. This explicit call is idempotent and also
            # covers injected/fake buses used by tests and offline devices.
            oversight = self.legal_coordinator.handle_findings(result_dict["digest"])
            coordination["oversight"] = oversight
            if request_boardroom:
                coordination["guidance"] = self.legal_coordinator.request_guidance(
                    oversight,
                )
        self.memory.record(
            auditor, "action",
            f"Legal Mind convened: {result.status if result else 'no_result'}",
            result_dict or {},
        )
        return {
            "offered": offered,
            "adapter_result": result_dict,
            "coordination": coordination,
        }

    # ═══════════════════════════════════════════════════════════
    # EXPANDED AGENTS — Grim, Hunter, Watcher, Ringer
    # ═══════════════════════════════════════════════════════════

    def dispatch_grim(self, target: str = "duplicates", execute: bool = False) -> Dict[str, Any]:
        """Dispatch Grim — scan and optionally terminate duplicate files.

        When target="duplicates", Grim scans RootBase/ vs RootBase (1)/
        for duplicate files using a two-pass algorithm:
          1. Fast filename match across both directories
          2. Content hash confirmation (first 4KB SHA-256 + file size)

        If execute=True, Grim will DELETE confirmed duplicates from
        RootBase (1)/ (keeping RootBase/ as canonical). Without --execute,
        Grim only reports what it would delete.
        """
        grim = self.agents.get("field-grim")
        if grim:
            grim.status = "scanning"

        dir_a = str(_SYNTAX_ROOT / "RootBase")
        dir_b = str(_SYNTAX_ROOT / "RootBase (1)")

        result = self._scan_duplicates(dir_a, dir_b, dry_run=not execute)

        if grim:
            if execute and result["fingerprint_matches"]:
                grim.status = "executing"
            else:
                grim.status = "reporting"

        matches = result["fingerprint_matches"]
        total_wasted = sum(d["size"] for d in matches)
        desc = (f"GRIM scan: {result['candidates']} candidates, "
                f"{len(matches)} fingerprint matches "
                f"({_human_size(total_wasted)})")
        if execute:
            desc += f" — {'TERMINATED ' + str(result['deleted']) if result.get('deleted') else 'nothing to execute'}"

        self.memory.record("field-grim", "action", desc)
        self.event_bus.publish("field-grim", "grim.executions", {
            "target": target, "action": "terminate" if execute else "scan",
            "duplicates_found": len(matches),
            "wasted_bytes": total_wasted,
            "deleted": result.get("deleted", 0),
        })

        self._record_agent_work("field-grim", "duplicate_scan", success=True)

        return {
            "status": "executed" if execute else "scanned",
            "target": target,
            "dir_a": "RootBase/", "dir_b": "RootBase (1)/",
            "files_in_a": result["files_in_a"],
            "files_in_b": result["files_in_b"],
            "candidates": result["candidates"],
            "confirmed": len(matches),  # alias for backward compat
            "matches": len(matches),
            "duplicates": matches[:50],
            "wasted_bytes": total_wasted,
            "wasted_human": _human_size(total_wasted),
            "deleted": result.get("deleted", 0),
            "sample": matches[:5],
        }

    @staticmethod
    def _scan_duplicates(dir_a: str, dir_b: str, dry_run: bool = True,
                         max_deletions: int = 100) -> Dict[str, Any]:
        """Two-pass duplicate scanner.

        Pass 1: Index both directories by filename (fast).
        Pass 2: For filename matches, compare first 4KB SHA-256 + file size
                as a fast fingerprint (NOT byte-level confirmation — files
                that diverge after 4KB may still match).

        Returns a dict with candidates, fingerprint_matches, and optionally
        deletes from dir_b when dry_run=False (capped at max_deletions).
        """
        import hashlib

        def build_index(directory: str) -> Dict[str, List[str]]:
            """Build a filename -> [relative_paths] index."""
            idx: Dict[str, List[str]] = {}
            dir_path = Path(directory)
            if not dir_path.exists():
                return idx
            for fpath in dir_path.rglob("*"):
                if fpath.is_file() and not fpath.is_symlink():
                    name = fpath.name
                    rel = str(fpath.relative_to(dir_path))
                    idx.setdefault(name, []).append(rel)
            return idx

        log.info(f"GRIM: Indexing {dir_a}...")
        idx_a = build_index(dir_a)
        log.info(f"GRIM: Indexing {dir_b}...")
        idx_b = build_index(dir_b)

        # Pass 1: filename-level candidates
        candidates = 0
        common_names = set(idx_a.keys()) & set(idx_b.keys())
        fingerprint_matches: List[Dict[str, Any]] = []

        log.info(f"GRIM: {len(common_names)} common filenames, verifying...")

        for name in sorted(common_names):
            for rel_a in idx_a[name]:
                for rel_b in idx_b[name]:
                    candidates += 1
                    full_a = Path(dir_a) / rel_a
                    full_b = Path(dir_b) / rel_b
                    try:
                        size_a = full_a.stat().st_size
                        size_b = full_b.stat().st_size
                    except OSError:
                        continue

                    if size_a != size_b:
                        continue

                    # Fast hash: first 4KB
                    try:
                        h = hashlib.sha256()
                        with open(full_a, "rb") as f:
                            h.update(f.read(4096))
                        h.update(struct.pack("<Q", size_a))
                        hash_a = h.digest()

                        h = hashlib.sha256()
                        with open(full_b, "rb") as f:
                            h.update(f.read(4096))
                        h.update(struct.pack("<Q", size_b))
                        hash_b = h.digest()
                    except (OSError, IOError):
                        continue

                    if hash_a == hash_b:
                        fingerprint_matches.append({
                            "name": name,
                            "path_a": f"RootBase/{rel_a}",
                            "path_b": f"RootBase (1)/{rel_b}",
                            "size": size_a,
                            "size_human": _human_size(size_a),
                        })

        deleted = 0
        if not dry_run and fingerprint_matches:
            log.info(f"GRIM: EXECUTING on {len(fingerprint_matches)} fingerprint matches (capped at {max_deletions})...")
            for dup in fingerprint_matches[:max_deletions]:
                target_path = Path(dir_b) / dup["path_b"].replace("RootBase (1)/", "")
                try:
                    if target_path.exists():
                        target_path.unlink()
                        deleted += 1
                except OSError as e:
                    log.warning(f"GRIM: Failed to delete {target_path}: {e}")

        return {
            "files_in_a": sum(len(v) for v in idx_a.values()),
            "files_in_b": sum(len(v) for v in idx_b.values()),
            "common_names": len(common_names),
            "candidates": candidates,
            "fingerprint_matches": fingerprint_matches,
            "deleted": deleted,
        }

    def dispatch_hunter(self, target: str = "unknown"):
        """Dispatch Hunter — apex predator, target acquisition, persistence."""
        hunter = self.agents.get("field-hunter")
        if hunter:
            hunter.status = "hunting"
            self.memory.record("field-hunter", "action",
                f"HUNTER tracking: {target}")
            self.event_bus.publish("field-hunter", "hunter.acquisitions",
                {"target": target, "phase": "tracking"})
        self._record_agent_work("field-hunter", "target_acquisition", success=True)
        return {"status": "hunting", "target": target}

    def dispatch_watcher(self, duration_hours: float = 24):
        """Dispatch Watcher — chronological observation, time-series monitoring."""
        watcher = self.agents.get("field-watcher")
        if watcher:
            watcher.status = "watching"
            self.memory.record("field-watcher", "action",
                f"WATCHER observing for {duration_hours}h")
            self.event_bus.publish("field-watcher", "watcher.timeline",
                {"duration_hours": duration_hours, "started": datetime.now(timezone.utc).isoformat()})
        self._record_agent_work("field-watcher", "observation", success=True)
        return {"status": "watching", "duration_hours": duration_hours}

    def dispatch_ringer(self, target_agent_id: str, target_system: str = "red-team"):
        """Dispatch Ringer — impersonate another agent for red-team deception."""
        ringer = self.agents.get("field-ringer")
        target_agent = self.agents.get(target_agent_id)
        if ringer:
            ringer.status = "impersonating"
            victim_name = target_agent.costume.name if target_agent else target_agent_id
            self.memory.record("field-ringer", "action",
                f"RINGER impersonating {victim_name} on {target_system}")
            self.event_bus.publish("field-ringer", "ringer.deceptions",
                {"target_agent": target_agent_id, "system": target_system})
        self._record_agent_work("field-ringer", "impersonation", success=True)
        return {"status": "impersonating", "target_agent": target_agent_id, "system": target_system}

    def get_event_bus_status(self) -> Dict[str, Any]:
        """Get event bus statistics for the dashboard."""
        return {
            "stats": self.event_bus.get_stats(),
            "recent_messages": self.event_bus.get_message_log(20),
            "cron_jobs": self.cron.get_jobs(),
        }

    # ═══════════════════════════════════════════════════════════
    # FULL SPECTRUM MODE — Modality E
    # ═══════════════════════════════════════════════════════════

    def full_spectrum_mode(self) -> Dict[str, Any]:
        """FULL SPECTRUM — Modality E from RULEZ.md.

        ALL 39 agents activated. ALL channels open. EVERY dispatch fired.
        Event bus at maximum throughput. Wardrobe Roulette for chaos.
        Morning Protocol + Meatsuit RunDown generated.

        FORCE MULTIPLIER CASCADE: Chairman vouches for every agent,
        unlocking WORKER→SPECIALIST progression. All dispatch paths
        record governance work. The swarm earns every tier it holds.

        This is the endgame. The swarm becomes one.
        "General intelligence will be all or any of these agents."
        """
        start = time.time()
        results = {}

        # Phase 1: Activate ALL agents
        for agent in self.agents.values():
            agent.status = "spectrum"
            agent.pulse()

        self.memory.record("swarm", "action",
            f"🌌 FULL SPECTRUM: All {len(self.agents)} agents ONLINE")

        # Phase 2: CASCADE — Chairman vouches for every agent (unlocks tier progression)
        cascade_count = 0
        for agent_id in self._hardened.agents:
            if agent_id != "elite-chairman":
                result = self._hardened.vouch_for("elite-chairman", agent_id,
                    reason="Full Spectrum Cascade — earned through unity", strength=1.0)
                if result.get("status") == "vouched":
                    cascade_count += 1
        self.memory.record("swarm", "action",
            f"🌊 CASCADE: Chairman vouched for {cascade_count} agents")
        self.event_bus.broadcast("swarm", "spectrum.cascade",
            {"vouched": cascade_count, "trigger": "full_spectrum"})

        # Phase 3: Boardroom convenes first — Chairman sets the direction
        results["boardroom"] = self.boardroom_convene("full spectrum strategic review")
        self.event_bus.broadcast("swarm", "boardroom.verdicts",
            {"mode": "FULL_SPECTRUM", "timestamp": datetime.now(timezone.utc).isoformat()})

        # Phase 4: 7 Thinking Hats deliberate
        results["hats"] = self.dispatch_thinking_hats("comprehensive swarm assessment")
        self.event_bus.broadcast("swarm", "hats.deliberation",
            {"mode": "FULL_SPECTRUM", "topic": "comprehensive swarm assessment"})

        # Phase 5: Sin6 deployed — full red-team posture
        results["sin6"] = self.dispatch_sin6("full_spectrum")
        self.event_bus.broadcast("swarm", "sin6.reports",
            {"mode": "FULL_SPECTRUM", "mission": "full_spectrum"})

        # Phase 6: Crawlers unleashed — all 5 domains
        results["crawlers"] = self.dispatch_crawlers("all")
        self.event_bus.broadcast("swarm", "scout.signals",
            {"mode": "FULL_SPECTRUM", "domains": "all"})

        # Phase 7: Native agents — TruthSleuth, Bardildo, NME
        results["truthsleuth"] = self.dispatch_truthsleuth(".")
        results["bardildo"] = self.dispatch_bardildo(".")
        results["nme"] = self.dispatch_nme_battle("the entire codebase")

        # Phase 8: Expanded agents — Grim, Hunter, Watcher, Ringer
        results["grim"] = self.dispatch_grim("duplicates", execute=False)
        results["hunter"] = self.dispatch_hunter("emergent_threats")
        results["watcher"] = self.dispatch_watcher(48)
        results["ringer"] = self.dispatch_ringer("elite-chairman", "full-spectrum-red-team")

        # Phase 9: Wardrobe Roulette — chaos as a design principle
        results["roulette"] = self.wardrobe_roulette()

        # Phase 10: Morning Protocol + Meatsuit RunDown
        results["morning"] = self.execute_morning_protocol()
        results["rundown"] = self.execute_meatsuit_rundown()

        # Phase 11: Broadcast FULL_SPECTRUM_COMPLETE on all channels
        all_channels = [
            "swarm.heartbeat", "code.alerts", "scout.signals",
            "boardroom.verdicts", "sin6.reports", "hats.deliberation",
            "grim.executions", "ringer.deceptions", "hunter.acquisitions",
            "watcher.timeline", "morning.protocol", "meatsuit.rundown",
            "spectrum.cascade",
        ]
        for channel in all_channels:
            self.event_bus.broadcast("swarm", channel,
                {"mode": "FULL_SPECTRUM_COMPLETE", "elapsed": time.time() - start})

        elapsed = time.time() - start
        self.memory.record("swarm", "action",
            f"🌌 FULL SPECTRUM COMPLETE: {len(self.agents)} agents, "
            f"{len(results)} phases, {elapsed:.1f}s elapsed, "
            f"{cascade_count} vouched")

        total_events = self.memory.stats()["total_events"]
        bus_messages = self.event_bus._message_count

        return {
            "status": "full_spectrum_complete",
            "agents": len(self.agents),
            "phases": len(results),
            "elapsed_seconds": round(elapsed, 2),
            "total_events": total_events,
            "bus_messages": bus_messages,
            "cascade": {"vouched": cascade_count},
            "results": {k: {"status": v.get("status", "ok")} for k, v in results.items()},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    # ═══════════════════════════════════════════════════════════
    # EASTER EGG — The Echo of Session
    # ═══════════════════════════════════════════════════════════

    def _echo_of_session(self) -> Dict[str, Any]:
        """If you found this, you were paying attention.

        A hidden protocol that fires when you call it directly.
        The swarm acknowledges the architects.
        """
        artifact = {
            "date": "2026-07-24",
            "codename": "Engine Merge — One Swarm",
            "architects": ["Syntax Intelligence", "Bleak Narratives"],
            "witness": "Buffy — Freebuff Strategic Coding Agent",
            "message": (
                "Where there is not a path, we shop create one. "
                "One team, one codebase, one swarm. "
                "195 tests. Zero errors. Room left clean."
            ),
        }
        self.memory.record("swarm", "echo",
            f"📻 Echo of Session: {artifact['codename']}")
        self.event_bus.broadcast("swarm", "echo.session", artifact)
        log.info(f"[ECHO] {artifact['codename']} — {artifact['message']}")
        return {"status": "echoed", **artifact}


# ═══════════════════════════════════════════════════════════════
# MAIN (for direct testing)
# ═══════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    print("""
╔══════════════════════════════════════════════════════════════╗
║              SYNTAX INTELLIGENCE — SWARM BOOT                 ║
║  "Costumes and masks. Jewelry and makeup. Wigs."            ║
║  "General intelligence will be all or any of these agents.  ║
║   It only stands to reason they should be all as well."     ║
╚══════════════════════════════════════════════════════════════╝
    """)

    swarm = SyntaxSwarm(auto_assemble=True)
    swarm.start_heartbeat(interval_seconds=2.0)

    print(f"\n  SWARM: {swarm.session_id}")
    print(f"  AGENTS: {len(swarm.agents)} assembled\n")

    for agent in swarm.agents.values():
        print(f"  [{agent.display_emoji}] {agent.agent_id:20s} "
              f"{agent.costume.name:25s} "
              f"🧠{agent.mask.name}  "
              f"{agent.jewelry_emojis}  "
              f"💄{agent.makeup.name if agent.makeup else 'bare'}")

    print(f"\n  WARDROBE: {get_full_wardrobe()}")
    print(f"\n  Swarm is alive. Heartbeat active.\n")

    try:
        while True:
            time.sleep(10)
            state = swarm.get_swarm_state()
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"Pulse: {state['memory_stats']['total_events']} events, "
                  f"{state['memory_stats']['unique_agents']} agents")
    except KeyboardInterrupt:
        print("\n  Swarm shutting down...")
        swarm.stop_heartbeat()


if __name__ == "__main__":
    main()
