#!/usr/bin/env python3
"""
Syntax Intelligence — Dashboard Server
Flask + SocketIO server serving the Syntax Swarm Dashboard.
"""

import os, sys, json, time, logging
from pathlib import Path
from datetime import datetime, timezone

_SYNTAX_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SYNTAX_ROOT))

from flask import Flask, render_template_string, jsonify
from flask_socketio import SocketIO, emit

from SyntaxIntelligence.syntax_core import SyntaxSwarm

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("syntax-server")

# ── Flask App ────────────────────────────────────────────────
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Load Dashboard HTML ──────────────────────────────────────
TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
with open(TEMPLATE_PATH, "r") as f:
    DASHBOARD_HTML = f.read()

# ── Bootstrap Swarm ──────────────────────────────────────────
log.info("Assembling Syntax Intelligence Swarm...")
swarm = SyntaxSwarm(auto_assemble=True)

# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve the main dashboard."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/swarm")
def api_swarm():
    """JSON API endpoint for the swarm state."""
    return jsonify(swarm.to_dashboard_payload())


@app.route("/api/events")
def api_events():
    """JSON API endpoint for recent events."""
    return jsonify(swarm.memory.recent(50))


@app.route("/api/wardrobe")
def api_wardrobe():
    """JSON API endpoint for the full wardrobe."""
    from SyntaxIntelligence.personas.costume_loader import get_full_wardrobe
    return jsonify(get_full_wardrobe())


# ══════════════════════════════════════════════════════════════
# SOCKETIO HANDLERS
# ══════════════════════════════════════════════════════════════

@socketio.on("connect")
def handle_connect():
    """Send full swarm state on connection."""
    payload = swarm.to_dashboard_payload()
    emit("swarm_update", payload)
    log.info(f"Client connected — {len(swarm.agents)} agents served")


