#!/usr/bin/env python3
"""
SYNTAX INTELLIGENCE — DASHBOARD API
Flask REST API for the Hardened Swarm Engine.
Provides endpoints for agent management, task lifecycle, vouching,
tier progress, and swarm state.

Usage:
    python3 dashboard.py                   # Default port 5200
    python3 dashboard.py --port 8080       # Custom port
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request, send_from_directory, redirect, url_for
from pathlib import Path
from SyntaxIntelligence.hardened_engine import HardenedSwarm
from SyntaxIntelligence.swarm_charter import AgentTier

log = logging.getLogger("syntax.dashboard")

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR))
swarm: HardenedSwarm = None
_dispatchers = None


def get_swarm() -> HardenedSwarm:
    """Get or create the global swarm instance."""
    global swarm
    if swarm is None:
        swarm = HardenedSwarm()
        swarm.load_state()
    return swarm


def get_dispatchers():
    """Get or create the dispatcher registry."""
    global _dispatchers
    if _dispatchers is None:
        try:
            from SyntaxIntelligence.dispatchers import create_default_dispatchers
            _dispatchers = create_default_dispatchers(get_swarm())
        except Exception as e:
            log.warning(f"Failed to initialize dispatchers: {e}")
    return _dispatchers


@app.after_request
def add_cors(response):
    """Add CORS headers for browser clients."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ═══════════════════════════════════════════════════════════════
# SWARM ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/swarm", methods=["GET"])
def get_swarm_state():
    """Get full swarm state."""
    return jsonify(get_swarm().get_swarm_state())


@app.route("/api/swarm/start", methods=["POST"])
def start_swarm():
    """Start the swarm (cron jobs, heartbeat)."""
    s = get_swarm()
    s.start()
    return jsonify({"status": "started", "session_id": s.session_id})


@app.route("/api/swarm/stop", methods=["POST"])
def stop_swarm():
    """Stop the swarm and save state."""
    s = get_swarm()
    s.stop()
    return jsonify({"status": "stopped", "session_id": s.session_id})


@app.route("/api/swarm/save", methods=["POST"])
def save_swarm():
    """Manually save swarm state."""
    s = get_swarm()
    s.save_state()
    return jsonify({"status": "saved", "session_id": s.session_id})


@app.route("/api/swarm/morning-protocol", methods=["POST"])
def morning_protocol():
    """Execute the Morning Protocol."""
    report = get_swarm().execute_morning_protocol()
    return jsonify(report)


@app.route("/api/swarm/cron", methods=["GET"])
def cron_status():
    """Get cron scheduler status."""
    return jsonify(get_swarm().get_cron_status())


# ═══════════════════════════════════════════════════════════════
# AGENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/agents", methods=["GET"])
def list_agents():
    """List all agents, optionally filtered by tier."""
    tier = request.args.get("tier")
    tier_enum = None
    if tier:
        try:
            tier_enum = AgentTier[tier.upper()]
        except KeyError:
            return jsonify({"error": f"Invalid tier: {tier}"}), 400
    return jsonify(get_swarm().list_agents(tier_enum))


@app.route("/api/agents", methods=["POST"])
def register_agent():
    """Register a new agent."""
    data = request.get_json()
    if not data or "agent_id" not in data or "name" not in data:
        return jsonify({"error": "agent_id and name required"}), 400

    s = get_swarm()
    agent = s.register_agent(
        data["agent_id"],
        data["name"],
        capabilities=data.get("capabilities", []),
    )
    return jsonify(agent.to_dict()), 201


@app.route("/api/agents/<agent_id>", methods=["GET"])
def get_agent(agent_id):
    """Get a specific agent."""
    agent = get_swarm().get_agent(agent_id)
    if not agent:
        return jsonify({"error": f"Agent '{agent_id}' not found"}), 404
    return jsonify(agent.to_dict())


