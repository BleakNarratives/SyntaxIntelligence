"""
AUTOCLAW VALIDATOR — Spec §2 hard/testable constraint enforcement layer.

Purpose
-------
Implements the spec §2 "Hard/testable layer" of two-tier constraint
enforcement for the Project Syntax autonomous agent runtime. The
companion (`handoff_validator.py`) is the handoff-shape gate; this
file is the resource-and-bound gate that prevents the runtime from
producing the failure mode that today killed three sessions in a row.

What it enforces (binary pass/fail, no judgment calls):

  1. RSS ceiling             — caps resident-set-size of the protected agent
                               process. Default 1.5 GiB on the Sysnrax/Crostini
                               build (empirically set after 2026-07-21 forensics:
                               freebuff instance peaked at 2.38 GiB RSS in the
                               2.6 GiB crosvm container; the OOM-killer took it
                               at 00:38:18 — see `anomalies.md` AN-12).
  2. Swap ceiling            — caps VmSwap. Default 1.0 GiB (current observed
                               swap use is ~137 MiB; 7x warning runway).
  3. Per-call time budget    — wall-clock cap on protected block. Default
                               600s (10 minutes — current long-running patterns
                               observed in bus_validator + dispatcher cycle).
  4. Token/API budget        — caller-supplied accounting. Default 100k.
  5. Sandbox path root       — block any file write whose absolute-resolved
                               path is NOT under `sandbox_root`. Default
                               `$HOME/bleaknarratives`.

Design constraints
------------------
  * Pure stdlib. Zero project imports. Matches `handoff_validator.py`
    and `bus_validator.py` vendoring-friendly contract.
  * Decorator-shaped API. `@autoclaw_protect` wraps a callable; resource
    monitoring runs in a background thread that the decorator joins on
    block exit. The thread is RAII-clean via `threading.Thread(daemon=True)`
    so the protection NEVER itself becomes a leak source.
  * CrashLog integration is best-effort: if `crash_log.py` is importable
    in the same directory, findings are appended as fsync'd JSONL rows
    before the protected block returns. If not, findings route to stderr.
    This is the actual fix for the "2 of 3 crashes didn't log" failure
    that came up in session 2026-07-21 (see anomalies.md AN-12): the
    crash_log row is written BEFORE the SIGNAL is raised, so a failing
    decorator block always leaves forensic artifacts on disk.

Modes
-----
  * HALT   (default) — block ceiling exceeded → raise ResourceCeilingExceeded;
                        optionally send SIGTERM to self (default grace), then
                        SIGKILL after 5s if still alive. bgthread joins.
  * TRACE  — block ceiling exceeded → log finding, won't raise. Behavior is
             unaltered; observability only. Use for fine-tuning ceilings in
             dev. Findings still emitted to crash_log.
  * OFF    — decorator is a no-op (still records resource snapshots into the
             crash_log so the pattern shows up over time; allows opt-out
             cheap instrumentation).

Spec status
-----------
Closes AN-12 minimum-viable. Open AN-* follow-ups (per-persona
enforcement, per-model-load enforcement) recorded as residual.
"""
from __future__ import annotations

import functools
import os
import queue
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ════════════════════════════════════════════════════════════════
# SEVERITY
# ════════════════════════════════════════════════════════════════

class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCK = "block"          # raises ResourceCeilingExceeded in HALT mode
    CRITICAL = "critical"    # always raises + SIGTERM to self


# ════════════════════════════════════════════════════════════════
# CEILING CONFIG
# ════════════════════════════════════════════════════════════════
#
# Empirically tuned for the Sysnrax Crostini container observed in
# session 2026-07-21 (host ceiling 2.6 GiB; freebuff RSS peaked 2.38 GiB;
# current fresh-restart baseline ~257 MiB). Numbers below are the
# PRODUCTION DEFAULTS; tests override with much smaller ceilings so
# they don't actually OOM the test process.

# 1.5 GiB
DEFAULT_RSS_BLOCK_BYTES = 1_610_612_736
# 75% of block = early warning
DEFAULT_RSS_WARN_PCT = 0.75
# 1.0 GiB
DEFAULT_SWAP_BLOCK_BYTES = 1_073_741_824
# 10 minutes
DEFAULT_TIME_BUDGET_SECONDS = 600.0
# 100k "tokens" (caller-supplied)
DEFAULT_TOKEN_BUDGET = 100_000


