"""Cutting tools and the tool database."""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

TOOL_TYPES: dict[str, str] = {
    "end_mill": "End Mill",
    "ball_nose": "Ball Nose",
    "bull_nose": "Bull Nose (radiused)",
    "v_bit": "V-Bit",
    "engraving": "Engraving (tapered, flat tip)",
    "drill": "Drill",
}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Tool:
    name: str = "End Mill 6 mm"
    type: str = "end_mill"
    diameter: float = 6.0
    number: int = 1
    angle: float = 90.0          # included angle for V-bits / engraving / drill points
    tip_diameter: float = 0.0    # flat at the tip (engraving tools, flat V-bits)
    corner_radius: float = 0.0   # bull nose
    flute_length: float = 22.0
    flutes: int = 2
    stepdown: float = 3.0
    stepover: float = 2.4
    feed_rate: float = 3000.0    # mm/min
    plunge_rate: float = 1000.0  # mm/min
    spindle_speed: int = 18000   # rpm
    spindle_dir: str = "cw"      # cw | ccw
    coolant: str = "off"         # off | mist | flood
    group: str = "End Mills"
    notes: str = ""
    id: str = field(default_factory=_new_id)

    @property
    def radius(self) -> float:
        return self.diameter / 2.0

    @property
    def is_tapered(self) -> bool:
        return self.type in ("v_bit", "engraving")

    def half_angle(self) -> float:
        return math.radians(max(1e-3, min(179.9, self.angle)) / 2.0)

    def radius_at_depth(self, depth: float) -> float:
        """Cutting radius at ``depth`` (>= 0) above the tool tip."""
        R = self.radius
        d = max(0.0, depth)
        if self.type in ("v_bit", "engraving"):
            return min(R, self.tip_diameter / 2.0 + d * math.tan(self.half_angle()))
        if self.type == "ball_nose":
            if d >= R:
                return R
            return math.sqrt(max(0.0, R * R - (R - d) ** 2))
        if self.type == "bull_nose" and self.corner_radius > 0:
            rc = min(self.corner_radius, R)
            if d >= rc:
                return R
            return R - rc + math.sqrt(max(0.0, rc * rc - (rc - d) ** 2))
        return R

    def depth_for_radius(self, r: float) -> float:
        """Depth below the surface at which a tapered tool cuts a radius ``r``."""
        if not self.is_tapered:
            return 0.0
        r0 = self.tip_diameter / 2.0
        return max(0.0, (r - r0) / math.tan(self.half_angle()))

    def copy(self, new_id: bool = False) -> "Tool":
        t = Tool(**asdict(self))
        if new_id:
            t.id = _new_id()
        return t

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Tool":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def describe(self) -> str:
        t = TOOL_TYPES.get(self.type, self.type)
        if self.is_tapered:
            return f"{self.name} ({t} {self.angle:g}°, D{self.diameter:g})"
        return f"{self.name} ({t} D{self.diameter:g})"


def default_tools() -> list[Tool]:
    return [
        Tool("End Mill 3 mm", "end_mill", 3.0, 1, stepdown=1.5, stepover=1.2, feed_rate=2000,
             plunge_rate=600, spindle_speed=20000, flute_length=12),
        Tool("End Mill 6 mm", "end_mill", 6.0, 2, stepdown=3.0, stepover=2.4, feed_rate=3500,
             plunge_rate=1000, spindle_speed=18000),
        Tool("Compression 6 mm", "end_mill", 6.0, 3, stepdown=6.0, stepover=2.4, feed_rate=5000,
             plunge_rate=1200, spindle_speed=18000, flute_length=25),
        Tool("End Mill 8 mm", "end_mill", 8.0, 4, stepdown=4.0, stepover=3.2, feed_rate=4000,
             plunge_rate=1200, spindle_speed=18000, flute_length=30),
        Tool("Ball Nose 3 mm", "ball_nose", 3.0, 5, stepdown=1.0, stepover=0.3, feed_rate=2000,
             plunge_rate=600, spindle_speed=20000, group="Ball Nose"),
        Tool("Ball Nose 6 mm", "ball_nose", 6.0, 6, stepdown=2.0, stepover=0.6, feed_rate=3000,
             plunge_rate=800, spindle_speed=18000, group="Ball Nose"),
        Tool("V-Bit 60°", "v_bit", 12.7, 7, angle=60, stepdown=3.0, stepover=0.5, feed_rate=2500,
             plunge_rate=800, spindle_speed=18000, group="V-Bits"),
        Tool("V-Bit 90°", "v_bit", 12.7, 8, angle=90, stepdown=3.0, stepover=0.5, feed_rate=2500,
             plunge_rate=800, spindle_speed=18000, group="V-Bits"),
        Tool("Engraver 30° 0.2 mm", "engraving", 3.175, 9, angle=30, tip_diameter=0.2, stepdown=0.5,
             stepover=0.1, feed_rate=1500, plunge_rate=400, spindle_speed=24000, group="Engraving"),
        Tool("Drill 5 mm", "drill", 5.0, 10, angle=118, stepdown=5.0, feed_rate=1000,
             plunge_rate=600, spindle_speed=12000, group="Drills"),
        Tool("Drill 8 mm", "drill", 8.0, 11, angle=118, stepdown=5.0, feed_rate=1000,
             plunge_rate=600, spindle_speed=10000, group="Drills"),
    ]


class ToolLibrary:
    """A list of tools persisted as JSON."""

    def __init__(self, tools: list[Tool] | None = None, path: Path | None = None):
        self.tools: list[Tool] = list(tools) if tools is not None else default_tools()
        self.path = path

    def get(self, tool_id: str) -> Tool | None:
        for t in self.tools:
            if t.id == tool_id:
                return t
        return None

    def find(self, name: str) -> Tool | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def add(self, tool: Tool) -> Tool:
        self.tools.append(tool)
        return tool

    def remove(self, tool_id: str) -> None:
        self.tools = [t for t in self.tools if t.id != tool_id]

    def groups(self) -> list[str]:
        seen: list[str] = []
        for t in self.tools:
            if t.group not in seen:
                seen.append(t.group)
        return seen

    def to_dict(self) -> dict:
        return {"format": "iyicam-tools", "version": 1, "tools": [t.to_dict() for t in self.tools]}

    def save(self, path: Path | None = None) -> None:
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        self.path = path

    @classmethod
    def load(cls, path: Path) -> "ToolLibrary":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([Tool.from_dict(d) for d in data.get("tools", [])], path)

    @classmethod
    def load_or_default(cls, path: Path) -> "ToolLibrary":
        path = Path(path)
        if path.exists():
            try:
                return cls.load(path)
            except (OSError, ValueError, TypeError):
                pass
        return cls(None, path)
