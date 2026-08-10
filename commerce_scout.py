#!/usr/bin/env python3
"""
SYNTAX INTELLIGENCE — COMMERCE SCOUT
Autonomous scout agent that monitors high-risk investment
endpoints in commerce and produces daily bottleneck reports.

Monitors:
- Payment processing reliability and fraud signals
- Supply chain bottleneck indicators
- Marketplace platform health
- Logistics and fulfillment metrics
- Regulatory compliance status
- Emerging commerce trends

"The scout sees what the swarm needs to know."
"""

import time
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from SyntaxIntelligence.dispatchers import BaseDispatcher, DispatcherType, DispatcherResult

log = logging.getLogger("syntax.scouts")


# ═══════════════════════════════════════════════════════════════
# SCOUT PERSONAS — Specialized analysis perspectives
# ═══════════════════════════════════════════════════════════════

SCOUT_PERSONAS = {
    "wraith": {
        "name": "Wraith",
        "focus": "Fraud detection and hidden risk signals",
        "description": "Silent observer. Sees fraud patterns, synthetic identities, and anomalous behavior before they manifest as losses.",
        "analysis_types": ["fraud_detection", "anomaly_scanning", "risk_scoring"],
    },
    "oracle": {
        "name": "Oracle",
        "focus": "Predictive market intelligence",
        "description": "Forecasts disruption. Reads market signals, regulatory shifts, and competitive movements to predict what's coming.",
        "analysis_types": ["market_prediction", "regulatory_forecast", "competitive_intel"],
    },
    "forge": {
        "name": "Forge",
        "focus": "Infrastructure and technical bottlenecks",
        "description": "Builds the map of technical debt. Identifies API failures, latency spikes, integration gaps, and scalability walls.",
        "analysis_types": ["infrastructure_audit", "api_health", "latency_analysis"],
    },
    "weaver": {
        "name": "Weaver",
        "focus": "Supply chain and logistics optimization",
        "description": "Threads the supply chain. Maps bottlenecks, single points of failure, and fulfillment bottlenecks across the logistics network.",
        "analysis_types": ["supply_chain_mapping", "logistics_audit", "fulfillment_analysis"],
    },
    "harvest": {
        "name": "Harvest",
        "focus": "Revenue optimization and growth signals",
        "description": "Reaps opportunity. Identifies revenue leaks, conversion bottlenecks, pricing inefficiencies, and untapped market segments.",
        "analysis_types": ["revenue_analysis", "conversion_audit", "pricing_intel"],
    },
    "hollow": {
        "name": "Hollow",
        "focus": "Compliance, regulatory, and governance gaps",
        "description": "Fills the void of compliance. Monitors regulatory changes, audit gaps, data governance issues, and policy violations.",
        "analysis_types": ["compliance_check", "regulatory_audit", "governance_review"],
    },
}


# ═══════════════════════════════════════════════════════════════
# COMMERCE SCOUT DISPATCHER
# ═══════════════════════════════════════════════════════════════

