#!/usr/bin/env python3
"""Syntax Legal Mind coordination layer.

This module is intentionally a coordinator, not a second audit engine. The
existing Legal Mind/OutClaw adapter publishes deterministic findings; this
layer fans a safe digest into the Syntax swarm and, when explicitly requested,
asks the Vertical AI Boardroom for oversight guidance.

Contracts:
    outclaw.findings -> legal.oversight.request
                       -> boardroom.guidance.request (explicit only)
                       -> boardroom.guidance

Only redacted digest fields cross these coordination boundaries. Raw legal
text, raw reports, and executable actions never enter a boardroom packet.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional


LEGAL_FINDINGS_CHANNEL = "outclaw.findings"
OVERSIGHT_REQUEST_CHANNEL = "legal.oversight.request"
BOARDROOM_REQUEST_CHANNEL = "boardroom.guidance.request"
BOARDROOM_GUIDANCE_CHANNEL = "boardroom.guidance"
SHARED_OVERSIGHT_EVENT = "syntax_legal_oversight_requested"
SHARED_GUIDANCE_EVENT = "syntax_boardroom_guidance"
MAX_PACKET_TEXT = 512

BoardroomRunner = Callable[[Dict[str, Any]], Dict[str, Any]]
SharedEmitter = Callable[[str, str, Dict[str, Any]], Any]
OversightProposer = Callable[[str, str, str, str], Any]


def _safe_int(value: Any, *, default: int = 0, maximum: int = 1_000_000) -> int:
    """Normalize untrusted metric values without letting callbacks raise."""
    try:
        if isinstance(value, bool):
            return default
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return default
        return min(int(number), maximum)
    except (TypeError, ValueError, OverflowError):
        return default


def _cap(value: Any, limit: int = MAX_PACKET_TEXT) -> str:
    """Convert a boardroom-facing value to bounded text."""
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _redact_boundary_text(value: Any, limit: int = MAX_PACKET_TEXT) -> str:
    """Redact PII before bounded text crosses a coordination boundary.

    OutClaw owns the canonical legal redaction rules. Keep this import lazy so
    Syntax remains usable when OutClaw is absent, and retain a conservative
    numeric fallback for malformed/offline direct publishers.
    """
    text = str(value or "")
    try:
        from OutClaw.outclaw_bus import redact_excerpt

        return redact_excerpt(text, max_chars=limit)
    except Exception:
        import re

        text = re.sub(
            r"\b\d{1,3}[:\-][A-Z]{2,4}[\-\.:]?\d{2,8}[A-Z]?\b",
            "REDACTED-NUMBER",
            text,
        )
        text = re.sub(r"\b\d[\d\-\.]{6,}\b", "REDACTED-NUMBER", text)
        text = re.sub(
            r"\b(?:[A-Z][a-z]{1,20}\s+)+[A-Z][a-z]{1,20}\b",
            "REDACTED-NAME",
            text,
        )
        return _cap(text, limit)


def _safe_digest(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only redacted, bounded fields suitable for cross-agent traffic."""
    if not isinstance(value, Mapping):
        value = {}
    findings = []
    for finding in value.get("high_findings", []) or []:
        if not isinstance(finding, Mapping):
            continue
        findings.append({
            "rule": _redact_boundary_text(finding.get("rule")),
            "citation_fp": _redact_boundary_text(finding.get("citation_fp"), 64),
            "excerpt": _redact_boundary_text(finding.get("excerpt")),
        })

    counts = value.get("severity_counts", {})
    if not isinstance(counts, Mapping):
        counts = {}
    safe_counts = {
        _redact_boundary_text(key, 64): _safe_int(number)
        for key, number in counts.items()
    }
    return {
        "audit_id": _redact_boundary_text(value.get("audit_id"), 64),
        "severity_counts": safe_counts,
        "safe_to_draft": value.get("safe_to_draft") is True,
        "high_count": _safe_int(value.get("high_count")),
        "high_findings": findings[:5],
    }