@dataclass(frozen=True)
class ResourceCeiling:
    """Resource limits enforced by @autoclaw_protect.

    All bytes fields are bytes (not KiB). All time fields are seconds.
    `sandbox_root` MUST be an absolute path; constructor asserts.

    `kill_on_breach` (default False) controls the abort mechanism:
      * kill_on_breach=False (default): HALT-mode mid-call findings
        are LOGGED to crash_log AND raised from the wrapper's `finally`
        clause after `fn` returns. ResourceCeilingExceeded IS raisable
        by callers. Tests run cleanly. fn is not interrupted mid-call
        (Python has no first-class cancellation for in-flight callables);
        the abort is GRACEFUL — fn completes, then decoration raises.
      * kill_on_breach=True: mid-call CRITICAL findings send SIGTERM
        to self (Python's default SIGTERM handler terminates the
        process; crash_log's `atexit` flushes final rows before exit).
        Use this ONLY when the wrapped function is untrusted or
        indefinitely-running. NOT recommended outside diagnostics.
    """
    rss_block_bytes: int = DEFAULT_RSS_BLOCK_BYTES
    rss_warn_pct: float = DEFAULT_RSS_WARN_PCT
    swap_block_bytes: int = DEFAULT_SWAP_BLOCK_BYTES
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS
    token_budget: int = DEFAULT_TOKEN_BUDGET
    sandbox_root: Path = field(
        default_factory=lambda: Path(os.path.expanduser("~/bleaknarratives")).resolve()
    )
    mode: str = "HALT"             # HALT | TRACE | OFF
    poll_interval_seconds: float = 1.0
    signal_grace_seconds: float = 5.0
    kill_on_breach: bool = False   # default = graceful raise, not signal-kill

    def __post_init__(self) -> None:
        # Path is frozen via dataclass(frozen=True); need object.__setattr__
        # for the post-init normalization.
        if not self.sandbox_root.is_absolute():
            raise ValueError(
                f"ResourceCeiling.sandbox_root must be absolute; got {self.sandbox_root!r}"
            )
        if self.mode not in ("HALT", "TRACE", "OFF"):
            raise ValueError(
                f"ResourceCeiling.mode must be one of HALT|TRACE|OFF; got {self.mode!r}"
            )
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.rss_block_bytes <= 0:
            raise ValueError("rss_block_bytes must be > 0")

    @property
    def rss_warn_bytes(self) -> int:
        return int(self.rss_block_bytes * self.rss_warn_pct)


# ════════════════════════════════════════════════════════════════
# RESOURCE SNAPSHOT — read /proc/self/status on Linux
# ════════════════════════════════════════════════════════════════

@dataclass
class ResourceSnapshot:
    """One sample of the agent's resource state."""
    pid: int
    timestamp: float
    rss_bytes: int
    swap_bytes: int
    vm_peak_bytes: int
    threads: int
    wallclock_seconds: float = 0.0
    tokens_used: int = 0

    def to_row(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "timestamp": self.timestamp,
            "rss_bytes": self.rss_bytes,
            "swap_bytes": self.swap_bytes,
            "vm_peak_bytes": self.vm_peak_bytes,
            "threads": self.threads,
            "wallclock_seconds": self.wallclock_seconds,
            "tokens_used": self.tokens_used,
        }


def _read_proc_self_status() -> Optional[Dict[str, int]]:
    """Parse /proc/self/status into a dict of KiB-counter fields.

    Returns None on non-Linux platforms or when /proc is unreadable.
    Fields parsed: VmRSS, VmSwap, VmPeak, Threads.
    """
    try:
        with open("/proc/self/status", "r") as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    out: Dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        if key not in ("VmRSS", "VmSwap", "VmPeak", "Threads"):
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        out[key] = value
    return out


def take_snapshot(tokens_used: int = 0, wallclock: float = 0.0) -> ResourceSnapshot:
    """Capture a fresh ResourceSnapshot. Returns zeroed snapshot on non-Linux."""
    pid = os.getpid()
    ts = time.time()
    proc = _read_proc_self_status() or {}
    rss_kb = proc.get("VmRSS", 0)
    swap_kb = proc.get("VmSwap", 0)
    peak_kb = proc.get("VmPeak", 0)
    threads = proc.get("Threads", 0)
    return ResourceSnapshot(
        pid=pid,
        timestamp=ts,
        rss_bytes=rss_kb * 1024,
        swap_bytes=swap_kb * 1024,
        vm_peak_bytes=peak_kb * 1024,
        threads=threads,
        wallclock_seconds=wallclock,
        tokens_used=tokens_used,
    )


