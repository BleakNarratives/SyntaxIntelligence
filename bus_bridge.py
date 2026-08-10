#!/usr/bin/env python3
"""
SyntaxIntelligence bus bridge — mirrors SyntaxEventBus events onto the shared
RootBase agent_bus (``agent_events.jsonl``) so the rest of the ecosystem
(thoth, error_interceptor, swarm_transport, aFiREFLY, Meridian dashboard)
can observe Syntax swarm activity.

Design:
- Wraps an existing ``SyntaxEventBus`` instance transparently.
- Every ``publish()`` / ``broadcast()`` call also emits a ``syntax.event``
  record onto the shared JSONL bus when it is reachable.
- The bridge is **optional**: if ``agent_bus`` cannot be imported (Termux
  without RootBase, or a standalone Syntax test), the wrapper is a silent
  no-op and the in-memory bus works exactly as before.
- Zero changes needed to existing SyntaxEventBus consumers.

Usage:
    from SyntaxIntelligence.bus_bridge import bridge_syntax_bus

    swarm = SyntaxSwarm(auto_assemble=True)
    bridge_syntax_bus(swarm.event_bus, source="syntax_core")
    # From now on, every publish/broadcast also lands on agent_events.jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict

_AGENT_BUS_AVAILABLE = False
_emit_event: Callable[..., Any] | None = None

try:
    _thoth = Path(__file__).resolve().parents[1] / "RootBase" / "thoth_orchestrator"
    if _thoth.is_dir() and str(_thoth) not in sys.path:
        sys.path.insert(0, str(_thoth))
    from agent_bus import emit_event as _emit_event  # noqa: F401
    _AGENT_BUS_AVAILABLE = True
except (ImportError, OSError):
    pass


def bridge_syntax_bus(
    bus: Any,
    *,
    source: str = "syntax",
    origin: str = "syntax_swarm",
) -> bool:
    """Wrap an existing SyntaxEventBus so publish/broadcast also emit to the
    shared RootBase agent_bus.

    Returns True if the bridge was successfully activated, False if the
    shared bus is unavailable (degraded gracefully — the in-memory bus
    continues to work normally).

    ``bus`` must be a ``SyntaxEventBus`` instance (or any object with
    ``publish`` and ``broadcast`` methods matching that signature).
    """
    if not _AGENT_BUS_AVAILABLE or _emit_event is None:
        return False

    # Guard against double-bridging (stacking wrappers)
    if getattr(bus, "_bridged", False):
        return True

    _orig_publish = bus.publish
    _orig_broadcast = bus.broadcast

    def _bridged_publish(sender_id: str, channel: str, data: Dict[str, Any]) -> None:
        _orig_publish(sender_id, channel, data)
        try:
            _emit_event(
                "syntax.event",
                {
                    "sender_id": sender_id,
                    "channel": channel,
                    "data": _safe_summary(data),
                },
                source=source,
                origin=origin,
                channel="syntax",
                tags=["syntax", channel.replace(".", "-")],
            )
        except Exception:
            pass

    def _bridged_broadcast(sender_id: str, channel: str, data: Dict[str, Any]) -> None:
        _orig_broadcast(sender_id, channel, data)
        try:
            _emit_event(
                "syntax.broadcast",
                {
                    "sender_id": sender_id,
                    "channel": channel,
                    "data": _safe_summary(data),
                },
                source=source,
                origin=origin,
                channel="syntax",
                tags=["syntax", channel.replace(".", "-"), "broadcast"],
            )
        except Exception:
            pass

    bus.publish = _bridged_publish  # type: ignore[method-assign]
    bus.broadcast = _bridged_broadcast  # type: ignore[method-assign]
    bus._bridged = True
    return True


def _safe_summary(data: Dict[str, Any], max_keys: int = 6) -> Dict[str, Any]:
    """Return a summary safe for the shared bus (no giant payloads)."""
    if not isinstance(data, dict):
        return {"value": str(data)[:200]}
    summary: Dict[str, Any] = {}
    for i, (k, v) in enumerate(data.items()):
        if i >= max_keys:
            summary["_truncated"] = len(data) - max_keys
            break
        if isinstance(v, (str, int, float, bool, type(None))):
            summary[k] = v
        elif isinstance(v, (list, tuple)):
            summary[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            summary[k] = f"{{{len(v)} keys}}"
        else:
            summary[k] = str(type(v).__name__)
    return summary


def is_bridged(bus: Any) -> bool:
    """Check whether a SyntaxEventBus has been bridged."""
    return getattr(bus, "_bridged", False)
