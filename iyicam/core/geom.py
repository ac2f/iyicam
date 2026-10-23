"""2D geometry kernel: points, segments (line / arc / cubic bezier), contours, transforms.

Conventions
-----------
* Units are millimetres and the Y axis points up (machine coordinates).
* Points are plain ``(x, y)`` tuples.
* Arcs are stored by their end points and a DXF style *bulge*
  (``bulge = tan(sweep / 4)``); a positive bulge runs counter-clockwise.
* Segments are immutable; editing returns new objects.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Pt = tuple[float, float]

EPS = 1e-9
TAU = 2.0 * math.pi


# ---------------------------------------------------------------------------
# point helpers
# ---------------------------------------------------------------------------

def add(a: Pt, b: Pt) -> Pt:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def mul(a: Pt, s: float) -> Pt:
    return (a[0] * s, a[1] * s)


def dot(a: Pt, b: Pt) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Pt, b: Pt) -> float:
    return a[0] * b[1] - a[1] * b[0]


def norm(a: Pt) -> float:
    return math.hypot(a[0], a[1])


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def unit(a: Pt) -> Pt:
    n = math.hypot(a[0], a[1])
    if n < EPS:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def perp(a: Pt) -> Pt:
    """Left normal (rotate +90 degrees)."""
    return (-a[1], a[0])


def angle_of(a: Pt) -> float:
    return math.atan2(a[1], a[0])


def polar(c: Pt, r: float, ang: float) -> Pt:
    return (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))


def same(a: Pt, b: Pt, tol: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def rotate_pt(p: Pt, ang: float, about: Pt = (0.0, 0.0)) -> Pt:
    c, s = math.cos(ang), math.sin(ang)
    x, y = p[0] - about[0], p[1] - about[1]
    return (about[0] + x * c - y * s, about[1] + x * s + y * c)


def norm_angle(a: float) -> float:
    """Normalise an angle into [0, 2pi)."""
    a = math.fmod(a, TAU)
    return a + TAU if a < 0 else a


def point_line_distance(p: Pt, a: Pt, b: Pt) -> float:
    """Distance from ``p`` to the segment ``a-b``."""
    ab = sub(b, a)
    l2 = dot(ab, ab)
    if l2 < EPS * EPS:
        return dist(p, a)
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / l2))
    return dist(p, (a[0] + ab[0] * t, a[1] + ab[1] * t))


def polygon_area(pts: Sequence[Pt]) -> float:
    """Signed shoelace area of a closed polygon (no repeated last point needed)."""
    n = len(pts)
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s * 0.5


def point_in_polygon(p: Pt, pts: Sequence[Pt]) -> bool:
    """Even-odd ray casting test."""
    x, y = p
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y):
            xc = xi + (y - yi) * (xj - xi) / (yj - yi)
            if xc > x:
                inside = not inside
        j = i
    return inside


def circumcenter(a: Pt, b: Pt, c: Pt) -> Pt | None:
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    a2 = a[0] * a[0] + a[1] * a[1]
    b2 = b[0] * b[0] + b[1] * b[1]
    c2 = c[0] * c[0] + c[1] * c[1]
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy)


def simplify_polyline(pts: Sequence[Pt], tol: float) -> list[Pt]:
    """Douglas-Peucker simplification (keeps first and last point)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = pts[i], pts[j]
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            d = point_line_distance(pts[k], a, b)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


# ---------------------------------------------------------------------------
# bounding box
# ---------------------------------------------------------------------------

