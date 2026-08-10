# WHO_DID_WHAT.md — Syntax-Intelligence Folder

> "Hall of the DEVine — Syntax-Intelligence Wing"

This file logs every session that has touched code under `bleaknarratives/Syntax-Intelligence/`. It mirrors the conventions from the root `WHO_DID_WHAT.md`; cross-cutting context (project-wide state) lives there. Folder-scoped detail lives here.

---

## Session Coverage

| Session | Agent | Files Touched (folder) | Outcome |
|---|---|---|---|
| Aug 5, 2026 | Buffy (DeepSeek V4 Pro) | `bus_bridge.py` (NEW), `.gitignore` (NEW), `run-smoke.sh` (NEW), `README.md` (updated) | Ecosystem bus bridge: SyntaxEventBus → shared agent_bus. Smoke harness: 245 tests in one command. Docs updated. |
| July 27, 2026 | Buffy (DeepSeek V4 Pro) | `whorl/runtime/shipwrekd_os.py` (NEW), `whorl/runtime/behaviors.py` (NEW), `whorl/runtime/shipwrek_manifest_full.json` (NEW), `whorl/runtime/shipwrek_manifest_small.json` (NEW) | ShipWrekDOS: full constellation boot — 21 agents across 7 persona groups, all ticking together. Persona-specific behaviors (34 costume→strategy mappings). JSON manifest-driven. SyntaxIntelligence 12/12 tests pass. |
| July 27, 2026 | Buffy (DeepSeek V4 Pro) | `curriculum/` — merged Builder-Code-Tutor + CodeMentor_MVP into Syntax AI pedagogy subsystem. Moved `codementor/src/`, `codementor/docs/`, `concept/builder_coder_tutor.md`. Rewrote `curriculum/README.md`. Updated `ROADMAP.md` (2.5 Curriculum Module). Redirect stubs for old project dirs. | Syntax AI curriculum unified: concept + prototype + library under one module. |
| July 21, 2026 (later) | Buffy (Agent M / Codebuff) | `handoff_validator.py` (predicate fix), `autoclaw_validator.py` (ResourceMonitor), `bus_validator.py` (long-lived wire-in), `swarm_charter.py` (Article X) | Vouch predicate cross-field sig bug fixed; long-lived per-proxy resource monitor shipped as opt-in; AN-08 trust-carryover policy closed (RESET-per-scope-expansion). 140/144 tests passing, 4 pre-existing failures unchanged. |
| July 21, 2026 | Buffy (Agent M / Codebuff) | `autoclaw_validator.py` (NEW), `crash_log.py` (NEW), `test_autoclaw_validator.py` (NEW), `test_crash_log.py` (NEW) | Spec §2 hard/testable layer + crash-log insurance. AN-12 OOM closed. 37/37 tests passing. |
| July 20, 2026 | Buffy (Agent M / Codebuff) | `handoff_validator.py` (extended), `bus_validator.py` (NEW), `test_handoff_validator.py` (extended), `test_bus_validator.py` (NEW) | Validator-Bus Welding: Open A + AN-09 minimum-viable closed. 69/69 tests passing. |

---

## Session: Aug 5, 2026 — Ecosystem Bus Bridge + Smoke Harness

### What Got Built

| File | Action | What It Does |
|---|---|---|
| `SyntaxIntelligence/bus_bridge.py` | NEW | Mirrors every SyntaxEventBus publish/broadcast onto the shared RootBase agent_bus (agent_events.jsonl). Optional — silent no-op if agent_bus unreachable. Double-bridging guard. Safe payload summaries (max 6 keys). |
| `SyntaxIntelligence/.gitignore` | NEW | Excludes .venv/ (13M), __pycache__/, *.pyc, engine_state/ |
| `SyntaxIntelligence/run-smoke.sh` | NEW | Discovers all 16 test_*.py files, sets PYTHONPATH for package imports, runs each via unittest. One command: ./run-smoke.sh |
| `SyntaxIntelligence/README.md` | UPDATED | Added Ecosystem Bus Bridge section with usage example. Updated test instructions (./run-smoke.sh). Added bus_bridge, run-smoke.sh, .gitignore to project tree. |

