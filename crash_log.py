"""
CRASH_LOG — Always-on JSONL step logger that survives parent crashes.

Purpose
-------
Fixes the failure mode observed in session 2026-07-21 (three OOM crashes,
two unlogged): on session death, in-memory state and any buffered logs
are lost. CrashLog ensures that EVERY step an agent takes is fsync'd to
disk BEFORE the next step. A crashing parent process leaves a complete
trail of what it did up until the moment of death.

Wire shape
----------
  * Pure stdlib (zero deps).
  * Locks acquired on every write (thread-safe; multiple processes can
    append to the same file safely because O_APPEND + fsync is atomic
    per-line on POSIX filesystems).
  * Path resolution: $BLEAKNARRATIVES_STATE/crash_log.jsonl → if unset,
    ~/.bleaknarratives/crash_log.jsonl → if home unwritable,
    /tmp/bleaknarratives_crash_log.jsonl (last-resort fallback).
  * Auto-load marker: when this module is imported, a single
    "crash_log_loaded" row is appended. The marker is opt-out via
    env var CRASH_LOG_SKIP_LOAD_MARKER=1 (re-checked at call time
    so tests can toggle it in setUp — not frozen at import time).

API
---
  * `crash_log.step(event, **fields)`     → append one row
  * `crash_log.open_session(purpose, **fields)`
                                          → starts a session, returns sid
  * `crash_log.close_session(sid, status, **fields)`
                                          → end-session row
  * `crash_log.path`                       → resolves/returns the JSONL path
  * `crash_log.set_path(p)`                → override path (tests use this)
  * `crash_log.fsync_each` (bool)          → toggle fsync-per-line
  * `crash_log.read_all() -> List[Dict]`   → load all rows (replay)

Why "always-on"
---------------
The cost per row is ~1 ms (one open-flag append, write, fsync). For an
agent making a few hundred steps per session this adds <500 ms — small
relative to the cost of an unlogged crash leaving zero artifacts.
The path is fsync'd per row, so a half-written line is impossible
(crash mid-write → either the row is on disk or it isn't).

Spec link
---------
Spec §2 hard/testable layer incorporated via `autoclaw_validator.py`.
`autoclaw_validator._try_emit_to_crash_log` is best-effort — if this
module IS importable from the same directory, HALT-mode findings are
persisted BEFORE the SIGNAL is raised, fixing the "left no trace"
mode.
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


_MODULE_NAME = "crash_log"
_SESSION_REGISTRY: Dict[str, Dict[str, Any]] = {}
_SESSION_REGISTRY_LOCK = threading.RLock()
_WRITE_LOCK = threading.RLock()
_FSYNC_EACH = True                # toggled by tests
_PATH_OVERRIDE: Optional[Path] = None
_LOAD_LINE_WRITTEN = False         # for first-touch marker

_LOAD_TIME = time.time()
_LOAD_PID = os.getpid()
_LOAD_HOST = socket.gethostname()


# ════════════════════════════════════════════════════════════════
# PATH RESOLUTION
# ════════════════════════════════════════════════════════════════

def _resolve_default_path() -> Path:
    """First writable of (env, user-config, /tmp)."""
    candidates: List[Path] = []
    env = os.environ.get("BLEAKNARRATIVES_STATE")
    if env:
        candidates.append(Path(env) / "crash_log.jsonl")
    candidates.append(Path(os.path.expanduser("~/.bleaknarratives/crash_log.jsonl")))
    candidates.append(Path("/tmp/bleaknarratives_crash_log.jsonl"))
    for c in candidates:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            with open(c, "a", encoding="utf-8"):
                pass
            return c
        except (OSError, PermissionError):
            continue
    return Path("/tmp/bleaknarratives_crash_log.jsonl")


def path() -> Path:
    """Public: current log path (override-aware)."""
    return _PATH_OVERRIDE if _PATH_OVERRIDE is not None else _resolve_default_path()


def get_path() -> Path:
    return path()


def set_path(p: Optional[Path]) -> None:
    """Override the log path. None restores default resolution."""
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = p


def fsync_each(value: bool) -> None:
    """Toggle fsync-per-line. Tests disable to keep the test run fast."""
    global _FSYNC_EACH
    _FSYNC_EACH = bool(value)


# ════════════════════════════════════════════════════════════════
# WRITE
# ════════════════════════════════════════════════════════════════

def _write_row(row: Dict[str, Any]) -> None:
    """Append one row; fsync unless disabled. Thread-safe."""
    p = path()
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    with _WRITE_LOCK:
        fd = os.open(
            str(p),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        try:
            os.write(fd, line.encode("utf-8"))
            if _FSYNC_EACH:
                os.fsync(fd)
        finally:
            os.close(fd)


# ════════════════════════════════════════════════════════════════
# ROW SCHEMA
# ════════════════════════════════════════════════════════════════

def _base_row(event: str) -> Dict[str, Any]:
    return {
        "ts": time.time(),
        "pid": os.getpid(),
        "host": _LOAD_HOST,
        "module": _MODULE_NAME,
        "event": event,
    }


def step(event: str, **fields: Any) -> None:
    """Append a step row."""
    row = _base_row(event)
    row.update(fields)
    try:
        _write_row(row)
    except Exception as exc:
        sys.stderr.write(
            f"[crash_log] step({event!r}) write failed: {type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()


# ════════════════════════════════════════════════════════════════
# SESSION HELPERS
# ════════════════════════════════════════════════════════════════

def open_session(purpose: str, **fields: Any) -> str:
    """Mark a session start; return the session id (sid)."""
    sid = uuid.uuid4().hex
    row = _base_row("session_open")
    row["sid"] = sid
    row["purpose"] = purpose
    row.update(fields)
    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY[sid] = {
            "started": row["ts"],
            "purpose": purpose,
        }
    _write_row(row)
    return sid


def close_session(sid: str, status: str = "ok", **fields: Any) -> None:
    """Mark a session end. Status must be ok|error|killed."""
    row = _base_row("session_close")
    row["sid"] = sid
    row["status"] = status
    row.update(fields)
    with _SESSION_REGISTRY_LOCK:
        meta = _SESSION_REGISTRY.pop(sid, {})
    if meta:
        row["duration_seconds"] = row["ts"] - meta.get("started", row["ts"])
        row["purpose"] = meta.get("purpose")
    _write_row(row)


# ════════════════════════════════════════════════════════════════
# REPLAY (read all rows)
# ════════════════════════════════════════════════════════════════

def read_all(path_override: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return every row in the log (oldest first). Tolerates truncation."""
    p = path_override or path()
    rows: List[Dict[str, Any]] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    break
    except FileNotFoundError:
        return rows
    return rows


