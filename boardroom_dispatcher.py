#!/usr/bin/env python3
"""Deterministic, offline Boardroom dispatcher for Syntax Intelligence.

The Boardroom turns structured scout findings into a reviewable decision packet.
It deliberately performs no network calls and never executes the proposed action;
the Brown Hat produces an action item for a human or higher-level workflow to
review and authorize.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from SyntaxIntelligence.dispatchers import BaseDispatcher, DispatcherResult, DispatcherType


_MAX_TEXT = 240
_MAX_OPTIONS = 8
_MAX_ITEMS = 100

_SEVERITY_WEIGHT = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


class BoardroomDispatcher(BaseDispatcher):
    """Chairman + Devil's Advocate + Brown Hat decision review."""

    dispatcher_type = DispatcherType.BOARDROOM
    capabilities = [
        "strategic_decision",
        "risk_review",
        "execution_planning",
        "boardroom",
    ]
    min_tier = 2

    def execute(
        self,
        agent_id: str,
        task_id: str,
        task_data: Dict[str, Any],
    ) -> DispatcherResult:
        decision = self._text(task_data.get("decision", ""))
        context = self._text(task_data.get("context", ""))
        findings = self._findings(task_data)
        bottlenecks = self._bottlenecks(task_data)
        options = self._options(task_data)

        if not decision and not findings and not bottlenecks:
            return DispatcherResult(
                dispatcher="boardroom",
                task_id=task_id,
                status="failed",
                summary="Boardroom needs a decision, finding, or bottleneck to review.",
                confidence=1.0,
            )

        focus = self._select_focus(findings, bottlenecks)
        risk_level = self._risk_level(focus)
        chairman = self._chairman(decision, focus, options)
        devil = self._devils_advocate(focus, context, risk_level)
        brown = self._brown_hat(decision, focus, options, risk_level)

        perspectives = [chairman, devil, brown]
        summary = (
            f"Boardroom reviewed {len(findings)} finding(s) and "
            f"{len(bottlenecks)} bottleneck(s): {brown['recommendation']}"
        )
        confidence = self._confidence(focus, findings, bottlenecks)

        return DispatcherResult(
            dispatcher="boardroom",
            task_id=task_id,
            status="completed",
            findings=perspectives,
            summary=summary,
            confidence=confidence,
            metadata={
                "decision": decision,
                "context_present": bool(context),
                "options": options,
                "risk_level": risk_level,
                "focus": focus,
                "recommendation": brown["recommendation"],
                "action_item": brown["action_item"],
                "execution_allowed": False,
                "input_counts": {
                    "findings": len(findings),
                    "bottlenecks": len(bottlenecks),
                },
            },
        )

    @staticmethod
    def _text(value: Any, limit: int = _MAX_TEXT) -> str:
        return str(value or "").strip()[:limit]

    @classmethod
    def _findings(cls, task_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = task_data.get("findings", [])
        if not isinstance(raw, list):
            return []
        return [
            {
                "title": cls._text(item.get("title", item.get("message", "Untitled finding"))),
                "severity": cls._text(item.get("severity", "info"), 32).lower(),
                "category": cls._text(item.get("category", "general"), 64),
                "impact": cls._text(item.get("impact", "Impact not supplied")),
                **({"confidence": item["confidence"]} if "confidence" in item else {}),
            }
            for item in raw[:_MAX_ITEMS] if isinstance(item, dict)
        ]

    @classmethod
    def _bottlenecks(cls, task_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = task_data.get("bottlenecks", [])
        if not isinstance(raw, list):
            return []
        return [
            {
                "area": cls._text(item.get("area", "general"), 64),
                "severity": cls._text(item.get("severity", "info"), 32).lower(),
                "titles": [cls._text(title) for title in item.get("titles", [])[:_MAX_OPTIONS]] if isinstance(item.get("titles", []), list) else [],
                "impact_summary": cls._text(item.get("impact_summary", "Impact not supplied")),
            }
            for item in raw[:_MAX_ITEMS] if isinstance(item, dict)
        ]

    @staticmethod
    def _options(task_data: Dict[str, Any]) -> List[str]:
        raw = task_data.get("options", [])
        if not isinstance(raw, list):
            return []
        return [str(item).strip()[:_MAX_TEXT] for item in raw if str(item).strip()][:_MAX_OPTIONS]

    @staticmethod
    def _select_focus(
        findings: List[Dict[str, Any]],
        bottlenecks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidates: List[Dict[str, Any]] = []
        for item in findings:
            candidates.append({
                "title": item["title"],
                "severity": item["severity"],
                "category": item["category"],
                "impact": item["impact"],
            })
        for item in bottlenecks:
            titles = item["titles"]
            candidates.append({
                "title": titles[0] if titles else item["area"],
                "severity": item["severity"],
                "category": item["area"],
                "impact": item["impact_summary"],
            })
        if not candidates:
            return {
                "title": "No dominant risk identified",
                "severity": "info",
                "category": "general",
                "impact": "Insufficient structured evidence",
            }
        return max(candidates, key=lambda item: _SEVERITY_WEIGHT.get(item["severity"], 0))

    @staticmethod
    def _risk_level(focus: Dict[str, Any]) -> str:
        severity = focus.get("severity", "info")
        return {
            "critical": "critical",
            "high": "high",
            "medium": "moderate",
        }.get(severity, "low")

    @staticmethod
    def _chairman(
        decision: str,
        focus: Dict[str, Any],
        options: List[str],
    ) -> Dict[str, Any]:
        subject = decision or focus["title"]
        selected = options[0] if options else "the smallest reversible mitigation"
        return {
            "role": "chairman",
            "title": "Strategic direction",
            "analysis": f"Prioritize '{focus['title']}' while reviewing: {subject[:240]}",
            "key_points": [
                f"Primary focus: {focus['category']}",
                f"Preferred path: {selected}",
                "Keep the first move reversible and measurable",
            ],
            "recommendation": "Proceed to a bounded mitigation review.",
        }

    @staticmethod
    def _devils_advocate(
        focus: Dict[str, Any],
        context: str,
        risk_level: str,
    ) -> Dict[str, Any]:
        caveats = [
            f"Evidence is limited to the supplied {focus['category']} data",
            "The proposed mitigation may move risk rather than remove it",
        ]
        if not context:
            caveats.append("No contextual narrative was supplied; verify assumptions before action")
        if risk_level in {"critical", "high"}:
            caveats.append("High-severity input requires human confirmation before execution")
        return {
            "role": "devils_advocate",
            "title": "Adversarial review",
            "analysis": f"Challenge the dominant risk: {focus['title']}",
            "key_points": caveats,
            "recommendation": "Do not treat this packet as proof or authorization.",
        }

    @staticmethod
    def _brown_hat(
        decision: str,
        focus: Dict[str, Any],
        options: List[str],
        risk_level: str,
    ) -> Dict[str, Any]:
        action = f"Validate '{focus['title']}' with the smallest available evidence check"
        if options:
            action = f"Review option '{options[0]}' against '{focus['title']}'"
        recommendation = "hold_for_review" if risk_level in {"critical", "high"} else "pilot_reversibly"
        return {
            "role": "brown_hat",
            "title": "Execution mandate",
            "analysis": f"Turn the review into one bounded next step for: {decision or focus['title']}",
            "key_points": [
                action,
                "Record the result before expanding scope",
                "Execution remains human-gated",
            ],
            "recommendation": recommendation,
            "action_item": action,
        }

    @staticmethod
    def _confidence(
        focus: Dict[str, Any],
        findings: List[Dict[str, Any]],
        bottlenecks: List[Dict[str, Any]],
    ) -> float:
        if not findings and not bottlenecks:
            return 0.35
        supplied = [item.get("confidence") for item in findings]
        numeric = [
            float(value) for value in supplied
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ]
        return round(min(0.95, max(0.5, sum(numeric) / len(numeric))) if numeric else 0.6, 4)
