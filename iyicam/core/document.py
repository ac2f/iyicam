"""Project document: sheets, vector layers, vectors, material setup and toolpath definitions."""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .geom import Arc, BBox, Bezier, Contour, Line, Segment, Transform, contours_bbox
from .tooling import Tool

FORMAT = "iyicam-project"
FORMAT_VERSION = 1

LAYER_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"]

XY_ORIGINS = ["bottom_left", "bottom_center", "bottom_right", "center_left", "center",
              "center_right", "top_left", "top_center", "top_right"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Layer:
    name: str
    color: str = "#1f77b4"
    visible: bool = True
    locked: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class VectorObject:
    contours: list[Contour]
    layer_id: str
    name: str = ""
    id: str = field(default_factory=new_id)

    def bbox(self) -> BBox:
        return contours_bbox(self.contours)

    @property
    def closed(self) -> bool:
        return bool(self.contours) and all(c.closed for c in self.contours)

    def transform(self, T: Transform) -> None:
        self.contours = [c.transformed(T) for c in self.contours]

    def copy(self, keep_id: bool = False) -> "VectorObject":
        return VectorObject([c.copy() for c in self.contours], self.layer_id, self.name,
                            self.id if keep_id else new_id())


@dataclass
class MaterialSetup:
    """Stock definition.  Toolpaths are computed with Z = 0 at the material top."""

    thickness: float = 18.0
    z_zero: str = "top"            # 'top' or 'bottom' (machine Z zero)
    safe_z: float = 10.0           # rapid height above the material surface
    clearance: float = 2.0         # rapid-down height above the surface before plunging
    home: tuple[float, float, float] = (0.0, 0.0, 20.0)  # X, Y in sheet coordinates, Z above surface
    xy_origin: str = "bottom_left"  # where X0 Y0 lies on the sheet
    rapid_rate: float = 8000.0     # mm/min, used for time estimates


@dataclass
class Sheet:
    name: str
    width: float = 1220.0
    height: float = 2440.0
    vectors: list[VectorObject] = field(default_factory=list)
    thickness: float | None = None  # overrides the material thickness when set
    id: str = field(default_factory=new_id)

    def vector(self, vid: str) -> VectorObject | None:
        for v in self.vectors:
            if v.id == vid:
                return v
        return None

    def bbox(self) -> BBox:
        bb = BBox()
        for v in self.vectors:
            bb = bb.union(v.bbox())
        return bb


@dataclass
class Selection:
    """Which vectors a toolpath machines: explicit vector ids or whole layers."""

    mode: str = "vectors"  # 'vectors' | 'layers'
    vector_ids: list[str] = field(default_factory=list)
    layer_ids: list[str] = field(default_factory=list)


@dataclass
class ToolpathDef:
    name: str
    op: str                      # registered operation type id
    tool: Tool
    params: dict = field(default_factory=dict)
    selection: Selection = field(default_factory=Selection)
    enabled: bool = True
    sheet_ids: list[str] | None = None  # None = every sheet
    id: str = field(default_factory=new_id)


class Project:
    def __init__(self, name: str = "Untitled"):
        self.name = name
        self.units = "mm"
        self.material = MaterialSetup()
        self.layers: list[Layer] = [Layer("Default", LAYER_COLORS[0])]
        self.sheets: list[Sheet] = [Sheet("Sheet 1", 600.0, 400.0)]
        self.toolpaths: list[ToolpathDef] = []
        self.metadata: dict = {}
        self.path: Path | None = None

    # -- layers -------------------------------------------------------------------
    def layer(self, lid: str) -> Layer | None:
        for l in self.layers:
            if l.id == lid:
                return l
        return None

    def layer_by_name(self, name: str) -> Layer | None:
        for l in self.layers:
            if l.name == name:
                return l
        return None

    def add_layer(self, name: str, color: str | None = None) -> Layer:
        if color is None:
            color = LAYER_COLORS[len(self.layers) % len(LAYER_COLORS)]
        l = Layer(name, color)
        self.layers.append(l)
        return l

    def ensure_layer(self, name: str, color: str | None = None) -> Layer:
        return self.layer_by_name(name) or self.add_layer(name, color)

    def remove_layer(self, lid: str, move_to: str | None = None) -> None:
        """Delete a layer; its vectors move to ``move_to`` (or are deleted when None)."""
        for s in self.sheets:
            if move_to is None:
                s.vectors = [v for v in s.vectors if v.layer_id != lid]
            else:
                for v in s.vectors:
                    if v.layer_id == lid:
                        v.layer_id = move_to
        self.layers = [l for l in self.layers if l.id != lid]
        for t in self.toolpaths:
            t.selection.layer_ids = [x for x in t.selection.layer_ids if x != lid]
        if not self.layers:
            self.layers.append(Layer("Default", LAYER_COLORS[0]))

    # -- sheets --------------------------------------------------------------------
    def sheet(self, sid: str) -> Sheet | None:
        for s in self.sheets:
            if s.id == sid:
                return s
        return None

    def add_sheet(self, name: str | None = None, width: float | None = None,
                  height: float | None = None) -> Sheet:
        ref = self.sheets[-1] if self.sheets else None
        s = Sheet(name or f"Sheet {len(self.sheets) + 1}",
                  width if width is not None else (ref.width if ref else 600.0),
                  height if height is not None else (ref.height if ref else 400.0))
        self.sheets.append(s)
        return s

    def remove_sheet(self, sid: str) -> None:
        self.sheets = [s for s in self.sheets if s.id != sid]
        if not self.sheets:
            self.sheets.append(Sheet("Sheet 1"))

    def sheet_thickness(self, sheet: Sheet) -> float:
        return sheet.thickness if sheet.thickness is not None else self.material.thickness

    # -- vectors -------------------------------------------------------------------
    def iter_vectors(self) -> Iterator[tuple[Sheet, VectorObject]]:
        for s in self.sheets:
            for v in s.vectors:
                yield s, v

    def find_vector(self, vid: str) -> tuple[Sheet, VectorObject] | None:
        for s, v in self.iter_vectors():
            if v.id == vid:
                return s, v
        return None

    def remove_vectors(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        for s in self.sheets:
            s.vectors = [v for v in s.vectors if v.id not in ids]

    def vectors_for(self, tdef: ToolpathDef, sheet: Sheet) -> list[VectorObject]:
        sel = tdef.selection
        if sel.mode == "layers":
            lids = set(sel.layer_ids)
            return [v for v in sheet.vectors if v.layer_id in lids]
        ids = set(sel.vector_ids)
        return [v for v in sheet.vectors if v.id in ids]

    # -- toolpaths -------------------------------------------------------------------
    def toolpath(self, tid: str) -> ToolpathDef | None:
        for t in self.toolpaths:
            if t.id == tid:
                return t
        return None

    # -- persistence -------------------------------------------------------------------
    def to_dict(self) -> dict:
        return project_to_dict(self)

    def load_state(self, d: dict) -> None:
        other = project_from_dict(d)
        path = self.path
        self.__dict__.update(other.__dict__)
        self.path = path

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path)
        p = project_from_dict(json.loads(path.read_text(encoding="utf-8")))
        p.path = path
        return p


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------

def _r(v: float) -> float:
    return round(v, 9)


def contour_to_dict(c: Contour) -> dict:
    if not c.segs:
        return {"closed": c.closed, "start": [0, 0], "segs": []}
    out = []
    for s in c.segs:
        if isinstance(s, Line):
            out.append(["L", _r(s.b[0]), _r(s.b[1])])
        elif isinstance(s, Arc):
            out.append(["A", _r(s.b[0]), _r(s.b[1]), _r(s.bulge)])
        elif isinstance(s, Bezier):
            out.append(["C", _r(s.c1[0]), _r(s.c1[1]), _r(s.c2[0]), _r(s.c2[1]), _r(s.b[0]), _r(s.b[1])])
        else:
            raise TypeError(type(s))
    return {"closed": c.closed, "start": [_r(c.start[0]), _r(c.start[1])], "segs": out}


def contour_from_dict(d: dict) -> Contour:
    p = tuple(d["start"])
    segs: list[Segment] = []
    for s in d["segs"]:
        k = s[0]
        if k == "L":
            q = (s[1], s[2])
            segs.append(Line(p, q))
        elif k == "A":
            q = (s[1], s[2])
            segs.append(Arc(p, q, s[3]))
        elif k == "C":
            q = (s[5], s[6])
            segs.append(Bezier(p, (s[1], s[2]), (s[3], s[4]), q))
        else:
            raise ValueError(f"unknown segment kind {k!r}")
        p = q
    return Contour(segs, d.get("closed", False))


def vector_to_dict(v: VectorObject) -> dict:
    return {"id": v.id, "layer": v.layer_id, "name": v.name,
            "contours": [contour_to_dict(c) for c in v.contours]}


def vector_from_dict(d: dict) -> VectorObject:
    return VectorObject([contour_from_dict(c) for c in d["contours"]], d["layer"],
                        d.get("name", ""), d["id"])


def toolpath_to_dict(t: ToolpathDef) -> dict:
    return {"id": t.id, "name": t.name, "op": t.op, "tool": t.tool.to_dict(),
            "params": _jsonable(t.params), "enabled": t.enabled, "sheets": t.sheet_ids,
            "selection": {"mode": t.selection.mode, "vectors": list(t.selection.vector_ids),
                          "layers": list(t.selection.layer_ids)}}


def _jsonable(v):
    if isinstance(v, Tool):
        return {"__tool__": v.to_dict()}
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _unjson(v):
    if isinstance(v, dict):
        if "__tool__" in v:
            return Tool.from_dict(v["__tool__"])
        return {k: _unjson(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unjson(x) for x in v]
    return v


def toolpath_from_dict(d: dict) -> ToolpathDef:
    sel = d.get("selection", {})
    return ToolpathDef(d["name"], d["op"], Tool.from_dict(d["tool"]), _unjson(d.get("params", {})),
                       Selection(sel.get("mode", "vectors"), list(sel.get("vectors", [])),
                                 list(sel.get("layers", []))),
                       d.get("enabled", True), d.get("sheets"), d["id"])


def project_to_dict(p: Project) -> dict:
    m = p.material
    return {
        "format": FORMAT, "version": FORMAT_VERSION, "name": p.name, "units": p.units,
        "material": {"thickness": m.thickness, "z_zero": m.z_zero, "safe_z": m.safe_z,
                     "clearance": m.clearance, "home": list(m.home), "xy_origin": m.xy_origin,
                     "rapid_rate": m.rapid_rate},
        "layers": [{"id": l.id, "name": l.name, "color": l.color, "visible": l.visible,
                    "locked": l.locked} for l in p.layers],
        "sheets": [{"id": s.id, "name": s.name, "width": s.width, "height": s.height,
                    "thickness": s.thickness, "vectors": [vector_to_dict(v) for v in s.vectors]}
                   for s in p.sheets],
        "toolpaths": [toolpath_to_dict(t) for t in p.toolpaths],
        "metadata": copy.deepcopy(p.metadata),  # snapshots (undo) must not share it
    }


def project_from_dict(d: dict) -> Project:
    if d.get("format") != FORMAT:
        raise ValueError("not an iyiCAM project")
    p = Project(d.get("name", "Untitled"))
    p.units = d.get("units", "mm")
    m = d.get("material", {})
    p.material = MaterialSetup(
        thickness=m.get("thickness", 18.0), z_zero=m.get("z_zero", "top"), safe_z=m.get("safe_z", 10.0),
        clearance=m.get("clearance", 2.0), home=tuple(m.get("home", (0, 0, 20))),
        xy_origin=m.get("xy_origin", "bottom_left"), rapid_rate=m.get("rapid_rate", 8000.0))
    p.layers = [Layer(l["name"], l.get("color", "#1f77b4"), l.get("visible", True), l.get("locked", False),
                      l["id"]) for l in d.get("layers", [])] or p.layers
    p.sheets = [Sheet(s["name"], s.get("width", 600.0), s.get("height", 400.0),
                      [vector_from_dict(v) for v in s.get("vectors", [])], s.get("thickness"), s["id"])
                for s in d.get("sheets", [])] or p.sheets
    p.toolpaths = [toolpath_from_dict(t) for t in d.get("toolpaths", [])]
    p.metadata = d.get("metadata", {})
    return p
