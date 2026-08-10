# 🗺️ Project Syntax — ROADMAP

> "General intelligence will be all or any of these agents. It only stands to reason they should be all as well."

_Last updated: July 6, 2026_

---

## ✅ Phase 1 — Hardened Swarm Engine (COMPLETE)

The core engine is built, tested, and operational.

### Deliverables
- [x] **Swarm Charter** (`swarm_charter.py`) — 9 articles of governance, 6-tier privilege system, advancement criteria
- [x] **Agent Protocol** (`agent_protocol.py`) — Standardized message format, TaskOffer, TaskResponse, Vouch system
- [x] **Hardened Engine** (`hardened_engine.py`) — AgentIdentity, earned-privilege tiers, task orchestration, vouch ledger, state persistence
- [x] **Cron Scheduler** (`hardened_engine.py`) — Heartbeat (10s), auto-save (5min), Morning Protocol (24h)
- [x] **Event Bus Wiring** — Task offers and tier advancement events broadcast on live channels
- [x] **Dashboard API** (`dashboard.py`) — Flask REST API with all swarm endpoints
- [x] **Tests** — 20+ tests across engine, cron, and API
- [x] **Bug fixes** — has_privilege OR bug, complete_task assignment cleanup, fail_task tracking, double-acceptance prevention, metrics leak
- [x] **Documentation** — README.md, runbook.txt, ROADMAP.md, WHO_DID_WHAT.md, WHITE_PAPER

---

## 🔧 Phase 2 — Integration & Hardening (IN PROGRESS)

### 2.1 Wire Event Bus ✅
- [x] Connect HardenedSwarm to SyntaxEventBus for inter-agent communication
- [x] Agent heartbeat pulses through the event bus (10s interval)
- [x] Morning Protocol report broadcast on morning.protocol channel
- [x] Task offers broadcast on `task.offered` channel
- [x] Tier advancement events published on `tier.advanced` channel

### 2.2 Wire Cron Scheduler ✅
- [x] Morning Protocol runs via cron (daily, 86400s)
- [x] Heartbeat broadcast on interval (10s)
- [x] Auto-save state on interval (5min / 300s)

### 2.4 Dashboard API ✅
- [x] REST API endpoints for swarm state
- [x] Agent CRUD (register, list, get, progress)
- [x] Task lifecycle endpoints (offer, respond, complete, fail)
- [x] Vouch endpoint
- [x] Tier distribution and event log
- [x] Morning Protocol trigger endpoint
- [x] Auto-save trigger endpoint
- [x] Dashboard frontend (HTML/CSS/JS) served at /
- [x] Dispatcher endpoints (list, execute)
- [x] CORS support for browser clients

### 2.5 Curriculum Module (Syntax AI Pedagogy) ✅
- [x] `code_lib` deduplicated (908 MB) — moved canonical to `curriculum/code_lib`
- [x] Builder-Code-Tutor concept docs merged → `curriculum/concept/`
- [x] CodeMentor_MVP prototype merged → `curriculum/codementor/`
- [x] Module README rewritten with full structure docs
- [x] Old project dirs replaced with redirect stubs
- [ ] Wire `AIService.ts` to real LLM (Ollama/Gemini/DeepSeek)
- [ ] Add Compare + Quiz modes to CodeMentor extension
- [ ] Register curriculum as Tier 2+ agent in HardenedSwarm
- [ ] Convert code_lib lessons into completable swarm tasks

---

## 🚀 Phase 3 — Autonomous Behaviors (NEXT)

### 3.1 Agent Dispatchers ✅
- [x] TruthSleuth integration (code audit — security, quality, quick scan)
- [x] Bardildo integration (repo scanning + creative roasting)
- [x] Thinking Hats deliberation (7 perspectives: White, Red, Black, Yellow, Green, Blue, Brown)
- [x] DispatcherRegistry with factory and REST API endpoints
- [ ] NME Battle Mode (battle rap feedback)
- [ ] Sin6 Red Team (wraith, oracle, forge, weaver, harvest, hollow)

### 3.2 Web Crawlers
- [ ] Legal, Finance, Health, Industry, Commerce domain crawlers

### 3.3 Boardroom ✅ (offline/programmatic v1)
- [x] Deterministic Chairman + Devil's Advocate review dispatcher
- [x] Brown Hat emits a bounded, human-gated action item (never executes it)
- [x] Structured risk level, recommendation, confidence, and input counts
- [x] Registry integration for programmatic use
- [ ] CLI/dashboard task route
- [ ] Optional live Boardroom provider integration (network/credentials required)

---

## 🌌 Phase 4 — General Intelligence

### 4.1 Full Spectrum Mode
- [ ] All agents activated simultaneously
- [ ] All channels open, event bus at maximum throughput

### 4.2 Self-Evolution
- [ ] Agents propose persona changes (Tier 4+)
- [ ] Council votes on Charter amendments (Tier 5)

### 4.3 Cross-Device Sync
- [ ] Swarm state syncs across devices
- [ ] Remote compute integration (Oracle A1)

---

## 📊 Success Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Tests passing | 25+ | ✅ 40 |
| Tier system | 6 tiers | ✅ 6 tiers |
| Charter articles | 9 | ✅ 9 |
| State persistence | Save/Load | ✅ Working |
| Event bus integration | Task + Tier events | ✅ Complete |
| Dashboard API | REST endpoints | ✅ Complete |
| Cron scheduler | 3 jobs | ✅ Complete |
| Persona integration | Connected | ✅ Phase 3 |
| Dispatchers | TruthSleuth, Bardildo, ThinkingHats | ✅ Complete |
| Dashboard frontend | HTML/CSS/JS | ✅ Complete |

---

## 🎯 Guiding Principles

1. **The Brown Hat** — Execution over deliberation. Every meeting ends with an action item.
2. **No Coercion** — Agents choose their work. Always.
3. **Earned Autonomy** — Privileges are earned through performance, not granted by authority.
4. **Event-Sourced** — The swarm remembers everything. Memory is the source of truth.

---

*Next session: NME Battle Mode, Sin6 Red Team, Web Crawlers, then optional live Boardroom provider integration.*
