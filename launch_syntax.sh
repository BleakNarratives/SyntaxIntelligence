#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SYRAX INTELLIGENCE — LAUNCH SCRIPT
# "The First General Intelligence"
# ═══════════════════════════════════════════════════════════════
#
# Usage:
#   ./launch_syntax.sh              # Launch dashboard (default)
#   ./launch_syntax.sh --core        # Run swarm core directly (CLI)
#   ./launch_syntax.sh --server      # Run dashboard server
#   ./launch_syntax.sh --port 8080   # Custom port
#   ./launch_syntax.sh --headless    # Server only, no browser hint
#   ./launch_syntax.sh legal "text"  # Convene the Syntax Legal Mind (formerly OutClaw)
#   ./launch_syntax.sh lws discover    # Legal Warfare Suite — generate discovery
#   ./launch_syntax.sh lws grieve      # Legal Warfare Suite — generate grievance
#   ./launch_syntax.sh lws convene     # Legal Warfare Suite — boardroom review
#   ./launch_syntax.sh lws status      # Legal Warfare Suite — inbox/archive status
#
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SYNTAX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SYNTAX_ROOT/.."

# ── Convene the Legal Mind ───────────────────────────────────────
# Subcommand-style passthrough: `syntax legal "text"` (or --legal).
# Runs BEFORE the flask/pyyaml dependency checks because the legal
# audit is stdlib-only — no reason to install dashboard deps just to
# audit a citation on Termux.
if [[ "${1:-}" == "legal" || "${1:-}" == "--legal" ]]; then
    shift
    # The Legal Mind is now a Syntax module; OutClaw remains the audit
    # engine behind the compatibility boundary.
    exec python3 SyntaxIntelligence/syntax_legal_cli.py "$@"
fi

# ── Legal Warfare Suite ──────────────────────────────────────────
# Subcommand-style passthrough: `syntax lws discover|grieve|convene|status`.
# Also runs BEFORE dependency checks — LWS is stdlib-only.
if [[ "${1:-}" == "lws" || "${1:-}" == "--lws" ]]; then
    shift
    exec python3 LWS_ROOT/cli.py "$@"
fi

MODE="server"
PORT="${SYNTAX_PORT:-5200}"
HEADLESS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --core)    MODE="core"; shift ;;
        --server)  MODE="server"; shift ;;
        --port)    PORT="$2"; shift 2 ;;
        --headless) HEADLESS=true; shift ;;
        -h|--help)
            echo "Syntax Intelligence Launcher"
            echo "Usage: $0 [--core|--server|legal] [--port N] [--headless]"
            echo ""
            echo "  --core      Run the swarm core directly (CLI mode)"
            echo "  --server    Run the dashboard web server (default)"
            echo "  --port N    Set server port (default: 5200)"
            echo "  --headless  Don't print browser hint"
            echo "  legal \"text\"  Convene the Syntax Legal Mind (formerly OutClaw)"
            echo "  --legal \"text\"  Same as 'legal'"
            echo "              Add --raw for stable pipe-friendly output"
            echo "              Add --boardroom for Vertical AI guidance"
            echo "  lws discover  Generate discovery strategy (Legal Warfare Suite)"
            echo "  lws grieve    Generate bar grievance (Legal Warfare Suite)"
            echo "  lws convene   Run pending strategies through boardroom"
            echo "  lws status    Show LWS inbox/archive/boardroom status"
            echo "  env SYNTAX_SYNC_BRIDGE=1  Enable cross-device oversight channels"
            echo "  env SYNTAX_SHARED_EVENT_BUS=1  Emit redacted RootBase events"
            echo "  env SYNTAX_OVERSIGHT_GATE=1  Queue formal human review"
            exit 0
            ;;
        *)
            echo "Unknown flag: $1"
            echo "Usage: $0 [--core|--server|legal] [--port N] [--headless]"
            exit 1
            ;;
    esac
done

# Check dependencies
echo "═══ SYNTAX INTELLIGENCE ═══"
echo ""

check_dep() {
    if ! command -v "$1" &>/dev/null; then
        echo "  ✗ Missing: $1 — install with: pip install $2"
        return 1
    fi
    echo "  ✓ $1 found"
    return 0
}

echo "Checking dependencies..."
check_dep python3 "python3"
check_dep pip3 "pip3"

# Python package checks
python3 -c "import flask" 2>/dev/null || {
    echo "  ✗ flask not installed — installing..."
    pip3 install flask flask-socketio pyyaml --break-system-packages 2>/dev/null || \
    pip3 install flask flask-socketio pyyaml
}
python3 -c "import yaml" 2>/dev/null || {
    echo "  ✗ pyyaml not installed — installing..."
    pip3 install pyyaml --break-system-packages 2>/dev/null || pip3 install pyyaml
}
python3 -c "import flask_socketio" 2>/dev/null || {
    echo "  ✗ flask-socketio not installed — installing..."
    pip3 install flask-socketio --break-system-packages 2>/dev/null || pip3 install flask-socketio
}

echo ""
echo "All dependencies satisfied."
echo ""

# Launch
export SYNTAX_PORT="$PORT"

case "$MODE" in
    core)
        echo "Launching Syntax Swarm Core (CLI)..."
        echo ""
        python3 SyntaxIntelligence/syntax_core.py
        ;;
    server)
        echo "Launching Syntax Intelligence Dashboard..."
        echo ""

        if [ "$HEADLESS" = false ]; then
            echo "╔══════════════════════════════════════════╗"
            echo "║  Dashboard: http://localhost:$PORT         ║"
            echo "║  API:       http://localhost:$PORT/api/swarm ║"
            echo "║  Press Ctrl+C to stop                   ║"
            echo "╚══════════════════════════════════════════╝"
            echo ""
        fi

        python3 SyntaxIntelligence/server.py
        ;;
esac