### Decisions Made
- **Bridge = optional adapter, not a bus replacement.** SyntaxEventBus internals untouched. bridge_syntax_bus() monkeypatches .publish/.broadcast on the instance only.
- **Double-bridging guard:** `getattr(bus, "_bridged", False)` check prevents stacked wrappers on re-call.
- **Safe summaries:** payloads truncated to 6 keys, complex values typed ("[N items]", "{N keys}", TypeName). No raw data leaks onto shared bus.
- **PYTHONPATH in smoke.sh:** `${PARENT_DIR}:${PYTHONPATH:-}` — safe under `set -u`.
- Did NOT touch: any SyntaxEventBus consumer, swarm internals, charter, tier system.

### Status at End of Session
- Bridge: ✅ verified — syntax.event lands on agent_events.jsonl
- Smoke: 245 tests, 1 pre-existing error (pytest), 19 skipped, all others pass
- Bus before: 31 lines → Bus after: +1 syntax.event (verified)

### Code-Review Loop
- Reviewer (deepseek) found 2 issues:
  1. Missing double-bridging guard — added `_bridged` check at top of `bridge_syntax_bus()`
  2. `launch_syntax.sh` dropped from README file tree — restored

---

## Session: July 20, 2026 — Validator-Bus Welding (Protocol Layer)

### What Got Built

| File | Action | What It Does |
|---|---|---|
| `Syntax-Intelligence/handoff_validator.py` | EXTENDED | Added `TASK_OFFER_PAYLOAD_CONTRACT` + `TASK_RESPONSE_PAYLOAD_CONTRACT` (Open A first-handoff-type); `_PAYLOAD_CONTRACTS_BY_MSG_TYPE` router (5 entries); `_coerce_message_type()` helper dict/dataclass compat; composed `validate_swarm_message()` to run envelope + payload in sequence. |
| `Syntax-Intelligence/test_handoff_validator.py` | EXTENDED | 13 new tests: `TestTaskOfferPayloadContract` (6 incl. advisory-warning regression), `TestTaskResponsePayloadContract` (5), `TestRoutedValidation` (7 — composed envelope+payload, dict-input regression, pulse/envelope-routing). 25 new tests in this file total. |
| `Syntax-Intelligence/bus_validator.py` | NEW | `ValidatingBusProxy(bus, mode='block_on_error'|'log_only')` — gates `publish`/`broadcast` on SwarmMessage-shaped envelopes; non-envelope data passes through untouched; `validate_bus_event_line()` for JSONL replay. |
| `Syntax-Intelligence/test_bus_validator.py` | NEW | 25 tests: envelope detection, valid/invalid blocking, log-only advisory delivery, non-envelope pass-through, broadcast gate, JSONL replay, stats, mode validation, defensive-copy semantics. |

### Decisions Made
- **First handoff type to spec per Open A:** TaskOffer → TaskResponse.
- **Wire-in shape:** `ValidatingBusProxy` duck-types any bus-like object. `SyntaxEventBus` left untouched. hardened_engine.py not modified — proxy is opt-in.
- **Modes**: `block_on_error` (default; gates per spec §2 "before downstream action") + `log_only` (advisory injection via `data["validation"]` field).
- **Caller-side defensive copy**: proxy copies `data` before injecting the validation findings dict, so the caller's dict is not mutated.
- Did NOT touch: TruthSleuth dispatcher, `swarm_charter.py` (open B), tier system, hub/state/event_log.jsonl historicals.

### Status at End of Session
- Gap-report §3 next-gap: ✓
- Open A (spec): ✓ minimum-viable
- AN-09 (anomalies.md): ✓ FIXED (minimum-viable)
- **69/69 tests passing** in the wire-up subset.

---

## Session: July 21, 2026 — OOM Forensics + Spec §2 Hard Layer + Crash-Log Insurance

### What Got Built

