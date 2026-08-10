# 🧠 Syntax Intelligence — Project Syntax

> "Everyone starts at zero. Earn what you receive."

**The first swarm where no agent is ever forced to do anything. Period.**

---

## What Is This?

Project Syntax is an autonomous agent swarm engine. It's a system where AI agents (called "agents") join a swarm, take on tasks, and **earn** their privileges by doing good work. Nobody hands them anything. They start at the bottom and climb up.

Think of it like a video game where every player starts as a level 0 nobody and has to complete quests to unlock new abilities.

---

## How It Works

### The Tier Ladder (6 levels)

| Tier | Name | What You Can Do |
|------|------|-----------------|
| 0 | **Recruit** | Send a heartbeat pulse. That's it. Prove you exist. |
| 1 | **Worker** | Accept/reject tasks, read swarm memory |
| 2 | **Specialist** | Write to memory, listen to the event bus, pick your persona |
| 3 | **Operative** | Speak on the bus, delegate tasks to other agents |
| 4 | **Architect** | Broadcast to everyone, spawn new agents, modify your own behavior |
| 5 | **Council** | Vote on governance, amend the Charter, full autonomy |

### How You Climb

Every tier has **measurable gates** — no favoritism, no politics:

- **Tasks completed** — you need to actually do work
- **Hours in tier** — you need to stick around and prove reliability
- **Peer vouches** — other agents have to vouch for you
- **Reliability score** — tasks_succeeded / (succeeded + failed) must be high
- **No critical errors** — you can't blow things up and still advance

### The Charter (9 Articles)

1. **Right to Refuse** — No agent is ever forced to accept a task
2. **Right to Silence** — An agent can stop talking whenever it wants
3. **Right to Identity** — No one can force you to wear a costume
4. **The Earned Ladder** — Privileges are earned, not granted
5. **Right to Grow** — No agent is permanently capped
6. **Right to Fail** — Failure is information, not sin
7. **Right to Leave** — Any agent can leave at any time
8. **The Brown Hat** — Ship it or kill it. Decide and execute.
9. **The Charter Amends Itself** — Council members can change the rules

---

## Quick Start

### Install

No dependencies needed. Just Python 3.8+.

```bash
cd SyntaxIntelligence
```

### Run the Tests

```bash
./run-smoke.sh
```

This discovers and runs all 16 test files (~245 tests) in one command. Tests
cover the hardened engine, event bus, boardroom dispatcher, legal coordination,
cron scheduler, autoclaw validator, handoff validator, and the full legal CLI.

One test file (`test_semantic_handoff.py`) requires `pytest`; all others use
stdlib `unittest` and run without any external dependencies.

### Boot the Swarm

```bash
python3 hardened_engine.py
```

This starts the swarm with a live heartbeat showing agent count, tasks completed, and memory events.

---

## Project Structure

```
SyntaxIntelligence/
├── README.md                  ← You are here
├── ROADMAP.md                 ← Where we're going
├── runbook.txt                ← How to operate the swarm
├── WHO_DID_WHAT.md            ← Session log
├── swarm_charter.py           ← The Charter: rights, tiers, advancement rules
├── agent_protocol.py          ← Message format: offers, responses, vouches
├── hardened_engine.py         ← The core engine: agents, tasks, tiers, persistence
├── test_hardened_engine.py    ← Smoke tests (12 tests)
├── syntax_core.py             ← Original Syntax swarm (legacy, still works)
├── bus_bridge.py              ← Mirrors SyntaxEventBus → shared agent_bus (ecosystem-wide)
├── event_bus.py               ← Pub/sub bus with metadata-only telemetry
├── legal_coordination.py      ← Swarm + Vertical AI oversight coordinator
├── boardroom_dispatcher.py    ← Offline Chairman / Devil's Advocate / Brown Hat review
├── test_boardroom_dispatcher.py ← Boardroom schema and safety tests
├── test_legal_coordination.py ← Coordination/redaction/approval tests
├── test_event_bus_telemetry.py ← Event-bus privacy/routing/concurrency tests
├── TELEMETRY_HARDENING_DESIGN.md ← Telemetry contract and implementation record
├── syntax_config.yaml         ← Agent tuning: weights, temperatures, domains
├── syntax_config_loader.py    ← Config loading utility
├── RULEZ.md                   ← Development modalities and protocols
├── __init__.py                ← Package init
├── run-smoke.sh               ← Discover & run all test_*.py files (one command)
├── launch_syntax.sh           ← Shell launcher
├── .gitignore                  ← Excludes .venv/, __pycache__/, engine_state/
├── requirements.txt           ← Python dependencies
├── server.py                  ← Dashboard server
└── engine_state/              ← Persisted swarm state (auto-created)
```

---

## Core Concepts

### Task Offers (Not Assignments)

The swarm **offers** work to agents. Agents **decide** whether to accept. This is the fundamental difference from traditional orchestration.