# ════════════════════════════════════════════════════════════════
# FINDINGS + ERRORS
# ════════════════════════════════════════════════════════════════

@dataclass
class ResourceFinding:
    """One surfaced ceiling observation."""
    severity: Severity
    code: str
    message: str
    snapshot: ResourceSnapshot
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "snapshot": self.snapshot.to_row(),
            "detail": dict(self.detail),
        }


class ResourceCeilingExceeded(RuntimeError):
    """Raised in HALT mode when a resource ceiling is breached."""
    def __init__(self, findings: List[ResourceFinding]):
        self.findings = list(findings)
        msgs = [f.message for f in findings]
        super().__init__(
            f"ResourceCeilingExceeded: {len(findings)} finding(s) — " + " | ".join(msgs)
        )


class SandboxViolation(PermissionError):
    """Raised when a write targets outside sandbox_root."""
    def __init__(self, attempted_path: Path, sandbox_root: Path):
        self.attempted_path = str(attempted_path)
        self.sandbox_root = str(sandbox_root)
        super().__init__(
            f"SandboxViolation: write to {self.attempted_path!r} "
            f"not inside sandbox_root {self.sandbox_root!r}"
        )


# ════════════════════════════════════════════════════════════════
# CRASH LOG INTEGRATION — best effort
# ════════════════════════════════════════════════════════════════
#
# Try to emit findings via `crash_log.py` if it's importable from
# the same directory. This is the fix for the "2 of 3 crashes didn't
# leave artifacts" failure mode: every finding is fsyc'd to disk
# BEFORE the SIGNAL is raised (during HALT mode in particular).

def _try_emit_to_crash_log(event: str, payload: Dict[str, Any]) -> bool:
    """Best-effort emit. Returns True if it landed, False if we couldn't.

    import-from-same-directory only. No mutation of sys.path; uses the
    fact that this module lives next to crash_log.py in practice.
    """
    try:
        import crash_log  # type: ignore
    except ImportError:
        return False
    try:
        crash_log.step(event, **payload)
        return True
    except Exception:
        return False


def _emit(event: str, payload: Dict[str, Any]) -> None:
    """Crash log if available; stderr fallback."""
    landed = _try_emit_to_crash_log(event, payload)
    if not landed:
        # Fall back to a one-line stderr write so SOMETHING shows up
        # in the operator's terminal even if crash_log.py is missing.
        sys.stderr.write(
            f"[autoclaw_validator] {event} "
            f"{sorted(payload.items())}\n"
        )
        sys.stderr.flush()


# ════════════════════════════════════════════════════════════════
# SANDBOX PATH ENFORCEMENT
# ════════════════════════════════════════════════════════════════

def assert_within_sandbox(path: Union[str, Path], sandbox_root: Path) -> Path:
    """Resolve and assert `path` is under `sandbox_root`.

    Returns the resolved Path on success. Raises SandboxViolation on
    escape. Symlinks are resolved before judgment. Path IS to-write
    semantics: parents need not exist yet, but the resolved path
    must not escape.

    Uses `os.path.commonpath` because `Path.is_relative_to` is
    Python-3.9+. We target 3.7+ here.
    """
    if not str(path):
        raise SandboxViolation(Path(""), sandbox_root)
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        # Relative-path writes are sandboxed by cwd; operator opted
        # in to that by passing a relative path. Resolve against cwd.
        raw = os.path.abspath(raw)
    resolved = Path(os.path.realpath(raw))
    root_resolved = Path(os.path.realpath(os.fspath(sandbox_root)))
    try:
        common = os.path.commonpath([str(resolved), str(root_resolved)])
    except ValueError:
        # Different drives on Windows, etc. — definitively outside sandbox.
        raise SandboxViolation(resolved, root_resolved)
    if common != str(root_resolved):
        raise SandboxViolation(resolved, root_resolved)
    return resolved


# ════════════════════════════════════════════════════════════════
# SHARED EVALUATE-SNAPSHOT HELPER
# ════════════════════════════════════════════════════════════════
#
# Both `_Monitor` (per-call, decorator-scoped) and `ResourceMonitor`
# (long-lived, agent-scoped) need to evaluate a `ResourceSnapshot`
# against a `ResourceCeiling` and return a list of `ResourceFinding`s.
# The RSS + swap gates are identical in both — only the per-call
# `TIME` and `TOKEN` gates differ (ResourceMonitor has no wallclock
# start-time or tokens_supplier because it operates agent-wide, not
# call-scoped). This helper owns the shared subset so the two
# monitor classes can't disagree about what counts as a breach
# across preflight, postcall, and long-lived ticks.

