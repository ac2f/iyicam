"""A sheet as the machining code sees it: its bounding box and, for a leftover, its outline."""
from __future__ import annotations

import numpy as np

from . import clip
from .geom import BBox


class Plate(BBox):
    """The bounding box of a sheet. ``region`` are the outlines of its material when the sheet is a
    leftover of any other shape than a rectangle (outer boundaries and holes), else ``None``."""

    __slots__ = ("region",)

    def __init__(self, xmin=0.0, ymin=0.0, xmax=0.0, ymax=0.0, region=None):
        super().__init__(xmin, ymin, xmax, ymax)
        self.region = region

    @classmethod
    def of(cls, sheet) -> "Plate":
        outline = getattr(sheet, "outline", None)
        return cls(0.0, 0.0, sheet.width, sheet.height, clip.normalize(outline) if outline else None)

    def contains(self, P: np.ndarray, grow: float = 0.0) -> np.ndarray:
        """Which of the points (n x 2) lie on the plate (``grow`` widens it)."""
        P = np.asarray(P, float).reshape(-1, 2)
        if self.region:
            region = clip.offset(self.region, grow) if grow else self.region
            return clip.region_mask(P, region)
        return ((P[:, 0] >= self.xmin - grow) & (P[:, 0] <= self.xmax + grow)
                & (P[:, 1] >= self.ymin - grow) & (P[:, 1] <= self.ymax + grow))
