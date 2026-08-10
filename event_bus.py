#!/usr/bin/env python3
"""
Syntax Intelligence — Event Bus
Inter-agent communication system with pub/sub hooks and triggers.

Every agent can emit events and subscribe to channels. The event bus routes
messages between agents, records metadata-only telemetry, and supports
cron-style scheduled triggers.

"""

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable


class SyntaxEventBus:
    """
    Publish/subscribe event bus for inter-agent communication.

    Usage:
        bus = SyntaxEventBus()

        # Agent subscribes to a channel
        bus.subscribe("agent-01", "code.alerts", my_callback)

        # Agent publishes to a channel
        bus.publish("agent-02", "code.alerts", {"severity": "high", "msg": "overflow"})

        # Broadcast to all agents
        bus.broadcast("swarm.heartbeat", {"pulse": 42})
    """

    def __init__(self):
        self._subscriptions: Dict[str, Dict[str, Callable]] = {}  # channel -> agent_id -> callback
        self._message_log: List[Dict] = []
        self._lock = threading.Lock()
        self._message_count = 0

    def subscribe(self, agent_id: str, channel: str, callback: Callable):
        """Subscribe an agent to a channel. Callback receives (agent_id, channel, data)."""
        with self._lock:
            if channel not in self._subscriptions:
                self._subscriptions[channel] = {}
            self._subscriptions[channel][agent_id] = callback
            self._append_log_locked(
                "subscribe", agent_id, channel, f"Subscribed to {channel}"
            )

    def unsubscribe(self, agent_id: str, channel: str):
        """Unsubscribe an agent from a channel."""
        with self._lock:
            if channel in self._subscriptions:
                self._subscriptions[channel].pop(agent_id, None)
                self._append_log_locked(
                    "unsubscribe", agent_id, channel, f"Unsubscribed from {channel}"
                )

    def publish(self, sender_id: str, channel: str, data: Dict[str, Any]):
        """Publish a message to a channel. All subscribers receive it."""
        with self._lock:
            self._message_count += 1
            subs = self._subscriptions.get(channel, {}).copy()
            self._append_log_locked(
                "publish", sender_id, channel, "[REDACTED]",
                msg_id=self._message_count,
            )

        for agent_id, callback in subs.items():
            if agent_id != sender_id:  # Don't echo to self
                try:
                    callback(agent_id, channel, data)
                except Exception as e:
                    with self._lock:
                        self._append_log_locked(
                            "error", agent_id, channel, type(e).__name__
                        )

    def broadcast(self, sender_id: str, channel: str, data: Dict[str, Any]):
        """Broadcast to all subscribers across all channels (swarm-wide)."""
        with self._lock:
            self._message_count += 1
            all_subs = {}
            for ch, agents in self._subscriptions.items():
                all_subs.update(agents)
            self._append_log_locked(
                "broadcast", sender_id, channel, "[REDACTED]",
                msg_id=self._message_count,
            )

        for agent_id, callback in all_subs.items():
            if agent_id != sender_id:
                try:
                    callback(agent_id, channel, data)
                except Exception as e:
                    with self._lock:
                        self._append_log_locked(
                            "error", agent_id, channel, type(e).__name__
                        )

    def get_message_log(self, n: int = 50) -> List[Dict]:
        """Get recent message log entries."""
        with self._lock:
            return [dict(entry) for entry in self._message_log[-n:]]

    def get_stats(self) -> Dict[str, Any]:
        """Get event bus statistics."""
        with self._lock:
            channels = {}
            for ch, agents in self._subscriptions.items():
                channels[ch] = len(agents)
            return {
                "total_messages": self._message_count,
                "active_channels": len(self._subscriptions),
                "channel_subscribers": channels,
            }

    def _log(self, msg_type: str, agent_id: str, channel: str,
             detail: str, msg_id: int = 0):
        self._message_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": msg_type,
            "agent_id": agent_id,
            "channel": channel,
            "detail": detail,
            "msg_id": msg_id,
        })

    def _append_log_locked(self, msg_type: str, agent_id: str, channel: str,
                           detail: str, msg_id: int = 0):
        """Append one flat telemetry entry; caller must hold ``self._lock``."""
        self._message_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": msg_type,
            "agent_id": agent_id,
            "channel": channel,
            "detail": detail,
            "msg_id": msg_id,
        })


class SyntaxCronScheduler:
    """
    Lightweight cron-style scheduler for recurring tasks.
    Hooks into the SyntaxEventBus to trigger agent actions on schedule.

    Usage:
        cron = SyntaxCronScheduler(event_bus)

        # Run every 60 seconds
        cron.schedule("morning_protocol", 60, callback_fn)

        # Run once after 10 seconds
        cron.schedule_once("one_shot", 10, callback_fn)
    """

    def __init__(self, event_bus: SyntaxEventBus):
        self.bus = event_bus
        self._jobs: Dict[str, Dict] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def schedule(self, job_id: str, interval_seconds: float, callback: Callable):
        """Schedule a recurring job."""
        with self._lock:
            self._jobs[job_id] = {
                "interval": interval_seconds,
                "callback": callback,
                "last_run": 0,
                "run_count": 0,
            }
        with self.bus._lock:
            self.bus._append_log_locked(
                "schedule", "cron", job_id,
                f"Scheduled every {interval_seconds}s",
            )

    def schedule_once(self, job_id: str, delay_seconds: float, callback: Callable):
        """Schedule a one-shot job after a delay."""
        def one_shot():
            callback()
            with self._lock:
                self._jobs.pop(f"once_{job_id}", None)

        with self._lock:
            self._jobs[f"once_{job_id}"] = {
                "interval": delay_seconds,
                "callback": one_shot,
                "last_run": time.time() - delay_seconds + 0.1,  # Fire almost immediately
                "run_count": 0,
                "one_shot": True,
            }

    def start(self):
        """Start the scheduler loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the scheduler."""
        self._running = False

    def _loop(self):
        while self._running:
            now = time.time()
            with self._lock:
                for job_id, job in list(self._jobs.items()):
                    if now - job["last_run"] >= job["interval"]:
                        try:
                            job["callback"]()
                            job["last_run"] = now
                            job["run_count"] += 1
                        except Exception as e:
                            with self.bus._lock:
                                self.bus._append_log_locked(
                                    "error", "cron", job_id, type(e).__name__
                                )

            time.sleep(1)

    def get_jobs(self) -> Dict[str, Any]:
        """Get all scheduled jobs with status."""
        with self._lock:
            return {
                job_id: {
                    "interval": j["interval"],
                    "run_count": j["run_count"],
                    "last_run_ago": time.time() - j["last_run"] if j["last_run"] else None,
                }
                for job_id, j in self._jobs.items()
            }
