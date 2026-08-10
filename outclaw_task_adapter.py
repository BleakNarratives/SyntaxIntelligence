#!/usr/bin/env python3
"""
Syntax -> OutClaw task adapter.

This module owns the domain boundary between Syntax task offers and the
canonical OutClaw audit pipeline.  It deliberately does not change either
system's event bus or bridge ownership:

    task.offered -> audit_text() -> OutClawBus.publish_findings()

The adapter is synchronous because SyntaxEventBus callbacks are synchronous;
callers that need background execution should place the adapter behind their
own worker boundary rather than changing the task protocol here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Mapping, Optional

from SyntaxIntelligence.hardened_engine import HardenedSwarm


LEGAL_AUDIT_CAPABILITY = "legal_audit"
AUDIT_OPERATION = "outclaw.audit_text"
FINDINGS_CHANNEL = "outclaw.findings"
DEFAULT_MAX_TEXT_CHARS = 64 * 1024
DEFAULT_SEEN_CAP = 1024


@dataclass
class AdapterResult:
    """Outcome of handling one task-offer event."""

    status: str
    task_id: Optional[str] = None
    reason: Optional[str] = None
    digest: Optional[Dict[str, Any]] = None
    lifecycle: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "task_id": self.task_id,
            "reason": self.reason,
            "digest": self.digest,
            "lifecycle": self.lifecycle,
        }


class OutClawTaskAdapter:
    """Route eligible Syntax task offers into the deterministic OutClaw audit.

    The caller explicitly registers ``agent_id`` with the swarm and grants it
    the ``legal_audit`` capability.  The adapter only subscribes and performs
    task work; it does not silently alter governance state.
    """

    def __init__(
        self,
        swarm: HardenedSwarm,
        outclaw_bus: Any,
        *,
        audit_fn: Optional[Callable[..., Any]] = None,
        agent_id: str = "outclaw-auditor",
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
        seen_cap: int = DEFAULT_SEEN_CAP,
    ) -> None:
        if agent_id not in swarm.agents:
            raise ValueError(
                f"Adapter agent '{agent_id}' must be registered before attaching."
            )
        if max_text_chars <= 0:
            raise ValueError("max_text_chars must be positive")
        if seen_cap <= 0:
            raise ValueError("seen_cap must be positive")

        if audit_fn is None:
            from OutClaw.outclaw_unified import audit_text  # via OutClaw/__init__ shim

            audit_fn = audit_text

        self.swarm = swarm
        self.outclaw_bus = outclaw_bus
        self.audit_fn = audit_fn
        self.agent_id = agent_id
        self.max_text_chars = max_text_chars
        self._seen_cap = seen_cap
        self._seen: Deque[str] = deque(maxlen=seen_cap)
        self._seen_set: set[str] = set()
        self._in_flight: set[str] = set()
        self._attached = False
        self.last_result: Optional[AdapterResult] = None

    def attach(self) -> None:
        """Subscribe once to the Syntax task-offer channel."""
        if self._attached:
            return
        self.swarm.event_bus.subscribe(
            self.agent_id,
            "task.offered",
            self._on_task_offered,
        )
        self._attached = True

    def detach(self) -> None:
        """Remove the task subscription without changing swarm state."""
        if not self._attached:
            return
        self.swarm.event_bus.unsubscribe(self.agent_id, "task.offered")
        self._attached = False

    def _on_task_offered(
        self,
        _subscriber_id: str,
        _channel: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.last_result = self.handle_offer(payload)

    def handle_offer(self, payload: Mapping[str, Any]) -> AdapterResult:
        """Handle one offer payload and return a deterministic adapter result."""
        if not isinstance(payload, Mapping):
            return AdapterResult(status="rejected", reason="task_payload_not_mapping")

        task_id = payload.get("task_id")
        context = payload.get("context")
        target = payload.get("target")

        if target and target != self.agent_id:
            return AdapterResult(status="ignored", task_id=self._task_id(task_id), reason="not_targeted")
        if not isinstance(task_id, str) or not task_id:
            return AdapterResult(status="rejected", reason="missing_task_id")
        if not isinstance(context, Mapping):
            return self._request_info(task_id, "context must be a mapping")

        capabilities = payload.get("required_capabilities", [])
        if not isinstance(capabilities, list) or not all(
            isinstance(capability, str) for capability in capabilities
        ):
            return self._reject(task_id, "required_capabilities_must_be_string_list")
        if LEGAL_AUDIT_CAPABILITY not in capabilities:
            return AdapterResult(status="ignored", task_id=task_id, reason="capability_not_required")

        operation = context.get("operation")
        if operation is None:
            return self._request_info(task_id, "context.operation is required")
        if operation != AUDIT_OPERATION:
            return self._reject(task_id, "unsupported_operation")

        identity = self._identity(task_id, context)
        if identity is not None and self._was_seen(identity):
            return AdapterResult(status="duplicate", task_id=task_id)

        text = context.get("audit_text")
        if not isinstance(text, str) or not text.strip():
            return self._request_info(task_id, "context.audit_text must be non-empty text")
        if len(text) > self.max_text_chars:
            return self._reject(task_id, "audit_text_too_large")

        use_llm = context.get("use_llm", False)
        if not isinstance(use_llm, bool):
            return self._request_info(task_id, "context.use_llm must be boolean")
        if context.get("result_channel", FINDINGS_CHANNEL) != FINDINGS_CHANNEL:
            return self._reject(task_id, "unsupported_result_channel")

        accepted = self.swarm.respond_to_task(
            self.agent_id,
            task_id,
            "accept",
            reason="OutClaw legal audit capability available",
        )
        if accepted.get("status") != "accepted":
            return AdapterResult(
                status="not_accepted",
                task_id=task_id,
                reason=accepted.get("reason", accepted.get("status")),
                lifecycle=accepted,
            )
        if identity is not None:
            self._in_flight.add(identity)

        published = False
        try:
            report = self.audit_fn(text, use_llm=use_llm)
            digest = self.outclaw_bus.publish_findings(report.to_dict())
            published = True
            completion = self.swarm.complete_task(
                task_id,
                self.agent_id,
                self._safe_completion(digest),
            )
            if completion.get("status") != "completed":
                # Publication already succeeded. Release the assignment
                # without marking it critical, hold the identity closed so
                # a retry cannot publish the same digest twice, and surface
                # the lifecycle inconsistency explicitly.
                recovery = self.swarm.fail_task(
                    task_id,
                    self.agent_id,
                    reason="completion_failed_after_publication",
                    critical=False,
                )
                if identity is not None:
                    self._finish_identity(identity)
                return AdapterResult(
                    status="completion_failed",
                    task_id=task_id,
                    digest=digest,
                    reason="task_completion_failed_after_publication",
                    lifecycle={
                        "accepted": accepted,
                        "completed": completion,
                        "recovery": recovery,
                    },
                )
            if identity is not None:
                self._finish_identity(identity)
            return AdapterResult(
                status="completed",
                task_id=task_id,
                digest=digest,
                lifecycle={"accepted": accepted, "completed": completion},
            )
        except Exception as exc:  # adapter boundary: task failure must not escape the bus
            if identity is not None:
                if published:
                    # Publication succeeded, so close the identity even if
                    # completion itself raised; a retry must not republish.
                    self._finish_identity(identity)
                else:
                    self._in_flight.discard(identity)
            failure = self.swarm.fail_task(
                task_id,
                self.agent_id,
                reason=(
                    "completion_failed_after_publication"
                    if published
                    else "outclaw_audit_execution_failed"
                ),
                critical=False,
            )
            return AdapterResult(
                status="completion_failed" if published else "failed",
                task_id=task_id,
                reason=(
                    "task_completion_raised_after_publication"
                    if published
                    else type(exc).__name__
                ),
                lifecycle={"accepted": accepted, "failed": failure},
            )

    def _request_info(self, task_id: str, reason: str) -> AdapterResult:
        lifecycle = self.swarm.respond_to_task(
            self.agent_id,
            task_id,
            "request_info",
            reason=reason,
            requested_info=reason,
        )
        return AdapterResult(
            status="request_info",
            task_id=task_id,
            reason=reason,
            lifecycle=lifecycle,
        )

    def _reject(self, task_id: str, reason: str) -> AdapterResult:
        lifecycle = self.swarm.respond_to_task(
            self.agent_id,
            task_id,
            "reject",
            reason=reason,
        )
        return AdapterResult(
            status="rejected",
            task_id=task_id,
            reason=reason,
            lifecycle=lifecycle,
        )

    @staticmethod
    def _task_id(value: Any) -> Optional[str]:
        return value if isinstance(value, str) else None

    @staticmethod
    def _identity(task_id: Any, context: Any) -> Optional[str]:
        if isinstance(context, Mapping) and isinstance(context.get("idempotency_key"), str):
            return context["idempotency_key"]
        return task_id if isinstance(task_id, str) else None

    def _was_seen(self, identity: str) -> bool:
        return identity in self._seen_set or identity in self._in_flight

    def _finish_identity(self, identity: str) -> None:
        self._in_flight.discard(identity)
        if identity in self._seen_set:
            return
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(identity)
        self._seen_set.add(identity)

    @staticmethod
    def _safe_completion(digest: Mapping[str, Any]) -> Dict[str, Any]:
        """Keep raw legal text/findings out of Syntax task completion state."""
        return {
            "operation": AUDIT_OPERATION,
            "result_channel": FINDINGS_CHANNEL,
            "audit_id": digest.get("audit_id"),
            "severity_counts": digest.get("severity_counts", {}),
            "safe_to_draft": digest.get("safe_to_draft", False),
            "high_count": digest.get("high_count", 0),
        }
