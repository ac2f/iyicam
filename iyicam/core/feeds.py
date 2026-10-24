"""Feeds and speeds from chip load (router bits)."""
from __future__ import annotations

import math
from dataclasses import dataclass

# chip load (mm per tooth) for a 6 mm cutter, depth of cut and stepover as fractions of the diameter
MATERIALS: dict[str, tuple[str, float, float, float]] = {
    "mdf": ("MDF", 0.30, 1.0, 0.45),
    "plywood": ("Plywood", 0.25, 1.0, 0.40),
    "softwood": ("Softwood", 0.28, 1.0, 0.40),
    "hardwood": ("Hardwood", 0.20, 0.75, 0.40),
    "chipboard": ("Chipboard / melamine", 0.25, 1.0, 0.40),
    "acrylic": ("Acrylic (cast)", 0.10, 0.5, 0.35),
    "hdpe": ("HDPE / plastics", 0.18, 0.75, 0.40),
    "pvc": ("PVC foam (Forex)", 0.25, 1.0, 0.45),
    "aluminium": ("Aluminium", 0.05, 0.25, 0.30),
    "brass": ("Brass", 0.03, 0.15, 0.25),
    "foam": ("Foam / tooling board", 0.40, 2.0, 0.50),
}


@dataclass
class Cutting:
    chip_load: float      # mm per tooth
    feed_rate: float      # mm/min
    plunge_rate: float    # mm/min
    stepdown: float       # mm
    stepover: float       # mm
    surface_speed: float  # m/min


def suggest(material: str, diameter: float, flutes: int, rpm: float) -> Cutting:
    """Starting values for a router bit; always check the tool maker's data."""
    _, base, depth_k, over_k = MATERIALS[material]
    scale = max(0.3, min(2.0, diameter / 6.0))
    chip = base * scale
    feed = rpm * max(1, flutes) * chip
    return Cutting(chip, feed, feed * 0.35, diameter * depth_k, diameter * over_k,
                   math.pi * diameter * rpm / 1000.0)