@app.route("/api/agents/<agent_id>", methods=["DELETE"])
def delete_agent(agent_id):
    """Unregister an agent."""
    s = get_swarm()
    success = s.unregister_agent(agent_id)
    if not success:
        return jsonify({"error": f"Agent '{agent_id}' not found"}), 404
    return jsonify({"status": "removed", "agent_id": agent_id})


@app.route("/api/agents/<agent_id>/progress", methods=["GET"])
def agent_progress(agent_id):
    """Get tier advancement progress for an agent."""
    progress = get_swarm().get_agent_progress(agent_id)
    if "error" in progress:
        return jsonify(progress), 404
    return jsonify(progress)


# ═══════════════════════════════════════════════════════════════
# TASK ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/tasks", methods=["POST"])
def offer_task():
    """Offer a task to the swarm."""
    data = request.get_json()
    if not data or "title" not in data:
        return jsonify({"error": "title required"}), 400

    s = get_swarm()
    result = s.offer_task(
        title=data["title"],
        description=data.get("description", ""),
        capabilities=data.get("capabilities"),
        priority=data.get("priority", 0),
        min_tier=data.get("min_tier", 0),
        target_agent=data.get("target_agent"),
    )
    return jsonify(result), 201


@app.route("/api/tasks/<task_id>/respond", methods=["POST"])
def respond_to_task(task_id):
    """Agent responds to a task offer."""
    data = request.get_json()
    if not data or "agent_id" not in data or "decision" not in data:
        return jsonify({"error": "agent_id and decision required"}), 400

    result = get_swarm().respond_to_task(
        data["agent_id"],
        task_id,
        data["decision"],
        reason=data.get("reason", ""),
        delegate_to=data.get("delegate_to"),
        requested_info=data.get("requested_info"),
    )
    status_code = 200 if result.get("status") != "error" else 400
    return jsonify(result), status_code


@app.route("/api/tasks/<task_id>/complete", methods=["POST"])
def complete_task(task_id):
    """Complete a task."""
    data = request.get_json()
    if not data or "agent_id" not in data:
        return jsonify({"error": "agent_id required"}), 400

    result = get_swarm().complete_task(
        task_id,
        data["agent_id"],
        result=data.get("result"),
    )
    return jsonify(result)


@app.route("/api/tasks/<task_id>/fail", methods=["POST"])
def fail_task(task_id):
    """Fail a task."""
    data = request.get_json()
    if not data or "agent_id" not in data:
        return jsonify({"error": "agent_id required"}), 400

    result = get_swarm().fail_task(
        task_id,
        data["agent_id"],
        reason=data.get("reason", ""),
        critical=data.get("critical", False),
    )
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# VOUCH ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.route("/api/vouch", methods=["POST"])
def vouch():
    """One agent vouches for another."""
    data = request.get_json()
    if not data or "voucher_id" not in data or "vouched_id" not in data:
        return jsonify({"error": "voucher_id and vouched_id required"}), 400

    result = get_swarm().vouch_for(
        data["voucher_id"],
        data["vouched_id"],
        reason=data.get("reason", ""),
        strength=data.get("strength", 1.0),
    )
    status_code = 200 if result.get("status") != "denied" else 403
    return jsonify(result), status_code


# ═══════════════════════════════════════════════════════════════
# EVENT & MEMORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/events", methods=["GET"])
def list_events():
    """Get recent swarm events."""
    n = request.args.get("limit", 50, type=int)
    return jsonify(get_swarm().memory.recent(n))


@app.route("/api/events/<agent_id>", methods=["GET"])
def agent_events(agent_id):
    """Get events for a specific agent."""
    n = request.args.get("limit", 20, type=int)
    return jsonify(get_swarm().memory.by_agent(agent_id, n))


@app.route("/api/memory/stats", methods=["GET"])
def memory_stats():
    """Get memory statistics."""
    return jsonify(get_swarm().memory.stats())