class BBox:
    __slots__ = ("xmin", "ymin", "xmax", "ymax")

    def __init__(self, xmin=math.inf, ymin=math.inf, xmax=-math.inf, ymax=-math.inf):
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax

    @classmethod
    def of_points(cls, pts: Iterable[Pt]) -> "BBox":
        bb = cls()
        for p in pts:
            bb.add_point(p)
        return bb

    def add_point(self, p: Pt) -> None:
        x, y = p
        if x < self.xmin:
            self.xmin = x
        if x > self.xmax:
            self.xmax = x
        if y < self.ymin:
            self.ymin = y
        if y > self.ymax:
            self.ymax = y

    def union(self, other: "BBox") -> "BBox":
        return BBox(min(self.xmin, other.xmin), min(self.ymin, other.ymin),
                    max(self.xmax, other.xmax), max(self.ymax, other.ymax))

    @property
    def empty(self) -> bool:
        return self.xmin > self.xmax or self.ymin > self.ymax

    @property
    def width(self) -> float:
        return 0.0 if self.empty else self.xmax - self.xmin

    @property
    def height(self) -> float:
        return 0.0 if self.empty else self.ymax - self.ymin

    @property
    def center(self) -> Pt:
        return ((self.xmin + self.xmax) * 0.5, (self.ymin + self.ymax) * 0.5)

    def inflated(self, d: float) -> "BBox":
        return BBox(self.xmin - d, self.ymin - d, self.xmax + d, self.ymax + d)

    def contains(self, p: Pt, tol: float = 0.0) -> bool:
        return (self.xmin - tol <= p[0] <= self.xmax + tol
                and self.ymin - tol <= p[1] <= self.ymax + tol)

    def contains_box(self, o: "BBox", tol: float = 0.0) -> bool:
        return (self.xmin - tol <= o.xmin and o.xmax <= self.xmax + tol
                and self.ymin - tol <= o.ymin and o.ymax <= self.ymax + tol)

    def intersects(self, o: "BBox") -> bool:
        return not (o.xmin > self.xmax or o.xmax < self.xmin
                    or o.ymin > self.ymax or o.ymax < self.ymin)

    def corners(self) -> list[Pt]:
        return [(self.xmin, self.ymin), (self.xmax, self.ymin),
                (self.xmax, self.ymax), (self.xmin, self.ymax)]

    def __repr__(self) -> str:
        return f"BBox({self.xmin:g}, {self.ymin:g}, {self.xmax:g}, {self.ymax:g})"


# ---------------------------------------------------------------------------
# affine transform
# ---------------------------------------------------------------------------

