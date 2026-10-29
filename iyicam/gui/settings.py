"""Application preferences stored as JSON in the user directory."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Iterable

from ..core import paths
from ..core.params import Param


@dataclass
class Settings:
    units: str = "mm"                 # display units: mm | inch
    grid: float = 10.0
    snap_grid: bool = False
    snap_objects: bool = True
    show_toolpaths: bool = True
    show_tool_width: bool = False
    show_directions: bool = False
    show_cut_order: bool = False      # number the cuts of the toolpaths on the drawing
    show_route: bool = False          # arrows for the rapid moves between the cuts
    show_starts: bool = False         # where every cut starts and closes (and whether that is a good place)
    strategy_directions: dict = field(default_factory=dict)  # cutting direction per cut order strategy
    sequence_defaults: dict = field(default_factory=dict)  # cut order / start point settings chosen last: new toolpaths
    memory: dict = field(default_factory=dict)  # what every dialog was last set to, by dialog (see remember)
    dirs: dict = field(default_factory=dict)    # the folder last used for every kind of file
    window_geometry: str = ""
    auto_recalculate: bool = True     # recalculate toolpaths after edits (small projects)
    recent_files: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    last_post: str = "mach3"
    sim_resolution: float = 0.0       # 0 = automatic
    window_state: str = ""
    theme: str = "system"             # system | light | dark
    open_tabs: list[str] = field(default_factory=list)   # the projects that were open when the program was closed
    active_tab: int = 0

    def add_recent(self, path: str) -> None:
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        del self.recent_files[10:]

    # -- what the dialogs remember ---------------------------------------------------------------
    def recall(self, key: str, params: Iterable[Param] | None = None) -> dict:
        """The values the dialog ``key`` was last accepted with. With ``params`` only values that still fit
        them come back (a known name, the right kind, in range, a choice that exists)."""
        stored = self.memory.get(key)
        if not isinstance(stored, dict):
            return {}
        if params is None:
            return dict(stored)
        out = {}
        for p in params:
            if p.name in stored and p.kind != "tool":
                try:
                    v = stored[p.name]
                    out[p.name] = p.coerce(v) if v is not None else None
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
        return out

    def remember(self, key: str, values: dict, params: Iterable[Param] | None = None, save: bool = True) -> None:
        """Keep the values of dialog ``key`` (the ones of ``params`` when given) for the next time."""
        names = None if params is None else {p.name for p in params if p.kind != "tool"}
        clean = {k: _json_safe(v) for k, v in values.items() if names is None or k in names}
        clean = {k: v for k, v in clean.items() if v is not _DROP}
        if self.memory.get(key) != clean:
            self.memory[key] = clean
            if save:
                self.save()

    def last_dir(self, kind: str) -> str:
        """The folder last used for files of this kind (empty: the program's default)."""
        for d in (self.dirs.get(kind), self.dirs.get("any")):
            if d and os.path.isdir(d):
                return d
        return ""

    def set_last_dir(self, kind: str, path: str) -> None:
        """Remember the folder of ``path`` (a file or a folder) for files of this kind."""
        if not path:
            return
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d and self.dirs.get(kind) != d:
            self.dirs[kind] = d
            self.dirs["any"] = d
            self.save()

    # -- file ------------------------------------------------------------------------------------
    def save(self) -> None:
        """Write the settings; the file is replaced in one piece, so a crash never leaves half of it. A user
        directory that cannot be written to is not an error of the program."""
        f = paths.settings_file()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass

    @classmethod
    def load(cls) -> "Settings":
        f = paths.settings_file()
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        default = cls()
        out = {}
        for fld in fields(cls):
            if fld.name not in data:
                continue
            v, d = data[fld.name], getattr(default, fld.name)
            # a value of another kind than the setting's (a hand-edited file) is ignored
            if isinstance(d, bool) != isinstance(v, bool) or not isinstance(v, type(d)) and not (
                    isinstance(d, float) and isinstance(v, int)):
                continue
            if isinstance(d, float):
                v = float(v)
            if fld.name == "units" and v not in ("mm", "inch"):
                continue
            if fld.name == "theme" and v not in ("system", "light", "dark"):
                continue
            out[fld.name] = v
        return cls(**out)


_DROP = object()


def _json_safe(v: Any) -> Any:
    """``v`` as something the settings file can hold, or ``_DROP``."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        items = [_json_safe(x) for x in v]
        return _DROP if any(x is _DROP for x in items) else items
    if isinstance(v, dict):
        items = {str(k): _json_safe(x) for k, x in v.items()}
        return _DROP if any(x is _DROP for x in items.values()) else items
    return _DROP


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