@app.route("/api/event-bus/stats", methods=["GET"])
def event_bus_stats():
    """Get event bus statistics."""
    return jsonify(get_swarm().event_bus.get_stats())


@app.route("/api/event-bus/log", methods=["GET"])
def event_bus_log():
    """Get recent event bus messages."""
    n = request.args.get("limit", 50, type=int)
    return jsonify(get_swarm().event_bus.get_message_log(n))


# ═══════════════════════════════════════════════════════════════
# DISPATCHER ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/dispatchers", methods=["GET"])
def list_dispatchers():
    """List all registered dispatchers."""
    registry = get_dispatchers()
    if not registry:
        return jsonify([])
    return jsonify(registry.list_dispatchers())


@app.route("/api/dispatchers/<dispatcher_type>/execute", methods=["POST"])
def execute_dispatcher(dispatcher_type):
    """Execute a dispatcher on a task."""
    data = request.get_json()
    if not data or "task_id" not in data or "task_data" not in data:
        return jsonify({"error": "task_id and task_data required"}), 400

    registry = get_dispatchers()
    if not registry:
        return jsonify({"error": "Dispatchers not initialized"}), 503

    # Find agent for this dispatcher type
    agent_id = None
    for d in registry.list_dispatchers():
        if d["type"] == dispatcher_type:
            agent_id = d["agent_id"]
            break

    if not agent_id:
        return jsonify({"error": f"Dispatcher '{dispatcher_type}' not found"}), 404

    result = registry.execute_task(agent_id, data["task_id"], data["task_data"])
    if result:
        return jsonify(result.to_dict())
    return jsonify({"error": "Execution failed"}), 500


# ═══════════════════════════════════════════════════════════════
# CHARTER ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.route("/api/charter", methods=["GET"])
def get_charter():
    """Get the Swarm Charter."""
    return jsonify(get_swarm().charter.to_dict())


# ═══════════════════════════════════════════════════════════════
# FRONTEND
# ═══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def serve_dashboard():
    """Serve the dashboard frontend."""
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/dashboard", methods=["GET"])
def serve_dashboard_alias():
    """Alias for the dashboard."""
    return redirect(url_for("serve_dashboard"))


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Syntax Dashboard API")
    parser.add_argument("--port", type=int, default=5200, help="Port (default 5200)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default 0.0.0.0)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Initialize swarm
    s = get_swarm()
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          SYNTAX DASHBOARD API — PROJECT SYNTAX              ║
║  "Everyone starts at zero. Earn what you receive."          ║
╚══════════════════════════════════════════════════════════════╝

  Session:  {s.session_id}
  Agents:   {len(s.agents)}
  Charter:  v{s.charter.version}

  API Endpoints:
    GET  /api/swarm              — Full swarm state
    POST /api/swarm/start        — Start cron jobs
    POST /api/swarm/stop         — Stop and save
    POST /api/swarm/save         — Manual save
    POST /api/swarm/morning-protocol — Run morning protocol
    GET  /api/swarm/cron         — Cron status

    GET  /api/agents             — List agents
    POST /api/agents             — Register agent
    GET  /api/agents/<id>        — Get agent
    DEL  /api/agents/<id>        — Remove agent
    GET  /api/agents/<id>/progress — Tier progress

    POST /api/tasks              — Offer task
    POST /api/tasks/<id>/respond — Agent responds
    POST /api/tasks/<id>/complete — Complete task
    POST /api/tasks/<id>/fail    — Fail task

    POST /api/vouch              — Vouch for agent

    GET  /api/events             — Recent events
    GET  /api/events/<agent_id>  — Agent events
    GET  /api/memory/stats       — Memory stats
    GET  /api/event-bus/stats    — Event bus stats
    GET  /api/event-bus/log      — Event bus log
    GET  /api/charter            — The Charter

  Running on: http://{args.host}:{args.port}
""")

    app.run(host=args.host, port=args.port, debug=False)