def _evaluate_resource_ceiling(
    snap: ResourceSnapshot,
    ceiling: ResourceCeiling,
) -> List[ResourceFinding]:
    """RSS + swap breach evaluation shared by `_Monitor` and `ResourceMonitor`.

    TIME and TOKEN budgets are intentionally NOT included here —
    ResourceMonitor has no call-scoped wallclock / tokens supplier,
    so those gates belong only in the per-call `_Monitor` path.
    `_Monitor.evaluate_snapshot` calls this helper, then appends
    TIME and TOKEN findings onto the returned list.

    Detail dicts are uniform so preflight, postcall, and long-lived
    ticks emit the same observability shape into crash_log.
    """
    findings: List[ResourceFinding] = []
    if snap.rss_bytes >= ceiling.rss_block_bytes:
        findings.append(ResourceFinding(
            severity=Severity.CRITICAL,
            code="RSS_BLOCK_EXCEEDED",
            message=f"RSS {snap.rss_bytes} >= block ceiling {ceiling.rss_block_bytes} bytes",
            snapshot=snap,
            detail={
                "ceiling_rss_block_bytes": ceiling.rss_block_bytes,
                "observed_rss_bytes": snap.rss_bytes,
            },
        ))
    elif snap.rss_bytes >= ceiling.rss_warn_bytes:
        findings.append(ResourceFinding(
            severity=Severity.WARNING,
            code="RSS_WARN_APPROACHING",
            message=f"RSS {snap.rss_bytes} approaching block ceiling ({ceiling.rss_warn_bytes} bytes = {ceiling.rss_warn_pct*100:.0f}% of block)",
            snapshot=snap,
            detail={
                "ceiling_rss_block_bytes": ceiling.rss_block_bytes,
                "ceiling_rss_warn_bytes": ceiling.rss_warn_bytes,
                "observed_rss_bytes": snap.rss_bytes,
            },
        ))
    if snap.swap_bytes >= ceiling.swap_block_bytes:
        findings.append(ResourceFinding(
            severity=Severity.CRITICAL,
            code="SWAP_BLOCK_EXCEEDED",
            message=f"swap {snap.swap_bytes} >= block ceiling {ceiling.swap_block_bytes} bytes",
            snapshot=snap,
            detail={
                "ceiling_swap_block_bytes": ceiling.swap_block_bytes,
                "observed_swap_bytes": snap.swap_bytes,
            },
        ))
    return findings


# ════════════════════════════════════════════════════════════════
# MONITOR (background thread)
# ════════════════════════════════════════════════════════════════