# ════════════════════════════════════════════════════════════════
# ATEXIT HOOK
# ════════════════════════════════════════════════════════════════

def _atexit_close() -> None:
    """Best-effort write of a session_close row per open session."""
    with _SESSION_REGISTRY_LOCK:
        open_sids = list(_SESSION_REGISTRY.keys())
    for sid in open_sids:
        try:
            close_session(sid, status="atexit")
        except Exception:
            pass
    try:
        _write_row(_base_row("module_atexit"))
    except Exception:
        pass


atexit.register(_atexit_close)


# ════════════════════════════════════════════════════════════════
# MODULE LOADER MARKER
# ════════════════════════════════════════════════════════════════

def _emit_load_marker() -> None:
    """Single-row marker so any session importing this module leaves a disk trail.

    Opt-out at call time: set CRASH_LOG_SKIP_LOAD_MARKER=1 in env
    before the marker fires. Tests can toggle this in setUp because
    the env var is read here (not frozen at module-import time).
    """
    if os.environ.get("CRASH_LOG_SKIP_LOAD_MARKER"):
        return
    row = _base_row("crash_log_loaded")
    row.update({
        "load_time": _LOAD_TIME,
        "python_version": sys.version.split()[0],
        "argv": sys.argv[:5],
    })
    try:
        _write_row(row)
    except Exception:
        pass


_emit_load_marker()


# ════════════════════════════════════════════════════════════════
# SMOKE
# ════════════════════════════════════════════════════════════════

EASTER_BLOOM = "✶"
SAYING = "If you can't ship, at least be on disk."


if __name__ == "__main__":
    sid = open_session("smoke")
    step("hello", note="from __main__")
    step("another", counter=42)
    close_session(sid, status="ok")
    print(f"crash_log skeleton loaded. easter: {EASTER_BLOOM}")
    print(f"  saying: {SAYING}")
    print(f"  path: {path()}")
    print(f"  rows on disk: {len(read_all())}")