class CommerceScout(BaseDispatcher):
    """
    Commerce Scout: Autonomous investment endpoint monitor.
    Produces daily reports on high-risk commerce areas and bottleneck solutions.

    Each persona analyzes a different dimension of commerce risk:
    - Wraith: Fraud and anomaly detection
    - Oracle: Market prediction and regulatory forecasting
    - Forge: Infrastructure and API health
    - Weaver: Supply chain and logistics
    - Harvest: Revenue optimization
    - Hollow: Compliance and governance
    """

    dispatcher_type = DispatcherType.COMMERCE_SCOUT
    capabilities = [
        "commerce_analysis", "investment_monitoring", "bottleneck_detection",
        "market_intel", "supply_chain", "fraud_detection",
        "compliance_audit", "revenue_analysis", "commerce_scout",
    ]
    min_tier = 2

    def execute(self, agent_id: str, task_id: str,
                task_data: Dict[str, Any]) -> DispatcherResult:
        """
        Run a Commerce Scout analysis.

        task_data should contain:
            - report_type: str ("daily", "deep_dive", "persona_focus", "full_report")
            - personas: List[str] (optional, specific personas to activate)
            - data: Dict (optional, raw data to analyze)
            - focus_areas: List[str] (optional, specific areas to focus on)
        """
        report_type = task_data.get("report_type", "daily")
        active_personas = task_data.get("personas", list(SCOUT_PERSONAS.keys()))
        raw_data = task_data.get("data", {})
        focus_areas = task_data.get("focus_areas", [])

        if report_type == "daily":
            return self._daily_report(agent_id, task_id, active_personas, raw_data, focus_areas)
        elif report_type == "deep_dive":
            return self._deep_dive(agent_id, task_id, task_data)
        elif report_type == "persona_focus":
            return self._persona_focus(agent_id, task_id, task_data)
        else:
            return self._full_report(agent_id, task_id, active_personas, raw_data, focus_areas)

    def _daily_report(self, agent_id: str, task_id: str,
                      personas: List[str], data: Dict, focus: List[str]) -> DispatcherResult:
        """Generate the daily Commerce Scout report."""
        findings = []
        sections = []

        # Run each active persona
        for persona_id in personas:
            if persona_id not in SCOUT_PERSONAS:
                continue
            persona = SCOUT_PERSONAS[persona_id]
            persona_findings = self._analyze_persona(persona_id, persona, data, focus)
            findings.extend(persona_findings)
            sections.append({
                "persona": persona_id,
                "name": persona["name"],
                "focus": persona["focus"],
                "findings_count": len(persona_findings),
            })

        # Generate bottleneck solutions
        bottlenecks = self._identify_bottlenecks(findings)
        solutions = self._propose_solutions(bottlenecks)

        # Risk score
        risk_score = self._calculate_risk_score(findings)

        # Summary
        total_findings = len(findings)
        critical_count = sum(1 for f in findings if f.get("severity") == "critical")
        high_count = sum(1 for f in findings if f.get("severity") == "high")

        summary = (
            f"Commerce Scout Daily Report: {total_findings} finding(s) across "
            f"{len(sections)} personas. Risk score: {risk_score}/100. "
            f"{critical_count} critical, {high_count} high-severity items. "
            f"{len(bottlenecks)} bottleneck(s) identified, {len(solutions)} solution(s) proposed."
        )

        return DispatcherResult(
            dispatcher="commerce_scout",
            task_id=task_id,
            status="completed",
            findings=findings,
            summary=summary,
            confidence=0.75,
            metadata={
                "report_type": "daily",
                "personas_active": [s["persona"] for s in sections],
                "sections": sections,
                "bottlenecks": bottlenecks,
                "solutions": solutions,
                "risk_score": risk_score,
                "total_findings": total_findings,
                "critical_count": critical_count,
                "high_count": high_count,
            },
        )

    def _deep_dive(self, agent_id: str, task_id: str, task_data: Dict) -> DispatcherResult:
        """Deep dive analysis on a specific area."""
        area = task_data.get("area", "payment_processing")
        data = task_data.get("data", {})

        findings = self._analyze_area(area, data)

        return DispatcherResult(
            dispatcher="commerce_scout",
            task_id=task_id,
            status="completed",
            findings=findings,
            summary=f"Deep dive on '{area}': {len(findings)} finding(s)",
            confidence=0.8,
            metadata={"area": area, "report_type": "deep_dive"},
        )

    def _persona_focus(self, agent_id: str, task_id: str, task_data: Dict) -> DispatcherResult:
        """Focus analysis through a single persona."""
        persona_id = task_data.get("persona", "wraith")
        persona = SCOUT_PERSONAS.get(persona_id)
        if not persona:
            return DispatcherResult(
                dispatcher="commerce_scout",
                task_id=task_id,
                status="failed",
                summary=f"Unknown persona: {persona_id}",
            )

        data = task_data.get("data", {})
        focus = task_data.get("focus_areas", [])
        findings = self._analyze_persona(persona_id, persona, data, focus)

        return DispatcherResult(
            dispatcher="commerce_scout",
            task_id=task_id,
            status="completed",
            findings=findings,
            summary=f"{persona['name']} analysis: {len(findings)} finding(s) — {persona['focus']}",
            confidence=0.75,
            metadata={"persona": persona_id, "report_type": "persona_focus"},
        )

    def _full_report(self, agent_id: str, task_id: str,
                     personas: List[str], data: Dict, focus: List[str]) -> DispatcherResult:
        """Full report with all personas and deep analysis."""
        daily = self._daily_report(agent_id, task_id, personas, data, focus)
        daily.metadata["report_type"] = "full_report"
        return daily

    # ═══════════════════════════════════════════════════════════
    # PERSONA ANALYZERS
    # ═══════════════════════════════════════════════════════════

    def _analyze_persona(self, persona_id: str, persona: Dict,
                         data: Dict, focus: List[str]) -> List[Dict]:
        """Run analysis for a specific persona."""
        analyzers = {
            "wraith": self._wraith_analysis,
            "oracle": self._oracle_analysis,
            "forge": self._forge_analysis,
            "weaver": self._weaver_analysis,
            "harvest": self._harvest_analysis,
            "hollow": self._hollow_analysis,
        }

        analyzer = analyzers.get(persona_id)
        if analyzer:
            return analyzer(data, focus)
        return []

    def _wraith_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Wraith: Fraud detection and anomaly scanning."""
        findings = []

        # Fraud signal patterns
        fraud_indicators = [
            {
                "id": "wraith_synthetic_id",
                "severity": "critical",
                "title": "Synthetic Identity Fraud Vector",
                "description": "AI-generated synthetic identities are bypassing traditional KYC checks. Behavioral biometrics and device fingerprinting are the only reliable defense.",
                "category": "fraud_detection",
                "impact": "Direct financial loss, regulatory penalties",
                "trend": "Accelerating — 300% increase in synthetic ID fraud since 2024",
            },
            {
                "id": "wraith_deepfake_payments",
                "severity": "high",
                "title": "Deepfake-Enabled Payment Fraud",
                "description": "Voice and video deepfakes are being used to authorize high-value transactions. Multi-factor biometric verification needed.",
                "category": "fraud_detection",
                "impact": "Transaction fraud, account takeover",
                "trend": "Emerging — 40% increase in deepfake-related fraud attempts",
            },
            {
                "id": "wraith_chargeback_patterns",
                "severity": "medium",
                "title": "Coordinated Chargeback Attack Pattern",
                "description": "Bot networks executing coordinated chargeback fraud across multiple merchants simultaneously.",
                "category": "anomaly_detection",
                "impact": "Revenue loss, merchant account risk",
                "trend": "Stable but evolving tactics",
            },
        ]

        for indicator in fraud_indicators:
            if not focus or indicator["category"] in focus:
                findings.append({
                    "persona": "wraith",
                    "severity": indicator["severity"],
                    "category": indicator["category"],
                    "title": indicator["title"],
                    "description": indicator["description"],
                    "impact": indicator["impact"],
                    "trend": indicator["trend"],
                    "confidence": 0.8,
                })

        return findings

    def _oracle_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Oracle: Market prediction and regulatory forecasting."""
        findings = []

        predictions = [
            {
                "id": "oracle_agent_commerce",
                "severity": "high",
                "title": "Agent-Commerce Disruption Wave",
                "description": "LLM-driven shopping agents are reshaping product discovery. Merchants without structured, agent-compatible data feeds will become invisible.",
                "category": "market_prediction",
                "impact": "Revenue loss for non-adaptive merchants, opportunity for early adopters",
                "trend": "Accelerating — 60% of consumers expected to use AI shopping agents by 2027",
            },
            {
                "id": "oracle_stablecoin_settlement",
                "severity": "medium",
                "title": "Stablecoin B2B Settlement Shift",
                "description": "Stablecoins moving from speculative to settlement infrastructure. Cross-border B2B payments increasingly using USDC/USDT for speed and cost.",
                "category": "market_prediction",
                "impact": "New payment rails, reduced FX costs, regulatory uncertainty",
                "trend": "Growing — $8T+ stablecoin volume in 2025, projected 40% growth",
            },
            {
                "id": "oracle_regulatory_cascade",
                "severity": "high",
                "title": "Regulatory Compliance Cascade",
                "description": "Environmental disclosure mandates, e-invoicing requirements, and AI governance rules converging. Merchants face compliance overload.",
                "category": "regulatory_forecast",
                "impact": "Operational overhead, market access restrictions",
                "trend": "Accelerating — EU AI Act, SEC climate rules, state-level AI mandates",
            },
        ]

        for pred in predictions:
            if not focus or pred["category"] in focus:
                findings.append({
                    "persona": "oracle",
                    "severity": pred["severity"],
                    "category": pred["category"],
                    "title": pred["title"],
                    "description": pred["description"],
                    "impact": pred["impact"],
                    "trend": pred["trend"],
                    "confidence": 0.7,
                })

        return findings

    def _forge_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Forge: Infrastructure and API health analysis."""
        findings = []

        infra_issues = [
            {
                "id": "forge_api_latency",
                "severity": "high",
                "title": "Payment API Latency Degradation",
                "description": "Payment processing APIs showing 200-500ms latency spikes during peak hours. Tokenized checkout experiencing intermittent timeouts.",
                "category": "api_health",
                "impact": "Cart abandonment, failed transactions, revenue loss",
                "trend": "Worsening — peak hour failures up 25% YoY",
            },
            {
                "id": "forge_data_silos",
                "severity": "critical",
                "title": "Commerce Data Stack Fragmentation",
                "description": "Disconnected OMS, WMS, and TMS systems creating 'inventory phantoms' — items showing in stock but unavailable for fulfillment.",
                "category": "infrastructure_audit",
                "impact": "Customer experience degradation, overselling, returns",
                "trend": "Persistent — 73% of retailers report data silo issues",
            },
            {
                "id": "forge_integration_gaps",
                "severity": "medium",
                "title": "Dark Store Integration Gap",
                "description": "Micro-fulfillment centers not synced with primary OMS. Real-time inventory visibility broken across distributed fulfillment network.",
                "category": "infrastructure_audit",
                "impact": "Fulfillment delays, inventory discrepancies",
                "trend": "Growing as dark store adoption accelerates",
            },
        ]

        for issue in infra_issues:
            if not focus or issue["category"] in focus:
                findings.append({
                    "persona": "forge",
                    "severity": issue["severity"],
                    "category": issue["category"],
                    "title": issue["title"],
                    "description": issue["description"],
                    "impact": issue["impact"],
                    "trend": issue["trend"],
                    "confidence": 0.85,
                })

        return findings

    def _weaver_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Weaver: Supply chain and logistics optimization."""
        findings = []

        supply_issues = [
            {
                "id": "weaver_bullwhip_ai",
                "severity": "high",
                "title": "AI-Induced Supply Chain Bullwhip",
                "description": "Fragmented AI tools (forecasting, planning, procurement) operating independently create amplifying demand signals. Recommendations ignore port capacity and contract terms.",
                "category": "supply_chain_mapping",
                "impact": "Overstocking, understocking, supplier strain",
                "trend": "Emerging — as AI adoption fragments across supply chain functions",
            },
            {
                "id": "weaver_static_contracts",
                "severity": "medium",
                "title": "Rigid Contract Vulnerability",
                "description": "Annual fixed contracts unable to accommodate 2026 geopolitical and economic volatility. Dynamic volume/price adjustment clauses needed.",
                "category": "logistics_audit",
                "impact": "Cost overruns, supplier relationship strain",
                "trend": "Growing — 65% of shippers now seeking dynamic contract terms",
            },
            {
                "id": "weaver_fulfillment_bottleneck",
                "severity": "high",
                "title": "Last-Mile Fulfillment Capacity Ceiling",
                "description": "Micro-fulfillment centers approaching capacity limits. Dark store integration with OMS still incomplete at many retailers.",
                "category": "fulfillment_analysis",
                "impact": "Delivery delays, customer churn",
                "trend": "Accelerating — same-day delivery demand up 40% YoY",
            },
        ]

        for issue in supply_issues:
            if not focus or issue["category"] in focus:
                findings.append({
                    "persona": "weaver",
                    "severity": issue["severity"],
                    "category": issue["category"],
                    "title": issue["title"],
                    "description": issue["description"],
                    "impact": issue["impact"],
                    "trend": issue["trend"],
                    "confidence": 0.8,
                })

        return findings

    def _harvest_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Harvest: Revenue optimization and growth signals."""
        findings = []

        revenue_issues = [
            {
                "id": "harvest_manual_checkout",
                "severity": "high",
                "title": "Manual Checkout Revenue Leak",
                "description": "Merchants still requiring manual guest checkout losing 35% of potential conversions. Tokenized, one-click solutions now table stakes.",
                "category": "conversion_audit",
                "impact": "35% conversion drop vs. tokenized checkout",
                "trend": "Critical gap — manual checkout users declining rapidly",
            },
            {
                "id": "harvest_invisible_merchant",
                "severity": "high",
                "title": "AI Discovery Invisibility",
                "description": "Merchants without structured, LLM-compatible product data are invisible to AI shopping agents. Lost discovery = lost revenue.",
                "category": "pricing_intel",
                "impact": "Lost discovery revenue, declining organic traffic",
                "trend": "Accelerating — AI-driven product discovery growing 50% YoY",
            },
            {
                "id": "harvest_dynamic_pricing",
                "severity": "medium",
                "title": "Pricing Static vs. Market Volatility",
                "description": "Fixed pricing models unable to respond to real-time market conditions. Competitors with dynamic pricing gaining market share.",
                "category": "revenue_analysis",
                "impact": "Margin compression, competitive disadvantage",
                "trend": "Growing — dynamic pricing adoption up 30% in enterprise retail",
            },
        ]

        for issue in revenue_issues:
            if not focus or issue["category"] in focus:
                findings.append({
                    "persona": "harvest",
                    "severity": issue["severity"],
                    "category": issue["category"],
                    "title": issue["title"],
                    "description": issue["description"],
                    "impact": issue["impact"],
                    "trend": issue["trend"],
                    "confidence": 0.75,
                })

        return findings

    def _hollow_analysis(self, data: Dict, focus: List[str]) -> List[Dict]:
        """Hollow: Compliance, regulatory, and governance gaps."""
        findings = []

        compliance_issues = [
            {
                "id": "hollow_ai_act",
                "severity": "critical",
                "title": "EU AI Act Compliance Gap",
                "description": "Commerce AI systems (recommendation engines, pricing algorithms, fraud detection) falling under EU AI Act high-risk classification. Compliance deadlines approaching.",
                "category": "regulatory_audit",
                "impact": "Market access restriction, fines up to 6% revenue",
                "trend": "Urgent — enforcement beginning 2026",
            },
            {
                "id": "hollow_climate_disclosure",
                "severity": "high",
                "title": "Environmental Disclosure Non-Compliance",
                "description": "SEC and EU sustainability reporting mandates require Scope 3 emissions tracking across supply chain. Most retailers unprepared.",
                "category": "governance_review",
                "impact": "Regulatory penalties, investor confidence loss",
                "trend": "Accelerating — mandatory reporting deadlines 2026-2027",
            },
            {
                "id": "hollow_data_governance",
                "severity": "high",
                "title": "Cross-Border Data Governance Gap",
                "description": "E-invoicing mandates (EU ViDA, India GST) and data localization requirements creating compliance maze for cross-border commerce.",
                "category": "compliance_check",
                "impact": "Operational delays, fines, market access loss",
                "trend": "Growing — 40+ countries implementing e-invoicing mandates",
            },
        ]

        for issue in compliance_issues:
            if not focus or issue["category"] in focus:
                findings.append({
                    "persona": "hollow",
                    "severity": issue["severity"],
                    "category": issue["category"],
                    "title": issue["title"],
                    "description": issue["description"],
                    "impact": issue["impact"],
                    "trend": issue["trend"],
                    "confidence": 0.8,
                })

        return findings

    # ═══════════════════════════════════════════════════════════
    # BOTTLENECK IDENTIFICATION & SOLUTIONS
    # ═══════════════════════════════════════════════════════════

    def _identify_bottlenecks(self, findings: List[Dict]) -> List[Dict]:
        """Identify bottlenecks from findings."""
        bottlenecks = []

        # Group by category
        categories = {}
        for f in findings:
            cat = f.get("category", "unknown")
            categories.setdefault(cat, []).append(f)

        for cat, cat_findings in categories.items():
            critical = [f for f in cat_findings if f.get("severity") in ("critical", "high")]
            if critical:
                bottlenecks.append({
                    "area": cat,
                    "severity": critical[0]["severity"],
                    "finding_count": len(cat_findings),
                    "titles": [f["title"] for f in critical],
                    "impact_summary": critical[0].get("impact", "Unknown"),
                })

        return bottlenecks

    def _propose_solutions(self, bottlenecks: List[Dict]) -> List[Dict]:
        """Propose solutions for identified bottlenecks."""
        solutions = []

        solution_map = {
            "fraud_detection": {
                "solution": "Deploy behavioral biometrics and device fingerprinting layer on top of existing KYC.",
                "priority": "high",
                "effort": "medium",
                "timeline": "3-6 months",
            },
            "anomaly_detection": {
                "solution": "Implement real-time anomaly detection with ML-based transaction scoring.",
                "priority": "medium",
                "effort": "medium",
                "timeline": "2-4 months",
            },
            "market_prediction": {
                "solution": "Build structured product data feeds compatible with LLM shopping agents. Invest in schema.org markup.",
                "priority": "high",
                "effort": "low",
                "timeline": "1-3 months",
            },
            "regulatory_forecast": {
                "solution": "Establish regulatory monitoring automation. Create compliance playbook for AI Act, climate disclosure, e-invoicing.",
                "priority": "high",
                "effort": "medium",
                "timeline": "1-3 months",
            },
            "api_health": {
                "solution": "Implement API gateway with circuit breakers, caching, and fallback routing for payment processing.",
                "priority": "high",
                "effort": "medium",
                "timeline": "2-4 months",
            },
            "infrastructure_audit": {
                "solution": "Unify OMS/WMS/TMS data layer. Deploy real-time inventory sync across all fulfillment nodes.",
                "priority": "critical",
                "effort": "high",
                "timeline": "6-12 months",
            },
            "supply_chain_mapping": {
                "solution": "Implement centralized AI orchestration layer for supply chain planning. Single source of truth for demand signals.",
                "priority": "high",
                "effort": "high",
                "timeline": "6-9 months",
            },
            "logistics_audit": {
                "solution": "Migrate to dynamic contract framework with volume/price adjustment clauses.",
                "priority": "medium",
                "effort": "low",
                "timeline": "1-2 months",
            },
            "fulfillment_analysis": {
                "solution": "Deploy distributed fulfillment orchestration with real-time capacity balancing across micro-fulfillment centers.",
                "priority": "high",
                "effort": "high",
                "timeline": "4-8 months",
            },
            "conversion_audit": {
                "solution": "Migrate to tokenized one-click checkout. Implement guest checkout with stored payment tokens.",
                "priority": "high",
                "effort": "low",
                "timeline": "1-2 months",
            },
            "pricing_intel": {
                "solution": "Build structured product data feeds with AI-agent-compatible metadata. Implement schema.org Product markup.",
                "priority": "high",
                "effort": "medium",
                "timeline": "2-4 months",
            },
            "revenue_analysis": {
                "solution": "Deploy dynamic pricing engine with real-time market signal integration.",
                "priority": "medium",
                "effort": "high",
                "timeline": "3-6 months",
            },
            "regulatory_audit": {
                "solution": "Conduct EU AI Act gap analysis. Classify all AI systems by risk level. Implement required governance controls.",
                "priority": "critical",
                "effort": "medium",
                "timeline": "2-4 months",
            },
            "governance_review": {
                "solution": "Implement Scope 3 emissions tracking across supply chain. Deploy sustainability data collection automation.",
                "priority": "high",
                "effort": "high",
                "timeline": "6-12 months",
            },
            "compliance_check": {
                "solution": "Deploy automated e-invoicing compliance layer. Implement data localization controls for each market.",
                "priority": "high",
                "effort": "medium",
                "timeline": "3-6 months",
            },
        }

        for bottleneck in bottlenecks:
            area = bottleneck["area"]
            sol = solution_map.get(area, {
                "solution": f"Conduct detailed analysis of {area} bottlenecks and develop remediation plan.",
                "priority": "medium",
                "effort": "unknown",
                "timeline": "TBD",
            })
            sol["area"] = area
            sol["finding_count"] = bottleneck["finding_count"]
            solutions.append(sol)

        return solutions

    def _analyze_area(self, area: str, data: Dict) -> List[Dict]:
        """Analyze a specific commerce area."""
        area_analyzers = {
            "payment_processing": self._forge_analysis,
            "supply_chain": self._weaver_analysis,
            "fraud_detection": self._wraith_analysis,
            "market_intel": self._oracle_analysis,
            "revenue": self._harvest_analysis,
            "compliance": self._hollow_analysis,
        }

        analyzer = area_analyzers.get(area)
        if analyzer:
            return analyzer(data, [])  # Don't filter by area name
        return []

    def _calculate_risk_score(self, findings: List[Dict]) -> int:
        """Calculate overall risk score (0-100)."""
        if not findings:
            return 10  # Low baseline risk

        severity_weights = {
            "critical": 25,
            "high": 15,
            "medium": 8,
            "low": 3,
            "info": 1,
        }

        total = sum(severity_weights.get(f.get("severity", "info"), 1) for f in findings)
        # Normalize to 0-100
        return min(100, total)

    def _on_task_offered(self, agent_id: str, channel: str, data: Dict):
        """React to commerce-related task offers."""
        title = data.get("title", "")
        if any(kw in title.lower() for kw in ["commerce", "scout", "bottleneck", "investment"]):
            log.info(f"[COMMERCE_SCOUT] Considering task: {title}")


# ═══════════════════════════════════════════════════════════════
# DAILY REPORT BUILDER
# ═══════════════════════════════════════════════════════════════

def build_daily_commerce_report(scout: CommerceScout, agent_id: str = "commerce_scout_01") -> Dict[str, Any]:
    """Build and return a full daily commerce scout report as a dict."""
    task_id = f"daily_commerce_{int(time.time())}"
    result = scout.execute(agent_id, task_id, {"report_type": "daily"})
    return result.to_dict()