def _compact_boardroom_digest(
    safe: Mapping[str, Any],
) -> tuple[str, Dict[str, int], list[Dict[str, str]]]:
    """Build valid JSON plus matching fields within the wire text boundary."""
    raw_counts = safe.get("severity_counts", {})
    if not isinstance(raw_counts, Mapping):
        raw_counts = {}
    raw_findings = safe.get("high_findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []

    # Try progressively smaller representations. This handles Unicode JSON
    # escaping and hostile key counts without ever truncating invalid JSON.
    for count_limit in (4, 2, 1, 0):
        counts = {
            _redact_boundary_text(key, 24): _safe_int(value)
            for key, value in list(raw_counts.items())[:count_limit]
        }
        for finding_limit, text_limit in ((2, 72), (1, 32), (0, 0)):
            findings = [
                {
                    "rule": _redact_boundary_text(item.get("rule"), min(48, text_limit)),
                    "excerpt": _redact_boundary_text(item.get("excerpt"), text_limit),
                }
                for item in raw_findings[:finding_limit]
                if isinstance(item, Mapping)
            ]
            payload = {
                "severity_counts": counts,
                "safe_to_draft": safe.get("safe_to_draft") is True,
                "high_count": _safe_int(safe.get("high_count")),
                "high_findings": findings,
            }
            content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if len(content) <= MAX_PACKET_TEXT:
                return content, counts, findings

    # The scalar-only fallback is deliberately guaranteed to be tiny.
    payload = {
        "safe_to_draft": safe.get("safe_to_draft") is True,
        "high_count": _safe_int(safe.get("high_count")),
    }
    return json.dumps(payload, separators=(",", ":")), {}, []


def _load_callable(path: Path, function_name: str) -> Optional[Callable[..., Any]]:
    """Load one optional local Python callable without importing at module load."""
    if not path.exists():
        return None
    module_name = f"syntax_optional_{path.stem}_{uuid.uuid4().hex}"
    try:
        module_dir = str(path.parent)
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        candidate = getattr(module, function_name, None)
        return candidate if callable(candidate) else None
    except Exception:
        return None


def load_rootbase_shared_emitter() -> Optional[SharedEmitter]:
    """Return RootBase/shared_event_bus.emit when explicitly enabled.

    The shared JSONL bus is opt-in because it is a filesystem side effect and
    is separate from the in-process SyntaxEventBus. Payloads are already
    redacted before this function is called.
    """
    enabled = os.environ.get("SYNTAX_SHARED_EVENT_BUS", "").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    project_root = Path(__file__).resolve().parent.parent
    emit = _load_callable(project_root / "RootBase" / "shared_event_bus.py", "emit")
    return emit  # type: ignore[return-value]


def load_oversight_proposer() -> Optional[OversightProposer]:
    """Load the formal human-approval queue only when explicitly enabled."""
    enabled = os.environ.get("SYNTAX_OVERSIGHT_GATE", "").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    project_root = Path(__file__).resolve().parent.parent
    proposer = _load_callable(
        project_root / "RootBase" / "oversight_gate.py", "propose_action",
    )
    return proposer  # type: ignore[return-value]


def load_vertical_boardroom_runner() -> Optional[BoardroomRunner]:
    """Load the canonical Vertical AI Boardroom runner lazily.

    Importing the boardroom can load provider/network dependencies, so this
    is only called by an explicit guidance request, never at Syntax startup.
    """
    project_root = Path(__file__).resolve().parent.parent
    run_boardroom = _load_callable(
        project_root / "RootBase" / "Official-Vertical-AI-Boardroom" / "boardroom.py",
        "run_boardroom",
    )
    if run_boardroom is None:
        return None

    def runner(context: Dict[str, Any]) -> Dict[str, Any]:
        return run_boardroom(context, rounds=1)

    return runner


class SyntaxLegalCoordinator:
    """Route Legal Mind findings into swarm and boardroom oversight channels."""

    def __init__(
        self,
        event_bus: Any,
        *,
        sync_bridge: Any = None,
        shared_emit: Optional[SharedEmitter] = None,
        boardroom_runner: Optional[BoardroomRunner] = None,
        oversight_proposer: Optional[OversightProposer] = None,
        agent_id: str = "syntax-legal-coordinator",
    ) -> None:
        self.event_bus = event_bus
        self.sync_bridge = sync_bridge
        self.shared_emit = shared_emit
        self.boardroom_runner = boardroom_runner
        self.oversight_proposer = (
            oversight_proposer
            if oversight_proposer is not None
            else load_oversight_proposer()
        )
        self.agent_id = agent_id
        self._attached = False
        self._seen_audits: set[str] = set()
        self._lock = threading.Lock()
        self.last_oversight: Optional[Dict[str, Any]] = None
        self.last_guidance: Optional[Dict[str, Any]] = None

    def attach(self) -> None:
        """Attach idempotently to the existing Syntax event bus."""
        if self._attached:
            return
        self.event_bus.subscribe(
            self.agent_id, LEGAL_FINDINGS_CHANNEL, self._on_findings,
        )
        self._attached = True

    def detach(self) -> None:
        """Detach without changing any swarm governance state."""
        if not self._attached:
            return
        self.event_bus.unsubscribe(self.agent_id, LEGAL_FINDINGS_CHANNEL)
        self._attached = False

    def _on_findings(
        self, _subscriber_id: str, _channel: str, payload: Mapping[str, Any],
    ) -> None:
        self.handle_findings(payload)

    def handle_findings(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Publish a redacted oversight request for one findings digest."""
        digest = _safe_digest(payload)
        audit_id = digest["audit_id"] or f"anonymous-{uuid.uuid4().hex[:8]}"
        with self._lock:
            if audit_id in self._seen_audits:
                return self.last_oversight or digest
            self._seen_audits.add(audit_id)

        packet = {
            "request_id": f"legal-oversight-{uuid.uuid4().hex[:10]}",
            "audit_id": audit_id,
            "source": "syntax-legal-mind",
            "digest": digest,
            "oversight_required": bool(
                digest["high_count"] or not digest["safe_to_draft"]
            ),
            "human_approval_required": True,
            "execution_allowed": False,
        }
        if packet["oversight_required"] and self.oversight_proposer is not None:
            try:
                self.oversight_proposer(
                    self.agent_id,
                    "legal_review",
                    audit_id,
                    "Human review required before drafting or relying on this audit.",
                )
                packet["formal_oversight_queued"] = True
            except Exception:
                packet["formal_oversight_queued"] = False
        else:
            packet["formal_oversight_queued"] = False

        self.last_oversight = packet
        self.event_bus.publish(self.agent_id, OVERSIGHT_REQUEST_CHANNEL, packet)
        self._publish_cross_device(SHARED_OVERSIGHT_EVENT, packet)
        return packet

    def request_guidance(
        self,
        digest_or_packet: Mapping[str, Any],
        *,
        boardroom_runner: Optional[BoardroomRunner] = None,
    ) -> Dict[str, Any]:
        """Ask Vertical AI for guidance using only a redacted legal digest."""
        packet = digest_or_packet if isinstance(digest_or_packet, Mapping) else {}
        digest = packet.get("digest", packet)
        safe = _safe_digest(digest if isinstance(digest, Mapping) else {})
        request_id = str(packet.get("request_id", "")) or f"legal-oversight-{uuid.uuid4().hex[:10]}"
        boardroom_content, boardroom_counts, boardroom_findings = _compact_boardroom_digest(safe)
        context = {
            "type": "legal_oversight",
            "label": f"Legal audit {safe.get('audit_id') or 'unknown'}",
            "session_id": request_id,
            "raw": None,
            "data": {
                # The canonical Boardroom compressor reads data.content.
                # Keep the serialized content bounded and digest-only.
                "content": boardroom_content,
                "severity_counts": boardroom_counts,
                "safe_to_draft": safe["safe_to_draft"],
                "high_count": safe["high_count"],
                "high_findings": boardroom_findings,
                "guidance_request": (
                    "Assess legal-audit risk, next review step, and conditions "
                    "for human approval. Do not execute or approve actions."
                ),
            },
        }
        request = {
            "request_id": request_id,
            "source": "syntax-legal-mind",
            "context": context,
            "human_approval_required": True,
            "execution_allowed": False,
        }
        self.event_bus.publish(self.agent_id, BOARDROOM_REQUEST_CHANNEL, request)
        self._publish_cross_device("syntax_boardroom_guidance_requested", request)

        runner = boardroom_runner or self.boardroom_runner
        if runner is None:
            runner = load_vertical_boardroom_runner()
        if runner is None:
            return self._publish_guidance({
                "request_id": request_id,
                "status": "unavailable",
                "verdict": "unknown",
                "guidance": "Vertical AI Boardroom runner unavailable; human review required.",
            })

        try:
            result = runner(context)
            synthesis = result.get("synthesis", {}) if isinstance(result, Mapping) else {}
            guidance = {
                "request_id": request_id,
                "status": "completed",
                "verdict": _redact_boundary_text(synthesis.get("verdict", "unknown"), 64),
                "guidance": _redact_boundary_text(synthesis.get("chairman_statement", "")),
                "primary_risk": _redact_boundary_text(synthesis.get("primary_risk", "")),
                "primary_opportunity": _redact_boundary_text(synthesis.get("primary_opportunity", "")),
            }
        except Exception as exc:
            guidance = {
                "request_id": request_id,
                "status": "failed",
                "verdict": "unknown",
                "guidance": f"Boardroom guidance failed: {type(exc).__name__}",
            }
        return self._publish_guidance(guidance)

    def _publish_guidance(self, guidance: Dict[str, Any]) -> Dict[str, Any]:
        guidance = {
            **guidance,
            "source": "vertical-ai-boardroom",
            "human_approval_required": True,
            "execution_allowed": False,
        }
        self.last_guidance = guidance
        self.event_bus.publish(self.agent_id, BOARDROOM_GUIDANCE_CHANNEL, guidance)
        self._publish_cross_device(SHARED_GUIDANCE_EVENT, guidance)
        return guidance

    def _publish_cross_device(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Best-effort cross-device delivery; local bus is always authoritative."""
        if self.shared_emit is not None:
            try:
                self.shared_emit(self.agent_id, event_type, payload)
            except Exception:
                pass
        if self.sync_bridge is not None:
            try:
                if event_type == SHARED_OVERSIGHT_EVENT:
                    channel = OVERSIGHT_REQUEST_CHANNEL
                elif event_type == "syntax_boardroom_guidance_requested":
                    channel = BOARDROOM_REQUEST_CHANNEL
                else:
                    channel = BOARDROOM_GUIDANCE_CHANNEL
                self.sync_bridge.publish_local(channel, dict(payload), sender_id=self.agent_id)
            except Exception:
                pass
