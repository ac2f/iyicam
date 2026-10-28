"""``iyicam selftest``: use every part of the program once, on a made up project, and say what does not work.

It is meant for a freshly installed or packaged program (a part missing from the package, a library that
cannot be loaded): it writes only to a temporary folder and never touches the settings of the user.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path


class SelfTest:
    def __init__(self, tmp: Path, gui: bool):
        self.tmp, self.gui = tmp, gui
        self.results: list[tuple[str, str]] = []      # (name, "" when it worked, else what went wrong)

    def run(self) -> int:
        checks = [("libraries", self.libraries), ("built-in features", self.registry), ("project file", self.project),
                  ("toolpaths and every post-processor", self.toolpaths), ("DXF export and import", self.dxf),
                  ("SVG import", self.svg), ("nesting", self.nesting)]
        if self.gui:
            checks += [("window, tabs and themes", self.window)]
        width = max(len(n) for n, _ in checks)
        for name, fn in checks:
            t = time.time()
            try:
                detail = fn() or ""
                self.results.append((name, ""))
                print(f"  ok    {name:<{width}}  {detail} ({time.time() - t:.1f} s)", flush=True)
            except Exception as exc:  # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
                self.results.append((name, msg))
                print(f"  FAIL  {name:<{width}}  {msg}", flush=True)
                print("        " + traceback.format_exc(limit=4).replace("\n", "\n        "), file=sys.stderr)
        failed = [n for n, e in self.results if e]
        print(("\nEverything works." if not failed else f"\n{len(failed)} of {len(self.results)} checks failed: "
               + ", ".join(failed)), flush=True)
        return 1 if failed else 0

    # -- the checks ------------------------------------------------------------------------------------
    def libraries(self) -> str:
        import ezdxf
        import numpy
        import pyclipper
        import svgelements
        names = (f"numpy {numpy.__version__}, ezdxf {ezdxf.__version__}, pyclipper {pyclipper.__version__}, "
                 f"svgelements {svgelements.SVGELEMENTS_VERSION}")
        if self.gui:
            import PySide6
            names += f", PySide6 {PySide6.__version__}"
        return names

    def registry(self) -> str:
        from .builtins import ensure_registered
        from .core.registry import REGISTRY
        reg = ensure_registered(REGISTRY)
        ops = {"profile", "pocket", "drill", "engrave", "vcarve", "inlay", "fluting"}
        if not ops <= set(reg.operations):
            raise RuntimeError(f"toolpath types missing: {sorted(ops - set(reg.operations))}")
        if len(reg.posts) < 6:     # the post-processors are files that are part of the package
            raise RuntimeError(f"only {len(reg.posts)} post-processors found (the .toml files are missing?)")
        for ext in ("dxf", "svg", "plt"):
            if reg.importer_for(ext) is None:
                raise RuntimeError(f"no importer for .{ext}")
        return f"{len(reg.operations)} toolpath types, {len(reg.posts)} post-processors, {len(reg.commands)} commands"

    def _project(self):
        from .core import shapes
        from .core.document import Project, Selection, ToolpathDef, VectorObject
        from .core.tooling import Tool
        p = Project("selftest")
        p.material.thickness = 18.0
        s = p.sheets[0]
        s.width, s.height = 600.0, 400.0
        layer = p.layers[0].id
        objs = [VectorObject([shapes.rectangle(40, 40, 160, 100, corner_radius=10), shapes.circle((120, 90), 25)],
                             layer, "panel"),
                VectorObject([shapes.circle((300, 100), 45)], layer, "disc"),
                VectorObject([shapes.polyline([(380, 40), (500, 40), (500, 140), (440, 180), (380, 140)], True)],
                             layer, "house")]
        s.vectors.extend(objs)
        tool = Tool(name="EM6", type="end_mill", diameter=6.0, number=1, stepdown=3.0, stepover=2.4,
                    feed_rate=3000, plunge_rate=1000, spindle_speed=18000)
        ids = [o.id for o in objs]
        p.toolpaths.append(ToolpathDef("Profile", "profile", tool, {"through": True}, Selection("vectors", ids)))
        p.toolpaths.append(ToolpathDef("Pocket", "pocket", tool, {"cut_depth": 4.0}, Selection("vectors", ids[1:2])))
        return p, s, objs

    def project(self) -> str:
        from .core.document import Project
        p, s, objs = self._project()
        f = self.tmp / "selftest.icam"
        p.save(f)
        q = Project.load(f)
        if len(q.sheets[0].vectors) != len(objs) or len(q.toolpaths) != 2:
            raise RuntimeError("the project that was loaded is not the one that was saved")
        return f"{f.stat().st_size} bytes"

    def toolpaths(self) -> str:
        from .builtins import ensure_registered
        from .cam.job import compute_all, sheet_toolpaths
        from .core.registry import REGISTRY
        from .post.export import post_toolpaths
        ensure_registered(REGISTRY)
        p, s, _ = self._project()
        results = compute_all(p)
        errors = [r.error for r in results.values() if r.error]
        if errors:
            raise RuntimeError("; ".join(errors))
        tps = sheet_toolpaths(p, results, s)
        if not tps:
            raise RuntimeError("no toolpath was calculated")
        lines = 0
        for post in REGISTRY.posts.values():
            for _suffix, text in post_toolpaths(p, s, tps, post):
                if not any(w in text for w in ("G1", "G01")) or "nan" in text.lower():
                    raise RuntimeError(f"post-processor {post.id} wrote no usable G-code")
                lines += text.count("\n")
        return f"{len(tps)} toolpaths, {lines} lines of G-code from {len(REGISTRY.posts)} post-processors"

    def dxf(self) -> str:
        from .builtins import ensure_registered
        from .core.document import Project
        from .core.registry import REGISTRY
        from .formats.report import ImportReport, run_importer
        reg = ensure_registered(REGISTRY)
        p, s, objs = self._project()
        f = self.tmp / "out.dxf"
        reg.exporters["dxf"].func(str(f), p, s, objs)
        q = Project("back")
        imp = reg.importer_for("dxf")
        got = run_importer(imp.func, str(f), q, q.sheets[0], ImportReport(), layer_id=q.layers[0].id)
        n_in = sum(len(o.contours) for o in objs)
        n_out = sum(len(o.contours) for o in got)
        if n_out != n_in:
            raise RuntimeError(f"{n_in} outlines written, {n_out} read back")
        return f"{n_out} outlines"

    def svg(self) -> str:
        from .builtins import ensure_registered
        from .core.document import Project
        from .core.registry import REGISTRY
        from .formats.report import ImportReport, run_importer
        reg = ensure_registered(REGISTRY)
        f = self.tmp / "in.svg"
        f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                     '<rect x="5" y="5" width="40" height="30" rx="4"/><circle cx="70" cy="30" r="15"/></svg>',
                     encoding="utf-8")
        q = Project("svg")
        got = run_importer(reg.importer_for("svg").func, str(f), q, q.sheets[0], ImportReport(),
                           layer_id=q.layers[0].id)
        if len(got) < 2:
            raise RuntimeError(f"{len(got)} vector(s) read from a drawing with two shapes")
        return f"{len(got)} vectors"

    def nesting(self) -> str:
        from .core import shapes
        from .core.document import VectorObject
        from .nesting.nest import NestSettings
        from .nesting.parts import nest_objects
        p, s, _ = self._project()
        s.vectors.clear()
        # (drawn apart: shapes inside each other would count as one part with holes)
        parts = [VectorObject([shapes.rectangle(10 + 190 * (i % 3), 10 + 100 * (i // 3), 90 + 12 * i, 60 + 7 * (i % 3),
                                                corner_radius=5)], p.layers[0].id, f"r{i}") for i in range(6)]
        parts.append(VectorObject([shapes.circle((300, 290), 35)], p.layers[0].id, "c"))
        s.vectors.extend(parts)
        out = nest_objects(p, parts, NestSettings(400, 300, 5, 4, [0.0, 90.0], "left", iterations=2),
                           use_sheets=[s])
        if out.placed != len(parts) or out.unplaced:
            raise RuntimeError(f"{out.placed} of {len(parts)} parts placed")
        return f"{out.placed} parts on {out.result.sheets} plate(s)"

    def window(self) -> str:
        from PySide6.QtWidgets import QApplication

        from .gui.app import create_window
        app, win = create_window(["iyicam"], load_plugins=False)
        try:
            win.resize(1100, 700)
            win.show()
            app.processEvents()
            p, s, objs = self._project()
            win.ctrl.add_vectors(objs, "selftest")
            f = self.tmp / "tab.icam"
            p.save(f)
            win.open_project(str(f))
            if win.tabs.count() != 2:
                raise RuntimeError("opening a project did not give a second tab")
            for name in ("dark", "light"):
                win.set_theme(name)
                app.processEvents()
                img = win.grab().toImage()
                if img.isNull() or img.width() < 100:
                    raise RuntimeError("the window cannot be drawn")
            for c in win.documents():
                c.dirty = False
            win.close()
            QApplication.processEvents()
        finally:
            for c in list(app.topLevelWidgets()):
                c.deleteLater()
        return "2 tabs, light and dark theme"


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform in ("win32", "darwin"))


def run(gui: bool = True) -> int:
    """Run the checks; the exit code is 0 when everything works."""
    print(f"iyiCAM self test (Python {sys.version.split()[0]}, {sys.platform}"
          f"{', frozen' if getattr(sys, 'frozen', False) else ''})")
    if gui and not _has_display():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="iyicam-selftest-") as tmp:
        old = os.environ.get("IYICAM_HOME")
        os.environ["IYICAM_HOME"] = str(Path(tmp) / "home")       # (the settings of the user stay as they are)
        try:
            return SelfTest(Path(tmp), gui).run()
        finally:
            if old is None:
                os.environ.pop("IYICAM_HOME", None)
            else:
                os.environ["IYICAM_HOME"] = old

