#!/usr/bin/env python3
"""
Syntax Intelligence — Config Loader
Loads syntax_config.yaml and applies weight/temp overrides to agent outfits.
"""

import yaml
import threading
from pathlib import Path
from typing import Dict, Any, Optional

_CONFIG_PATH = Path(__file__).parent / "syntax_config.yaml"
_config_cache: Optional[Dict] = None
_config_lock = threading.Lock()


def load_config() -> Dict[str, Any]:
    """Load the global Syntax config (thread-safe, cached)."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        with open(_CONFIG_PATH, 'r') as f:
            _config_cache = yaml.safe_load(f)
        return _config_cache


def get_agent_weight(agent_id: str) -> Dict[str, Any]:
    """Get weight overrides for a specific agent by ID or partial match."""
    cfg = load_config()
    weights = cfg.get("agent_weights", {})

    # Direct match
    if agent_id in weights:
        return weights[agent_id]

    # Fuzzy match: agent_id contains key or key contains agent_id
    for key, val in weights.items():
        if key in agent_id or agent_id in key:
            return val

    return {}


def get_swarm_config() -> Dict[str, Any]:
    """Get swarm-level config (heartbeat, memory, broadcast, port)."""
    return load_config().get("swarm", {})


def get_boardroom_config() -> Dict[str, Any]:
    """Get boardroom configuration."""
    return load_config().get("boardroom", {})


def get_scout_config() -> Dict[str, Any]:
    """Get scout/crawler configuration."""
    return load_config().get("scouts", {})


def get_section(section: str) -> Dict[str, Any]:
    """Get an arbitrary config section by name."""
    return load_config().get(section, {})


def apply_config_to_outfit(outfit_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply config overrides (temperature, weight, custom prompts) to an
    agent outfit dict. Returns the modified dict.
    """
    agent_id = outfit_dict.get("agent_id", "")
    weight_cfg = get_agent_weight(agent_id)

    if not weight_cfg.get("enabled", True):
        outfit_dict["status"] = "disabled"
        return outfit_dict

    # Temperature override
    if "temperature_override" in weight_cfg:
        outfit_dict["temperature"] = weight_cfg["temperature_override"]

    # Weight
    if "weight" in weight_cfg:
        outfit_dict["weight"] = weight_cfg["weight"]

    # Custom prompt suffix
    if "custom_prompt_suffix" in weight_cfg:
        outfit_dict["custom_prompt"] = weight_cfg["custom_prompt_suffix"]

    # Domain focus override
    if "domain_focus" in weight_cfg:
        outfit_dict["domain"] = weight_cfg["domain_focus"]

    return outfit_dict
