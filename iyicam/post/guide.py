"""What the words of a G-code line mean: ``X10.5`` ``I5`` ``J0`` ``F800`` ...

Used by every interface that shows G-code (the page, the desktop program, scripts) so that a person who sees
``G2 X10 Y0 I5 J0`` can learn what it says.  There are two things here:

* :data:`WORDS` / :func:`glossary`: the letters and the codes (G, M) with a sentence each, in English (``en``) and
  Turkish (``tr``);
* :func:`explain`: one line of a program explained *in its place*: the position the tool is at when the line
  starts is followed through the lines before it, so an arc's centre (``I`` and ``J`` count from the start of the arc)
  is given as a point on the sheet, with the radius.

The explanation is in the coordinates of the G-code (after the origin and the units of the post), as written.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

LANGS = ("en", "tr")

# --------------------------------------------------------------------------------------------------
# the letters
# --------------------------------------------------------------------------------------------------

WORDS: dict[str, dict[str, str]] = {
    "G": {"en": "Preparatory code: what kind of move to make or which mode is on (G0 rapid, G1 cut, G2 / G3 arc, "
                "G21 millimetres, G90 absolute ...).",
          "tr": "Hazırlık kodu: nasıl bir hareket yapılacağını ya da hangi modun açık olduğunu söyler (G0 hızlı, G1 kesme, "
                "G2 / G3 yay, G21 milimetre, G90 mutlak ...)."},
    "X": {"en": "Where to go along X (left to right on the sheet).",
          "tr": "X ekseninde gidilecek yer (sacda soldan sağa)."},
    "Y": {"en": "Where to go along Y (front to back on the sheet).",
          "tr": "Y ekseninde gidilecek yer (sacda önden arkaya)."},
    "Z": {"en": "Where to go along Z: the height of the tool. Z0 is usually the top of the material, a minus number is "
                "inside the material.",
          "tr": "Z ekseninde gidilecek yer: kesicinin yüksekliği. Z0 genelde malzemenin üstüdür, eksi sayı malzemenin "
                "içi demektir."},
    "A": {"en": "Angle of the rotary axis in degrees (when the material is wrapped around a cylinder).",
          "tr": "Döner eksenin açısı, derece (malzeme bir silindire sarılıysa)."},
    "I": {"en": "Arc centre, along X: how far the CENTRE of the arc is from the point where the arc STARTS, to the "
                "right (+) or to the left (−). Not a position on the sheet, a distance from the start of the arc.",
          "tr": "Yay merkezi, X yönünde: yayın MERKEZİ, yayın BAŞLADIĞI noktadan sağa (+) ya da sola (−) ne kadar "
                "uzakta. Sacda bir konum değil, yayın başlangıcından olan uzaklıktır."},
    "J": {"en": "Arc centre, along Y: how far the CENTRE of the arc is from the point where the arc STARTS, away (+) or "
                "toward you (−). Not a position on the sheet, a distance from the start of the arc.",
          "tr": "Yay merkezi, Y yönünde: yayın MERKEZİ, yayın BAŞLADIĞI noktadan ileriye (+) ya da geriye (−) ne kadar "
                "uzakta. Sacda bir konum değil, yayın başlangıcından olan uzaklıktır."},
    "K": {"en": "Arc centre, along Z. Only for arcs in a plane that has Z in it; the flat arcs of this program never "
                "need it.",
          "tr": "Yay merkezi, Z yönünde. Yalnızca Z'yi içeren düzlemdeki yaylar için; bu programın düz yayları bunu "
                "hiç kullanmaz."},
    "R": {"en": "Arc radius, instead of I and J. A minus radius is an arc of more than half a circle.",
          "tr": "Yay yarıçapı (I ve J yerine). Eksi yarıçap, yarım çemberden büyük yay demektir."},
    "F": {"en": "Feed rate: how fast the tool cuts (millimetres or inches per minute).",
          "tr": "İlerleme hızı: kesicinin ne kadar hızlı kestiği (dakikada milimetre ya da inç)."},
    "S": {"en": "Spindle speed in revolutions per minute.",
          "tr": "İş mili devri, dakikada devir."},
    "T": {"en": "Tool number.", "tr": "Takım numarası."},
    "M": {"en": "Machine command: spindle on / off, tool change, coolant, end of the program ...",
          "tr": "Makine komutu: iş mili aç / kapat, takım değiştir, soğutma, programın sonu ..."},
    "N": {"en": "Line number (only for reading, the machine does nothing with it).",
          "tr": "Satır numarası (yalnızca okumak için, makine bununla bir şey yapmaz)."},
    "O": {"en": "Program number.", "tr": "Program numarası."},
    "P": {"en": "A time: how long to wait (dwell) or how long a drill stays at the bottom.",
          "tr": "Bir süre: ne kadar beklenecek (dwell) ya da matkap dipte ne kadar kalacak."},
    "Q": {"en": "Peck depth of a drilling cycle: how deep each peck goes before the drill backs out.",
          "tr": "Delme döngüsünün gagalama (peck) derinliği: matkap geri çıkmadan her seferde ne kadar iner."},
    "L": {"en": "Repeat count.", "tr": "Tekrar sayısı."},
}

G_CODES: dict[str, dict[str, str]] = {
    "0": {"en": "Rapid move: the tool goes there as fast as the machine can (not cutting).",
          "tr": "Hızlı hareket: kesici oraya makinenin gidebildiği en hızlı şekilde gider (kesme yok)."},
    "1": {"en": "Cutting move in a straight line, at the feed rate F.",
          "tr": "F ilerleme hızıyla düz çizgi boyunca kesme hareketi."},
    "2": {"en": "Arc, clockwise (seen from above), at the feed rate F.",
          "tr": "Yay, saat yönünde (yukarıdan bakınca), F ilerleme hızıyla."},
    "3": {"en": "Arc, counter-clockwise (seen from above), at the feed rate F.",
          "tr": "Yay, saat yönünün tersine (yukarıdan bakınca), F ilerleme hızıyla."},
    "4": {"en": "Dwell: wait for the time given by P.", "tr": "Bekleme: P ile verilen süre kadar bekle."},
    "17": {"en": "Arcs are in the XY plane (the flat of the sheet).", "tr": "Yaylar XY düzleminde (sacın düzü)."},
    "20": {"en": "Units are inches.", "tr": "Birim inç."},
    "21": {"en": "Units are millimetres.", "tr": "Birim milimetre."},
    "28": {"en": "Go to the home position.", "tr": "Ev konumuna git."},
    "40": {"en": "Cutter compensation off (the toolpath is already offset).",
           "tr": "Kesici telafisi kapalı (takım yolu zaten kaydırılmış)."},
    "49": {"en": "Tool length offset off.", "tr": "Takım boyu telafisi kapalı."},
    "53": {"en": "Positions in this line are in machine coordinates.",
           "tr": "Bu satırdaki konumlar makine koordinatlarında."},
    "54": {"en": "Work coordinate system 1 (where the zero of the job is).", "tr": "1. iş koordinat sistemi (işin sıfırı)."},
    "80": {"en": "End of the drilling cycle.", "tr": "Delme döngüsünün sonu."},
    "81": {"en": "Drilling cycle: go down to Z at the feed rate and come back up.",
           "tr": "Delme döngüsü: F hızıyla Z'ye kadar in, geri çık."},
    "82": {"en": "Drilling cycle with a dwell (P) at the bottom.", "tr": "Dipte bekleyen (P) delme döngüsü."},
    "83": {"en": "Peck drilling cycle: go down Q at a time and back out to clear the chips.",
           "tr": "Gagalamalı delme döngüsü: her seferde Q kadar iner, talaş için geri çıkar."},
    "90": {"en": "Absolute positions: X Y Z are places on the sheet.", "tr": "Mutlak konum: X Y Z sacdaki yerlerdir."},
    "90.1": {"en": "Arc centres (I J) are absolute places.", "tr": "Yay merkezleri (I J) mutlak yerlerdir."},
    "91": {"en": "Incremental positions: X Y Z are distances from where the tool is.",
           "tr": "Artımlı konum: X Y Z kesicinin bulunduğu yerden olan uzaklıklardır."},
    "91.1": {"en": "Arc centres (I J) are counted from the start of the arc.",
             "tr": "Yay merkezleri (I J) yayın başlangıcından sayılır."},
    "93": {"en": "Inverse time feed: F is 1 / minutes for the move.", "tr": "Ters zaman ilerlemesi: F = 1 / hareketin dakikası."},
    "94": {"en": "Feed rate in units per minute.", "tr": "İlerleme hızı dakikada birim."},
    "98": {"en": "After the drilling cycle return to the starting height.", "tr": "Delme döngüsünden sonra başlangıç yüksekliğine dön."},
    "99": {"en": "After the drilling cycle return to the R height.", "tr": "Delme döngüsünden sonra R yüksekliğine dön."},
}

M_CODES: dict[str, dict[str, str]] = {
    "0": {"en": "Stop the program and wait for the operator.", "tr": "Programı durdur, operatörü bekle."},
    "1": {"en": "Optional stop.", "tr": "İsteğe bağlı durdurma."},
    "2": {"en": "End of the program.", "tr": "Program sonu."},
    "3": {"en": "Spindle on, clockwise.", "tr": "İş milini saat yönünde çalıştır."},
    "4": {"en": "Spindle on, counter-clockwise.", "tr": "İş milini saat yönünün tersine çalıştır."},
    "5": {"en": "Spindle stop.", "tr": "İş milini durdur."},
    "6": {"en": "Tool change (to the tool of T).", "tr": "Takım değiştir (T'deki takıma)."},
    "7": {"en": "Mist coolant on.", "tr": "Sis soğutmayı aç."},
    "8": {"en": "Flood coolant on.", "tr": "Sıvı soğutmayı aç."},
    "9": {"en": "Coolant off.", "tr": "Soğutmayı kapat."},
    "30": {"en": "End of the program, go back to its start.", "tr": "Program sonu, başa dön."},
}

# sentences that are built from a line (``{...}`` are filled in); (English, Turkish)
_S: dict[str, tuple[str, str]] = {
    "rapid": ("Rapid move (not cutting) to {to}.", "{to} noktasına hızlı hareket (kesme yok)."),
    "linear": ("Cut in a straight line to {to}.", "{to} noktasına düz çizgiyle kesme."),
    "dir_cw": ("clockwise", "saat yönünde"),
    "dir_ccw": ("counter-clockwise", "saat yönünün tersine"),
    "arc": ("Arc, {dir}, to {to}.", "{to} noktasına, {dir} yay."),
    "arc_centre": ("The centre of the circle is at {centre} ({detail}); radius {r}.",
                   "Dairenin merkezi {centre} noktasında ({detail}); yarıçap {r}."),
    "arc_rel": ("I{i}, J{j} = {i_dir}, {j_dir} from where the arc starts ({start})",
                "I{i}, J{j} = yayın başladığı yerden ({start}) {i_dir}, {j_dir}"),
    "arc_rel_nostart": ("I{i}, J{j}: from where the arc starts", "I{i}, J{j}: yayın başladığı yerden"),
    "arc_abs": ("I and J are the centre itself", "I ve J merkezin kendisidir"),
    "arc_r": ("The arc has radius {r}{big}.", "Yayın yarıçapı {r}{big}."),
    "big": (" (a minus radius: more than half a circle)", " (eksi yarıçap: yarım çemberden büyük)"),
    "right": ("{v} to the right", "{v} sağa"), "left": ("{v} to the left", "{v} sola"),
    "away": ("{v} away", "{v} ileri"), "toward": ("{v} toward you", "{v} geri"),
    "none": ("0 sideways", "0 yana"), "none_y": ("0 forward or back", "0 ileri-geri"),
    "feed": ("Speed {f} per minute.", "Hız dakikada {f}."),
    "dwell": ("Wait {p} (the unit depends on the post: seconds or milliseconds).",
              "{p} bekle (birim posta bağlı: saniye ya da milisaniye)."),
    "spindle": ("Spindle {rpm} rpm.", "İş mili {rpm} dev/dak."),
    "tool": ("Tool {n}.", "{n} numaralı takım."),
    "comment": ("A comment: the machine ignores it.", "Yorum: makine bunu yok sayar."),
    "empty": ("An empty line.", "Boş satır."),
    "header": ("A program marker (%) or number: it only frames the program.", "Program işareti (%) ya da numarası: programı çerçeveler."),
    "unknown": ("Not described here.", "Burada açıklanmadı."),
    "here": ("the same place", "aynı yer"),
    "drill_r": ("In a drilling cycle R is the height the tool returns to between holes.",
                "Delme döngüsünde R, kesicinin delikler arasında döndüğü yüksekliktir."),
}

# how to read an arc line, for a window that teaches it (three short paragraphs)
ARC_HELP: dict[str, list[str]] = {
    "en": ["G2 cuts an arc clockwise, G3 counter-clockwise. X and Y are where the arc ENDS; it starts where the tool is "
           "at that moment.",
           "The centre of the arc is given by I and J. I is how far the centre is from the START of the arc along X, "
           "J along Y. So I and J are not a place on the sheet, they are a distance (minus: to the left or toward you). "
           "From (10, 0), G2 X20 Y0 I5 J0 turns about the centre (15, 0): the radius is 5.",
           "Some controllers want R (the radius) instead of I J, and some do not know arcs at all and cut short straight "
           "lines; that is chosen in the settings of the post (the Arcs part)."],
    "tr": ["G2 saat yönünde, G3 saat yönünün tersine bir yay keser. X ve Y yayın BİTİŞ noktasıdır; yay, kesicinin o anda "
           "bulunduğu noktadan başlar.",
           "Yayın merkezi I ve J ile verilir. I, merkezin X yönünde, J ise Y yönünde yayın BAŞLANGIÇ noktasından ne kadar "
           "uzakta olduğudur. Yani I ve J sacda bir konum değil, bir uzaklıktır (eksi: sola ya da geriye). (10, 0) "
           "noktasından G2 X20 Y0 I5 J0, merkezi (15, 0) olan yarıçapı 5 bir yaydır.",
           "Bazı kontrolcüler I J yerine R (yarıçap) ister, bazıları yayı hiç bilmez ve kısa doğrularla keser; bunlar post "
           "ayarlarından seçilir (Yaylar bölümü)."],
}

_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_COMMENT = re.compile(r"\([^)]*\)|;.*$")


def _t(key: str, lang: str, **kw) -> str:
    en, tr = _S[key]
    return (tr if lang == "tr" else en).format(**kw)


def glossary(lang: str = "en") -> list[dict]:
    """The letters, then the G codes and the M codes, as ``[{"word", "text", "group"}]`` in a language."""
    lang = lang if lang in LANGS else "en"
    out = [{"word": w, "text": d[lang], "group": "letter"} for w, d in WORDS.items()]
    out += [{"word": f"G{n}", "text": d[lang], "group": "g"} for n, d in G_CODES.items()]
    out += [{"word": f"M{n}", "text": d[lang], "group": "m"} for n, d in M_CODES.items()]
    return out


# --------------------------------------------------------------------------------------------------
# following a program
# --------------------------------------------------------------------------------------------------

@dataclass
class State:
    """What a program has set up so far (modal values)."""
    x: float | None = None
    y: float | None = None
    z: float | None = None
    absolute: bool = True            # G90 / G91
    arc_absolute: bool = False       # G90.1 / G91.1 (I and J are places, not distances from the start)
    motion: int | None = None        # the last of G0 G1 G2 G3
    inch: bool | None = None         # G20 / G21 (None: not said yet)

    def unit(self) -> str:
        return "" if self.inch is None else (" in" if self.inch else " mm")


def _num(s: str) -> float:
    return float(s)


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def split(line: str) -> tuple[list[tuple[str, str]], str]:
    """``([(letter, number text)], comment)`` of a line.  A line number, ``%`` and comments are handled by the
    caller; the letters are upper case."""
    comments = " ".join(m.group(0).strip("();").strip() for m in _COMMENT.finditer(line))
    body = _COMMENT.sub(" ", line)
    return [(m.group(1).upper(), m.group(2)) for m in _WORD.finditer(body)], comments


def _g_text(num: str, lang: str) -> str:
    key = num
    if key not in G_CODES:
        key = _fmt(_num(num))
    d = G_CODES.get(key)
    return d[lang] if d else _t("unknown", lang)


def _m_text(num: str, lang: str) -> str:
    d = M_CODES.get(str(int(_num(num))))
    return d[lang] if d else _t("unknown", lang)


def _where(st: State, vals: dict[str, float]) -> tuple[float | None, float | None, float | None]:
    """The end point of a move as places on the sheet (``None`` for what is not known)."""
    out = []
    for axis, cur in (("X", st.x), ("Y", st.y), ("Z", st.z)):
        if axis not in vals:
            out.append(cur)
        elif st.absolute:
            out.append(vals[axis])
        else:
            out.append(None if cur is None else cur + vals[axis])
    return out[0], out[1], out[2]


def _point(x, y, z, vals: dict[str, float], unit: str, lang: str) -> str:
    parts = []
    for axis, v in (("X", x), ("Y", y), ("Z", z)):
        if axis in vals and v is not None:
            parts.append(f"{axis}{_fmt(v)}")
    if "A" in vals:
        parts.append(f"A{_fmt(vals['A'])}")
    return " ".join(parts) + (unit if parts else "") if parts else _t("here", lang)


def _side(v: float, axis: str, lang: str) -> str:
    if abs(v) < 1e-9:
        return _t("none" if axis == "I" else "none_y", lang)
    mag = _fmt(abs(v))
    if axis == "I":
        return _t("right" if v > 0 else "left", lang, v=mag)
    return _t("away" if v > 0 else "toward", lang, v=mag)


def _advance(st: State, vals: dict[str, float], drilling: bool) -> None:
    """Move the state on to where a line ends (a hole is at its X and Y: the tool comes back up by itself)."""
    x, y, z = _where(st, vals)
    for axis, v in (("X", x), ("Y", y)) if drilling else (("X", x), ("Y", y), ("Z", z)):
        if axis in vals:
            setattr(st, axis.lower(), v)


def explain_line(line: str, st: State, lang: str = "en", track: bool = False) -> dict:
    """Explain one line and move ``st`` on to what is set up after it.

    The answer: ``summary`` (a sentence or two), ``words`` (``[{"word": "I5", "text": ...}]``, in the order of the
    line), ``arc`` (the arc as numbers, when the line is one) and ``at`` (where the tool is after the line).  With
    ``track`` only ``st`` is moved on (no sentences are made: for the lines before the one that is asked about)."""
    lang = lang if lang in LANGS else "en"
    raw = line.rstrip("\r\n")
    text = raw.strip()
    out: dict = {"line": raw, "summary": "", "words": [], "arc": None, "at": None}
    if not text:
        out["summary"] = _t("empty", lang)
        return out
    words, comment = split(text)
    if not words:
        out["summary"] = _t("comment" if comment else "header", lang)
        return out

    vals: dict[str, float] = {}
    gs: list[str] = []
    ms: list[str] = []
    for letter, num in words:
        if letter == "G":
            gs.append(num)
        elif letter == "M":
            ms.append(num)
        else:
            vals.setdefault(letter, _num(num))
    # the codes that set a mode count before the move of the same line
    for g in gs:
        k = _fmt(_num(g))
        if k == "90":
            st.absolute = True
        elif k == "91":
            st.absolute = False
        elif k == "90.1":
            st.arc_absolute = True
        elif k == "91.1":
            st.arc_absolute = False
        elif k == "20":
            st.inch = True
        elif k == "21":
            st.inch = False
        elif k in ("0", "1", "2", "3"):
            st.motion = int(k)
    drilling = any(_fmt(_num(g)) in ("81", "82", "83") for g in gs)
    moves = any(a in vals for a in ("X", "Y", "Z", "A"))
    if track:
        if moves and (drilling or st.motion is not None):
            _advance(st, vals, drilling)
        return out

    # the words, one by one
    for letter, num in words:
        if letter == "G":
            t = _g_text(num, lang)
        elif letter == "M":
            t = _m_text(num, lang)
        elif letter == "R" and drilling:
            t = _t("drill_r", lang)
        else:
            t = WORDS.get(letter, {}).get(lang) or _t("unknown", lang)
        out["words"].append({"word": f"{letter}{num}", "text": t})

    unit = st.unit()
    summary: list[str] = []
    if moves and not drilling and st.motion is not None:
        x, y, z = _where(st, vals)
        to = _point(x, y, z, vals, unit, lang)
        if st.motion in (2, 3) and ("I" in vals or "J" in vals or "R" in vals):
            cw = st.motion == 2
            summary.append(_t("arc", lang, dir=_t("dir_cw" if cw else "dir_ccw", lang), to=to))
            sx, sy = st.x, st.y
            arc: dict = {"cw": cw, "start": [sx, sy] if sx is not None and sy is not None else None,
                         "end": [x, y] if x is not None and y is not None else None}
            if "R" in vals:
                r = vals["R"]
                summary.append(_t("arc_r", lang, r=_fmt(abs(r)) + unit, big=_t("big", lang) if r < 0 else ""))
                arc["radius"] = abs(r)
            else:
                i, j = vals.get("I", 0.0), vals.get("J", 0.0)
                known = sx is not None and sy is not None
                if st.arc_absolute:
                    cx, cy = i, j
                    detail = _t("arc_abs", lang)
                    r = math.hypot(sx - cx, sy - cy) if known else None
                elif known:
                    cx, cy = sx + i, sy + j
                    detail = _t("arc_rel", lang, i=_fmt(i), j=_fmt(j), i_dir=_side(i, "I", lang),
                                j_dir=_side(j, "J", lang), start=f"X{_fmt(sx)} Y{_fmt(sy)}")
                    r = math.hypot(i, j)
                else:                       # (the program starts with an arc: where it starts is not known)
                    cx = cy = None
                    detail = _t("arc_rel_nostart", lang, i=_fmt(i), j=_fmt(j))
                    r = math.hypot(i, j)
                if cx is not None:
                    summary.append(_t("arc_centre", lang, centre=f"X{_fmt(cx)} Y{_fmt(cy)}", detail=detail,
                                      r="?" if r is None else _fmt(r) + unit))
                    arc["centre"] = [cx, cy]
                else:
                    summary.append(detail + ".")
                if r is not None:
                    arc["radius"] = r
            out["arc"] = arc
        elif st.motion == 0:
            summary.append(_t("rapid", lang, to=to))
        elif st.motion == 1:
            summary.append(_t("linear", lang, to=to))
        elif st.motion in (2, 3):           # an arc without a centre: the machine will refuse it
            summary.append(_t("arc", lang, dir=_t("dir_cw" if st.motion == 2 else "dir_ccw", lang), to=to))
        _advance(st, vals, False)
        out["at"] = [st.x, st.y, st.z]
    elif moves and drilling:
        _advance(st, vals, True)
        out["at"] = [st.x, st.y, st.z]
    if "F" in vals and (st.motion in (1, 2, 3) or drilling):
        summary.append(_t("feed", lang, f=_fmt(vals["F"]) + unit))
    if "S" in vals:
        summary.append(_t("spindle", lang, rpm=_fmt(vals["S"])))
    if "T" in vals:
        summary.append(_t("tool", lang, n=_fmt(vals["T"])))
    if gs and (not moves or drilling):      # (the modes and cycles of the line; a plain move was said above)
        skip = {"0", "1", "2", "3"} | ({"4"} if "P" in vals else set())
        summary += [_g_text(g, lang) for g in gs if _fmt(_num(g)) not in skip]
    if ms:
        summary += [_m_text(m, lang) for m in ms]
    if "P" in vals and any(_fmt(_num(g)) == "4" for g in gs):
        summary.append(_t("dwell", lang, p=_fmt(vals["P"])))
    out["summary"] = " ".join(summary)
    return out


def explain(lines: list[str], index: int, lang: str = "en", *, inch: bool | None = None,
            arc_absolute: bool = False) -> dict:
    """Explain ``lines[index]`` after following the lines before it (their moves, units and modes).

    ``inch`` and ``arc_absolute`` are what the post says before the program says anything (its units, and ``ijk =
    "absolute"``)."""
    if not 0 <= index < len(lines):
        raise IndexError("no such line")
    st = State(inch=inch, arc_absolute=arc_absolute)
    for ln in lines[:index]:
        explain_line(ln, st, lang, track=True)
    return {**explain_line(lines[index], st, lang), "index": index}


class Explainer:
    """Explain any line of a long program, again and again, without reading it from its start each time: the state
    of the program is kept every ``every`` lines (a window with 50 000 lines of G-code answers a click at once)."""

    def __init__(self, lines: list[str], lang: str = "en", *, inch: bool | None = None, arc_absolute: bool = False,
                 every: int = 256):
        self.lines, self.lang, self.every = lines, lang, max(1, every)
        self._cp: dict[int, State] = {0: State(inch=inch, arc_absolute=arc_absolute)}
        self._top = 0

    def at(self, index: int) -> dict:
        if not 0 <= index < len(self.lines):
            raise IndexError("no such line")
        k = (index // self.every) * self.every
        while self._top < k:
            st = replace(self._cp[self._top])
            for ln in self.lines[self._top:self._top + self.every]:
                explain_line(ln, st, self.lang, track=True)
            self._top += self.every
            self._cp[self._top] = st
        st = replace(self._cp[k])
        for ln in self.lines[k:index]:
            explain_line(ln, st, self.lang, track=True)
        return {**explain_line(self.lines[index], st, self.lang), "index": index}
