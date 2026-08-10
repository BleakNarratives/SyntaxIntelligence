#!/usr/bin/env python3
"""
Syntax Intelligence — Costume Registry Loader
"Costumes and masks. Jewelry and makeup. Wigs."
Loads persona configurations from YAML and applies them to agent instances.
"""

import yaml
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

_REGISTRY_PATH = Path(__file__).parent / "costume_registry.yaml"
_registry_cache: Optional[Dict] = None
_registry_lock = threading.Lock()


def load_registry() -> Dict[str, Any]:
    """Load the full costume registry from YAML (thread-safe)."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    with _registry_lock:
        # Double-check inside lock in case another thread loaded it while we waited
        if _registry_cache is not None:
            return _registry_cache
        with open(_REGISTRY_PATH, 'r') as f:
            _registry_cache = yaml.safe_load(f)
        return _registry_cache


@dataclass
class Costume:
    """An agent's primary role/archetype."""
    id: str
    name: str
    emoji: str
    color: str
    domain: str
    voice: str
    temperature: float
    behavioral_flags: List[str]

    @classmethod
    def from_id(cls, costume_id: str) -> 'Costume':
        reg = load_registry()
        data = reg['costumes'].get(costume_id)
        if not data:
            raise KeyError(f"Costume '{costume_id}' not found in registry")
        return cls(**data)


@dataclass
class Mask:
    """Behavioral mode / stance overlay."""
    id: str
    name: str
    emoji: str
    description: str
    prompt_modifier: str

    @classmethod
    def from_id(cls, mask_id: str) -> 'Mask':
        reg = load_registry()
        data = reg['masks'].get(mask_id)
        if not data:
            raise KeyError(f"Mask '{mask_id}' not found in registry")
        return cls(**data)


@dataclass
class Jewelry:
    """Special capability / power."""
    id: str
    name: str
    emoji: str
    capability: str

    @classmethod
    def from_id(cls, jewelry_id: str) -> 'Jewelry':
        reg = load_registry()
        data = reg['jewelry'].get(jewelry_id)
        if not data:
            raise KeyError(f"Jewelry '{jewelry_id}' not found in registry")
        return cls(**data)


@dataclass
class Makeup:
    """Tone / finish / polish level."""
    id: str
    name: str
    emoji: str
    description: str
    temperature_modifier: float

    @classmethod
    def from_id(cls, makeup_id: str) -> 'Makeup':
        reg = load_registry()
        data = reg['makeup'].get(makeup_id)
        if not data:
            raise KeyError(f"Makeup '{makeup_id}' not found in registry")
        return cls(**data)


@dataclass
class Wig:
    """Temporary role shift / disguise."""
    id: str
    name: str
    emoji: str
    description: str

    @classmethod
    def from_id(cls, wig_id: str) -> 'Wig':
        reg = load_registry()
        data = reg['wigs'].get(wig_id)
        if not data:
            raise KeyError(f"Wig '{wig_id}' not found in registry")
        return cls(**data)


@dataclass
class AgentOutfit:
    """Complete agent assembly: costume + mask + jewelry + makeup + wigs."""
    agent_id: str
    costume: Costume
    mask: Mask
    jewelry: List[Jewelry] = field(default_factory=list)
    makeup: Optional[Makeup] = None
    wigs_available: List[Wig] = field(default_factory=list)
    active_wig: Optional[Wig] = None
    status: str = "idle"
    pulse_count: int = 0

    @property
    def effective_temperature(self) -> float:
        base = self.costume.temperature
        if self.makeup:
            base += self.makeup.temperature_modifier
        return max(0.1, min(1.0, base))

    @property
    def display_name(self) -> str:
        if self.active_wig:
            return f"{self.costume.name} (wearing {self.active_wig.name})"
        return self.costume.name

    @property
    def display_emoji(self) -> str:
        if self.active_wig:
            return self.active_wig.emoji
        return self.costume.emoji

    @property
    def display_color(self) -> str:
        return self.costume.color

    @property
    def jewelry_emojis(self) -> str:
        return " ".join(j.emoji for j in self.jewelry)

    def equip_wig(self, wig_id: str):
        """Put on a wig — temporarily shift roles."""
        self.active_wig = Wig.from_id(wig_id)

    def remove_wig(self):
        """Remove the wig — return to base costume."""
        self.active_wig = None

    def pulse(self):
        """Heartbeat — increment activity counter."""
        self.pulse_count += 1
        self.status = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "costume": self.costume.id,
            "costume_name": self.costume.name,
            "emoji": self.display_emoji,
            "color": self.display_color,
            "mask": self.mask.id,
            "mask_name": self.mask.name,
            "jewelry": [j.id for j in self.jewelry],
            "jewelry_emojis": self.jewelry_emojis,
            "makeup": self.makeup.id if self.makeup else None,
            "active_wig": self.active_wig.id if self.active_wig else None,
            "temperature": self.effective_temperature,
            "status": self.status,
            "pulse_count": self.pulse_count,
            "voice": self.costume.voice,
            "domain": self.costume.domain,
        }


def assemble_outfit(outfit_name: str) -> AgentOutfit:
    """Assemble a complete agent outfit from the registry by name."""
    reg = load_registry()
    data = reg['agent_outfits'].get(outfit_name)
    if not data:
        raise KeyError(f"Agent outfit '{outfit_name}' not found in registry")

    costume = Costume.from_id(data['costume'])
    mask = Mask.from_id(data['mask'])
    jewelry = [Jewelry.from_id(j) for j in data.get('jewelry', [])]
    makeup = Makeup.from_id(data['makeup']) if data.get('makeup') else None
    wigs = [Wig.from_id(w) for w in data.get('wigs_available', [])]

    return AgentOutfit(
        agent_id=data['agent_id'],
        costume=costume,
        mask=mask,
        jewelry=jewelry,
        makeup=makeup,
        wigs_available=wigs,
    )


def list_outfits() -> List[str]:
    """List all available pre-assembled agent outfits."""
    reg = load_registry()
    return list(reg.get('agent_outfits', {}).keys())


def list_all_costumes() -> List[Dict]:
    """List all available costumes."""
    reg = load_registry()
    return [{"id": k, "name": v["name"], "emoji": v["emoji"], "color": v["color"]}
            for k, v in reg.get("costumes", {}).items()]


def get_full_wardrobe() -> Dict[str, Any]:
    """Return the complete wardrobe for dashboard rendering."""
    reg = load_registry()
    return {
        "costumes": list_all_costumes(),
        "masks": [{"id": k, "name": v["name"], "emoji": v["emoji"]}
                  for k, v in reg.get("masks", {}).items()],
        "jewelry": [{"id": k, "name": v["name"], "emoji": v["emoji"]}
                    for k, v in reg.get("jewelry", {}).items()],
        "makeup": [{"id": k, "name": v["name"], "emoji": v["emoji"]}
                   for k, v in reg.get("makeup", {}).items()],
        "wigs": [{"id": k, "name": v["name"], "emoji": v["emoji"]}
                 for k, v in reg.get("wigs", {}).items()],
    }
