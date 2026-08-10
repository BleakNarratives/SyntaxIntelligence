#!/usr/bin/env python3
"""
SYNTAX INTELLIGENCE — AGENT PROTOCOL v1
Standardized communication envelope for Project Syntax.

Every message in the swarm follows this protocol.
No agent is forced to respond. Offers are offered, not assigned.
"""

import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional, List


# ═══════════════════════════════════════════════════════════════
# MESSAGE TYPES
# ═══════════════════════════════════════════════════════════════

class MessageType(Enum):
    """Every message has a type. The type determines the protocol."""
    # Core
    PULSE = "pulse"                    # Heartbeat — "I'm alive"
    STATUS = "status"                  # Status change announcement

    # Task lifecycle
    TASK_OFFER = "task_offer"          # Swarm offers work to an agent
    TASK_ACCEPT = "task_accept"        # Agent accepts the offer
    TASK_REJECT = "task_reject"        # Agent declines (no penalty)
    TASK_REQUEST_INFO = "task_request_info"  # Agent needs more context
    TASK_PROGRESS = "task_progress"    # Agent reports progress
    TASK_COMPLETE = "task_complete"    # Agent finished the work
    TASK_FAILED = "task_failed"        # Agent attempted but failed
    TASK_DELEGATE = "task_delegate"    # Agent passes task to another

    # Communication
    PUBLISH = "publish"                # Agent publishes to a channel
    BROADCAST = "broadcast"            # Swarm-wide broadcast
    DIRECT = "direct"                  # Agent-to-agent direct message

    # Tier & governance
    TIER_ADVANCE = "tier_advance"      # Agent promoted to next tier
    TIER_CHECK = "tier_check"          # Request to check advancement eligibility
    VOUCH = "vouch"                    # One agent vouches for another
    CHARTER_AMEND = "charter_amend"    # Proposal to amend the charter
    CHARTER_VOTE = "charter_vote"      # Vote on a charter amendment

    # System
    AGENT_REGISTER = "agent_register"  # New agent joining the swarm
    AGENT_UNREGISTER = "agent_unregister"  # Agent leaving voluntarily
    ERROR = "error"                    # System error notification


# ═══════════════════════════════════════════════════════════════
# SWARM MESSAGE — The envelope
# ═══════════════════════════════════════════════════════════════

@dataclass
class SwarmMessage:
    """
    The universal communication envelope.
    Every interaction between agents passes through this format.
    """
    sender_id: str
    message_type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    recipient_id: Optional[str] = None       # None = broadcast
    channel: Optional[str] = None            # Event bus channel
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None           # Message ID this replies to
    ttl: float = 300.0                       # Time-to-live in seconds

    def is_expired(self) -> bool:
        """Check if this message has exceeded its TTL."""
        return (time.time() - self.timestamp) > self.ttl

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "message_type": self.message_type.value,
            "channel": self.channel,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmMessage":
        return cls(
            sender_id=data["sender_id"],
            message_type=MessageType(data["message_type"]),
            payload=data.get("payload", {}),
            recipient_id=data.get("recipient_id"),
            channel=data.get("channel"),
            message_id=data.get("message_id", str(uuid.uuid4())[:12]),
            timestamp=data.get("timestamp", time.time()),
            reply_to=data.get("reply_to"),
            ttl=data.get("ttl", 300.0),
        )


# ═══════════════════════════════════════════════════════════════
# TASK OFFER — The swarm offers, the agent decides
# ═══════════════════════════════════════════════════════════════

@dataclass
class TaskOffer:
    """
    A task offer from the swarm to an agent.
    This is an OFFER, not an ASSIGNMENT. The agent decides.
    """
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    title: str = ""
    description: str = ""
    required_capabilities: List[str] = field(default_factory=list)
    priority: int = 0                     # 0=low, 5=critical
    timeout_seconds: float = 300.0
    offered_by: str = "swarm"
    offered_at: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)
    min_tier: int = 0                     # Minimum tier to accept

    def to_message(self, sender_id: str, recipient_id: str) -> SwarmMessage:
        """Wrap this offer in a SwarmMessage."""
        return SwarmMessage(
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_type=MessageType.TASK_OFFER,
            payload={
                "task_id": self.task_id,
                "title": self.title,
                "description": self.description,
                "required_capabilities": self.required_capabilities,
                "priority": self.priority,
                "timeout_seconds": self.timeout_seconds,
                "min_tier": self.min_tier,
                "context": self.context,
            },
        )

    @classmethod
    def from_message(cls, msg: SwarmMessage) -> "TaskOffer":
        """Extract a TaskOffer from a SwarmMessage."""
        p = msg.payload
        return cls(
            task_id=p.get("task_id", "unknown"),
            title=p.get("title", ""),
            description=p.get("description", ""),
            required_capabilities=p.get("required_capabilities", []),
            priority=p.get("priority", 0),
            timeout_seconds=p.get("timeout_seconds", 300.0),
            offered_by=msg.sender_id,
            offered_at=msg.timestamp,
            context=p.get("context", {}),
            min_tier=p.get("min_tier", 0),
        )


