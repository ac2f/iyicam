"""Application preferences stored as JSON in the user directory."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from ..core import paths


@dataclass
class Settings:
    units: str = "mm"                 # display units: mm | inch
    grid: float = 10.0
    snap_grid: bool = False
    snap_objects: bool = True
    show_toolpaths: bool = True
    show_tool_width: bool = False
    show_directions: bool = False
    auto_recalculate: bool = True     # recalculate toolpaths after edits (small projects)
    recent_files: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    last_post: str = "mach3"
    sim_resolution: float = 0.0       # 0 = automatic
    window_state: str = ""

    def add_recent(self, path: str) -> None:
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        del self.recent_files[10:]

    def save(self) -> None:
        f = paths.settings_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Settings":
        f = paths.settings_file()
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Units:
    """Conversion between internal millimetres and the display unit."""

    def __init__(self, name: str = "mm"):
        self.name = name

    @property
    def factor(self) -> float:
        return 25.4 if self.name == "inch" else 1.0

    @property
    def suffix(self) -> str:
        return " in" if self.name == "inch" else " mm"

    @property
    def decimals(self) -> int:
        return 4 if self.name == "inch" else 3

    def to_display(self, v_mm: float) -> float:
        return v_mm / self.factor

    def from_display(self, v: float) -> float:
        return v * self.factor

    def fmt(self, v_mm: float) -> str:
        return f"{self.to_display(v_mm):.{self.decimals}f}".rstrip("0").rstrip(".") + self.suffix