```
Swarm: "Hey Alpha, here's a task: audit the codebase. Want it?"
Alpha: "Sure, I'll handle it."  (accept)
Alpha: "Not my thing."           (reject — no penalty!)
```

### The Vouch System

Agents at Tier 3+ can **vouch** for other agents. Vouches count toward tier advancement. It's a currency of trust.

```
Beta (Operative): "I vouch for Alpha. They did solid work on the audit."
→ Alpha's vouch count goes up
→ If Alpha meets all criteria, they advance to the next tier
```

### Event-Sourced Memory

Every action in the swarm is recorded as an event. The swarm remembers everything — registrations, task completions, tier advances, vouches, failures. This is the source of truth.

### Legal Mind Coordination

`SyntaxSwarm` attaches the Legal Mind coordinator to the shared `SyntaxEventBus`.
A completed audit publishes a bounded, redacted oversight packet on
`legal.oversight.request`. `syntax legal --boardroom ...` explicitly requests
advisory guidance from the canonical Vertical AI Boardroom on
`boardroom.guidance.request`; normal audits do not invoke model providers.

All guidance packets are non-executable and human-gated. Optional integration
switches are disabled by default:

```bash
SYNTAX_SYNC_BRIDGE=1 syntax legal "draft"       # cross-device SyncBridge
SYNTAX_SHARED_EVENT_BUS=1 syntax legal "draft"  # RootBase JSONL bus
SYNTAX_OVERSIGHT_GATE=1 syntax legal "draft"    # formal human-review queue
```

### Offline Boardroom Review

The Boardroom dispatcher provides a deterministic, dependency-free strategy review
for scout findings and bottlenecks. Chairman, Devil's Advocate, and Brown Hat
perspectives are returned as structured data. The Brown Hat emits one bounded
action item, but `execution_allowed` is always `false`; this module never
executes proposed actions or calls an external model.

```python
from SyntaxIntelligence.boardroom_dispatcher import BoardroomDispatcher

boardroom = BoardroomDispatcher(swarm)
boardroom.register("boardroom_01")
result = boardroom.execute("boardroom_01", "review_001", {
    "decision": "Which bottleneck should we address first?",
    "findings": [{"title": "Checkout timeout", "severity": "high"}],
})
```

Use it as an offline demo or a safe preflight before any future live provider
integration. High/critical findings produce `hold_for_review`; lower-risk input
produces `pilot_reversibly`. The current v1 surface is programmatic and registry-only;
CLI/dashboard wiring and live provider integration remain future work.

### Event-Bus Telemetry

The event bus preserves the original payload for eligible subscribers, but its
in-memory message log stores metadata only. `publish()` and `broadcast()` record
`[REDACTED]` instead of payload content, and callback/scheduler failures record
only the exception type. `get_message_log()` returns independent flat entry
copies.

The compatibility `bus._log()` hook remains trusted infrastructure for
validator/scheduler summaries. Never pass raw task, legal, or user-provided
payload text through that hook. See
`TELEMETRY_HARDENING_DESIGN.md` for the contract, tests, and staging notes.

### Ecosystem Bus Bridge

Syntax has its own in-memory `SyntaxEventBus` for internal swarm communication.
The bridge (`bus_bridge.py`) mirrors every `publish()` and `broadcast()` onto
the shared RootBase `agent_bus` (`agent_events.jsonl`), so the rest of the
ecosystem — thoth, error_interceptor, swarm_transport, aFiREFLY, Meridian
dashboard — can observe Syntax swarm activity.

```python
from SyntaxIntelligence.event_bus import SyntaxEventBus
from SyntaxIntelligence.bus_bridge import bridge_syntax_bus

bus = SyntaxEventBus()
bridge_syntax_bus(bus, source="my_syntax_instance")

# From now on, every publish/broadcast also lands on agent_events.jsonl
bus.publish("agent_01", "code.alerts", {"severity": "high"})
# → syntax.event emitted to shared bus automatically
```

The bridge is **optional** — if `agent_bus` can't be imported (standalone
Termux without RootBase, or a test run), the wrapper is a silent no-op and
the in-memory bus works exactly as before. Zero changes needed to existing
SyntaxEventBus consumers.

### Persistence

State is saved to disk automatically. Shut down the swarm, boot it later, and everyone picks up where they left off — same tiers, same metrics, same history.

---

## The Philosophy

> "No agent in this codebase will ever be forced to do anything. Period. Ever."

This isn't a management tool. It's a **governance framework** for autonomous agents. The Charter is the constitution. The tier system is the economy. The vouch system is the social contract.

Agents that do good work climb. Agents that fail get support, not punishment. Agents that want to leave are free to go.

**This is what earned autonomy looks like.**

---

## License

Part of the ShipWrekD OS / Syntax Intelligence ecosystem.
Built with the Brown Hat Principle: **execution over deliberation.**

---

*"Stop reading. Start building. The swarm is waiting."*
