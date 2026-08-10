#!/usr/bin/env python3
"""
security_policy.py — Helical Security Policy Loader & Enforcer.

Loads ``security_policy.yaml`` and provides programmatic access to the
helical security model: subsystem operation gates, state knot unraveling,
tier-key bindings, and audit trail recording.

Usage::

    from security_policy import HelicalSecurityPolicy

    policy = HelicalSecurityPolicy.load()

    # Check if an agent can perform an operation
    if policy.authorize(agent_tier=3, operation="run_audit"):
        # allowed — tier 3 has audit_run key

    # Unravel a state knot (multi-key authorization)
    result = policy.unravel_knot(
        knot_name="full_legal_audit",
        keys_presented=["audit_run", "llm_escalation", "guidance_request"],
        agent_tier=4,
        agent_id="agent-7f3a",
    )
    # result.granted → True/False
    # result.reason → "granted" | "denied_tier" | "denied_key" | "denied_order"
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_HERE = Path(__file__).resolve().parent
_POLICY_PATH = _HERE / "security_policy.yaml"
_AUDIT_LOG_PATH = _HERE / "logs" / "security_audit.jsonl"

logger = logging.getLogger("SyntaxIntelligence.security_policy")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class KnotResult:
    """Result of a state knot unravel attempt."""

    granted: bool
    reason: str  # granted | denied_tier | denied_key | denied_order
    missing_keys: List[str] = field(default_factory=list)
    wrong_order: bool = False
    audit_entry: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorizationResult:
    """Result of a single-key authorization check."""

    granted: bool
    reason: str
    required_tier: int = 0
    required_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Security Policy
# ---------------------------------------------------------------------------


class HelicalSecurityPolicy:
    """Load and enforce the helical security policy.

    This is a singleton-like loader — call ``HelicalSecurityPolicy.load()``
    to get the canonical instance.  It reads ``security_policy.yaml`` once
    and caches the parsed structure.
    """

    _instance: Optional["HelicalSecurityPolicy"] = None

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data
        self._meta = data.get("meta", {})
        self._subsystems: Dict[str, Dict[str, Any]] = data.get("subsystems", {})
        self._knots: Dict[str, Dict[str, Any]] = data.get("state_knots", {})
        self._bindings: Dict[str, Dict[str, Any]] = data.get(
            "tier_key_bindings", {}
        )
        self._audit_config: Dict[str, Any] = data.get("audit_trail", {})
        self._override: Dict[str, Any] = data.get("emergency_override", {})
        self._audit_enabled = self._audit_config.get("enabled", True)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "HelicalSecurityPolicy":
        """Load the security policy from YAML. Caches after first load."""
        if cls._instance is not None:
            return cls._instance

        p = path or _POLICY_PATH
        try:
            import yaml
        except ImportError:
            # Fallback: minimal inline parser for stdlib-only envs
            raise ImportError(
                "PyYAML is required to load security_policy.yaml. "
                "Install with: pip install pyyaml"
            )

        if not p.exists():
            raise FileNotFoundError(
                f"Security policy not found at {p}. "
                "Run 'task security-policy' to validate."
            )

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError("security_policy.yaml is not a valid mapping.")

        cls._instance = cls(data)
        logger.info(
            "Security policy loaded — version %s, %d subsystems, %d state knots",
            cls._instance._meta.get("version", "unknown"),
            len(cls._instance._subsystems),
            len(cls._instance._knots),
        )
        return cls._instance

    @classmethod
    def reload(cls) -> "HelicalSecurityPolicy":
        """Force-reload the policy from disk."""
        cls._instance = None
        return cls.load()

    # ------------------------------------------------------------------
    # Inquiry
    # ------------------------------------------------------------------

    @property
    def version(self) -> str:
        return str(self._meta.get("version", "unknown"))

    @property
    def subsystems(self) -> List[str]:
        return sorted(self._subsystems.keys())

    @property
    def state_knots(self) -> List[str]:
        return sorted(self._knots.keys())

    def get_subsystem(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a subsystem definition by name."""
        return self._subsystems.get(name)

    def get_knot(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a state knot definition by name."""
        return self._knots.get(name)

    # ------------------------------------------------------------------
    # Key resolution
    # ------------------------------------------------------------------

    def keys_for_tier(self, tier: int) -> Set[str]:
        """Return all keys available to an agent at the given tier."""
        keys: Set[str] = set()
        binding_key = f"tier_{tier}_"

        for bkey, binding in self._bindings.items():
            if not bkey.startswith(binding_key):
                continue
            for k in binding.get("keys", []):
                if k != "none":
                    keys.add(k)
            # Inherit from parent tier
            parent = binding.get("inherits")
            if parent:
                parent_keys = self._keys_from_binding(parent)
                keys.update(parent_keys)

        return keys

    def _keys_from_binding(self, binding_name: str) -> Set[str]:
        """Resolve keys from a tier binding by name."""
        binding = self._bindings.get(binding_name, {})
        keys: Set[str] = set()
        for k in binding.get("keys", []):
            if k != "none":
                keys.add(k)
        parent = binding.get("inherits")
        if parent:
            keys.update(self._keys_from_binding(parent))
        return keys

    def tier_for_key(self, key_name: str) -> int:
        """Find the minimum tier required to hold a key."""
        for tier in range(6):
            if key_name in self.keys_for_tier(tier):
                return tier
        return 99  # key not found — effectively impossible

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def authorize(
        self,
        agent_tier: int,
        operation: str,
        *,
        subsystem: Optional[str] = None,
        agent_id: str = "",
    ) -> AuthorizationResult:
        """Check if an agent at the given tier can perform an operation.

        If *subsystem* is provided, only that subsystem is searched.
        Otherwise all subsystems are checked.
        """
        subs_to_check = (
            [subsystem] if subsystem else list(self._subsystems.keys())
        )

        for sub_name in subs_to_check:
            sub = self._subsystems.get(sub_name)
            if not sub:
                continue
            ops = sub.get("operations", {})
            if operation not in ops:
                continue

            op_def = ops[operation]
            required_tier = int(op_def.get("tier_floor", 99))
            required_key = op_def.get("key_required")
            required_keys: List[str] = (
                required_key if isinstance(required_key, list)
                else [required_key] if required_key and required_key != "none"
                else []
            )

            if agent_tier < required_tier:
                return AuthorizationResult(
                    granted=False,
                    reason=f"denied_tier (need {required_tier}, have {agent_tier})",
                    required_tier=required_tier,
                    required_key=required_keys[0] if required_keys else None,
                )

            agent_keys = self.keys_for_tier(agent_tier)
            missing = [
                k for k in required_keys if k not in agent_keys
            ]
            if missing:
                return AuthorizationResult(
                    granted=False,
                    reason=f"denied_key (missing: {missing})",
                    required_tier=required_tier,
                    required_key=missing[0],
                )

            # All checks passed
            return AuthorizationResult(
                granted=True,
                reason="granted",
                required_tier=required_tier,
                required_key=required_keys[0] if required_keys else None,
            )

        return AuthorizationResult(
            granted=False,
            reason=f"denied: unknown operation '{operation}'",
        )

    def unravel_knot(
        self,
        knot_name: str,
        keys_presented: List[str],
        agent_tier: int,
        agent_id: str = "",
    ) -> KnotResult:
        """Attempt to unravel a state knot.

        A state knot requires multiple keys presented in a specific
        helical order.  This method checks:
        1. All required keys are present
        2. Keys are presented in the correct helical order
        3. The agent's tier can hold each key

        Returns a ``KnotResult`` with ``granted`` and ``reason``.
        """
        knot = self._knots.get(knot_name)
        if not knot:
            return KnotResult(
                granted=False,
                reason=f"denied: unknown knot '{knot_name}'",
                missing_keys=[knot_name],
            )

        required_keys: List[str] = list(knot.get("required_keys", []))
        helical_order: List[str] = list(knot.get("helical_order", required_keys))
        agent_keys = self.keys_for_tier(agent_tier)

        # Check 1: all required keys are in agent's keyring
        missing = [k for k in required_keys if k not in agent_keys]
        if missing:
            self._write_audit(
                agent_id=agent_id,
                agent_tier=agent_tier,
                key_attempted=missing[0],
                keys_presented=keys_presented,
                state_knot=knot_name,
                helical_position=0,
                result="denied_key",
                reason=f"missing keys: {missing}",
            )
            return KnotResult(
                granted=False,
                reason=f"denied_key (missing: {missing})",
                missing_keys=missing,
            )

        # Check 2: keys are in helical order
        presented_set = set(keys_presented)
        order_map = {k: i for i, k in enumerate(helical_order)}
        # Filter to only keys that are in the helical order AND were presented
        ordered_presented = [
            k for k in keys_presented if k in order_map
        ]
        # Build what the correct order would be for this set of keys
        expected = [k for k in helical_order if k in presented_set]

        if ordered_presented != expected:
            self._write_audit(
                agent_id=agent_id,
                agent_tier=agent_tier,
                key_attempted="order_check",
                keys_presented=keys_presented,
                state_knot=knot_name,
                helical_position=-1,
                result="denied_order",
                reason=f"expected {expected}, got {ordered_presented}",
            )
            return KnotResult(
                granted=False,
                reason="denied_order",
                wrong_order=True,
            )

        # All checks passed
        audit = self._write_audit(
            agent_id=agent_id,
            agent_tier=agent_tier,
            key_attempted="state_knot_unravel",
            keys_presented=keys_presented,
            state_knot=knot_name,
            helical_position=len(helical_order),
            result="granted",
            reason="all keys presented in correct helical order",
        )
        return KnotResult(
            granted=True,
            reason="granted",
            audit_entry=audit,
        )

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        *,
        agent_id: str,
        agent_tier: int,
        key_attempted: str,
        keys_presented: List[str],
        state_knot: str,
        helical_position: int,
        result: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Write an entry to the security audit trail."""
        entry = {
            "timestamp": time.time(),
            "agent_id": agent_id,
            "agent_tier": agent_tier,
            "key_attempted": key_attempted,
            "keys_presented": keys_presented,
            "state_knot": state_knot or None,
            "helical_position": helical_position,
            "result": result,
            "reason": reason,
        }

        if self._audit_enabled:
            try:
                log_dir = _AUDIT_LOG_PATH.parent
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass  # audit logging is best-effort

        return entry

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def report(self) -> None:
        """Print a human-readable security policy summary to stdout."""
        print(f"\n{'═' * 60}")
        print("  HELICAL SECURITY POLICY")
        print(f"  Version {self.version}")
        print(f"{'═' * 60}")
        print(f"  Subsystems: {len(self._subsystems)}")
        print(f"  State Knots: {len(self._knots)}")
        print(f"  Audit Trail: {'ENABLED' if self._audit_enabled else 'DISABLED'}")
        print(f"{'═' * 60}\n")

        print("  SUBSYSTEMS:")
        for name, sub in sorted(self._subsystems.items()):
            ops_count = len(sub.get("operations", {}))
            floor = sub.get("tier_floor", 0)
            print(f"    {sub.get('id', name):8}  floor=T{floor}  {ops_count} ops  {name}")

        print(f"\n  STATE KNOTS ({len(self._knots)}):")
        for name, knot in sorted(self._knots.items()):
            keys = len(knot.get("required_keys", []))
            order = " → ".join(knot.get("helical_order", []))
            human = knot.get("human_approval_required", False)
            hflag = " [HUMAN]" if human else ""
            print(f"    {name}: {keys} keys  {order}{hflag}")

        print(f"\n  TIER-KEY BINDINGS:")
        for tier in range(6):
            keys = self.keys_for_tier(tier)
            tier_names = [
                "Recruit", "Worker", "Specialist",
                "Operative", "Architect", "Council",
            ]
            name = tier_names[tier] if tier < len(tier_names) else f"T{tier}"
            print(f"    T{tier} {name}: {len(keys)} keys — {sorted(keys)[:8]}...")

        print()

    def to_dict(self) -> Dict[str, Any]:
        """Return the full policy as a dict for programmatic use."""
        return dict(self._data)


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------


def smoke_test() -> Dict[str, Any]:
    """Verify the security policy loads and basic authorization works."""
    policy = HelicalSecurityPolicy.load()

    # Test 1: Tier 2 can run audit
    r1 = policy.authorize(agent_tier=2, operation="run_audit")
    assert r1.granted, f"T2 should be able to run_audit: {r1.reason}"

    # Test 2: Tier 1 cannot run audit
    r2 = policy.authorize(agent_tier=1, operation="run_audit")
    assert not r2.granted, f"T1 should not be able to run_audit"

    # Test 3: Tier 5 can amend charter
    r3 = policy.authorize(agent_tier=5, operation="amend_charter")
    assert r3.granted, f"T5 should be able to amend_charter: {r3.reason}"

    # Test 4: Tier 3 cannot amend charter
    r4 = policy.authorize(agent_tier=3, operation="amend_charter")
    assert not r4.granted, f"T3 should not be able to amend_charter"

    # Test 5: State knot unravel
    r5 = policy.unravel_knot(
        knot_name="full_legal_audit",
        keys_presented=["audit_run", "llm_escalation", "guidance_request"],
        agent_tier=4,
        agent_id="test-agent",
    )
    # T4 has audit_run (T2), llm_escalation (T3), guidance_request (T4) — yes
    assert r5.granted, f"Knot should unravel: {r5.reason}"

    # Test 6: Wrong helical order
    r6 = policy.unravel_knot(
        knot_name="full_legal_audit",
        keys_presented=["guidance_request", "audit_run", "llm_escalation"],
        agent_tier=4,
        agent_id="test-agent",
    )
    assert not r6.granted, f"Wrong order should be denied: {r6.reason}"
    assert r6.wrong_order

    return {
        "status": "ok",
        "version": policy.version,
        "subsystems": len(policy._subsystems),
        "state_knots": len(policy._knots),
        "tests_passed": 6,
    }


if __name__ == "__main__":
    policy = HelicalSecurityPolicy.load()
    if "--smoke" in sys.argv:
        result = smoke_test()
        print(json.dumps(result, indent=2))
    else:
        policy.report()
