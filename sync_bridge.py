#!/usr/bin/env python3
"""
sync_bridge.py -- cross-device file-message-queue bridge for the bleaknarratives swarm.

Design contract (post cross-device protocol design pass):
  * Writer-Owns directory layout: each device only writes to its own folder
    under ``~/bleaknarratives/sync_bus/<DEVICE_ID>/``. Peers read from there
    via rclone 'cloud' sync. No two devices ever write the same path, so
    rclone --delete is safe.
  * Idempotency: dedupe via .seen_ids.json (bounded). File rename was
    rejected because rclone sync would interpret consumer-side rename as
    a deletion.
  * TTL: the AUTHORING device deletes its own messages/ files after
    timestamp + ttl_seconds. Rclone propagates the deletion naturally.
  * Local Syntax event bus is the in-process transport; the bridge is
    layered on top so in-process subscribers keep working unchanged.
  * Pure stdlib.

Topology (matches the runbook):
  penguin (Chromebook, initiating side, rclone 'cloud' configured)
  moto4  (Motorola 4 5G 2024, Termux + sshd, carrier NAT outbound)
  a9     (Samsung A9 tablet, Termux + sshd, carrier NAT outbound)

Channels the OutClaw bus publishes today (re-exported for reference):
  outclaw.findings, outclaw.draft_blocked, swarm.heartbeat.

Usage:
    from sync_bridge import SyncBridge
    bridge = SyncBridge()
    bridge.start()
    # ... bridge wings forever, also never blocks process exit ...
    # To explicitly stop:
    bridge.stop()

Configuration via env (defaults are penguin-flavored):
    BUS_DEVICE_ID       default 'penguin'
    BUS_SYNC_ROOT       default '~/bleaknarratives/sync_bus'
    BUS_POLL_INTERVAL   default 30
    BUS_HEARTBEAT_INT   default 300
    BUS_TTL_SECONDS     default 604800  (7 days)
    BUS_SEEN_CAP        default 10000   (recent-seen message_id cap)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Soft-import the local syntax bus. Bridge stays operational even if the
# in-process bus module is unavailable -- egress becomes a no-op in that
# case (publishes vanish silently because there are no local subscribers
# to fan out to; ingress still dispatches via direct callback on the
# fallback log).
try:
    parent = str(Path(__file__).resolve().parent.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from SyntaxIntelligence.event_bus import SyntaxEventBus  # type: ignore
    _HAS_LOCAL_BUS = True
except Exception:
    SyntaxEventBus = None  # type: ignore
    _HAS_LOCAL_BUS = False

try:
    parent = str(Path(__file__).resolve().parent.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from SyntaxIntelligence.agent_protocol import SwarmMessage, MessageType  # type: ignore
    _HAS_PROTOCOL = True
except Exception:
    SwarmMessage = None  # type: ignore
    MessageType = None  # type: ignore
    _HAS_PROTOCOL = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DEVICE_ID = "penguin"
DEFAULT_SYNC_ROOT = "~/bleaknarratives/sync_bus"
DEFAULT_POLL_INTERVAL = 30
DEFAULT_HEARTBEAT_INTERVAL = 300
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_SEEN_CAP = 10_000
DEFAULT_PRESENCE_TTL = 600  # peer considered stale after 10 minutes of silence


def _device_id() -> str:
    env = os.environ.get("BUS_DEVICE_ID", "").strip()
    if env:
        return env
    try:
        h = socket.gethostname().lower().strip()
        if h:
            return h
    except Exception:
        pass
    return DEFAULT_DEVICE_ID


def _sync_root() -> Path:
    return Path(os.environ.get("BUS_SYNC_ROOT", DEFAULT_SYNC_ROOT)).expanduser().resolve()


# ---------------------------------------------------------------------------
# Wire envelope
# ---------------------------------------------------------------------------

@dataclass
class BridgeEnvelope:
    """
    The wire form of every cross-device message. Kept separate from
    SwarmMessage so the bridge can operate without importing agent_protocol
    (e.g. for tests that don't want to bootstrap the swarm charter).
    """
    message_id: str
    sender_id: str
    channel: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    origin_device: str = ""
    ttl: int = DEFAULT_TTL_SECONDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "channel": self.channel,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "origin_device": self.origin_device,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BridgeEnvelope":
        return cls(
            message_id=str(d.get("message_id", "")),
            sender_id=str(d.get("sender_id", "")),
            channel=str(d.get("channel", "")),
            payload=dict(d.get("payload", {})),
            timestamp=float(d.get("timestamp", 0.0)),
            origin_device=str(d.get("origin_device", "")),
            ttl=int(d.get("ttl", DEFAULT_TTL_SECONDS)),
        )


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class SyncBridge:
    """
    Daemon-thread bridge that fans messages between a local Syntax event bus
    and a filesystem-backed message queue under ``<sync_root>/<device>/``.

    Idempotency: a local ``.seen_ids.json`` records the message_ids already
    dispatched, capped at ``seen_cap`` (oldest evicted first). Failure modes:
      * Seen file unreachable: treated as empty (bus remains consistent).
      * Author-device TTL expired: message file is deleted by its author.
        Bridge tolerates race (catch FileNotFoundError on dispatch + cleanup).
    """

    def __init__(
        self,
        sync_root: Optional[Path] = None,
        device_id: Optional[str] = None,
        local_bus: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        seen_cap: int = DEFAULT_SEEN_CAP,
        now: Optional[Callable[[], float]] = None,
    ):
        self._sync_root = sync_root or _sync_root()
        self._device_id = device_id or _device_id()
        self._local_bus = local_bus or (SyntaxEventBus() if _HAS_LOCAL_BUS else None)
        self._poll_interval = max(1.0, float(poll_interval))
        self._heartbeat_interval = max(5.0, float(heartbeat_interval))
        self._ttl = int(ttl_seconds)
        self._seen_cap = max(100, int(seen_cap))
        self._now = now or time.time

        self._my_dir = self._sync_root / self._device_id
        self._my_messages = self._my_dir / "messages"
        self._my_presence = self._my_dir / "presence.json"
        self._my_seen = self._my_dir / ".seen_ids.json"

        # Two distinct state bits:
        #   * ``_stop`` (threading.Event) is consumed by the worker threads
        #     to interrupt their interruptible sleeps. Start = clear.
        #   * ``_running`` (plain bool) reflects whether the bridge has
        #     been started (not stopped). Public via ``is_running``.
        # Previous versions conflated these: ``is_running`` derived from
        # ``not _stop.is_set()``, but a fresh threading.Event is clear,
        # so ``is_running`` was True at construction time and the
        # ``not self.is_running`` gate silently skipped first start().
        self._stop = threading.Event()
        self._running = False
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

        self._seen: List[str] = []
        self._subscribers: Dict[str, List[Callable[[BridgeEnvelope], None]]] = {}
        self._fallback_log: List[Dict[str, Any]] = []
        self._published_count = 0
        self._ingested_count = 0
        self._egress_filter: Callable[[Dict[str, Any]], bool] = self._default_egress_filter

    # ---- public API ------------------------------------------------------
    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def sync_root(self) -> Path:
        return self._sync_root

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def local_bus(self) -> Any:
        """Public accessor for the bridge's local SyntaxEventBus, if any.

        Useful when a second component (e.g. OutClawBus or orchestrator)
        needs to share the same bus instance so its publishes reach the
        bridge's egress subscribers. Returns None when no bus was wired
        (e.g. on TermuxNode where the bridge runs in fallback mode).
        """
        return self._local_bus

    def record_peer_presence(self, peer: Dict[str, Any]) -> None:
        """Allow tests / foreign code to inject peer presence records."""
        with self._lock:
            self._fallback_log.append({"kind": "peer_presence", "data": peer})

    def add_subscriber(self, channel: str, callback: Callable[[BridgeEnvelope], None]) -> None:
        """Subscribe to inbound envelopes (post-local-bus-dispatch, for tests)."""
        self._subscribers.setdefault(channel, []).append(callback)

    def set_egress_filter(self, fn: Callable[[Dict[str, Any]], bool]) -> None:
        """Override the default egress filter (default: emit if origin is local)."""
        self._egress_filter = fn

    def start(self) -> None:
        """Idempotent: register egress subscriptions + spawn worker threads."""
        if self._running:
            return  # already started -- silently no-op
        # Directory + state setup. Surface any mkdir/heartbeat failure via
        # stderr so a half-broken bridge doesn't quietly look live.
        try:
            self._my_dir.mkdir(parents=True, exist_ok=True)
            self._my_messages.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            sys.stderr.write(
                f"[sync_bridge] WARN: failed to create dirs under "
                f"{self._sync_root}: {exc!r}\n"
            )
            sys.stderr.flush()
            return
        self._load_seen()
        self._update_presence()
        # Subscribe to local Syntax bus (egress side). Subscribe to known
        # channels plus the sentinel ``bus.bridge`` channel callers can
        # publish to directly. Subscription errors are surfaced to stderr
        # so the operator (and tests) can spot a missing bus without
        # needing to call diagnostics().
        if self._local_bus is not None:
            subscription_succeeded = 0
            for channel in self._known_channels():
                try:
                    self._local_bus.subscribe(
                        f"bridge-{self._device_id}", channel, self._egress_cb
                    )
                    subscription_succeeded += 1
                except Exception as exc:
                    sys.stderr.write(
                        f"[sync_bridge] WARN: failed to subscribe to "
                        f"channel {channel!r}: {exc!r}\n"
                    )
                    sys.stderr.flush()
            try:
                self._local_bus.subscribe(
                    f"bridge-{self._device_id}", "bus.bridge", self._egress_cb
                )
                subscription_succeeded += 1
            except Exception as exc:
                sys.stderr.write(
                    f"[sync_bridge] WARN: failed to subscribe to "
                    f"channel 'bus.bridge': {exc!r}\n"
                )
                sys.stderr.flush()
            with self._lock:
                self._fallback_log.append(
                    {"kind": "subscribed", "count": subscription_succeeded}
                )

        # Spawn daemon workers.
        t_egress = threading.Thread(target=self._egress_loop, name=f"bridge-egress-{self._device_id}", daemon=True)
        t_poller = threading.Thread(target=self._poller_loop, name="bridge-poller", daemon=True)
        self._threads = [t_egress, t_poller]
        self._stop.clear()
        self._running = True
        t_egress.start()
        t_poller.start()

    def stop(self) -> None:
        """Idempotent: signal worker threads, join them, drop thread list."""
        if not self._running:
            return  # already stopped -- silently no-op
        self._stop.set()
        for t in self._threads:
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._threads = []
        self._running = False

    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            peers = []
            for peer_dir in sorted(self._sync_root.iterdir()):
                if peer_dir.name == self._device_id:
                    continue
                if peer_dir.name.startswith("."):
                    continue
                presence = peer_dir / "presence.json"
                if presence.exists():
                    try:
                        peers.append({
                            "device": peer_dir.name,
                            "presence": json.loads(presence.read_text()),
                        })
                    except Exception:
                        peers.append({"device": peer_dir.name, "presence": None})
                else:
                    peers.append({"device": peer_dir.name, "presence": None})
            return {
                "device_id": self._device_id,
                "sync_root": str(self._sync_root),
                "running": self.is_running,
                "published_count": self._published_count,
                "ingested_count": self._ingested_count,
                "seen_count": len(self._seen),
                "peers": peers,
            }

    def publish_local(self, channel: str, payload: Dict[str, Any], sender_id: Optional[str] = None) -> Optional[BridgeEnvelope]:
        """
        Egress entry point used in tests + by other modules. Writes an envelope
        to MY_DIR/messages/ only -- it does NOT forward to the local bus.

        Why: the local bus already has the bridge subscribed (via
        ``start()`` adding _egress_cb to known channels). Forwarding here
        would create a second envelope + a second publish cycle, both of
        which the egress filter would accept, producing duplicate disk
        writes and an unstable feedback loop. Direct local-bus publish is
        done by the caller (``OutClawBus._dispatch`` or tests).
        Returns the created envelope, or None if the egress filter rejected it.
        """
        envelope = BridgeEnvelope(
            message_id=_new_mid(),
            sender_id=sender_id or f"{self._device_id}.local",
            channel=channel,
            payload=payload,
            timestamp=self._now(),
            origin_device=self._device_id,
            ttl=self._ttl,
        )
        if not self._egress_filter(envelope.to_dict()):
            return None
        self._write_envelope(envelope)
        return envelope

    # ---- egress ----------------------------------------------------------
    def _egress_cb(self, agent_id: str, channel: str, data: Dict[str, Any]) -> None:
        """Local-bus inbound -> egress filter + disk write (loop guard)."""
        if isinstance(data, dict) and data.get("_bridge_origin") == "ingress":
            # This was an ingress-echo; do NOT re-egress.
            return
        envelope = dict_to_envelope(data, source_device=self._device_id, channel=channel)
        if not self._egress_filter(envelope.to_dict()):
            return
        self._write_envelope(envelope)
        self._published_count += 1

    def _default_egress_filter(self, envelope_dict: Dict[str, Any]) -> bool:
        """Default: emit if the origin_device matches this device."""
        origin = envelope_dict.get("origin_device") or envelope_dict.get("sender_id", "")
        if not origin:
            return True  # locally-emitted and not yet tagged -- emit
        # If the dict already shows a peer origin, do not re-egress.
        if origin.startswith(self._device_id + "."):
            return True
        if origin == self._device_id:
            return True
        # Anything else arrived via ingress path; skip.
        return False

    def _write_envelope(self, env: BridgeEnvelope) -> None:
        filename = f"{int(env.timestamp)}_{env.message_id}.json"
        path = self._my_messages / filename
        tmp_path = self._my_messages / f".{env.message_id}.tmp"
        try:
            tmp_path.write_text(json.dumps(env.to_dict(), sort_keys=True))
            os.replace(tmp_path, path)
        except Exception as exc:
            with self._lock:
                self._fallback_log.append({"kind": "write_fail", "env": env.to_dict(), "err": str(exc)})

    def _egress_loop(self) -> None:
        """Egress is event-driven (subscribed to local bus); loop runs as a
        heartbeat-only lookout. Keep body tiny."""
        last_heartbeat = 0.0
        while not self._stop.is_set():
            now = self._now()
            if now - last_heartbeat >= self._heartbeat_interval:
                self._update_presence()
                last_heartbeat = now
            self._stop.wait(self._heartbeat_interval)

    def _update_presence(self) -> None:
        body = {
            "device_id": self._device_id,
            "hostname": socket.gethostname(),
            "role": os.environ.get("BUS_ROLE", "TermuxNode"),
            "tier": os.environ.get("BUS_TIER", "OPERATIVE"),
            "last_seen": self._now(),
            "pid": os.getpid(),
            "published_count": self._published_count,
            "ingested_count": self._ingested_count,
            "system_load": _loadavg(),
        }
        tmp = self._my_dir / ".presence.json.tmp"
        try:
            tmp.write_text(json.dumps(body, sort_keys=True))
            os.replace(tmp, self._my_presence)
        except Exception:
            pass

    # ---- ingress ---------------------------------------------------------
    def _poller_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                with self._lock:
                    self._fallback_log.append({"kind": "poll_fail", "err": str(exc)})
            # Interruptible sleep.
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        self._update_presence()
        # 1. Process peer messages/.
        for peer_dir in sorted(self._sync_root.iterdir()):
            if peer_dir.name == self._device_id or peer_dir.name.startswith("."):
                continue
            peer_messages = peer_dir / "messages"
            if not peer_messages.is_dir():
                continue
            for file in sorted(peer_messages.iterdir()):
                if file.name.startswith("."):
                    continue
                if not file.name.endswith(".json"):
                    continue
                self._process_peer_file(file)
        # 2. Self-cleanup of expired messages.
        self._cleanup_local_expired()

    def _process_peer_file(self, file: Path) -> None:
        try:
            text = file.read_text()
        except (FileNotFoundError, IsADirectoryError):
            return
        try:
            data = json.loads(text)
        except Exception:
            return
        envelope = BridgeEnvelope.from_dict(data)
        if envelope.message_id in self._seen:
            return  # already dispatched; loop prevention
        if envelope.origin_device == self._device_id:
            return  # self-echo guard
        if not envelope.message_id:
            return
        # Mark seen BEFORE dispatch so re-entrant invocations remain idempotent.
        self._mark_seen(envelope.message_id)
        self._ingested_count += 1
        # Forward to local bus (tag as ingress so egress skips it).
        self._forward_to_local_bus(envelope, from_disk=True)
        # Notify any registered test subscribers.
        for callback in list(self._subscribers.get(envelope.channel, [])):
            try:
                callback(envelope)
            except Exception as exc:
                with self._lock:
                    self._fallback_log.append({"kind": "subscriber_error", "err": str(exc)})

    def _forward_to_local_bus(self, envelope: BridgeEnvelope, *, from_disk: bool) -> None:
        if self._local_bus is None:
            return
        payload_with_origin = dict(envelope.payload)
        payload_with_origin["_bridge_origin"] = "ingress" if from_disk else "egress"
        try:
            self._local_bus.publish(
                envelope.sender_id or f"{envelope.origin_device or 'peer'}.unknown",
                envelope.channel,
                payload_with_origin,
            )
        except Exception as exc:
            with self._lock:
                self._fallback_log.append({"kind": "local_publish_fail", "err": str(exc)})

    def _cleanup_local_expired(self) -> int:
        """Author-side TTL cleanup. Deletes any messages/* file past TTL."""
        if not self._my_messages.is_dir():
            return 0
        removed = 0
        now = self._now()
        for file in list(self._my_messages.iterdir()):
            if file.name.startswith(".") or not file.name.endswith(".json"):
                continue
            try:
                env = BridgeEnvelope.from_dict(json.loads(file.read_text()))
            except Exception:
                # Corrupt files: leave alone so debug can still inspect.
                continue
            if env.timestamp and (now - env.timestamp) > self._ttl:
                try:
                    file.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed

    # ---- seen_ids bookkeeping --------------------------------------------
    def _mark_seen(self, mid: str) -> None:
        if not mid:
            return
        with self._lock:
            if mid in self._seen:
                return
            self._seen.append(mid)
            if len(self._seen) > self._seen_cap:
                # Evict oldest (FIFO).
                excess = len(self._seen) - self._seen_cap
                del self._seen[:excess]
        self._save_seen()

    def _load_seen(self) -> None:
        if not self._my_seen.is_file():
            self._seen = []
            return
        try:
            data = json.loads(self._my_seen.read_text())
            self._seen = list(data.get("seen", [])) if isinstance(data, dict) else []
        except Exception:
            self._seen = []

    def _save_seen(self) -> None:
        if not self._my_seen.parent.is_dir():
            self._my_seen.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._my_seen.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"seen": self._seen[-self._seen_cap:]}))
            os.replace(tmp, self._my_seen)
        except Exception:
            pass

    # ---- known channels (best-effort egress subscription) ---------------
    def _known_channels(self) -> List[str]:
        return [
            "outclaw.findings",
            "outclaw.draft_blocked",
            "legal.oversight.request",
            "boardroom.guidance.request",
            "boardroom.guidance",
            "swarm.heartbeat",
            "bus.bridge",
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dict_to_envelope(
    data: Dict[str, Any],
    *,
    source_device: str,
    channel: Optional[str] = None,
) -> BridgeEnvelope:
    """
    Build a BridgeEnvelope from a local-bus payload (which may be raw or already wrapped).

    ``channel`` is the channel passed in the local-bus callback signature;
    it overrides ``data.get("channel")`` when the raw payload doesn't carry
    one. This is the common case for egress: an agent publishes via
    ``local_bus.publish(agent, channel, data)`` where data is the digest and
    channel is the route. Without this override the envelope would always
    fall back to ``"bus.bridge"`` and cross-device routing would break.
    """
    if isinstance(data, BridgeEnvelope):
        return data
    if not isinstance(data, dict):
        return BridgeEnvelope(
            message_id=_new_mid(),
            sender_id=f"{source_device}.unknown",
            channel=channel or "bus.bridge",
            payload={"_raw": str(data)},
            timestamp=time.time(),
            origin_device=source_device,
        )
    # Accept either a raw payload or an already-shaped envelope.
    if {"message_id", "channel", "payload"}.issubset(data.keys()):
        env = BridgeEnvelope.from_dict(data)
        if not env.origin_device:
            env.origin_device = source_device
        return env
    return BridgeEnvelope(
        message_id=_new_mid(),
        sender_id=f"{source_device}.unknown",
        channel=str(data.get("channel") or channel or "bus.bridge"),
        payload=dict(data),
        timestamp=float(data.get("timestamp") or time.time()),
        origin_device=source_device,
        ttl=int(data.get("ttl") or DEFAULT_TTL_SECONDS),
    )


def _new_mid() -> str:
    return f"{int(time.time() * 1000):013d}-{os.urandom(4).hex()}"


def _loadavg() -> List[float]:
    try:
        if hasattr(os, "getloadavg"):
            return list(os.getloadavg())
    except Exception:
        pass
    return [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# Module-mode script for manual smoke testing
# ---------------------------------------------------------------------------

_DEFAULT_BRIDGE: Optional[SyncBridge] = None
_DEFAULT_LOCK = threading.Lock()


def default_bridge() -> SyncBridge:
    global _DEFAULT_BRIDGE
    with _DEFAULT_LOCK:
        if _DEFAULT_BRIDGE is None:
            _DEFAULT_BRIDGE = SyncBridge()
        return _DEFAULT_BRIDGE


if __name__ == "__main__":
    print(f"Starting bridge on device={_device_id()} root={_sync_root()}")
    bridge = SyncBridge()
    bridge.start()
    try:
        # Smoke-test egress.
        env = bridge.publish_local(
            "bus.bridge",
            {"smoke": "hello", "device": bridge.device_id},
        )
        print(f"Smoke egress message_id={env.message_id if env else '(filtered)'}")
        time.sleep(2.0)
        print(json.dumps(bridge.diagnostics(), indent=2, default=str))
    finally:
        bridge.stop()
