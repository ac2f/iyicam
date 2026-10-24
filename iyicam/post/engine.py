"""Template driven post-processor engine.

A post-processor is described by *options* (number formats, arc handling,
modal behaviour, line numbers, ...) and text *blocks* (header, tool change,
move templates, footer).  Definitions are usually TOML files (see
``iyicam/post/builtin``); Python subclasses can override any hook for
controller specific logic.

Move templates use word placeholders that expand to ``<letter><number>`` or to
nothing when the value is modal and unchanged::

    linear = "{N}{G}{X}{Y}{Z}{F}"    ->  G1X10.5Y3F2000

Every block can also use the job / tool variables documented in
``docs/postprocessors.md``.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math
import re
import string
from dataclasses import dataclass, field
from typing import Any

from ..cam.moves import (ArcMove, Comment, DrillCycle, Dwell, Feed, PathStats, Rapid, Toolpath, arc_sweep,
                         drill_expand, format_time)
from ..core.document import Project, Sheet

DEFAULT_OPTIONS: dict[str, Any] = {
    "units": "mm",               # mm | inch
    "line_ending": "lf",         # lf | crlf
    "decimals": 3,
    "feed_decimals": 0,
    "trailing_zeros": False,
    "leading_zero": True,
    "integer_point": False,      # write "10." instead of "10" (Fanuc style)
    "g_leading_zero": False,     # G00/G01 instead of G0/G1
    "arcs": "ijk",               # ijk | r | none
    "ijk": "incremental",        # incremental | absolute
    "arc_max_sweep": 180.0,      # degrees, larger arcs are split
    "arc_min_radius": 0.01,
    "arc_max_radius": 5000.0,
    "arc_tolerance": 0.005,      # used when arcs are converted into lines
    "helical": True,             # allow G2/G3 with a Z change
    "arc_xy_always": True,       # always write X and Y on arc moves
    "modal_coords": True,
    "modal_motion": True,
    "modal_feed": True,
    "rapid_z_first": True,       # split diagonal rapids so the tool never dives sideways
    "line_numbers": False,
    "line_number_start": 10,
    "line_number_step": 10,
    "line_number_max": 99999,
    "line_number_space": True,   # "N10 G1X.." vs "N10G1X.."
    "comments": True,
    "uppercase": True,
    "drill_cycles": False,       # canned cycles G81/G83 instead of expanded moves
    "dwell_units": "s",          # s | ms
    "tool_change_first": True,
    "max_spindle": 0,            # 0 = no limit
    "split_by_tool": False,      # one file per tool (single spindle machines)
    "rotary_letter": "A",        # angle word when the material is wrapped around a rotary axis
    "rotary_feed": "inverse_time",  # inverse_time (G93) | degrees (degrees count as units) | plain
}

DEFAULT_BLOCKS: dict[str, str] = {
    "header": "%\n({file_name})\n{unit_code} G90 G17",
    "first_tool_change": "",
    "tool_change": "M5\nT{tool_number} M6\nS{spindle_speed} {spindle_dir_code}",
    "toolpath_start": "({toolpath_name})",
    "toolpath_end": "",
    "rapid": "{G} {X} {Y} {Z}",
    "linear": "{G} {X} {Y} {Z} {F}",
    "arc": "{G} {X} {Y} {Z} {I} {J} {F}",
    "arc_r": "{G} {X} {Y} {Z} {R} {F}",
    "drill": "G98 G81 {X} {Y} {Z} {R} {F}",
    "drill_peck": "G98 G83 {X} {Y} {Z} {R} {Q} {F}",
    "drill_next": "{X} {Y}",
    "drill_end": "G80",
    "dwell": "G4 P{dwell}",
    "comment": "({text})",
    "footer": "M5\nG0 Z{safe_z}\nG0 X{home_x} Y{home_y}\nM30\n%",
    "inverse_time_on": "G93",
    "inverse_time_off": "G94",
}

ORIGINS = {
    "bottom_left": (0.0, 0.0), "bottom_center": (0.5, 0.0), "bottom_right": (1.0, 0.0),
    "center_left": (0.0, 0.5), "center": (0.5, 0.5), "center_right": (1.0, 0.5),
    "top_left": (0.0, 1.0), "top_center": (0.5, 1.0), "top_right": (1.0, 1.0),
}


@dataclass
class PostJob:
    toolpaths: list[Toolpath]
    project: Project
    sheet: Sheet
    file_name: str = "output"
    program_number: int = 1000
    extra: dict = field(default_factory=dict)  # additional template variables


class _Vars(dict):
    """Format mapping that reports unknown placeholders clearly."""

    def __missing__(self, key):
        raise KeyError(f"unknown post variable {{{key}}}")


class PostProcessor:
    """Template based post-processor.  Subclass and override hooks for custom logic."""

    def __init__(self, definition: dict | None = None, path=None):
        d = copy.deepcopy(definition or {})
        self.path = path
        self.id = d.get("id", "custom")
        self.name = d.get("name", self.id)
        self.description = d.get("description", "")
        self.extension = d.get("extension", "nc").lstrip(".")
        self.options = {**DEFAULT_OPTIONS, **d.get("options", {})}
        self.blocks = {**DEFAULT_BLOCKS, **d.get("blocks", {})}
        self.definition = d

    # -- public API -----------------------------------------------------------------
    def generate(self, job: PostJob) -> str:
        return self._generate(job, job.toolpaths)

    def generate_files(self, job: PostJob) -> list[tuple[str, str]]:
        """``[(file suffix, text)]``; several files when ``split_by_tool`` is set."""
        if not self.options["split_by_tool"]:
            return [("", self.generate(job))]
        groups: list[list[Toolpath]] = []
        for tp in job.toolpaths:
            if groups and groups[-1][-1].tool.number == tp.tool.number:
                groups[-1].append(tp)
            else:
                groups.append([tp])
        files = []
        for i, g in enumerate(groups):
            suffix = f"_{i + 1}_T{g[0].tool.number}"
            files.append((suffix, self._generate(job, g)))
        return files

    # -- coordinate handling ---------------------------------------------------------
    def _setup(self, job: PostJob) -> None:
        m = job.project.material
        fx, fy = ORIGINS.get(m.xy_origin, (0.0, 0.0))
        self._ox = fx * job.sheet.width
        self._oy = fy * job.sheet.height
        thick = job.project.sheet_thickness(job.sheet)
        self._oz = thick if m.z_zero == "bottom" else 0.0
        self._k = 1.0 / 25.4 if self.options["units"] == "inch" else 1.0
        self._rotary = m.rotary and m.rotary_diameter > 0
        self._circ = math.pi * m.rotary_diameter
        self._yl = self.options["rotary_letter"] if self._rotary else "Y"
        self._inverse = self._rotary and self.options["rotary_feed"] == "inverse_time"

    def tx(self, x: float) -> float:
        return (x - self._ox) * self._k

    def ty(self, y: float) -> float:
        """Output Y, or the angle in degrees when the sheet is wrapped around a rotary axis."""
        if self._rotary:
            return (y - self._oy) / self._circ * 360.0
        return (y - self._oy) * self._k

    def tz(self, z: float) -> float:
        return (z + self._oz) * self._k

    # -- number formatting -----------------------------------------------------------
    def fmt(self, v: float, decimals: int | None = None, coord: bool = True) -> str:
        o = self.options
        d = o["decimals"] if decimals is None else decimals
        if abs(v) < 0.5 * 10 ** (-d):
            v = 0.0
        s = f"{v:.{d}f}"
        if not o["trailing_zeros"] and "." in s:
            s = s.rstrip("0").rstrip(".")
        if s in ("-0", "-0."):
            s = "0"
        if coord and o["integer_point"] and "." not in s:
            s += "."
        if not o["leading_zero"]:
            if s.startswith("0.") and len(s) > 2:
                s = s[1:]
            elif s.startswith("-0.") and len(s) > 3:
                s = "-" + s[2:]
        return s

    def fmt_feed(self, f: float) -> str:
        return self.fmt(f * self._k, self.options["feed_decimals"], coord=False)

    def gcode(self, n: int) -> str:
        return f"G{n:02d}" if self.options["g_leading_zero"] else f"G{n}"

    # -- generation -------------------------------------------------------------------
    def _generate(self, job: PostJob, toolpaths: list[Toolpath]) -> str:
        self._setup(job)
        self.lines: list[str] = []
        self._last: dict[str, str | None] = {"X": None, "Y": None, "Z": None, "F": None, "G": None}
        self._pos: list[float] | None = None
        self._cycle: DrillCycle | None = None
        self._cycle_z0 = 0.0
        self._pending: Rapid | None = None
        stats = PathStats()
        for tp in toolpaths:
            stats = stats.merge(tp.stats(job.project.material.rapid_rate))
        base = self.job_vars(job, toolpaths, stats)
        self.block("header", base)
        if self._inverse:
            self.block("inverse_time_on", base)
        prev_tool = None
        for i, tp in enumerate(toolpaths):
            tv = {**base, **self.tool_vars(tp, prev_tool, job)}
            if prev_tool is None:
                if self.options["tool_change_first"]:
                    self.block("first_tool_change" if self.blocks.get("first_tool_change") else "tool_change", tv)
            elif prev_tool.number != tp.tool.number:
                self.block("tool_change", tv)
            elif self.blocks.get("tool_same"):
                self.block("tool_same", tv)
            self.block("toolpath_start", tv)
            # the first move of a toolpath always writes every coordinate
            self._last.update({"X": None, self._yl: None, "Z": None, "G": None})
            for m in tp.moves:
                self.move(m, tv)
            self._end_cycle(tv)
            self.block("toolpath_end", tv)
            prev_tool = tp.tool
        if self._inverse:
            self.block("inverse_time_off", base)
        self.block("footer", base)
        lines = self.finalize(self.lines, job)
        text = self._join(lines)
        from ..core.registry import REGISTRY
        for _, flt in REGISTRY.gcode_filters:
            text = flt(text, job, self)
        return text

    def _join(self, lines: list[str]) -> str:
        eol = "\r\n" if self.options["line_ending"] == "crlf" else "\n"
        return eol.join(lines) + eol

    def finalize(self, lines: list[str], job: PostJob) -> list[str]:
        o = self.options
        out = []
        n = o["line_number_start"]
        for ln in lines:
            if o["uppercase"] and not self._is_comment(ln):
                ln = ln.upper()
            if o["line_numbers"] and ln and not ln.startswith("%") and not self._is_comment(ln):
                ln = f"N{n}{' ' if o['line_number_space'] else ''}{ln}"
                n += o["line_number_step"]
                if n > o["line_number_max"]:
                    n = o["line_number_start"]
            out.append(ln)
        return out

    def _is_comment(self, ln: str) -> bool:
        return ln.startswith("(") or ln.startswith(";")

    # -- variables ---------------------------------------------------------------------
    def job_vars(self, job: PostJob, toolpaths: list[Toolpath], stats: PathStats) -> dict:
        m = job.project.material
        now = _dt.datetime.now()
        inch = self.options["units"] == "inch"
        v = {
            "file_name": job.file_name, "project_name": job.project.name, "sheet_name": job.sheet.name,
            "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"),
            "units": "inch" if inch else "mm", "unit_code": "G20" if inch else "G21",
            "program_number": job.program_number, "post_name": self.name,
            "material_thickness": self.fmt(job.project.sheet_thickness(job.sheet) * self._k),
            "sheet_width": self.fmt(job.sheet.width * self._k), "sheet_height": self.fmt(job.sheet.height * self._k),
            "safe_z": self.fmt(self.tz(m.safe_z)),
            "home_x": self.fmt(self.tx(m.home[0])), "home_y": self.fmt(self.ty(m.home[1])),
            "home_z": self.fmt(self.tz(m.home[2])),
            "toolpath_count": len(toolpaths), "total_time": format_time(stats.time_s), "N": "",
            "xmin": self.fmt(self.tx(stats.xmin) if math.isfinite(stats.xmin) else 0.0),
            "ymin": self.fmt(self.ty(stats.ymin) if math.isfinite(stats.ymin) else 0.0),
            "xmax": self.fmt(self.tx(stats.xmax) if math.isfinite(stats.xmax) else 0.0),
            "ymax": self.fmt(self.ty(stats.ymax) if math.isfinite(stats.ymax) else 0.0),
            "zmin": self.fmt(self.tz(stats.zmin)),
            "rotary_diameter": self.fmt(m.rotary_diameter * self._k) if m.rotary else "",
            "tools": ", ".join(sorted({f"T{t.tool.number} {t.tool.name}" for t in toolpaths})),
        }
        v.update(job.extra)
        return v

    def tool_vars(self, tp: Toolpath, prev, job: PostJob) -> dict:
        t = tp.tool
        rpm = t.spindle_speed
        if self.options["max_spindle"]:
            rpm = min(rpm, self.options["max_spindle"])
        coolant = {"mist": "M7", "flood": "M8"}.get(t.coolant, "")
        st = tp.stats(job.project.material.rapid_rate)
        return {
            "tool_number": t.number, "tool_name": t.name, "tool_type": t.type,
            "tool_diameter": self.fmt(t.diameter * self._k), "tool_angle": self.fmt(t.angle, 1, coord=False),
            "spindle_speed": int(round(rpm)), "spindle_dir_code": "M4" if t.spindle_dir == "ccw" else "M3",
            "coolant_code": coolant, "coolant_off": "M9" if coolant else "",
            "prev_tool_number": prev.number if prev else 0,
            "toolpath_name": tp.name, "feed_rate": self.fmt_feed(t.feed_rate), "plunge_rate": self.fmt_feed(t.plunge_rate),
            "toolpath_time": format_time(st.time_s),
        }

    # -- blocks --------------------------------------------------------------------------
    def block(self, name: str, variables: dict) -> None:
        tpl = self.blocks.get(name) or ""
        if not tpl:
            return
        if not self.options["comments"]:
            tpl = "\n".join(l for l in tpl.split("\n") if not self._is_comment(l.strip()))
        if self._rotary:  # Y positions are angles on a wrapped job
            tpl = re.sub(r"Y(?=\{(?:home_y|ymin|ymax)\})", self._yl, tpl)
        text = string.Formatter().vformat(tpl, (), _Vars(variables))
        for ln in text.split("\n"):
            self.emit(ln)

    def emit(self, line: str) -> None:
        ln = re.sub(r"[ \t]+", " ", line).strip()
        if ln:
            self.lines.append(ln)

    def comment(self, text: str, variables: dict) -> None:
        if not self.options["comments"]:
            return
        clean = text.replace("(", "[").replace(")", "]") if self.blocks["comment"].lstrip().startswith("(") else text
        self.block("comment", {**variables, "text": clean})

    # -- words ------------------------------------------------------------------------------
    def _word(self, letter: str, value: str, modal: bool) -> str:
        if modal and self._last.get(letter) == value:
            return ""
        self._last[letter] = value
        return f"{letter}{value}"

    def _motion(self, n: int) -> str:
        g = self.gcode(n)
        if self.options["modal_motion"] and self._last["G"] == g:
            return ""
        self._last["G"] = g
        return g

    def _coords(self, x: float, y: float, z: float, force_xy: bool = False) -> dict:
        mc = self.options["modal_coords"]
        return {"X": self._word("X", self.fmt(self.tx(x)), mc and not force_xy),
                "Y": self._word(self._yl, self.fmt(self.ty(y)), mc and not force_xy),
                "Z": self._word("Z", self.fmt(self.tz(z)), mc)}

    def _feed_word(self, f: float) -> str:
        return self._word("F", self.fmt_feed(f), self.options["modal_feed"])

    # -- moves ------------------------------------------------------------------------------
    def move(self, m, tv: dict) -> None:
        if isinstance(m, Comment):
            self.comment(m.text, tv)
            return
        if isinstance(m, DrillCycle):
            self.drill(m, tv)
            return
        if self._cycle is not None:
            if isinstance(m, Rapid) and m.z >= self._cycle_z0 - 1e-9:
                # travel at the initial level between canned cycle holes is implied by the
                # next hole; it is only written when the cycle series ends
                self._pending = m
                return
            self._end_cycle(tv)
        if isinstance(m, Dwell):
            d = m.seconds * (1000 if self.options["dwell_units"] == "ms" else 1)
            self.block("dwell", {**tv, "dwell": self.fmt(d, 3, coord=False)})
            return
        if isinstance(m, Rapid):
            self.rapid(m, tv)
        elif isinstance(m, Feed):
            self.linear(m.x, m.y, m.z, m.f, tv)
        elif isinstance(m, ArcMove):
            self.arc(m, tv)

    def rapid(self, m: Rapid, tv: dict) -> None:
        p = self._pos
        if p is not None and self.options["rapid_z_first"]:
            moves_xy = abs(p[0] - m.x) > 1e-9 or abs(p[1] - m.y) > 1e-9
            if moves_xy and m.z > p[2] + 1e-9:
                self._rapid_line(p[0], p[1], m.z, tv)
            elif moves_xy and m.z < p[2] - 1e-9:
                self._rapid_line(m.x, m.y, p[2], tv)
        self._rapid_line(m.x, m.y, m.z, tv)

    def _rapid_line(self, x: float, y: float, z: float, tv: dict) -> None:
        w = self._coords(x, y, z)
        if not (w["X"] or w["Y"] or w["Z"]):
            self._pos = [x, y, z]
            return
        self.block("rapid", {**tv, **w, "G": self._motion(0), "N": ""})
        self._pos = [x, y, z]

    def linear(self, x: float, y: float, z: float, f: float, tv: dict) -> None:
        w = self._coords(x, y, z)
        if not (w["X"] or w["Y"] or w["Z"]):
            self._pos = [x, y, z]
            return
        fw = self._rotary_feed(x, y, z, f) if self._rotary and self._pos is not None else self._feed_word(f)
        self.block("linear", {**tv, **w, "G": self._motion(1), "F": fw, "N": ""})
        self._pos = [x, y, z]

    def _rotary_feed(self, x: float, y: float, z: float, f: float) -> str:
        """Feed word that keeps the surface speed of a wrapped move at ``f``."""
        px, py, pz = self._pos
        flat = math.dist((px, py, pz), (x, y, z))
        mode = self.options["rotary_feed"]
        if flat < 1e-9 or mode == "plain":
            return self._feed_word(f)
        if mode == "inverse_time":  # F = 1 / minutes, required on every line
            self._last["F"] = None
            return "F" + self.fmt(f / flat, 3, coord=False)
        # degrees: the controller counts degrees like length units along the move
        out = math.hypot((x - px) * self._k, self.ty(y) - self.ty(py), (z - pz) * self._k)
        return self._word("F", self.fmt(f * out / flat, self.options["feed_decimals"], coord=False),
                          self.options["modal_feed"])

    def arc(self, m: ArcMove, tv: dict) -> None:
        o = self.options
        if self._pos is None:
            self.linear(m.x, m.y, m.z, m.f, tv)
            return
        sx, sy, sz = self._pos
        r = math.hypot(sx - m.cx, sy - m.cy)
        if math.hypot(m.x - sx, m.y - sy) * self._k < 10 ** -o["decimals"]:
            # start and end coincide in the output: a G2/G3 here would be read as a full circle
            self.linear(m.x, m.y, m.z, m.f, tv)
            return
        sweep = arc_sweep(sx, sy, m)
        helical = abs(m.z - sz) > 1e-9
        if (o["arcs"] == "none" or r * self._k < o["arc_min_radius"] or r * self._k > o["arc_max_radius"]
                or (helical and not o["helical"]) or self._rotary):  # wrapped arcs are no circles
            self._linearize(sx, sy, sz, m, r, sweep, tv)
            return
        max_sw = math.radians(o["arc_max_sweep"])
        if o["arcs"] == "r":
            # R words are ill-conditioned near 180 degrees: keep each arc to a quarter circle
            max_sw = min(max_sw, math.pi / 2)
        n = max(1, math.ceil(abs(sweep) / max_sw - 1e-9))
        a0 = math.atan2(sy - m.cy, sx - m.cx)
        for i in range(1, n + 1):
            if i == n:
                x, y, z = m.x, m.y, m.z
            else:
                a = a0 + sweep * i / n
                x, y, z = m.cx + r * math.cos(a), m.cy + r * math.sin(a), sz + (m.z - sz) * i / n
            px, py = self._pos[0], self._pos[1]
            w = self._coords(x, y, z, force_xy=o["arc_xy_always"])
            words = {**w, "G": self._motion(2 if m.cw else 3), "F": self._feed_word(m.f), "N": "",
                     "I": "", "J": "", "K": "", "R": ""}
            if o["arcs"] == "r":
                sw = abs(sweep) / n
                rad = r * self._k * (-1 if sw > math.pi + 1e-9 else 1)
                words["R"] = "R" + self.fmt(rad)
            elif o["ijk"] == "absolute":
                words["I"] = "I" + self.fmt(self.tx(m.cx))
                words["J"] = "J" + self.fmt(self.ty(m.cy))
            else:
                words["I"] = "I" + self.fmt((m.cx - px) * self._k)
                words["J"] = "J" + self.fmt((m.cy - py) * self._k)
            self.block("arc_r" if o["arcs"] == "r" else "arc", {**tv, **words})
            self._pos = [x, y, z]

    def _linearize(self, sx, sy, sz, m: ArcMove, r: float, sweep: float, tv: dict) -> None:
        tol = self.options["arc_tolerance"]
        step = 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 4
        n = max(1, math.ceil(abs(sweep) / step))
        a0 = math.atan2(sy - m.cy, sx - m.cx)
        for i in range(1, n + 1):
            if i == n:
                self.linear(m.x, m.y, m.z, m.f, tv)
            else:
                a = a0 + sweep * i / n
                self.linear(m.cx + r * math.cos(a), m.cy + r * math.sin(a), sz + (m.z - sz) * i / n, m.f, tv)

    # -- drilling ------------------------------------------------------------------------------
    def drill(self, m: DrillCycle, tv: dict) -> None:
        if not self.options["drill_cycles"] or self._inverse:
            z0 = self._pos[2] if self._pos else m.r
            for sub in drill_expand(z0, m):
                self.move(sub, tv)
            return
        c = self._cycle
        same = (c is not None and abs(c.z - m.z) < 1e-9 and abs(c.r - m.r) < 1e-9 and c.peck == m.peck
                and c.dwell == m.dwell and c.f == m.f)
        self._pending = None
        if same:
            mc = self.options["modal_coords"]
            self.block("drill_next", {**tv, "X": self._word("X", self.fmt(self.tx(m.x)), mc),
                                      "Y": self._word(self._yl, self.fmt(self.ty(m.y)), mc), "N": ""})
        else:
            self._end_cycle(tv)
            if self._pos is None:
                self._rapid_line(m.x, m.y, max(m.r, 0.0), tv)
            self._cycle_z0 = self._pos[2]
            self._last["X"] = self._last[self._yl] = None
            xw = self._word("X", self.fmt(self.tx(m.x)), False)
            yw = self._word(self._yl, self.fmt(self.ty(m.y)), False)
            words = {"X": xw, "Y": yw, "Z": "Z" + self.fmt(self.tz(m.z)), "R": "R" + self.fmt(self.tz(m.r)),
                     "Q": "Q" + self.fmt(m.peck * self._k) if m.peck > 0 else "",
                     "P": "P" + self.fmt(m.dwell * (1000 if self.options["dwell_units"] == "ms" else 1), 3,
                                         coord=False) if m.dwell > 0 else "",
                     "F": "F" + self.fmt_feed(m.f), "G": "", "N": ""}
            self.block("drill_peck" if m.peck > 0 else "drill", {**tv, **words})
            self._last["F"] = self.fmt_feed(m.f)
            self._cycle = m
        self._pos = [m.x, m.y, self._cycle_z0]

    def _end_cycle(self, tv: dict) -> None:
        if self._cycle is None:
            return
        self.block("drill_end", tv)
        self._cycle = None
        self._last["G"] = None
        self._last["Z"] = self.fmt(self.tz(self._cycle_z0))
        pending, self._pending = self._pending, None
        if pending is not None:
            self.rapid(pending, tv)
