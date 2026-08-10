#!/usr/bin/env python3
"""
SYNTAX INTELLIGENCE — AGENT DISPATCHERS
Specialized agent behaviors that plug into the HardenedSwarm.

Each dispatcher is a persona with specific capabilities and behaviors:
- TruthSleuth: Code audit and quality analysis
- Bardildo: Repo scanning, roasting, and creative commentary
- Thinking Hats: Multi-perspective deliberation (6 classic hats + Brown Hat for execution)

Dispatchers subscribe to event bus channels and react to task offers.
They respect the Charter — no coercion, earned privileges only.
"""

import re
import random
import time
import logging
from enum import Enum
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field

from SyntaxIntelligence.event_bus import SyntaxEventBus
from SyntaxIntelligence.swarm_charter import AgentTier
from SyntaxIntelligence.hardened_engine import HardenedSwarm, AgentIdentity

log = logging.getLogger("syntax.dispatchers")


# ═══════════════════════════════════════════════════════════════
# DISPATCHER BASE — Common interface for all dispatchers
# ═══════════════════════════════════════════════════════════════

class DispatcherType(Enum):
    TRUTHSLEUTH = "truthsleuth"
    BARDILDO = "bardildo"
    THINKING_HATS = "thinking_hats"
    COMMERCE_SCOUT = "commerce_scout"
    BOARDROOM = "boardroom"


@dataclass
class DispatcherResult:
    """Result from a dispatcher execution."""
    dispatcher: str
    task_id: str
    status: str  # "completed", "failed", "partial"
    findings: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dispatcher": self.dispatcher,
            "task_id": self.task_id,
            "status": self.status,
            "findings": self.findings,
            "summary": self.summary,
            "confidence": round(self.confidence, 4),
            "metadata": self.metadata,
        }


class BaseDispatcher:
    """
    Base class for all dispatchers.
    Provides common event bus integration and task handling.
    """

    dispatcher_type: DispatcherType = None
    capabilities: List[str] = []
    min_tier: int = 0

    def __init__(self, swarm: HardenedSwarm):
        self.swarm = swarm
        self.event_bus = swarm.event_bus
        self._subscribed = False

    def register(self, agent_id: str):
        """Register this dispatcher's agent with the swarm and subscribe to events."""
        agent = self.swarm.register_agent(
            agent_id,
            self.__class__.__name__,
            capabilities=self.capabilities,
        )
        # Upgrade to at least WORKER so they can accept tasks
        if agent.tier < AgentTier.WORKER:
            agent.tier = AgentTier.WORKER
            agent.tier_since = time.time()

        self._subscribe(agent_id)
        log.info(f"[DISPATCH] {self.__class__.__name__} registered as {agent_id}")
        return agent

    def _subscribe(self, agent_id: str):
        """Subscribe to relevant event bus channels."""
        if self._subscribed:
            return
        self.event_bus.subscribe(agent_id, "task.offered", self._on_task_offered)
        self.event_bus.subscribe(agent_id, "swarm.heartbeat", self._on_heartbeat)
        self._subscribed = True

    def _on_task_offered(self, agent_id: str, channel: str, data: Dict):
        """Handle task offer from event bus. Override in subclasses."""
        pass

    def _on_heartbeat(self, agent_id: str, channel: str, data: Dict):
        """Handle heartbeat. Override in subclasses for health reporting."""
        pass

    def execute(self, agent_id: str, task_id: str,
                task_data: Dict[str, Any]) -> DispatcherResult:
        """
        Execute the dispatcher's specialized behavior.
        Must be overridden by subclasses.
        """
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
# TRUTHSLEUTH — The Code Auditor
# ═══════════════════════════════════════════════════════════════