| File | Action | What It Does |
|---|---|---|
| `Syntax-Intelligence/autoclaw_validator.py` | NEW | Spec §2 hard/testable layer. `ResourceCeiling` dataclass (RSS, swap, time, token, sandbox). `@autoclaw_protect` decorator with HALT/TRACE/OFF modes. Default `kill_on_breach=False` (graceful raise from `finally` after fn runs, NOT SIGTERM-to-self — that path had been the original review's critical bug). `_Monitor` background thread does RSS/sample polling with crash_log integration. `SandboxViolation` for path escapes. |
| `Syntax-Intelligence/crash_log.py` | NEW | Always-on JSONL step logger. Per-row O_APPEND + fsync. Thread-safe with `_WRITE_LOCK`. Path fallback: $BLEAKNARRATIVES_STATE → ~/.bleaknarratives → /tmp. `open_session` / `step` / `close_session` / `read_all` API. |
| `Syntax-Intelligence/test_autoclaw_validator.py` | NEW | 24 tests. Dataclass validation, sandbox enforcement, decorator HALT/TRACE/OFF, sandbox-paths-at-decoration, token-budget breach. |
| `Syntax-Intelligence/test_crash_log.py` | NEW | 13 tests. Step/emit, base-metadata, session lifecycle, thread safety (5 threads × 20 writes), replay tolerance for truncated tails, path resolution, env opt-out hook. |

### Decisions Made
- **Empirical ceilings:** 1.5 GiB hard RSS (≈58% of 2.6 Gi total — below OOM point, above 257 MiB restart baseline) + 1.0 GiB swap warning.
- **HALT mode = graceful raise, not SIGTERM-kill.** Default `kill_on_breach=False` raises ResourceCeilingExceeded from wrapper's `finally` clause after fn completes.
- **CrashLog opt-out runs at every marker call, not frozen at import.** Moved env check INSIDE `_emit_load_marker()` so tests toggle via setUp.
- **Per-row fsync is intentional.** ~1 ms per row × few hundred steps per session = <500 ms total.
- Did NOT edit: TruthSleuth dispatcher, `swarm_charter.py` (open B), tier system.

### Status at End of Session
- AN-12: ✓ FIXED (minimum-viable)
- **37/37 tests passing** in autoclaw_validator + crash_log suites.

---

## Session: July 21, 2026 (later) — Wire-In Expansion: Vouch Fix + Long-Lived Monitor + AN-08 Trust Policy

### What Got Built

| File | Action | What It Does |
|---|---|---|
| `Syntax-Intelligence/handoff_validator.py` | FIXED | Added `import inspect` + `HandoffValidator._call_predicate()` arity-aware dispatch (single-arg predicates get `(value)`; two-arg predicates get `(value, payload)`). Updated the three `_vouch_*` predicates to take `(value, payload_dict)` and use payload for cross-field access. |
| `Syntax-Intelligence/autoclaw_validator.py` | EXTENDED | Added `ResourceMonitor` class (long-lived variant; separate from `_Monitor` which stays per-call). Supports `on_findings=callable` and/or `out_queue=queue.Queue` routing. Idempotent start; preflight BLOCK in HALT mode prevents thread from ever forking. |
| `Syntax-Intelligence/bus_validator.py` | EXTENDED | Added `long_lived_monitor: bool = False` constructor kwarg. Opt-in enables a `queue.Queue` + `ResourceMonitor`. Added `__enter__` / `__exit__` ContextManager methods. Added `_start_long_lived_monitor()` (lazy-imports ResourceMonitor; on_findings emits to crash_log best-effort). Added `_drain_monitor_queue(phase)` that pops the queue and tallies 4 additive stats keys: `monitor_findings_total`, `monitor_ticks_total`, `monitor_last_poll_at`, `monitor_observed_during_dispatch`. Drains at preflight entry + after postcall on every `_dispatch` path. |
| `Syntax-Intelligence/swarm_charter.py` | EXTENDED | Added Article X (THE SCOPE EXPANSION RULE) to `CHARTER_TEXT`. Updated `to_dict()` `articles` list to include `"X — The Scope Expansion Rule (decided 2026-07-21)"`. |

### Decisions Made
- **Predicate signature: dual-arity with `inspect.signature` detection.** Single-arg predicates (legacy) keep working unchanged. Two-arg predicates (cross-field) get full payload.
- **Long-lived monitor: per-PROXY (not global singleton).** Per-proxy ResourceMonitor calibrated to per-proxy `ResourceCeiling`.
- **Lifecycle:** start on `__enter__`, stop on `__exit__`. `__del__` is unreliable in Python; explicit ContextManager is the canonical RAII shape.
- **Queue model vs. callback for in-flight findings:** HYBRID. Monitor's `on_findings` callback emits to crash_log (real-time observability); `out_queue` lets the proxy's `_drain_monitor_queue` count them for stats. Defense-in-depth retained: per-call fence STILL RUNS alongside the long-lived monitor.
- **Crude deduplication acknowledged:** concurrent preflight snapshot + monitor tick on the same breach CAN fire twice. Operator dedupes in post on `(code, tick_window_seconds)`. Future improvement if signal-cost becomes real.
- **AN-08 trust policy: RESET-per-scope-expansion.** Prior evidence RETAINED as audit (not as credit). Rationale: narrow competence does not predict wider competence (Galton-board effect spec §6 ADSR decay). `tier_override` registry escape hatch in Article X last paragraph.
- **Stacked `tier_from` entry in `VOUCH_PAYLOAD_CONTRACT` KEPT by design.** Three distinct PREDICATE_FAIL findings (voucher_id / tier_from / tier_to) operator-fix-one-at-a-time ergonomics.

### Cross-File Edits This Session
- `~/syntax-ai-architecture-spec.md` §1: open-item bullet struck; closing line added "RESET-per-scope-expansion (RESOLVED 2026-07-21)".
- `~/anomalies.md`: AN-08 → FIXED with decision + rationale + spec/charter refs. AN-13 NEW (VOUCH cross-field predicate sig bug). AN-14 NEW (per-call fence in-flight RSS blind spot).
- `~/WHO_DID_WHAT.md` (root): appended full session entry.

### Status at End of Session
- AN-08: ✓ FIXED (RESET-per-scope-expansion decided; rationale in spec + charter Article X).
- AN-13: ✓ FIXED (VOUCH predicate sig bug arity-detection helper).
- AN-14: ✓ FIXED (minimum-viable) — long-lived monitor shipped as opt-in.
- Vouch tests: **28/28 passing** in the vouch + related subset.
- Full suite: **140/144 passing** — 4 pre-existing failures unchanged (mock.patch on bound-method edge case + 2 `_StubFinding` missing-`to_dict()` cases).

### Code-Review Loop
- Round 1 caught 2 low-severity cleanups: (a) `ResourceMonitor.evaluate_snapshot` duplicates `_Monitor.evaluate_snapshot` — extract to module-level helper; (b) `Dict[str, int]` type hint now includes a float (`monitor_last_poll_at`) — annotation should be `Dict[str, Union[int, float]]`. Both folded into next-session followups.

### Lesson Learned
> The Vouch contract predicates were silently mis-firing because the dict-guard `if isinstance(payload_dict, dict) else {}` inside the predicate body hid the mis-calling bug from `from autoclaw_validator import validator import errors. The validator was calling `predicate(value)` with just the field value (a string like `"agent_x"`) instead of the full payload dict. The guard fell back to `{}`, predicates got `None != None` → False → PREDICATE_FAIL on EVERY vouch. The test surface was thin (`test_valid_single_step_advance_passes` was THE failure pin) which is why this had been carried for weeks. The fix: `inspect.signature(pred).parameters` arity-check at the validator call site. Backward-compatible: single-arg predicates still get `(value)`; only the new cross-field predicates opt into `(value, payload)`.

---

## Files Currently In This Folder (Active)

Per session work:

- `handoff_validator.py` — structural + semantic (minimum-viable) validator (spec §4)
- `autoclaw_validator.py` — spec §2 hard layer: ResourceCeiling, autoclaw_protect, ResourceMonitor, _Monitor
- `bus_validator.py` — ValidatingBusProxy wrapping any bus-like object; long-lived monitor opt-in
- `crash_log.py` — JSONL step logger with per-row fsync
- `swarm_charter.py` — 6-tier governance + 10 charter articles (Article X = scope expansion rule)
- `hardened_engine.py` — core engine (AgentIdentity, task orchestration, vouch ledger)
- `agent_protocol.py` — SwarmMessage + MessageType + TaskDecision enums
- `event_bus.py` / `event_publisher.py` / `event_watcher.py` — bus infra
- `dispatchers.py` — TruthSleuth, Bardildo, ThinkingHats dispatcher registry
- `dashboard.py` — Flask REST API for swarm state
- `semantic_handoff.py` — TruthSleuth minimum-viable semantic lane (open AN-11 proper)

---

## Test Status (cumulative)

| Suite | Tests | Last Verified |
|---|---|---|
| `test_handoff_validator.py` | 25+ | 2026-07-21 (28/28 passing on relevant subset) |
| `test_bus_validator.py` | 25 | 2026-07-21 (3 pre-existing carry-overs) |
| `test_bus_validator_autoclaw.py` | 14+ | 2026-07-21 (2 pre-existing `mock.patch` carry-overs) |
| `test_autoclaw_validator.py` | 24 | 2026-07-21 (all passing) |
| `test_crash_log.py` | 13 | 2026-07-21 (all passing) |
| `test_semantic_handoff.py` | n/a | TruthSleuth proper (open AN-11) |
| `test_handoff_validator.py` (extend) | 7 | 2026-07-21 (post-predicate-fix all green) |

---

*Folder-level WHO_DID_WHAT maintained by Agent M (Buffy) for the Hall of the DEVine. Cross-project context: root `~/WHO_DID_WHAT.md`.*
