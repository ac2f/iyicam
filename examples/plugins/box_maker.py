"""Finger-joint box generator.

Creates the six (or five, without a lid) panels of a box with interlocking
finger joints, ready for profiling.  Optional dog-bones let the joints fit when
they are cut with a round tool.
"""

PLUGIN = {
    "name": "Box Maker",
    "version": "1.0",
    "author": "iyiCAM",
    "description": "Generates finger jointed box panels",
}


def fingers(length, t, finger, start_out, d_start, d_end):
    """Points of one edge along +X from x = d_start to x = length - d_end.

    The edge alternates between the outside line (y = 0) and the inside line
    (y = t); ``start_out`` chooses whether the first finger sticks out.  An odd
    number of fingers keeps the pattern symmetric so mating edges match.
    """
    if start_out is None:  # plain edge
        return [(d_start, 0.0), (length - d_end, 0.0)]
    n = max(3, int(length / finger))
    if n % 2 == 0:
        n += 1
    w = length / n
    pts = []
    for i in range(n):
        y = 0.0 if (i % 2 == 0) == start_out else t
        xa = i * w if i > 0 else d_start
        xb = (i + 1) * w if i < n - 1 else length - d_end
        pts += [(xa, y), (xb, y)]
    return pts


def panel(width, height, t, finger, edges):
    """Panel outline; ``edges`` = (bottom, right, top, left), each True (fingers out
    first), False (slot first) or None (plain edge)."""
    import math
    depth = [0.0 if e is None or e else t for e in edges]
    lengths = [width, height, width, height]
    angles = [0.0, math.pi / 2, math.pi, -math.pi / 2]
    origins = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    outline = []
    for k in range(4):
        pts = fingers(lengths[k], t, finger, edges[k], depth[k - 1], depth[(k + 1) % 4])
        c, s = math.cos(angles[k]), math.sin(angles[k])
        ox, oy = origins[k]
        for x, y in pts:
            # local +y points into the panel
            outline.append((ox + x * c - y * s, oy + x * s + y * c))
    clean = []
    for p in outline:
        if not clean or abs(p[0] - clean[-1][0]) > 1e-9 or abs(p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    if abs(clean[0][0] - clean[-1][0]) < 1e-9 and abs(clean[0][1] - clean[-1][1]) < 1e-9:
        clean.pop()
    return clean


def register(api):
    P = api.Param

    def run(ctx, v):
        W, D, H, t, f = v["width"], v["depth"], v["height"], v["thickness"], v["finger"]
        if min(W, D, H) <= 4 * t:
            raise api.CommandError("The box is too small for this material thickness")
        gap = v["gap"]
        x0 = v["x"] if v["x"] is not None else 10.0
        y0 = v["y"] if v["y"] is not None else 10.0
        # edges: bottom, right, top, left (True = fingers out first)
        specs = [
            ("Bottom", W, D, (False, False, False, False)),
            ("Front", W, H, (True, False, None if not v["lid"] else True, False)),
            ("Back", W, H, (True, False, None if not v["lid"] else True, False)),
            ("Left", D, H, (True, True, None if not v["lid"] else True, True)),
            ("Right", D, H, (True, True, None if not v["lid"] else True, True)),
        ]
        if v["lid"]:
            specs.append(("Lid", W, D, (False, False, False, False)))
        x = x0
        ids = []
        for name, w, h, edges in specs:
            pts = panel(w, h, t, f, edges)
            c = api.geom.Contour.polyline([(x + px, y0 + py) for px, py in pts], True)
            if v["dogbones"] and v["tool"] > 0:
                c = api.vectorops.dogbone(c, v["tool"], "outside")
            ids.append(ctx.add(c, name=name, select=False).id)
            x += w + gap
        ctx.select(ids)

    api.add_command("box_maker.box", "Finger Joint Box...", run, menu="Plugins", params=[
        P("width", "Width (outside)", "length", 200.0, min=10),
        P("depth", "Depth (outside)", "length", 150.0, min=10),
        P("height", "Height (outside)", "length", 100.0, min=10),
        P("thickness", "Material thickness", "length", 6.0, min=0.5),
        P("finger", "Finger width", "length", 20.0, min=1),
        P("lid", "With lid", "bool", False),
        P("dogbones", "Dog-bone corners", "bool", True),
        P("tool", "Tool diameter", "length", 3.0, min=0.0),
        P("gap", "Gap between panels", "length", 10.0, min=0.0),
        P("x", "Start X (empty = 10)", "length", None),
        P("y", "Start Y (empty = 10)", "length", None),
    ])