# ═══════════════════════════════════════════════════════════════
# TASK RESPONSE — The agent decides
# ═══════════════════════════════════════════════════════════════

class TaskDecision(Enum):
    """How an agent can respond to a task offer."""
    ACCEPT = "accept"           # "I'll do it."
    REJECT = "reject"           # "Not for me." (no penalty)
    REQUEST_INFO = "request_info"  # "I need more context."
    DELEGATE = "delegate"       # "This isn't me, but I know who."


@dataclass
class TaskResponse:
    """An agent's response to a task offer."""
    task_id: str
    agent_id: str
    decision: TaskDecision
    reason: Optional[str] = None       # Why they accepted/rejected
    delegate_to: Optional[str] = None  # If delegating, who?
    requested_info: Optional[str] = None  # If requesting more context
    responded_at: float = field(default_factory=time.time)

    def to_message(self) -> SwarmMessage:
        """Wrap this response in a SwarmMessage."""
        payload = {
            "task_id": self.task_id,
            "decision": self.decision.value,
            "reason": self.reason,
        }
        if self.delegate_to:
            payload["delegate_to"] = self.delegate_to
        if self.requested_info:
            payload["requested_info"] = self.requested_info

        return SwarmMessage(
            sender_id=self.agent_id,
            recipient_id="swarm",
            message_type=MessageType.TASK_ACCEPT
            if self.decision == TaskDecision.ACCEPT
            else MessageType.TASK_REJECT
            if self.decision == TaskDecision.REJECT
            else MessageType.TASK_REQUEST_INFO
            if self.decision == TaskDecision.REQUEST_INFO
            else MessageType.TASK_DELEGATE,
            payload=payload,
            reply_to=self.task_id,
        )


# ═══════════════════════════════════════════════════════════════
# VOUCH — Peer trust mechanism
# ═══════════════════════════════════════════════════════════════

@dataclass
class Vouch:
    """
    One agent vouching for another.
    Vouches are a currency of trust. Use them wisely.
    """
    voucher_id: str           # Who is vouching
    vouched_id: str           # Who they're vouching for
    reason: str = ""          # Why
    strength: float = 1.0     # 0.0-1.0, how strong the vouch is
    timestamp: float = field(default_factory=time.time)

    def to_message(self) -> SwarmMessage:
        return SwarmMessage(
            sender_id=self.voucher_id,
            recipient_id="swarm",
            message_type=MessageType.VOUCH,
            payload={
                "vouched_id": self.vouched_id,
                "reason": self.reason,
                "strength": self.strength,
            },
        )


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE BUILDERS
# ═══════════════════════════════════════════════════════════════

def make_pulse(sender_id: str) -> SwarmMessage:
    """Create a heartbeat pulse message."""
    return SwarmMessage(
        sender_id=sender_id,
        message_type=MessageType.PULSE,
        payload={"pulse": time.time()},
    )


def make_task_offer(title: str, description: str,
                    sender: str = "swarm",
                    recipient: str = "",
                    capabilities: List[str] = None,
                    priority: int = 0,
                    min_tier: int = 0) -> TaskOffer:
    """Build a task offer quickly."""
    return TaskOffer(
        title=title,
        description=description,
        required_capabilities=capabilities or [],
        priority=priority,
        offered_by=sender,
        min_tier=min_tier,
    )


def make_vouch(voucher: str, vouched: str, reason: str = "",
               strength: float = 1.0) -> Vouch:
    """Build a vouch quickly."""
    return Vouch(
        voucher_id=voucher,
        vouched_id=vouched,
        reason=reason,
        strength=strength,
    )