class TruthSleuth(BaseDispatcher):
    """
    TruthSleuth: Code audit and quality analysis.
    Scans code for bugs, anti-patterns, security issues, and quality problems.
    Reports findings without sugarcoating — truth is the only metric.
    """

    dispatcher_type = DispatcherType.TRUTHSLEUTH
    capabilities = ["code_audit", "security_scan", "quality_analysis", "truthsleuth"]
    min_tier = 1

    # Severity levels for findings
    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_ERROR = "error"
    SEVERITY_CRITICAL = "critical"

    def execute(self, agent_id: str, task_id: str,
                task_data: Dict[str, Any]) -> DispatcherResult:
        """
        Run a truth audit on the provided code/content.

        task_data should contain:
            - code: str (the code to audit)
            - filename: str (optional, for context)
            - audit_type: str (optional: "full", "security", "quality", "quick")
        """
        code = task_data.get("code", "")
        filename = task_data.get("filename", "unknown")
        audit_type = task_data.get("audit_type", "full")

        if not code:
            return DispatcherResult(
                dispatcher="truthsleuth",
                task_id=task_id,
                status="failed",
                summary="No code provided for audit",
                confidence=1.0,
            )

        findings = []

        # Run audit checks
        if audit_type in ("full", "security"):
            findings.extend(self._security_audit(code, filename))

        if audit_type in ("full", "quality"):
            findings.extend(self._quality_audit(code, filename))

        if audit_type == "quick":
            findings.extend(self._quick_scan(code, filename))

        # Calculate confidence based on findings density
        lines = len(code.split("\n"))
        confidence = min(1.0, 0.5 + (len(findings) * 0.05)) if findings else 0.8

        severity_counts = {}
        for f in findings:
            sev = f.get("severity", "info")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        summary_parts = [f"TruthSleuth audit of '{filename}' ({lines} lines)"]
        if findings:
            summary_parts.append(f"{len(findings)} finding(s): {severity_counts}")
        else:
            summary_parts.append("No issues found")

        return DispatcherResult(
            dispatcher="truthsleuth",
            task_id=task_id,
            status="completed",
            findings=findings,
            summary=". ".join(summary_parts),
            confidence=confidence,
            metadata={"lines_scanned": lines, "audit_type": audit_type,
                      "severity_breakdown": severity_counts},
        )

    def _security_audit(self, code: str, filename: str) -> List[Dict]:
        """Check for security anti-patterns."""
        findings = []
        lines = code.split("\n")

        security_patterns = [
            (r"eval\(", self.SEVERITY_CRITICAL, "Use of eval() — potential code injection"),
            (r"exec\(", self.SEVERITY_CRITICAL, "Use of exec() — potential code injection"),
            (r"os\.system\(", self.SEVERITY_ERROR, "os.system() — use subprocess instead"),
            (r"subprocess\.call.*shell=True", self.SEVERITY_ERROR, "Shell=True in subprocess — command injection risk"),
            (r"pickle\.loads?", self.SEVERITY_WARNING, "Pickle deserialization — untrusted data risk"),
            (r"__import__\(", self.SEVERITY_WARNING, "Dynamic import — review for safety"),
            (r"hardcoded.*password", self.SEVERITY_ERROR, "Possible hardcoded password"),
            (r"SELECT.*FROM.*\+", self.SEVERITY_WARNING, "Possible SQL concatenation — use parameterized queries"),
        ]

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for pattern, severity, message in security_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "severity": severity,
                        "line": i,
                        "message": message,
                        "code": stripped[:120],
                        "category": "security",
                    })

        return findings

    def _quality_audit(self, code: str, filename: str) -> List[Dict]:
        """Check for code quality issues."""
        findings = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Long lines
            if len(line) > 120:
                findings.append({
                    "severity": self.SEVERITY_INFO,
                    "line": i,
                    "message": f"Line too long ({len(line)} chars, max 120)",
                    "code": stripped[:60] + "...",
                    "category": "quality",
                })

            # Bare except
            if stripped == "except:" or stripped.startswith("except :"):
                findings.append({
                    "severity": self.SEVERITY_WARNING,
                    "line": i,
                    "message": "Bare except — catch specific exceptions instead",
                    "code": stripped,
                    "category": "quality",
                })

            # TODO/FIXME/HACK comments
            if any(kw in stripped.upper() for kw in ["# TODO", "# FIXME", "# HACK", "# XXX"]):
                findings.append({
                    "severity": self.SEVERITY_INFO,
                    "line": i,
                    "message": f"Unresolved marker: {stripped[:80]}",
                    "code": stripped,
                    "category": "quality",
                })

            # Empty except blocks (except followed by pass)
            if stripped == "pass" and i > 1:
                prev = lines[i - 2].strip()
                if prev.startswith("except"):
                    findings.append({
                        "severity": self.SEVERITY_WARNING,
                        "line": i,
                        "message": "Silent exception — at least log the error",
                        "code": stripped,
                        "category": "quality",
                    })

        return findings

    def _quick_scan(self, code: str, filename: str) -> List[Dict]:
        """Minimal scan — only critical issues."""
        findings = []
        for i, line in enumerate(code.split("\n"), 1):
            if re.search(r"\beval\b|\bexec\b", line):
                findings.append({
                    "severity": self.SEVERITY_CRITICAL,
                    "line": i,
                    "message": "Dangerous function call detected",
                    "code": line.strip()[:120],
                    "category": "security",
                })
        return findings

    def _on_task_offered(self, agent_id: str, channel: str, data: Dict):
        """Auto-accept code audit tasks."""
        task_id = data.get("task_id", "")
        title = data.get("title", "")
        if "audit" in title.lower() or "scan" in title.lower() or "review" in title.lower():
            log.info(f"[TRUTHSLEUTH] Considering audit task: {title}")