class Transform:
    """2D affine matrix ``x' = a*x + c*y + e``, ``y' = b*x + d*y + f`` (SVG order)."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    @classmethod
    def identity(cls) -> "Transform":
        return cls()

    @classmethod
    def translate(cls, dx: float, dy: float) -> "Transform":
        return cls(e=dx, f=dy)

    @classmethod
    def rotate(cls, ang: float, about: Pt = (0.0, 0.0)) -> "Transform":
        c, s = math.cos(ang), math.sin(ang)
        cx, cy = about
        return cls(c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy)

    @classmethod
    def scale(cls, sx: float, sy: float | None = None, about: Pt = (0.0, 0.0)) -> "Transform":
        if sy is None:
            sy = sx
        cx, cy = about
        return cls(sx, 0.0, 0.0, sy, cx - sx * cx, cy - sy * cy)

    @classmethod
    def mirror(cls, horizontal: bool, about: Pt = (0.0, 0.0)) -> "Transform":
        """Mirror left/right (``horizontal=True``, flips X) or top/bottom (flips Y)."""
        return cls.scale(-1.0, 1.0, about) if horizontal else cls.scale(1.0, -1.0, about)

    def __matmul__(self, o: "Transform") -> "Transform":
        """``self @ o`` applies ``o`` first, then ``self``."""
        return Transform(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    def apply(self, p: Pt) -> Pt:
        x, y = p
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def apply_vec(self, v: Pt) -> Pt:
        x, y = v
        return (self.a * x + self.c * y, self.b * x + self.d * y)

    @property
    def det(self) -> float:
        return self.a * self.d - self.b * self.c

    def is_conformal(self, tol: float = 1e-9) -> bool:
        """True for rotation + uniform scale (+ mirror): circles stay circles."""
        c1 = self.a * self.a + self.b * self.b
        c2 = self.c * self.c + self.d * self.d
        return abs(c1 - c2) <= tol * max(1.0, c1) and abs(self.a * self.c + self.b * self.d) <= tol * max(1.0, c1)

    def inverse(self) -> "Transform":
        det = self.det
        if abs(det) < 1e-15:
            raise ValueError("singular transform")
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        e, f = self.e, self.f
        return Transform(a, b, c, d, -(a * e + c * f), -(b * e + d * f))

    def to_list(self) -> list[float]:
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    def __repr__(self) -> str:
        return "Transform(%g, %g, %g, %g, %g, %g)" % tuple(self.to_list())


# ---------------------------------------------------------------------------
# segments
# ---------------------------------------------------------------------------

class Segment:
    """Base class.  ``a`` is the start point and ``b`` the end point."""

    __slots__ = ("a", "b")
    kind = "segment"

    def length(self) -> float:
        raise NotImplementedError

    def bbox(self) -> BBox:
        raise NotImplementedError

    def point_at(self, t: float) -> Pt:
        raise NotImplementedError

    def tangent_at(self, t: float) -> Pt:
        raise NotImplementedError

    def reversed(self) -> "Segment":
        raise NotImplementedError

    def transformed(self, T: Transform) -> list["Segment"]:
        raise NotImplementedError

    def flatten(self, tol: float) -> list[Pt]:
        """Points after the start point, ending with ``b``."""
        raise NotImplementedError

    def split(self, t: float) -> tuple["Segment", "Segment"]:
        raise NotImplementedError

    def nearest_t(self, p: Pt) -> float:
        raise NotImplementedError

    def with_ends(self, a: Pt, b: Pt) -> "Segment":
        raise NotImplementedError

    def t_at_length(self, s: float) -> float:
        L = self.length()
        return 0.0 if L < EPS else max(0.0, min(1.0, s / L))

    def distance_to(self, p: Pt) -> float:
        return dist(p, self.point_at(self.nearest_t(p)))


class Line(Segment):
    __slots__ = ()
    kind = "line"

    def __init__(self, a: Pt, b: Pt):
        self.a = (float(a[0]), float(a[1]))
        self.b = (float(b[0]), float(b[1]))

    def length(self) -> float:
        return dist(self.a, self.b)

    def bbox(self) -> BBox:
        return BBox.of_points((self.a, self.b))

    def point_at(self, t: float) -> Pt:
        return lerp(self.a, self.b, t)

    def tangent_at(self, t: float) -> Pt:
        return unit(sub(self.b, self.a))

    def reversed(self) -> "Line":
        return Line(self.b, self.a)

    def transformed(self, T: Transform) -> list[Segment]:
        return [Line(T.apply(self.a), T.apply(self.b))]

    def flatten(self, tol: float) -> list[Pt]:
        return [self.b]

    def split(self, t: float) -> tuple["Line", "Line"]:
        m = self.point_at(t)
        return Line(self.a, m), Line(m, self.b)

    def nearest_t(self, p: Pt) -> float:
        ab = sub(self.b, self.a)
        l2 = dot(ab, ab)
        if l2 < EPS * EPS:
            return 0.0
        return max(0.0, min(1.0, dot(sub(p, self.a), ab) / l2))

    def with_ends(self, a: Pt, b: Pt) -> "Line":
        return Line(a, b)

    def __repr__(self) -> str:
        return f"Line({self.a}, {self.b})"


class Arc(Segment):
    """Circular arc from ``a`` to ``b``; ``bulge = tan(sweep/4)``, positive = CCW."""

    __slots__ = ("bulge",)
    kind = "arc"

    def __init__(self, a: Pt, b: Pt, bulge: float):
        self.a = (float(a[0]), float(a[1]))
        self.b = (float(b[0]), float(b[1]))
        self.bulge = float(bulge)

    # -- construction helpers ------------------------------------------------
    @staticmethod
    def from_center(center: Pt, radius: float, start_angle: float, sweep: float) -> "Arc":
        """Arc by centre, radius, start angle and signed sweep (|sweep| < 2pi)."""
        a = polar(center, radius, start_angle)
        b = polar(center, radius, start_angle + sweep)
        return Arc(a, b, math.tan(sweep / 4.0))

    @staticmethod
    def from_center_points(a: Pt, b: Pt, center: Pt, ccw: bool) -> "Segment":
        sa = angle_of(sub(a, center))
        ea = angle_of(sub(b, center))
        if ccw:
            sweep = norm_angle(ea - sa)
        else:
            sweep = -norm_angle(sa - ea)
        if abs(sweep) < 1e-12:
            sweep = TAU if ccw else -TAU
        if abs(sweep) >= TAU - 1e-9:
            # full circle: not representable by one bulge segment
            raise ValueError("full circle needs two arcs")
        return Arc(a, b, math.tan(sweep / 4.0))

    @staticmethod
    def through(p0: Pt, pm: Pt, p1: Pt) -> "Segment":
        """Arc through three points (falls back to a line when collinear)."""
        c = circumcenter(p0, pm, p1)
        if c is None:
            return Line(p0, p1)
        ccw = cross(sub(pm, p0), sub(p1, pm)) > 0
        return Arc.from_center_points(p0, p1, c, ccw)

    # -- geometry -------------------------------------------------------------
    @property
    def sweep(self) -> float:
        return 4.0 * math.atan(self.bulge)

    @property
    def ccw(self) -> bool:
        return self.bulge > 0

    @property
    def chord(self) -> float:
        return dist(self.a, self.b)

    @property
    def radius(self) -> float:
        b = abs(self.bulge)
        if b < 1e-15:
            return math.inf
        return self.chord * (1.0 + b * b) / (4.0 * b)

    @property
    def center(self) -> Pt:
        b = self.bulge
        d = self.chord
        m = lerp(self.a, self.b, 0.5)
        if d < EPS:
            return m
        n = perp(unit(sub(self.b, self.a)))
        h = d * (1.0 - b * b) / (4.0 * b)
        return (m[0] + n[0] * h, m[1] + n[1] * h)

    @property
    def start_angle(self) -> float:
        return angle_of(sub(self.a, self.center))

    @property
    def end_angle(self) -> float:
        return angle_of(sub(self.b, self.center))

    def length(self) -> float:
        return self.radius * abs(self.sweep)

    def point_at(self, t: float) -> Pt:
        if t <= 0.0:
            return self.a
        if t >= 1.0:
            return self.b
        return polar(self.center, self.radius, self.start_angle + self.sweep * t)

    def tangent_at(self, t: float) -> Pt:
        c = self.center
        p = self.point_at(t)
        r = unit(sub(p, c))
        return perp(r) if self.bulge > 0 else (r[1], -r[0])

    def bbox(self) -> BBox:
        bb = BBox.of_points((self.a, self.b))
        c, r = self.center, self.radius
        sa, sw = self.start_angle, self.sweep
        for k in range(4):
            ang = k * math.pi / 2
            if self._angle_in_sweep(ang, sa, sw):
                bb.add_point(polar(c, r, ang))
        return bb

    @staticmethod
    def _angle_in_sweep(ang: float, sa: float, sw: float) -> bool:
        if sw >= 0:
            return norm_angle(ang - sa) <= sw
        return norm_angle(sa - ang) <= -sw

    def reversed(self) -> "Arc":
        return Arc(self.b, self.a, -self.bulge)

    def transformed(self, T: Transform) -> list[Segment]:
        if T.is_conformal():
            sign = 1.0 if T.det > 0 else -1.0
            return [Arc(T.apply(self.a), T.apply(self.b), self.bulge * sign)]
        out: list[Segment] = []
        for bz in self.to_beziers():
            out.extend(bz.transformed(T))
        return out

    def segment_count(self, tol: float) -> int:
        r = self.radius
        sw = abs(self.sweep)
        if tol >= r:
            return max(1, math.ceil(sw / (math.pi / 2)))
        step = 2.0 * math.acos(1.0 - tol / r)
        return max(1, math.ceil(sw / step))

    def flatten(self, tol: float) -> list[Pt]:
        n = self.segment_count(tol)
        c, r, sa, sw = self.center, self.radius, self.start_angle, self.sweep
        pts = [polar(c, r, sa + sw * i / n) for i in range(1, n)]
        pts.append(self.b)
        return pts

    def split(self, t: float) -> tuple["Arc", "Arc"]:
        m = self.point_at(t)
        sw = self.sweep
        return Arc(self.a, m, math.tan(sw * t / 4.0)), Arc(m, self.b, math.tan(sw * (1.0 - t) / 4.0))

    def nearest_t(self, p: Pt) -> float:
        c = self.center
        sa, sw = self.start_angle, self.sweep
        ang = angle_of(sub(p, c))
        rel = norm_angle(ang - sa) if sw >= 0 else norm_angle(sa - ang)
        t = rel / abs(sw)
        if t <= 1.0:
            return t
        return 0.0 if dist(p, self.a) <= dist(p, self.b) else 1.0

    def with_ends(self, a: Pt, b: Pt) -> "Arc":
        return Arc(a, b, self.bulge)

    def to_beziers(self) -> list["Bezier"]:
        sw = self.sweep
        n = max(1, math.ceil(abs(sw) / (math.pi / 2) - 1e-9))
        c, r, sa = self.center, self.radius, self.start_angle
        step = sw / n
        k = 4.0 / 3.0 * math.tan(step / 4.0)
        out = []
        prev = self.a
        for i in range(n):
            a0 = sa + step * i
            a1 = a0 + step
            p1 = self.b if i == n - 1 else polar(c, r, a1)
            c1 = (prev[0] - k * r * math.sin(a0), prev[1] + k * r * math.cos(a0))
            c2 = (p1[0] + k * r * math.sin(a1), p1[1] - k * r * math.cos(a1))
            out.append(Bezier(prev, c1, c2, p1))
            prev = p1
        return out

    def __repr__(self) -> str:
        return f"Arc({self.a}, {self.b}, bulge={self.bulge:.6g})"


class Bezier(Segment):
    """Cubic bezier ``a, c1, c2, b``."""

    __slots__ = ("c1", "c2")
    kind = "bezier"

    def __init__(self, a: Pt, c1: Pt, c2: Pt, b: Pt):
        self.a = (float(a[0]), float(a[1]))
        self.c1 = (float(c1[0]), float(c1[1]))
        self.c2 = (float(c2[0]), float(c2[1]))
        self.b = (float(b[0]), float(b[1]))

    def point_at(self, t: float) -> Pt:
        mt = 1.0 - t
        w0, w1, w2, w3 = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
        return (w0 * self.a[0] + w1 * self.c1[0] + w2 * self.c2[0] + w3 * self.b[0],
                w0 * self.a[1] + w1 * self.c1[1] + w2 * self.c2[1] + w3 * self.b[1])

    def derivative(self, t: float) -> Pt:
        mt = 1.0 - t
        p0, p1, p2, p3 = self.a, self.c1, self.c2, self.b
        return (3 * mt * mt * (p1[0] - p0[0]) + 6 * mt * t * (p2[0] - p1[0]) + 3 * t * t * (p3[0] - p2[0]),
                3 * mt * mt * (p1[1] - p0[1]) + 6 * mt * t * (p2[1] - p1[1]) + 3 * t * t * (p3[1] - p2[1]))

    def tangent_at(self, t: float) -> Pt:
        d = self.derivative(t)
        if norm(d) < 1e-12:
            # degenerate handle at an end point
            if t < 0.5:
                d = sub(self.c2 if same(self.c1, self.a) else self.c1, self.a)
            else:
                d = sub(self.b, self.c1 if same(self.c2, self.b) else self.c2)
            if norm(d) < 1e-12:
                d = sub(self.b, self.a)
        return unit(d)

    def split(self, t: float) -> tuple["Bezier", "Bezier"]:
        p01 = lerp(self.a, self.c1, t)
        p12 = lerp(self.c1, self.c2, t)
        p23 = lerp(self.c2, self.b, t)
        p012 = lerp(p01, p12, t)
        p123 = lerp(p12, p23, t)
        m = lerp(p012, p123, t)
        return Bezier(self.a, p01, p012, m), Bezier(m, p123, p23, self.b)

    def _flat_enough(self, tol: float) -> bool:
        d1 = point_line_distance(self.c1, self.a, self.b)
        d2 = point_line_distance(self.c2, self.a, self.b)
        return max(d1, d2) * 0.75 <= tol

    def flatten(self, tol: float) -> list[Pt]:
        out: list[Pt] = []
        stack = [(self, 0)]
        while stack:
            bz, depth = stack.pop()
            if depth >= 16 or bz._flat_enough(tol):
                out.append(bz.b)
            else:
                l, r = bz.split(0.5)
                stack.append((r, depth + 1))
                stack.append((l, depth + 1))
        return out

    def length(self) -> float:
        # 8 point Gauss-Legendre on the derivative norm, subdivided for accuracy
        xs = (0.1834346424956498, 0.5255324099163290, 0.7966664774136267, 0.9602898564975363)
        ws = (0.3626837833783620, 0.3137066458778873, 0.2223810344533745, 0.1012285362903763)
        total = 0.0
        n = 4
        for k in range(n):
            t0, t1 = k / n, (k + 1) / n
            h, m = (t1 - t0) / 2, (t1 + t0) / 2
            for x, w in zip(xs, ws):
                total += w * h * (norm(self.derivative(m + h * x)) + norm(self.derivative(m - h * x)))
        return total

    def t_at_length(self, s: float) -> float:
        if s <= 0:
            return 0.0
        L = self.length()
        if s >= L:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if self.split(mid)[0].length() < s:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def bbox(self) -> BBox:
        bb = BBox.of_points((self.a, self.b))
        for axis in (0, 1):
            p0, p1, p2, p3 = self.a[axis], self.c1[axis], self.c2[axis], self.b[axis]
            # derivative coefficients: a t^2 + b t + c
            a = -p0 + 3 * p1 - 3 * p2 + p3
            b = 2 * (p0 - 2 * p1 + p2)
            c = p1 - p0
            roots = []
            if abs(a) < 1e-12:
                if abs(b) > 1e-12:
                    roots.append(-c / b)
            else:
                disc = b * b - 4 * a * c
                if disc >= 0:
                    sq = math.sqrt(disc)
                    roots += [(-b + sq) / (2 * a), (-b - sq) / (2 * a)]
            for t in roots:
                if 0.0 < t < 1.0:
                    bb.add_point(self.point_at(t))
        return bb

    def reversed(self) -> "Bezier":
        return Bezier(self.b, self.c2, self.c1, self.a)

    def transformed(self, T: Transform) -> list[Segment]:
        return [Bezier(T.apply(self.a), T.apply(self.c1), T.apply(self.c2), T.apply(self.b))]

    def nearest_t(self, p: Pt) -> float:
        n = 24
        best_t, best_d = 0.0, math.inf
        for i in range(n + 1):
            t = i / n
            d = dist(p, self.point_at(t))
            if d < best_d:
                best_t, best_d = t, d
        lo, hi = max(0.0, best_t - 1.0 / n), min(1.0, best_t + 1.0 / n)
        for _ in range(30):
            m1 = lo + (hi - lo) / 3
            m2 = hi - (hi - lo) / 3
            if dist(p, self.point_at(m1)) < dist(p, self.point_at(m2)):
                hi = m2
            else:
                lo = m1
        return (lo + hi) / 2

    def with_ends(self, a: Pt, b: Pt) -> "Bezier":
        da, db = sub(a, self.a), sub(b, self.b)
        return Bezier(a, add(self.c1, da), add(self.c2, db), b)

    def __repr__(self) -> str:
        return f"Bezier({self.a}, {self.c1}, {self.c2}, {self.b})"


def make_arc(a: Pt, b: Pt, bulge: float) -> Segment:
    """Arc constructor that degrades gracefully to a line."""
    if abs(bulge) < 1e-9 or same(a, b, 1e-12):
        return Line(a, b)
    return Arc(a, b, bulge)


def segment_area_contribution(s: Segment, tol: float = 0.01) -> float:
    """Contribution of a segment to the signed area of a closed contour."""
    if isinstance(s, Line):
        return (s.a[0] * s.b[1] - s.b[0] * s.a[1]) * 0.5
    if isinstance(s, Arc):
        chord = (s.a[0] * s.b[1] - s.b[0] * s.a[1]) * 0.5
        th = s.sweep
        r = s.radius
        return chord + 0.5 * r * r * (th - math.sin(th))
    pts = [s.a] + s.flatten(tol)
    return sum((pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]) * 0.5 for i in range(len(pts) - 1))


# ---------------------------------------------------------------------------
# contour
# ---------------------------------------------------------------------------

class Contour:
    """A connected chain of segments, open or closed."""

    __slots__ = ("segs", "closed")

    def __init__(self, segs: Iterable[Segment] = (), closed: bool = False):
        self.segs: list[Segment] = list(segs)
        self.closed = bool(closed)

    # -- construction ---------------------------------------------------------
    @classmethod
    def polyline(cls, pts: Sequence[Pt], closed: bool = False) -> "Contour":
        pts = list(pts)
        if closed and len(pts) > 1 and same(pts[0], pts[-1], 1e-9):
            pts.pop()
        segs: list[Segment] = [Line(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        if closed and len(pts) > 2:
            segs.append(Line(pts[-1], pts[0]))
        return cls(segs, closed and len(pts) > 2)

    # -- basic properties -------------------------------------------------------
    @property
    def start(self) -> Pt:
        return self.segs[0].a

    @property
    def end(self) -> Pt:
        return self.segs[-1].b

    def __len__(self) -> int:
        return len(self.segs)

    def __bool__(self) -> bool:
        return bool(self.segs)

    def nodes(self) -> list[Pt]:
        """Node points; for closed contours the start is not repeated."""
        if not self.segs:
            return []
        pts = [s.a for s in self.segs]
        if not self.closed:
            pts.append(self.segs[-1].b)
        return pts

    def length(self) -> float:
        return sum(s.length() for s in self.segs)

    def bbox(self) -> BBox:
        bb = BBox()
        for s in self.segs:
            bb = bb.union(s.bbox())
        return bb

    def flatten(self, tol: float = 0.01) -> list[Pt]:
        if not self.segs:
            return []
        pts = [self.segs[0].a]
        for s in self.segs:
            pts.extend(s.flatten(tol))
        if self.closed and len(pts) > 1 and same(pts[0], pts[-1], 1e-9):
            pts.pop()
        return pts

    def signed_area(self, tol: float = 0.01) -> float:
        if not self.closed:
            return 0.0
        total = sum(segment_area_contribution(s, tol) for s in self.segs)
        # close a tiny gap if the contour is not exactly closed
        a, b = self.end, self.start
        total += (a[0] * b[1] - b[0] * a[1]) * 0.5
        return total

    def area(self, tol: float = 0.01) -> float:
        return abs(self.signed_area(tol))

    def is_ccw(self) -> bool:
        return self.signed_area() > 0

    def is_closed_geometrically(self, tol: float = 1e-6) -> bool:
        return bool(self.segs) and same(self.start, self.end, tol)

    # -- derived contours -------------------------------------------------------
    def reversed(self) -> "Contour":
        return Contour([s.reversed() for s in reversed(self.segs)], self.closed)

    def transformed(self, T: Transform) -> "Contour":
        out: list[Segment] = []
        for s in self.segs:
            out.extend(s.transformed(T))
        return Contour(out, self.closed)

    def copy(self) -> "Contour":
        return Contour(list(self.segs), self.closed)

    def oriented(self, ccw: bool) -> "Contour":
        return self if (self.is_ccw() == ccw or not self.closed) else self.reversed()

    # -- queries ------------------------------------------------------------------
    def contains(self, p: Pt, tol: float = 0.01) -> bool:
        if not self.closed:
            return False
        return point_in_polygon(p, self.flatten(tol))

    def nearest(self, p: Pt) -> tuple[int, float, Pt, float]:
        """``(segment index, t, point, distance)`` of the closest point."""
        best = (0, 0.0, self.start, math.inf)
        for i, s in enumerate(self.segs):
            t = s.nearest_t(p)
            q = s.point_at(t)
            d = dist(p, q)
            if d < best[3]:
                best = (i, t, q, d)
        return best

    def point_at_length(self, s: float) -> Pt:
        for seg in self.segs:
            L = seg.length()
            if s <= L:
                return seg.point_at(seg.t_at_length(s))
            s -= L
        return self.end

    def __repr__(self) -> str:
        return f"Contour({len(self.segs)} segs, closed={self.closed})"


def contours_bbox(contours: Iterable[Contour]) -> BBox:
    bb = BBox()
    for c in contours:
        if c.segs:
            bb = bb.union(c.bbox())
    return bb
