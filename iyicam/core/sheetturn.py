"""Turning and mirroring a whole sheet (ArtCAM's Model > Rotate / Mirror): the plate, everything drawn on it, and the
points the toolpaths of it keep (tabs placed by hand) go together, so what was cut on it is cut the same way after.

``turn_sheet(project, sheet, how)``, ``how``: ``left`` (90° counter-clockwise), ``right`` (90° clockwise), ``half``
(180°), ``mirror_lr`` (left to right) or ``mirror_tb`` (top to bottom).  A quarter turn swaps the width and the height.
The lower left corner stays the origin; nothing leaves the plate.
"""
from __future__ import annotations

from .geom import Transform

HOW = ("left", "right", "half", "mirror_lr", "mirror_tb")


def sheet_transform(width: float, height: float, how: str) -> tuple[Transform, float, float]:
    """The transformation of the drawing and the new width and height of the plate."""
    W, H = width, height
    if how == "left":          # (x, y) -> (H - y, x)
        return Transform(0.0, 1.0, -1.0, 0.0, H, 0.0), H, W
    if how == "right":         # (x, y) -> (y, W - x)
        return Transform(0.0, -1.0, 1.0, 0.0, 0.0, W), H, W
    if how == "half":          # (x, y) -> (W - x, H - y)
        return Transform(-1.0, 0.0, 0.0, -1.0, W, H), W, H
    if how == "mirror_lr":     # (x, y) -> (W - x, y)
        return Transform(-1.0, 0.0, 0.0, 1.0, W, 0.0), W, H
    if how == "mirror_tb":     # (x, y) -> (x, H - y)
        return Transform(1.0, 0.0, 0.0, -1.0, 0.0, H), W, H
    raise ValueError(f"unknown way to turn a sheet: {how!r} (one of {', '.join(HOW)})")


def _only_here(project, tdef, sheet) -> bool:
    """Whether the points a toolpath keeps are those of this sheet: it machines vectors of this sheet and of no other."""
    ids = set(tdef.selection.vector_ids)
    here = {o.id for o in sheet.vectors}
    if tdef.selection.mode == "vectors" and ids:
        return ids <= here
    return tdef.sheet_ids == [sheet.id]


def turn_sheet(project, sheet, how: str) -> Transform:
    """Turn or mirror ``sheet`` in place (see the module); returns the transformation applied."""
    T, w, h = sheet_transform(sheet.width, sheet.height, how)
    for o in sheet.vectors:
        o.transform(T)
    if sheet.outline:
        sheet.outline = [[T.apply(tuple(p)) for p in poly] for poly in sheet.outline]
        if T.det < 0:                               # (a mirror turns the outlines round: they go the other way again)
            sheet.outline = [list(reversed(poly)) for poly in sheet.outline]
    sheet.width, sheet.height = w, h
    for t in project.toolpaths:
        pts = t.params.get("tab_points")
        if pts and _only_here(project, t, sheet):
            t.params["tab_points"] = [list(T.apply((float(p[0]), float(p[1])))) for p in pts]
    return T