class _Monitor:
    """Background-thread resource monitor used by @autoclaw_protect.

    Polls ResourceSnapshot at `poll_interval_seconds`. Captures the
    worst snapshot per category. Emits findings via _emit(). Triggers
    `callback` if a CRITICAL finding fires (the callback is responsible
    for raising — keeps the monitor thread pure-stdlib and not entangled
    with the protected call's exception machinery.

    Pending-breach store
    --------------------
    If HALT mode + kill_on_breach=False (graceful), the monitor
    accumulates findings in `pending_breach`. The wrapper's `finally`
    clause drains the store and raises ResourceCeilingExceeded from
    the accumulated findings. This is the path that lets callers
    `with assertRaises(ResourceCeilingExceeded): f()` in non-killing
    mode (the fix for the SIGTERM-kills-test-process bug).
    """

    def __init__(
        self,
        ceiling: ResourceCeiling,
        callback: Callable[[List[ResourceFinding]], None],
        tokens_supplier: Callable[[], int],
        start_wallclock: float,
    ):
        self._ceiling = ceiling
        self._callback = callback
        self._tokens_supplier = tokens_supplier
        self._start_wallclock = start_wallclock
        self._worst_rss = 0
        self._worst_swap = 0
        self._worst_tokens = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="autoclaw-monitor")
        self._lock = threading.Lock()
        self._started = False  # tracks whether _thread.start() was actually called
        self.pending_breach: List[ResourceFinding] = []
        self.preflight_breach: List[ResourceFinding] = []

    def start(self) -> None:
        if self._ceiling.mode == "OFF":
            return
        # Initial snapshot BEFORE entering protected work — catches a
        # pre-call baseline ceiling breach.
        snap = take_snapshot()
        findings = self.evaluate_snapshot(snap)
        if findings and self._ceiling.mode == "HALT":
            # preflight breaches always raise via callback (caller's
            # main thread, before fn runs). Crucially: we do NOT call
            # _thread.start() here, so stop() must guard its join.
            self._callback(findings)
            return
        self._thread.start()
        self._started = True

    def stop(self, join_timeout: float = 2.0) -> ResourceSnapshot:
        """Stop the monitor; return the last snapshot captured."""
        self._stop.set()
        # Guard join: only join if we actually started the thread.
        # Without this, preflight BLOCK path raises RuntimeError
        # ("cannot join thread before it is started") in the wrapper's
        # finally clause whenever preflight fires.
        if self._started:
            self._thread.join(timeout=join_timeout)
        return take_snapshot(
            tokens_used=self._tokens_supplier(),
            wallclock=time.monotonic() - self._start_wallclock,
        )

    def drain_pending_breach(self) -> List[ResourceFinding]:
        """Atomically return and clear pending_breach."""
        with self._lock:
            out = self.pending_breach
            self.pending_breach = []
            return out

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snap = take_snapshot(
                    tokens_used=self._tokens_supplier(),
                    wallclock=time.monotonic() - self._start_wallclock,
                )
                with self._lock:
                    self._worst_rss = max(self._worst_rss, snap.rss_bytes)
                    self._worst_swap = max(self._worst_swap, snap.swap_bytes)
                    self._worst_tokens = max(self._worst_tokens, snap.tokens_used)
                findings = self.evaluate_snapshot(snap)
                if findings:
                    self._callback(findings)
                    # In HALT-kill mode the callback sends SIGTERM and
                    # the process dies. In HALT-graceful mode the
                    # callback accumulates findings to pending_breach
                    # so the wrapper can raise from `finally`. In TRACE
                    # mode we keep polling to capture the post-breach
                    # trajectory.
            except Exception as exc:
                # The monitor never itself raises — it records and keeps going.
                _emit("monitor.error", {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(limit=4),
                })
            self._stop.wait(timeout=self._ceiling.poll_interval_seconds)

    def evaluate_snapshot(self, snap: ResourceSnapshot) -> List[ResourceFinding]:
        """Compare snapshot against ceiling; return findings list.

        RSS + swap via the shared `_evaluate_resource_ceiling`
        helper. TIME + TOKEN appended here because only `_Monitor`
        has a wallclock start-time + `tokens_supplier` available
        on the snapshot it's been handed.
        """
        findings = _evaluate_resource_ceiling(snap, self._ceiling)
        ceiling = self._ceiling

        # Time
        if snap.wallclock_seconds >= ceiling.time_budget_seconds:
            findings.append(ResourceFinding(
                severity=Severity.CRITICAL,
                code="TIME_BUDGET_EXCEEDED",
                message=f"wallclock {snap.wallclock_seconds:.1f}s >= time budget {ceiling.time_budget_seconds:.1f}s",
                snapshot=snap,
                detail={
                    "ceiling_time_budget_seconds": ceiling.time_budget_seconds,
                    "observed_wallclock_seconds": snap.wallclock_seconds,
                },
            ))

        # Tokens
        if snap.tokens_used >= ceiling.token_budget:
            findings.append(ResourceFinding(
                severity=Severity.CRITICAL,
                code="TOKEN_BUDGET_EXCEEDED",
                message=f"tokens_used {snap.tokens_used} >= budget {ceiling.token_budget}",
                snapshot=snap,
                detail={
                    "ceiling_token_budget": ceiling.token_budget,
                    "observed_tokens_used": snap.tokens_used,
                },
            ))

        return findings

    def worst_snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "rss_bytes_at_peak": self._worst_rss,
                "swap_bytes_at_peak": self._worst_swap,
                "tokens_at_peak": self._worst_tokens,
            }


# ════════════════════════════════════════════════════════════════
# RESOURCE MONITOR (long-lived, cross-call)
# ════════════════════════════════════════════════════════════════
#
# _Monitor above is per-call (decorator-scoped). ResourceMonitor
# below is proxy/agent-scoped — designed to start once on a
# ValidatingBusProxy (or any owner that wants continuous observation)
# and stop on owner destruction. Closes the gap the per-call fence
# leaves open: in-flight RSS spikes BETWEEN the synchronous prefight
# snapshot and the synchronous post-call snapshot.
#
# Routing is configurable: pass either `on_findings=callable` (fired
# synchronously in the monitor thread) or `out_queue=queue.Queue`
# (consumed asynchronously). At least one of the two MUST be set —
# otherwise there'd be nowhere for findings to go. An owner that
# wants both (real-time observability + retrospective counting)
# passes the callable for emission and the queue for stats.
#
# Defense-in-depth: ResourceMonitor runs alongside the per-call
# fence. Duplicate findings on overlapping breaches are a known
# property and crude-dedupe is documented in the bus_validator
# docstring; operators reading crash_log should dedupe on
# (code, tick_window_seconds) when correlating.