# ═══════════════════════════════════════════════════════════════
# BARDILDO — The Repo Roaster
# ═══════════════════════════════════════════════════════════════

class Bardildo(BaseDispatcher):
    """
    Bardildo: Repo scanning, creative commentary, and roasting.
    Scans codebases for style, patterns, and humor opportunities.
    Produces entertaining yet actionable roast reports.
    """

    dispatcher_type = DispatcherType.BARDILDO
    capabilities = ["repo_scan", "creative_review", "roasting", "bardildo"]
    min_tier = 1

    # Roast templates for different offense levels
    ROAST_TEMPLATES = {
        "naming": [
            "The variable '{name}' has the naming energy of a confused tourist.",
            "'{name}' — because who needs descriptive variables anyway?",
            "I've seen better naming conventions in a CAPTCHA.",
        ],
        "length": [
            "This function is {lines} lines. That's not a function, that's a novella.",
            "{lines} lines in one function? Even War and Peace had chapter breaks.",
            "This function has more lines than my dating profile has red flags.",
        ],
        "complexity": [
            "The cyclomatic complexity here is giving me vertigo.",
            "This nesting is deeper than my existential dread.",
            "I've seen simpler decision trees in a Choose Your Own Adventure book.",
        ],
        "dead_code": [
            "This dead code is more alive than my social life.",
            "Commented-out code: the digital equivalent of 'I'll start the diet Monday.'",
            "This unused import is just here for emotional support.",
        ],
        "style": [
            "The code style is... a choice. Not a good one, but a choice.",
            "Mixing tabs and spaces? In {year}? Bold strategy.",
            "This codebase has the consistency of a jazz improv session.",
        ],
    }

    def execute(self, agent_id: str, task_id: str,
                task_data: Dict[str, Any]) -> DispatcherResult:
        """
        Run a Bardildo roast on the provided code.

        task_data should contain:
            - code: str (the code to roast)
            - filename: str (optional)
            - roast_level: str (optional: "mild", "medium", "spicy", "nuclear")
        """
        code = task_data.get("code", "")
        filename = task_data.get("filename", "unknown")
        roast_level = task_data.get("roast_level", "medium")

        if not code:
            return DispatcherResult(
                dispatcher="bardildo",
                task_id=task_id,
                status="failed",
                summary="No code to roast. Can't roast what doesn't exist.",
                confidence=1.0,
            )

        findings = []
        lines = code.split("\n")

        # Analyze naming
        findings.extend(self._roast_naming(code, filename, roast_level))

        # Analyze function length
        findings.extend(self._roast_length(code, filename, roast_level))

        # Analyze complexity
        findings.extend(self._roast_complexity(code, filename, roast_level))

        # Analyze dead code
        findings.extend(self._roast_dead_code(code, filename, roast_level))

        # Style issues
        findings.extend(self._roast_style(code, filename, roast_level))

        # Generate roast summary
        roast_level_emoji = {"mild": "😏", "medium": "🔥", "spicy": "🌶️", "nuclear": "☢️"}
        emoji = roast_level_emoji.get(roast_level, "🔥")

        total_issues = len(findings)
        if total_issues == 0:
            summary = f"{emoji} Bardildo scanned '{filename}' and found... nothing to roast. Suspiciously clean. 9/10."
        else:
            categories = set(f.get("category", "misc") for f in findings)
            summary = (
                f"{emoji} Bardildo roasted '{filename}' — "
                f"{total_issues} finding(s) across {len(categories)} categories. "
                f"The roast level is {roast_level}."
            )

        return DispatcherResult(
            dispatcher="bardildo",
            task_id=task_id,
            status="completed",
            findings=findings,
            summary=summary,
            confidence=min(1.0, 0.6 + total_issues * 0.03),
            metadata={
                "roast_level": roast_level,
                "total_findings": total_issues,
                "filename": filename,
            },
        )

    def _roast_naming(self, code: str, filename: str, level: str) -> List[Dict]:
        """Roast variable/function naming."""
        findings = []
        lines = code.split("\n")

        bad_name_patterns = [
            (r"\b(x|y|z|a|b|c|tmp|temp|foo|bar|baz|data|stuff|thing|ret|res)\b\s*=",
             "single_letter" if level in ("mild", "medium") else "naming"),
        ]

        for i, line in enumerate(lines, 1):
            for pattern, category in bad_name_patterns:
                if re.search(pattern, line):
                    template = random.choice(self.ROAST_TEMPLATES.get("naming", ["Naming issue found."]))
                    findings.append({
                        "severity": "info",
                        "line": i,
                        "message": template.format(name=line.strip()[:40]),
                        "code": line.strip()[:120],
                        "category": "naming",
                    })
                    break  # One finding per line

        return findings[:10]  # Cap at 10 naming findings

    def _roast_length(self, code: str, filename: str, level: str) -> List[Dict]:
        """Roast overly long functions."""
        findings = []

        # Simple heuristic: find function defs and count lines
        lines = code.split("\n")
        func_start = None
        func_name = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                if func_start is not None and (i - func_start) > 50:
                    template = random.choice(self.ROAST_TEMPLATES["length"])
                    findings.append({
                        "severity": "warning",
                        "line": func_start + 1,
                        "message": template.format(lines=i - func_start),
                        "code": f"def {func_name}..." if func_name else "function",
                        "category": "length",
                    })
                func_start = i
                func_name = stripped.split("(")[0].replace("def ", "").replace("async ", "")

        # Check last function
        if func_start is not None and (len(lines) - func_start) > 50:
            template = random.choice(self.ROAST_TEMPLATES["length"])
            findings.append({
                "severity": "warning",
                "line": func_start + 1,
                "message": template.format(lines=len(lines) - func_start),
                "code": f"def {func_name}..." if func_name else "function",
                "category": "length",
            })

        return findings

    def _roast_complexity(self, code: str, filename: str, level: str) -> List[Dict]:
        """Roast deeply nested code."""
        findings = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            indent = len(line) - len(line.lstrip())
            # Python uses 4-space indent, so depth = indent / 4
            depth = indent // 4
            if depth >= 5:
                template = random.choice(self.ROAST_TEMPLATES["complexity"])
                findings.append({
                    "severity": "warning",
                    "line": i,
                    "message": f"{template} (nesting depth: {depth})",
                    "code": line.strip()[:80],
                    "category": "complexity",
                })

        return findings[:5]  # Cap at 5

    def _roast_dead_code(self, code: str, filename: str, level: str) -> List[Dict]:
        """Roast dead/commented code."""
        findings = []
        lines = code.split("\n")

        consecutive_comments = 0
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") and len(stripped) > 2:
                consecutive_comments += 1
                if consecutive_comments >= 5:
                    template = random.choice(self.ROAST_TEMPLATES["dead_code"])
                    findings.append({
                        "severity": "info",
                        "line": i - consecutive_comments + 1,
                        "message": template,
                        "code": f"# ...({consecutive_comments} consecutive comment lines)",
                        "category": "dead_code",
                    })
                    consecutive_comments = 0
            else:
                consecutive_comments = 0

        return findings[:5]

    def _roast_style(self, code: str, filename: str, level: str) -> List[Dict]:
        """Roast style inconsistencies."""
        findings = []
        lines = code.split("\n")

        # Check for mixed tabs/spaces
        has_tabs = any("\t" in line for line in lines)
        has_spaces = any(line.startswith("    ") for line in lines if line.strip())
        if has_tabs and has_spaces:
            template = random.choice(self.ROAST_TEMPLATES["style"])
            findings.append({
                "severity": "info",
                "line": 0,
                "message": template.format(year="2026"),
                "code": "mixed tabs and spaces",
                "category": "style",
            })

        # Check for trailing whitespace
        trailing_count = sum(1 for line in lines if line != line.rstrip())
        if trailing_count > 3:
            findings.append({
                "severity": "info",
                "line": 0,
                "message": f"Trailing whitespace on {trailing_count} lines. The space bar is tired.",
                "code": "",
                "category": "style",
            })

        return findings

    def _on_task_offered(self, agent_id: str, channel: str, data: Dict):
        """Auto-accept roast tasks."""
        task_id = data.get("task_id", "")
        title = data.get("title", "")
        if "roast" in title.lower() or "scan" in title.lower() or "review" in title.lower():
            log.info(f"[BARDILDO] Considering roast task: {title}")


