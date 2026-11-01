"""The heights and the material of a project in words, for the places that need to show them."""
from __future__ import annotations


def heights_of(ctrl) -> list[tuple[str, float | None]]:
    """The toolpaths with heights of their own: ``[(name, safe Z)]`` (a toolpath that only has its own rapid-down
    clearance has ``None``)."""
    return [(t.name, t.safe_z) for t in ctrl.project.toolpaths if t.safe_z is not None or t.clearance is not None]


def material_summary(ctrl) -> str:
    """``18 mm thick · Z zero at the top · safe Z 10 mm · rapid-down 2 mm`` (and how many toolpaths differ)."""
    p, u = ctrl.project, ctrl.units
    m = p.material
    text = (f"{u.fmt(p.sheet_thickness(ctrl.sheet))} thick · Z zero at the "
            f"{'top' if m.z_zero == 'top' else 'bottom'} · safe Z {u.fmt(m.safe_z)} · "
            f"rapid-down {u.fmt(m.clearance)}")
    own = heights_of(ctrl)
    if own:
        text += f" · {len(own)} toolpath(s) with their own heights"
    return text
