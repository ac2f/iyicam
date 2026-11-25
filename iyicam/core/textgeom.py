"""Text as contours, without a window system: the letters of a font file, laid out like the desktop's Text tool does.

The desktop program takes the outlines of the letters from Qt (``gui/qtgeom.py``), which needs a window system and the
fonts of the computer.  The web app runs the engine in the browser, where neither exists, so this module reads the font
file itself (``fontTools``, a pure Python package that ``ezdxf`` brings along) and builds the same kind of result: closed
contours (Beziers) in millimetres with the baseline at y = 0, capital letters ``height`` tall, outer outlines
counter-clockwise and holes clockwise.

Fonts: ``core/fonts/`` has DejaVu Sans, Serif and Sans Mono, regular and bold (subsets: Latin with the Turkish letters, Greek,
Cyrillic and common signs; see ``fonts/LICENSE-DejaVu.txt``).  Italic is the font slanted 12 degrees.  A caller that has a font
file of its own (a ``.ttf`` / ``.otf`` of the person) passes its bytes as ``font_bytes``.  Kerning is not applied.
"""
from __future__ import annotations

import hashlib
import io
import math
from functools import lru_cache
from pathlib import Path

from .geom import Bezier, Contour, Line, Transform, point_in_polygon, same

FONT_DIR = Path(__file__).resolve().parent / "fonts"
DEFAULT_FAMILY = "DejaVu Sans"
FAMILIES = {                      # family -> (regular file, bold file)
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "DejaVu Serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    "DejaVu Sans Mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
}
ITALIC_SLANT = math.radians(12.0)


class TextError(ValueError):
    pass


def families() -> list[str]:
    """The names of the fonts that come with the engine."""
    return list(FAMILIES)


class _Font:
    """A font file read once: outlines of its letters (in font units), advances and the sizes that scale them."""

    def __init__(self, data: bytes):
        from fontTools.ttLib import TTFont
        try:
            self.tt = TTFont(io.BytesIO(data), lazy=True)
            self.cmap = self.tt.getBestCmap() or {}
            self.glyphs = self.tt.getGlyphSet()
            self.hmtx = self.tt["hmtx"]
            self.upem = float(self.tt["head"].unitsPerEm) or 1000.0
        except Exception as exc:  # noqa: BLE001  (fontTools raises its own kinds for a file that is not a font)
            raise TextError(f"this is not a font file ({type(exc).__name__}: {exc})") from exc
        self._cache: dict[str, list[Contour]] = {}
        hhea = self.tt["hhea"]
        self.ascent, self.descent = float(hhea.ascent), float(hhea.descent)
        os2 = self.tt["OS/2"] if "OS/2" in self.tt else None
        cap = float(getattr(os2, "sCapHeight", 0) or 0) if os2 is not None else 0.0
        if cap <= 0:                                        # (old fonts: the height of the letter H)
            h = self.outline("H")
            cap = max((p[1] for c in h for s in c.segs for p in (s.a, s.b)), default=0.0) or self.ascent * 0.7
        self.cap = cap

    def glyph_name(self, ch: str) -> str | None:
        return self.cmap.get(ord(ch))

    def advance(self, ch: str) -> float:
        name = self.glyph_name(ch)
        if name is None:
            name = self.glyph_name(" ") or ".notdef"
        try:
            return float(self.hmtx[name][0])
        except KeyError:
            return self.upem * 0.5

    def outline(self, ch: str) -> list[Contour]:
        """The closed contours of a letter in font units (Y up), outer ones counter-clockwise, holes clockwise."""
        name = self.glyph_name(ch)
        if name is None or not ch.strip():
            return []
        hit = self._cache.get(name)
        if hit is None:
            hit = self._cache[name] = _orient(_draw(self.glyphs, name))
        return hit


def _draw(glyphs, name: str) -> list[Contour]:
    from fontTools.pens.basePen import BasePen

    class Pen(BasePen):
        def __init__(self, gs):
            super().__init__(gs)
            self.out: list[Contour] = []
            self.segs: list = []
            self.cur = None

        def _moveTo(self, pt):
            self._flush()
            self.cur = pt

        def _lineTo(self, pt):
            if not same(self.cur, pt, 1e-9):
                self.segs.append(Line(self.cur, pt))
            self.cur = pt

        def _curveToOne(self, p1, p2, p3):                  # (a quadratic curve of a TrueType font arrives here as a cubic)
            self.segs.append(Bezier(self.cur, p1, p2, p3))
            self.cur = p3

        def _closePath(self):
            self._flush()

        def _endPath(self):
            self._flush()

        def _flush(self):
            if self.segs:
                if not same(self.segs[-1].b, self.segs[0].a, 1e-9):
                    self.segs.append(Line(self.segs[-1].b, self.segs[0].a))
                self.out.append(Contour(self.segs, True))
            self.segs = []

    pen = Pen(glyphs)
    glyphs[name].draw(pen)
    pen._flush()
    return [c for c in pen.out if len(c.segs) >= 2 and c.area(1.0) > 1e-9]


