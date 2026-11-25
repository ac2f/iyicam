"""The job report: the toolpaths of every sheet with their tools, depths, cut lengths and times, as one HTML page.

The desktop program writes it from its window (``gui.main_window.job_report_html``); this is the same page made from a
project and what was calculated for it, so that any interface (the web app too) can offer it."""
from __future__ import annotations

import html

from ..core.document import Project
from .job import sheet_toolpaths
from .moves import PathStats, format_time


def job_report_html(project: Project, results: dict, inch: bool = False) -> str:
    """The report of ``project`` (``results``: what :func:`iyicam.service.ops.calculate` gave); lengths in mm or inches."""
    k = 25.4 if inch else 1.0
    unit = " in" if inch else " mm"
    dec = 4 if inch else 2

    def fmt(v: float) -> str:
        return f"{round(v / k, dec):g}{unit}"

    e = html.escape
    m = project.material
    out = [f"<html><head><meta charset='utf-8'><title>{e(project.name)}</title>"
           "<style>body{font-family:sans-serif;margin:24px}table{border-collapse:collapse}"
           "td,th{border:1px solid #bbb;padding:4px 8px;text-align:left}th{background:#eee}</style></head><body>",
           f"<h1>{e(project.name)}</h1>",
           f"<p>Material thickness {fmt(m.thickness)}, Z zero at the {m.z_zero}, XY origin "
           f"{m.xy_origin.replace('_', ' ')}, safe Z {fmt(m.safe_z)}.</p>"]
    for s in project.sheets:
        tps = sheet_toolpaths(project, results, s)
        out.append(f"<h2>{e(s.name)} — {fmt(s.width)} x {fmt(s.height)} x {fmt(project.sheet_thickness(s))}</h2>")
        if not tps:
            out.append("<p>No toolpaths.</p>")
            continue
        out.append("<table><tr><th>#</th><th>Toolpath</th><th>Tool</th><th>Spindle</th><th>Feed / plunge</th>"
                   "<th>Depth</th><th>Cut length</th><th>Time</th></tr>")
        total = PathStats()
        for i, tp in enumerate(tps, 1):
            st = tp.stats(m.machine())
            total = total.merge(st)
            t = tp.tool
            out.append(f"<tr><td>{i}</td><td>{e(tp.name)}</td><td>T{t.number} {e(t.describe())}</td>"
                       f"<td>{t.spindle_speed}</td><td>{t.feed_rate:g} / {t.plunge_rate:g}</td>"
                       f"<td>{fmt(-st.zmin)}</td><td>{fmt(st.feed_length)}</td><td>{format_time(st.time_s)}</td></tr>")
        out.append(f"<tr><th colspan=6>Total</th><th>{fmt(total.feed_length)}</th><th>{format_time(total.time_s)}</th></tr></table>")
    out.append("</body></html>")
    return "\n".join(out)