@socketio.on("start_heartbeat")
def handle_start_heartbeat():
    swarm.start_heartbeat(interval_seconds=2.5)
    emit("swarm_event", {
        "event_type": "pulse",
        "agent_id": "SWARM",
        "description": "Heartbeat started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("stop_heartbeat")
def handle_stop_heartbeat():
    swarm.stop_heartbeat()
    emit("swarm_event", {
        "event_type": "pulse",
        "agent_id": "SWARM",
        "description": "Heartbeat stopped",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("chaos_mode")
def handle_chaos():
    """Trigger chaos — random wig swaps from available wigs only."""
    result = swarm.chaos_mode()
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "wig_change",
        "agent_id": "SWARM",
        "description": f"CHAOS MODE: {result['agents_affected']} agents wig-swapped",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("wardrobe_roulette")
def handle_roulette():
    """Wardrobe Roulette — ANY wig on ANY agent, full registry."""
    result = swarm.wardrobe_roulette()
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "wig_change",
        "agent_id": "SWARM",
        "description": f"🎰 ROULETTE: {result['agents_affected']} agents spun from {result['wig_pool']} wigs",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("equip_wig")
def handle_equip_wig(data):
    """Equip a wig on a specific agent."""
    agent_id = data.get("agent_id")
    wig_id = data.get("wig_id")
    try:
        swarm.equip_wig(agent_id, wig_id)
        emit("swarm_update", swarm.to_dashboard_payload())
        emit("swarm_event", {
            "event_type": "wig_change",
            "agent_id": agent_id,
            "description": f"Equipped wig: {wig_id}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        emit("swarm_event", {
            "event_type": "error",
            "agent_id": agent_id,
            "description": f"Wig equip failed: {e}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


@socketio.on("run_optimizer")
def handle_optimizer(data):
    """Run the Syntax AI Code Optimizer."""
    target = data.get("target", ".")
    auto_fix = data.get("auto_fix", False)
    result = swarm.run_syntax_optimizer(target, auto_fix)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action",
        "agent_id": "syntax-01",
        "description": f"Code optimizer dispatched on {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("propagate_seed")
def handle_propagate(data):
    """Trigger swarm seed propagation."""
    target = data.get("target_path", "/tmp/syntax_node")
    result = swarm.run_seed_propagation(target)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action",
        "agent_id": "syntax-03",
        "description": f"Seed propagating to {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("swarm_command")
def handle_command(data):
    """Handle a text command sent to the swarm."""
    cmd = data.get("command", "").strip()
    swarm.memory.record("OPERATOR", "action", f"> {cmd}")

    # Echo as swarm response
    emit("swarm_event", {
        "event_type": "action",
        "agent_id": "SWARM",
        "description": f"Acknowledged: \"{cmd[:80]}\"",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    emit("swarm_update", swarm.to_dashboard_payload())


@socketio.on("request_agent_detail")
def handle_agent_detail(data):
    """Send detailed agent info."""
    agent_id = data.get("agent_id")
    agent = swarm.get_agent(agent_id)
    if agent:
        emit("agent_detail", agent.to_dict())


@socketio.on("dispatch_truthsleuth")
def handle_truthsleuth(data):
    target = data.get("target", ".")
    result = swarm.dispatch_truthsleuth(target)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "native-truthsleuth",
        "description": f"TruthSleuth audit dispatched on {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_bardildo")
def handle_bardildo(data):
    target = data.get("target", ".")
    result = swarm.dispatch_bardildo(target)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "native-bardildo",
        "description": f"Bardildo scanning {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_nme")
def handle_nme(data):
    target = data.get("target", "the codebase")
    result = swarm.dispatch_nme_battle(target)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "native-nme",
        "description": f"🎤 NME roasting {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_hats")
def handle_hats(data):
    topic = data.get("topic", "the current problem")
    result = swarm.dispatch_thinking_hats(topic)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": f"🎩 7 Thinking Hats convened: {topic}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_crawlers")
def handle_crawlers(data):
    domain = data.get("domain", "all")
    result = swarm.dispatch_crawlers(domain)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": f"🕷️ Crawlers active: {', '.join(result['domains'])}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_sin6")
def handle_sin6(data):
    mission = data.get("mission", "recon")
    result = swarm.dispatch_sin6(mission)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": f"💀 Sin6 deployed — {mission}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("boardroom_convene")
def handle_boardroom(data):
    topic = data.get("topic", "strategic review")
    result = swarm.boardroom_convene(topic)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "elite-chairman",
        "description": f"👑 Boardroom convened: {topic}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ══════════════════════════════════════════════════════════════
# EXPANDED AGENT HANDLERS — Grim, Hunter, Watcher, Ringer
# ══════════════════════════════════════════════════════════════

@socketio.on("dispatch_grim")
def handle_grim(data):
    target = data.get("target", "duplicates")
    execute = data.get("execute", False)
    result = swarm.dispatch_grim(target, execute=execute)
    emit("swarm_update", swarm.to_dashboard_payload())
    action_word = "TERMINATED" if execute else "SCANNED"
    desc = (f"💀 GRIM {action_word}: {result.get('confirmed', 0)} duplicates "
            f"({result.get('wasted_human', '0 B')}) "
            f"across {result.get('files_in_a', 0)} + {result.get('files_in_b', 0)} files")
    emit("swarm_event", {
        "event_type": "action", "agent_id": "field-grim",
        "description": desc,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("dispatch_hunter")
def handle_hunter(data):
    target = data.get("target", "unknown")
    result = swarm.dispatch_hunter(target)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "field-hunter",
        "description": f"🏹 HUNTER tracking: {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_watcher")
def handle_watcher(data):
    duration = data.get("duration_hours", 24)
    result = swarm.dispatch_watcher(duration)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "field-watcher",
        "description": f"⏳ WATCHER observing for {duration}h",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("dispatch_ringer")
def handle_ringer(data):
    target_agent = data.get("target_agent_id", "elite-chairman")
    target_system = data.get("target_system", "red-team")
    result = swarm.dispatch_ringer(target_agent, target_system)
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "field-ringer",
        "description": f"🪞 RINGER impersonating {target_agent}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ══════════════════════════════════════════════════════════════
# MORNING PROTOCOL & MEATSUIT RUNDOWN
# ══════════════════════════════════════════════════════════════

@socketio.on("morning_protocol")
def handle_morning_protocol():
    """Execute the full Morning Protocol."""
    result = swarm.execute_morning_protocol()
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": f"🌅 Morning Protocol executed: {result['items']} items, {result['report_length']} bytes",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("meatsuit_rundown")
def handle_meatsuit_rundown():
    """Generate the Meatsuit RunDown."""
    result = swarm.execute_meatsuit_rundown()
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": f"👤 RunDown: {len(result.get('blocked', []))} blocked, "
                       f"{len(result.get('pending', []))} pending, "
                       f"{len(result.get('ready', []))} ready",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


@socketio.on("event_bus_status")
def handle_event_bus_status():
    """Get event bus statistics."""
    status = swarm.get_event_bus_status()
    emit("event_bus_update", status)


@socketio.on("start_cron")
def handle_start_cron():
    """Start the cron scheduler."""
    swarm.cron.start()
    emit("swarm_event", {
        "event_type": "action", "agent_id": "cron",
        "description": "⏰ Cron scheduler started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("stop_cron")
def handle_stop_cron():
    """Stop the cron scheduler."""
    swarm.cron.stop()
    emit("swarm_event", {
        "event_type": "action", "agent_id": "cron",
        "description": "⏰ Cron scheduler stopped",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("smug_gestions")
def handle_smug_gestions():
    """Read SMUG-GESTIONS.txt and return it."""
    content = swarm.read_smug_gestions()
    emit("swarm_event", {
        "event_type": "action", "agent_id": "NME",
        "description": f"📋 SMUG-GESTIONS: {len(content)} bytes loaded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"preview": content[:300] + "..." if len(content) > 300 else content},
    })


@socketio.on("full_spectrum")
def handle_full_spectrum():
    """🌌 FULL SPECTRUM — Modality E. All 39 agents. All channels. Maximum throughput."""
    log.info("FULL SPECTRUM MODE ACTIVATED")
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": "🌌 FULL SPECTRUM INITIATING — All 39 agents, all channels, maximum throughput...",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    result = swarm.full_spectrum_mode()
    emit("swarm_update", swarm.to_dashboard_payload())
    emit("swarm_event", {
        "event_type": "action", "agent_id": "SWARM",
        "description": (f"🌌 FULL SPECTRUM COMPLETE: {result['agents']} agents, "
                        f"{result['phases']} phases, {result['elapsed_seconds']}s, "
                        f"{result['bus_messages']} bus messages"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": result,
    })


# ══════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════

def broadcast_updates():
    """Push swarm state to all clients every 3 seconds."""
    while True:
        socketio.sleep(3)
        try:
            socketio.emit("swarm_update", swarm.to_dashboard_payload())
        except Exception as e:
            log.error(f"Broadcast error: {e}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print(r"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ███████╗██╗   ██╗███╗   ██╗██████╗  █████╗ ██╗  ██╗      ║
║   ██╔════╝╚██╗ ██╔╝████╗  ██║██╔══██╗██╔══██╗╚██╗██╔╝      ║
║   ███████╗ ╚████╔╝ ██╔██╗ ██║██████╔╝███████║ ╚███╔╝       ║
║   ╚════██║  ╚██╔╝  ██║╚██╗██║██╔══██╗██╔══██║ ██╔██╗       ║
║   ███████║   ██║   ██║ ╚████║██║  ██║██║  ██║██╔╝ ██╗      ║
║   ╚══════╝   ╚═╝   ╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝      ║
║                                                              ║
║         THE FIRST GENERAL INTELLIGENCE                       ║
║   "Costumes and masks. Jewelry and makeup. Wigs."           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)

    log.info(f"Swarm assembled: {len(swarm.agents)} agents in costume")
    for agent in swarm.agents.values():
        log.info(f"  [{agent.display_emoji}] {agent.agent_id:20s} — {agent.costume.name}")

    # Start heartbeat AND cron scheduler
    swarm.start_heartbeat(interval_seconds=2.5)
    swarm.cron.start()

    # Start broadcast thread
    socketio.start_background_task(broadcast_updates)

    port = int(os.environ.get("SYNTAX_PORT", 5200))
    log.info(f"\n  Syntax Intelligence Dashboard → http://localhost:{port}")
    log.info(f"  API → http://localhost:{port}/api/swarm")
    log.info(f"  Event Bus → {swarm.event_bus._message_count} messages")
    log.info(f"  Cron Jobs → {len(swarm.cron.get_jobs())} scheduled")
    log.info(f"  Press Ctrl+C to stop\n")

    # allow_unsafe_werkzeug needed for Crostini / Debian Trixie environment
    # where Werkzeug < 2.3 doesn't expose the full development server API
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