def _orient(contours: list[Contour]) -> list[Contour]:
    """Outer outlines counter-clockwise, the holes in them clockwise (what the desktop's text gives): by how deep each one lies
    inside the others."""
    polys = [c.flatten(2.0) for c in contours]
    out = []
    for i, c in enumerate(contours):
        p = polys[i][0]
        depth = sum(1 for j, q in enumerate(polys) if j != i and point_in_polygon(p, q))
        out.append(c.oriented(depth % 2 == 0))
    return out


@lru_cache(maxsize=16)
def _bundled(family: str, bold: bool) -> _Font:
    files = FAMILIES.get(family) or FAMILIES[DEFAULT_FAMILY]
    return _Font((FONT_DIR / files[1 if bold else 0]).read_bytes())


_custom: dict[str, _Font] = {}


def font_of(family: str = DEFAULT_FAMILY, bold: bool = False, font_bytes: bytes | None = None) -> _Font:
    """The font: the file the caller brings, else the bundled family (an unknown name falls back to DejaVu Sans)."""
    if font_bytes:
        key = hashlib.sha1(font_bytes).hexdigest()
        if key not in _custom:
            if len(_custom) > 4:
                _custom.clear()
            _custom[key] = _Font(font_bytes)
        return _custom[key]
    return _bundled(family if family in FAMILIES else DEFAULT_FAMILY, bool(bold))


def _place(font: _Font, ch: str, k: float, x: float, y: float, italic: bool) -> list[Contour]:
    """The letter at (x, y) (its baseline start), scaled by ``k`` mm per font unit."""
    T = Transform.translate(x, y) @ Transform.scale(k)
    if italic:
        T = T @ Transform.skew(ITALIC_SLANT)
    return [c.transformed(T) for c in font.outline(ch)]


def text_contours(text: str, family: str = DEFAULT_FAMILY, height: float = 10.0, bold: bool = False, italic: bool = False,
                  spacing: float = 0.0, line_spacing: float = 1.4, align: str = "left",
                  font_bytes: bytes | None = None) -> list[Contour]:
    """Outline contours of ``text`` with capital letters ``height`` mm tall, baseline of the first line at y = 0 and the next
    lines below it.  ``spacing`` (mm) is added after every letter; ``align`` ``left``, ``center`` or ``right`` for several lines."""
    if height <= 0:
        raise TextError("the height of the letters must be more than 0")
    font = font_of(family, bold, font_bytes)
    k = height / font.cap
    lines = text.replace("\t", "    ").replace("\r", "").split("\n")
    widths = [sum(font.advance(ch) * k + spacing for ch in ln) for ln in lines]
    wmax = max(widths, default=0.0)
    pitch = (font.ascent - font.descent) * k * line_spacing / 1.2
    out: list[Contour] = []
    for i, (ln, w) in enumerate(zip(lines, widths)):
        x = {"left": 0.0, "center": (wmax - w) / 2, "right": wmax - w}.get(align, 0.0)
        y = -i * pitch
        for ch in ln:
            out += _place(font, ch, k, x, y, italic)
            x += font.advance(ch) * k + spacing
    return out


def text_on_curve(text: str, family: str, height: float, curve: Contour, bold: bool = False, italic: bool = False,
                  spacing: float = 0.0, offset: float = 0.0, align: str = "center",
                  font_bytes: bytes | None = None) -> list[Contour]:
    """Characters placed along ``curve``; ``offset`` moves the baseline to the left of the curve."""
    if height <= 0:
        raise TextError("the height of the letters must be more than 0")
    font = font_of(family, bold, font_bytes)
    k = height / font.cap
    text = text.replace("\n", " ")
    advances = [font.advance(ch) * k + spacing for ch in text]
    total = sum(advances) - (spacing if text else 0.0)
    L = curve.length()
    if L <= 1e-9:
        return []
    s = {"start": 0.0, "center": (L - total) / 2, "end": L - total}.get(align, 0.0)
    if not curve.closed:
        s = max(0.0, s)
    out: list[Contour] = []
    for ch, adv in zip(text, advances):
        width = adv - spacing
        mid = s + width / 2
        if curve.closed:
            mid %= L
        if ch.strip():
            glyph = _place(font, ch, k, 0.0, 0.0, italic)
            at = min(max(mid, 0.0), L)
            p = curve.point_at_length(at)
            t = curve.tangent_at_length(at)
            ang = math.atan2(t[1], t[0])
            base = (p[0] - t[1] * offset, p[1] + t[0] * offset)
            T = Transform.translate(*base) @ Transform.rotate(ang) @ Transform.translate(-width / 2, 0.0)
            out += [c.transformed(T) for c in glyph]
        s += adv
    return out