class ResourceMonitor:
    """Long-lived background resource monitor (see module section above)."""

    def __init__(
        self,
        ceiling: ResourceCeiling,
        on_findings: Optional[Callable[[List[ResourceFinding]], None]] = None,
        out_queue: Optional["queue.Queue[ResourceFinding]"] = None,
    ) -> None:
        if on_findings is None and out_queue is None:
            raise ValueError(
                "ResourceMonitor requires at least one of "
                "on_findings= or out_queue=."
            )
        self._ceiling = ceiling
        self._on_findings = on_findings
        self._out_queue = out_queue
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="autoclaw-resource-monitor",
        )
        self._started = False
        self.ticks_total: int = 0
        self.findings_total: int = 0
        self.last_poll_at: float = 0.0

    def start(self) -> None:
        """Begin polling. Idempotent. Fires preflight callback once
        on entry; if preflight is already in HALT-mode BLOCK, the
        thread never starts (matching _Monitor semantics)."""
        if self._started:
            return
        snap = take_snapshot(wallclock=0.0)
        findings = self.evaluate_snapshot(snap)
        if findings and self._ceiling.mode == "HALT":
            self._dispatch(findings)
            return  # _thread.start() NOT called → stop() must guard join
        self._thread.start()
        self._started = True

    def stop(self, join_timeout: float = 2.0) -> ResourceSnapshot:
        """Stop polling, join the thread, return the final ResourceSnapshot."""
        self._stop.set()
        if self._started:
            self._thread.join(timeout=join_timeout)
        return take_snapshot(wallclock=0.0)

    def _dispatch(self, findings: List[ResourceFinding]) -> None:
        """Route findings to whichever route was supplied at construction."""
        if self._on_findings is not None:
            try:
                self._on_findings(findings)
            except Exception:
                pass  # monitor thread must not die on callback failures
        if self._out_queue is not None:
            for f in findings:
                try:
                    self._out_queue.put_nowait(f)
                except Exception:
                    pass  # queue full → don't block the monitor
        self.findings_total += len(findings)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.ticks_total += 1
            self.last_poll_at = time.time()
            try:
                snap = take_snapshot(wallclock=0.0)
                findings = self.evaluate_snapshot(snap)
            except Exception:
                findings = []
            if findings:
                self._dispatch(findings)
            # _stop.wait returns True if the event was set (i.e. stop
            # was signaled) — that's our break.
            if self._stop.wait(timeout=self._ceiling.poll_interval_seconds):
                break

    def evaluate_snapshot(self, snap: ResourceSnapshot) -> List[ResourceFinding]:
        """Pure evaluation: snapshot → list of ResourceFindings (no side effects).

        Delegates RSS + swap to the shared `_evaluate_resource_ceiling`
        helper — preflight, postcall, and long-lived ticks all agree
        on what counts as a breach. TIME/TOKEN gates are intentionally
        omitted: ResourceMonitor is the agent-wide observation layer
        with no wallclock / tokens supplier; per-call decoration
        handles those budgets via `_Monitor`.
        """
        return _evaluate_resource_ceiling(snap, self._ceiling)


# ════════════════════════════════════════════════════════════════
# DECORATOR
# ════════════════════════════════════════════════════════════════

