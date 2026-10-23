"""Spoilboard / stock surfacing toolpath.

Copy this file into ~/.iyicam/plugins to enable it.  It adds a new toolpath
type that zig-zags over the bounding box of the selected vectors (or over the
whole sheet) with a large flat cutter.
"""

PLUGIN = {
    "name": "Surfacing",
    "version": "1.0",
    "author": "iyiCAM",
    "description": "Zig-zag surfacing of the sheet or of the selection's bounding box",
}


def register(api):
    Param = api.Param

    class SurfacingOp(api.Operation):
        id = "surfacing"
        label = "Surfacing (Spoilboard)"
        description = "Flatten the stock or spoilboard with a zig-zag pattern"
        tool_types = ("end_mill", "bull_nose")

        @classmethod
        def params(cls):
            return [
                Param("depth", "Depth", "length", 0.5, min=0.0, group="Surfacing"),
                Param("stepover", "Stepover (0 = tool)", "length", 0.0, min=0.0, group="Surfacing"),
                Param("area", "Area", "choice", "selection",
                      choices=[("selection", "Bounding box of the vectors"), ("sheet", "Whole sheet")],
                      group="Surfacing"),
                Param("direction", "Lines along", "choice", "x", choices=[("x", "X"), ("y", "Y")],
                      group="Surfacing"),
                Param("overlap", "Run past the edge", "length", 2.0, min=0.0, group="Surfacing"),
            ]

        def compute(self, ctx):
            tool = ctx.tool
            v = self.values
            if v["area"] == "sheet":
                x0, y0, x1, y1 = 0.0, 0.0, ctx.sheet.width, ctx.sheet.height
            else:
                bb = api.geom.BBox()
                for o in ctx.objects:
                    bb = bb.union(o.bbox())
                x0, y0, x1, y1 = bb.xmin, bb.ymin, bb.xmax, bb.ymax
            r = tool.radius
            over = v["overlap"]
            so = v["stepover"] or tool.stepover
            z = -v["depth"]
            mb = api.MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate)
            if v["direction"] == "y":  # work in a swapped frame
                x0, y0, x1, y1 = y0, x0, y1, x1
            a0, a1 = x0 - r - over, x1 + r + over
            y = y0
            rows = []
            while y < y1 + 1e-9:
                rows.append(y)
                y += so
            if rows[-1] < y1 - 1e-9:
                rows.append(y1)

            def pt(a, b):
                return (b, a) if v["direction"] == "y" else (a, b)

            first = True
            for i, yy in enumerate(rows):
                s, e = (a0, a1) if i % 2 == 0 else (a1, a0)
                if first:
                    mb.travel(*pt(s, yy), z)
                    first = False
                else:
                    mb.feed(*pt(s, yy), z)
                mb.feed(*pt(e, yy), z)
            return [self.make_toolpath(ctx, mb.finish())]

    api.add_operation(SurfacingOp)