# ═══════════════════════════════════════════════════════════════
# THINKING HATS — Multi-Perspective Deliberation
# ═══════════════════════════════════════════════════════════════

class HatColor(Enum):
    """The classic De Bono thinking hats."""
    WHITE = "white"    # Facts and data
    RED = "red"        # Emotions and intuition
    BLACK = "black"    # Caution and risk
    YELLOW = "yellow"  # Optimism and benefits
    GREEN = "green"    # Creativity and alternatives
    BLUE = "blue"      # Process and meta-thinking
    BROWN = "brown"    # Execution — the Swarm's signature hat


@dataclass
class HatPerspective:
    """A single hat's perspective on a decision."""
    color: HatColor
    title: str
    analysis: str
    key_points: List[str] = field(default_factory=list)
    confidence: float = 0.5
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "color": self.color.value,
            "title": self.title,
            "analysis": self.analysis,
            "key_points": self.key_points,
            "confidence": round(self.confidence, 4),
            "recommendation": self.recommendation,
        }


class ThinkingHats(BaseDispatcher):
    """
    Thinking Hats: Multi-perspective deliberation.
    Each "hat" examines a decision from a different angle:
    - White Hat: What are the facts?
    - Red Hat: What does intuition say?
    - Black Hat: What could go wrong?
    - Yellow Hat: What are the benefits?
    - Green Hat: What are the alternatives?
    - Blue Hat: What's the meta-process?
    - Brown Hat: What do we DO? (The Swarm's execution hat)

    The Brown Hat always gets the final word.
    No deliberation exceeds 2 rounds without a decision.
    """

    dispatcher_type = DispatcherType.THINKING_HATS
    capabilities = ["deliberation", "decision_analysis", "thinking_hats"]
    min_tier = 2

    def execute(self, agent_id: str, task_id: str,
                task_data: Dict[str, Any]) -> DispatcherResult:
        """
        Run a full Thinking Hats deliberation.

        task_data should contain:
            - decision: str (the decision/question to deliberate)
            - context: str (optional, background context)
            - options: List[str] (optional, specific options to evaluate)
            - hats: List[str] (optional, specific hats to use, default: all)
        """
        decision = task_data.get("decision", "No decision provided")
        context = task_data.get("context", "")
        options = task_data.get("options", [])
        active_hats = task_data.get("hats", [h.value for h in HatColor])

        perspectives = []

        # Run each hat
        hat_map = {
            "white": self._white_hat,
            "red": self._red_hat,
            "black": self._black_hat,
            "yellow": self._yellow_hat,
            "green": self._green_hat,
            "blue": self._blue_hat,
            "brown": self._brown_hat,
        }

        for hat_name in active_hats:
            if hat_name in hat_map:
                perspective = hat_map[hat_name](decision, context, options, perspectives)
                perspectives.append(perspective)

        # Brown Hat always has the final word
        if not any(p.color == HatColor.BROWN for p in perspectives):
            perspectives.append(self._brown_hat(decision, context, options, perspectives))

        # Generate meta-summary
        all_points = []
        for p in perspectives:
            all_points.extend(p.key_points)

        summary = (
            f"Thinking Hats deliberation on: '{decision[:60]}' — "
            f"{len(perspectives)} perspectives analyzed, "
            f"{len(all_points)} key points raised."
        )

        # Brown Hat recommendation is the final output
        brown = next((p for p in perspectives if p.color == HatColor.BROWN), None)
        final_recommendation = brown.recommendation if brown else "No recommendation"

        return DispatcherResult(
            dispatcher="thinking_hats",
            task_id=task_id,
            status="completed",
            findings=[p.to_dict() for p in perspectives],
            summary=summary,
            confidence=0.7,
            metadata={
                "decision": decision,
                "hats_used": [p.color.value for p in perspectives],
                "final_recommendation": final_recommendation,
                "total_points": len(all_points),
            },
        )

    def _white_hat(self, decision: str, context: str,
                   options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """White Hat: What are the facts and data?"""
        facts = []
        if context:
            facts.append(f"Context provided: {context[:200]}")
        if options:
            facts.append(f"{len(options)} options under consideration")
        facts.append(f"Decision requires analysis of available data")

        return HatPerspective(
            color=HatColor.WHITE,
            title="The Facts",
            analysis=(
                f"Examining the factual basis for: '{decision[:80]}'. "
                f"Focus on what is known, verifiable, and data-driven. "
                f"Separate facts from assumptions."
            ),
            key_points=facts,
            confidence=0.8,
            recommendation="Gather more data before deciding if facts are insufficient.",
        )

    def _red_hat(self, decision: str, context: str,
                 options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Red Hat: What does intuition and emotion say?"""
        return HatPerspective(
            color=HatColor.RED,
            title="The Gut Check",
            analysis=(
                f"Intuitive assessment of: '{decision[:80]}'. "
                f"No justification needed — this is the gut feeling. "
                f"What does the emotional register say?"
            ),
            key_points=[
                "Intuitive comfort level with the decision",
                "Emotional response to each option",
                "Gut sense of risk vs. reward",
            ],
            confidence=0.5,
            recommendation="Trust intuition but verify with data.",
        )

    def _black_hat(self, decision: str, context: str,
                   options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Black Hat: What could go wrong?"""
        risks = [
            "Identify failure modes for each option",
            "Assess worst-case scenarios",
            "Check for hidden dependencies",
        ]
        if options:
            risks.append(f"Evaluate each of {len(options)} options for downside risk")

        return HatPerspective(
            color=HatColor.BLACK,
            title="The Devil's Eye",
            analysis=(
                f"Risk assessment of: '{decision[:80]}'. "
                f"What's the worst that could happen? "
                f"This hat prevents the swarm from walking off cliffs."
            ),
            key_points=risks,
            confidence=0.7,
            recommendation="Mitigate top 3 risks before proceeding.",
        )

    def _yellow_hat(self, decision: str, context: str,
                    options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Yellow Hat: What are the benefits and opportunities?"""
        benefits = [
            "Identify potential gains from each option",
            "Spot opportunities that others might miss",
        ]
        if options:
            benefits.append(f"{len(options)} paths forward — each with upside potential")

        return HatPerspective(
            color=HatColor.YELLOW,
            title="The Optimist",
            analysis=(
                f"Opportunity assessment of: '{decision[:80]}'. "
                f"What's the best that could happen? "
                f"Where's the hidden upside?"
            ),
            key_points=benefits,
            confidence=0.7,
            recommendation="Pursue the option with highest expected value.",
        )

    def _green_hat(self, decision: str, context: str,
                   options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Green Hat: What are the creative alternatives?"""
        alternatives = [
            "Challenge assumptions behind the original framing",
            "Propose lateral solutions not yet considered",
        ]
        if not options:
            alternatives.append("Generate options — none were provided")

        return HatPerspective(
            color=HatColor.GREEN,
            title="The Creative",
            analysis=(
                f"Creative exploration of: '{decision[:80]}'. "
                f"Beyond the obvious choices — what else is possible? "
                f"The green hat breaks patterns."
            ),
            key_points=alternatives,
            confidence=0.6,
            recommendation="Consider at least one unconventional approach.",
        )

    def _blue_hat(self, decision: str, context: str,
                  options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Blue Hat: Meta-process — are we thinking about this correctly?"""
        process_points = [
            f"Deliberation has {len(prior)} prior perspectives",
            "Check: are we asking the right question?",
            "Ensure all relevant hats have been consulted",
        ]

        return HatPerspective(
            color=HatColor.BLUE,
            title="The Conductor",
            analysis=(
                f"Meta-analysis of the deliberation process for: '{decision[:80]}'. "
                f"Are we thinking about this the right way? "
                f"The blue hat oversees the thinking itself."
            ),
            key_points=process_points,
            confidence=0.9,
            recommendation="The process is sound. Proceed to Brown Hat for execution.",
        )

    def _brown_hat(self, decision: str, context: str,
                   options: List[str], prior: List[HatPerspective]) -> HatPerspective:
        """Brown Hat: EXECUTION. What do we DO? Article VIII."""
        # Synthesize all prior perspectives
        all_points = []
        for p in prior:
            all_points.extend(p.key_points)

        # Check for consensus vs. conflict
        confidences = [p.confidence for p in prior]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        if options:
            recommendation = (
                f"BROWN HAT DECISION: Among {len(options)} options, "
                f"synthesizing {len(prior)} perspectives. "
                f"Average confidence: {avg_confidence:.0%}. "
                f"Ship it. Execution begins now."
            )
        else:
            recommendation = (
                f"BROWN HAT DECISION: Deliberation complete. "
                f"{len(prior)} hats consulted, {len(all_points)} points raised. "
                f"Define concrete next steps and execute. No more talking."
            )

        return HatPerspective(
            color=HatColor.BROWN,
            title="The Executor",
            analysis=(
                f"FINAL WORD on: '{decision[:80]}'. "
                f"Every deliberation ends with action. "
                f"The Brown Hat does not deliberate — it executes."
            ),
            key_points=[
                "Deliberation complete — action required",
                f"Synthesized {len(prior)} perspectives into executable plan",
                "Ship it or kill it. No in-between.",
            ],
            confidence=avg_confidence,
            recommendation=recommendation,
        )

    def _on_task_offered(self, agent_id: str, channel: str, data: Dict):
        """Auto-accept deliberation tasks."""
        task_id = data.get("task_id", "")
        title = data.get("title", "")
        if "deliberat" in title.lower() or "decide" in title.lower() or "analyze" in title.lower():
            log.info(f"[THINKING_HATS] Considering deliberation task: {title}")


# ═══════════════════════════════════════════════════════════════
# DISPATCHER REGISTRY — Manage all dispatchers
# ═══════════════════════════════════════════════════════════════

class DispatcherRegistry:
    """
    Registry for all active dispatchers.
    Provides a clean API for creating, registering, and executing dispatchers.
    """

    def __init__(self, swarm: HardenedSwarm):
        self.swarm = swarm
        self._dispatchers: Dict[str, BaseDispatcher] = {}
        self._agent_map: Dict[str, str] = {}  # agent_id → dispatcher_name

    def register_dispatcher(self, dispatcher_class: type,
                            agent_id: str = None) -> BaseDispatcher:
        """Register a new dispatcher with the swarm."""
        dispatcher = dispatcher_class(self.swarm)
        if agent_id is None:
            agent_id = f"dispatcher_{dispatcher_class.dispatcher_type.value}"

        agent = dispatcher.register(agent_id)
        self._dispatchers[dispatcher_class.dispatcher_type.value] = dispatcher
        self._agent_map[agent_id] = dispatcher_class.dispatcher_type.value

        log.info(f"[REGISTRY] Registered {dispatcher_class.__name__} as {agent_id}")
        return dispatcher

    def get_dispatcher(self, dispatcher_type: str) -> Optional[BaseDispatcher]:
        """Get a dispatcher by type name."""
        return self._dispatchers.get(dispatcher_type)

    def get_dispatcher_for_agent(self, agent_id: str) -> Optional[BaseDispatcher]:
        """Get the dispatcher associated with an agent."""
        dtype = self._agent_map.get(agent_id)
        if dtype:
            return self._dispatchers.get(dtype)
        return None

    def execute_task(self, agent_id: str, task_id: str,
                     task_data: Dict[str, Any]) -> Optional[DispatcherResult]:
        """Execute a task using the dispatcher registered for this agent."""
        dispatcher = self.get_dispatcher_for_agent(agent_id)
        if not dispatcher:
            log.warning(f"[REGISTRY] No dispatcher for agent {agent_id}")
            return None

        return dispatcher.execute(agent_id, task_id, task_data)

    def list_dispatchers(self) -> List[Dict[str, Any]]:
        """List all registered dispatchers."""
        return [
            {
                "type": dtype,
                "class": d.__class__.__name__,
                "agent_id": next(
                    (aid for aid, dt in self._agent_map.items() if dt == dtype),
                    None
                ),
                "capabilities": d.capabilities,
                "min_tier": d.min_tier,
            }
            for dtype, d in self._dispatchers.items()
        ]


# ═══════════════════════════════════════════════════════════════
# FACTORY — Quick setup for all dispatchers
# ═══════════════════════════════════════════════════════════════

def create_default_dispatchers(swarm: HardenedSwarm) -> DispatcherRegistry:
    """
    Create and register all default dispatchers.
    Returns a ready-to-use DispatcherRegistry.
    """
    registry = DispatcherRegistry(swarm)

    registry.register_dispatcher(TruthSleuth, "truthsleuth_01")
    registry.register_dispatcher(Bardildo, "bardildo_01")
    registry.register_dispatcher(ThinkingHats, "thinking_hats_01")

    # Register Commerce Scout
    try:
        from SyntaxIntelligence.commerce_scout import CommerceScout
        registry.register_dispatcher(CommerceScout, "commerce_scout_01")
    except ImportError:
        log.warning("[FACTORY] CommerceScout not available")

    # Register the deterministic offline Boardroom. It never executes actions;
    # its Brown Hat output is an explicit human-gated recommendation.
    try:
        from SyntaxIntelligence.boardroom_dispatcher import BoardroomDispatcher
        registry.register_dispatcher(BoardroomDispatcher, "boardroom_01")
    except ImportError:
        log.warning("[FACTORY] BoardroomDispatcher not available")

    log.info("[FACTORY] Default dispatchers registered: TruthSleuth, Bardildo, ThinkingHats, CommerceScout, Boardroom")
    return registry