def autoclaw_protect(
    ceiling: Optional[ResourceCeiling] = None,
    *,
    tokens_supplier: Optional[Callable[[], int]] = None,
    sandbox_paths: Optional[List[Union[str, Path]]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a callable with resource & sandbox enforcement.

    Parameters
    ----------
    ceiling : ResourceCeiling, optional
        Defaults to ResourceCeiling() (per the Sysnrax/Crostini production
        profile). Tests pass a much smaller ceiling to exercise the gate
        without OOMing the test process.
    tokens_supplier : callable -> int, optional
        Function that returns current cumulative token count. None defaults
        to a constant-zero supplier, which means the token budget is never
        breached. Pass a closure over your model's running token counter
        to actually exercise that gate.
    sandbox_paths : list of paths, optional
        If provided, each path is asserted under sandbox_root AT call-time,
        BEFORE the wrapped function runs. Useful for declarative docs that
        say "this function may only write to these locations."

    Returns
    -------
    A decorator. The decorated function preserves its signature.

    RAISES
    ------
    SandboxViolation     if any sandbox_path is outside the ceiling's root
                         (prerun check).
    ResourceCeilingExceeded if HALT mode and any ceiling breached during
                         protected execution. The exception's `.findings`
                         list carries every ResourceFinding collected.
    """
    if ceiling is None:
        ceiling = ResourceCeiling()

    if sandbox_paths:
        for p in sandbox_paths:
            assert_within_sandbox(p, ceiling.sandbox_root)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _invoke_protected(
                fn, args, kwargs,
                ceiling=ceiling,
                tokens_supplier=tokens_supplier,
            )
        return wrapper

    return decorator


def _invoke_protected(
    fn: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    ceiling: ResourceCeiling,
    tokens_supplier: Optional[Callable[[], int]],
) -> Any:
    """Body of @autoclaw_protect wrapper. Separated for testability."""
    # functools is imported at module top (the decorator below uses
    # @functools.wraps, which executes in the module-load frame of
    # the DECORATOR FUNCTION, not the wrapped function's frame).

    start_wallclock = time.monotonic()
    if ceiling.mode == "OFF":
        # No enforcement, but log a note so the ceiling is visible.
        _emit("autoclaw.mode", {"mode": "OFF", "ceiling": _ceiling_to_dict(ceiling)})
        return fn(*args, **kwargs)

    supplier = tokens_supplier or (lambda: 0)

    def _on_findings(findings: List[ResourceFinding]) -> None:
        """Run on every monitor poll that surfaces findings.

        Behavior:
          * INFO/WARNING findings: emit only (observer mode).
          * BLOCK/CRITICAL in HALT mode: emit + halt log + either:
              - kill_on_breach=True: send SIGTERM (process exits)
              - kill_on_breach=False (default): accumulate to
                monitor.pending_breach; wrapper raises from `finally`
                after fn returns.
          * BLOCK/CRITICAL in TRACE mode: emit only (observer).
        """
        for f in findings:
            _emit("autoclaw.finding", f.to_row())
        blocking = [f for f in findings if f.severity in (Severity.BLOCK, Severity.CRITICAL)]
        if not blocking:
            return
        if ceiling.mode != "HALT":
            return  # TRACE / OFF — observer only.
        _emit(
            "autoclaw.halt",
            {
                "findings_count": len(findings),
                "blocking_count": len(blocking),
                "codes": [f.code for f in findings],
                "kill_on_breach": ceiling.kill_on_breach,
            },
        )
        criticals = [f for f in blocking if f.severity == Severity.CRITICAL]
        if ceiling.kill_on_breach and criticals:
            _send_self_signal_with_grace(ceiling)
            return
        # Graceful: stash for post-call raise. Cleared on drain.
        with monitor._lock:  # noqa: SLF001 (intentional same-instance access)
            monitor.pending_breach.extend(blocking)

    monitor = _Monitor(
        ceiling=ceiling,
        callback=_on_findings,
        tokens_supplier=supplier,
        start_wallclock=start_wallclock,
    )

    # Pre-flight snapshot. If we are already over the block ceiling,
    # do not enter the protected block at all.
    preflight = take_snapshot(
        tokens_used=supplier(),
        wallclock=time.monotonic() - start_wallclock,
    )
    preflight_findings = monitor.evaluate_snapshot(preflight)
    blocking_preflight = [
        f for f in preflight_findings
        if f.severity in (Severity.BLOCK, Severity.CRITICAL)
    ]
    if blocking_preflight and ceiling.mode == "HALT":
        # Pre-flight BLOCK/CRITICAL => caller-visible raise before fn runs.
        for f in preflight_findings:
            _emit("autoclaw.finding", f.to_row())
        _emit(
            "autoclaw.halt",
            {
                "findings_count": len(preflight_findings),
                "blocking_count": len(blocking_preflight),
                "codes": [f.code for f in preflight_findings],
                "phase": "preflight",
                "kill_on_breach": ceiling.kill_on_breach,
            },
        )
        if ceiling.kill_on_breach and any(
            f.severity == Severity.CRITICAL for f in blocking_preflight
        ):
            _send_self_signal_with_grace(ceiling)
        raise ResourceCeilingExceeded(preflight_findings)
    if preflight_findings:
        # TRACE mode or warnings only — still emit, but don't raise.
        for f in preflight_findings:
            _emit("autoclaw.finding", f.to_row())

    monitor.start()
    try:
        return fn(*args, **kwargs)
    finally:
        final = monitor.stop()
        peak = monitor.worst_snapshot()
        _emit(
            "autoclaw.complete",
            {
                "final_snapshot": final.to_row(),
                "peak": peak,
                "ceiling": _ceiling_to_dict(ceiling),
            },
        )
        # Graceful-path raise: if the monitor accumulated a breach
        # while fn ran (HALT mode + kill_on_breach=False), the post-
        # call raise makes the breach caller-visible. fn's return
        # value (if any) is discarded. If fn itself raised, we CHAIN
        # the original exception via `from` so the operator sees both
        # signals — the breach AND the function's own error. Python's
        # automatic __context__ chain handles the case where this
        # finally-raise overrides an in-flight exception; the explicit
        # `from` upgrades that to __cause__, which surfaces in the
        # default traceback format.
        pending = monitor.drain_pending_breach()
        if pending:
            orig = sys.exc_info()[1]
            if orig is not None and not isinstance(orig, ResourceCeilingExceeded):
                # Chain fn's original error so the operator sees both
                # signals (breach + fn's own error in __cause__).
                raise ResourceCeilingExceeded(pending) from orig
            raise ResourceCeilingExceeded(pending)


def _send_self_signal_with_grace(ceiling: ResourceCeiling) -> None:  # pragma: no cover
    """Send SIGTERM to self, escalate to SIGKILL after grace. Best-effort.

    SIGTERM is preferred over SIGKILL because the runtime gets one
    final chance to write any in-flight log row before exiting. Only
    if grace_expires do we escalate. This is the explicit fix for the
    "left artifacts" failure mode: crash_log.py rows are fsync'd on
    each row by design, so anything captured between SIGTERM and exit
    is on disk.
    """
    pid = os.getpid()
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + ceiling.signal_grace_seconds
    while time.monotonic() < deadline:
        # If we're still alive after grace, escalate.
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


_CEILING_TO_DICT_FIELDS = (
    "rss_block_bytes", "rss_warn_pct", "swap_block_bytes",
    "time_budget_seconds", "token_budget", "mode",
    "poll_interval_seconds", "signal_grace_seconds",
    "kill_on_breach",
)
def _ceiling_to_dict(c: ResourceCeiling) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        f: getattr(c, f) for f in _CEILING_TO_DICT_FIELDS
    }
    out["sandbox_root"] = str(c.sandbox_root)
    out["rss_warn_bytes"] = c.rss_warn_bytes
    return out


# ════════════════════════════════════════════════════════════════
# PUBLIC CONVENIENCE: a "got here, didn't hang" sentinel
# ════════════════════════════════════════════════════════════════

EASTER_BLOOM = "🪨"
SAYING = "Hard ceilings, soft landings."


# ════════════════════════════════════════════════════════════════
# SMOKE
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"autoclaw_validator skeleton loaded. easter: {EASTER_BLOOM}")
    print(f"  saying: {SAYING}")
    print(f"  default RSS block ceiling: {DEFAULT_RSS_BLOCK_BYTES} bytes "
          f"({DEFAULT_RSS_BLOCK_BYTES / (1024**3):.2f} GiB)")
    print(f"  default RSS warn (% of block): {DEFAULT_RSS_WARN_PCT*100:.0f}% "
          f"= {int(DEFAULT_RSS_BLOCK_BYTES * DEFAULT_RSS_WARN_PCT):,} bytes")
    print(f"  default swap block ceiling: {DEFAULT_SWAP_BLOCK_BYTES} bytes "
          f"({DEFAULT_SWAP_BLOCK_BYTES / (1024**3):.2f} GiB)")
    print(f"  default time budget: {DEFAULT_TIME_BUDGET_SECONDS:.0f} seconds")
    print(f"  default token budget: {DEFAULT_TOKEN_BUDGET:,}")
    print(f"  modes: HALT (default) / TRACE / OFF")
    try:
        snap = take_snapshot()
        print(f"  current PID {snap.pid}: RSS={snap.rss_bytes:,} B, "
              f"swap={snap.swap_bytes:,} B, threads={snap.threads}")
    except Exception as exc:
        print(f"  snapshot probe failed: {exc}")
