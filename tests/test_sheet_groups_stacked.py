"""The sheets panel: the sheets of a group stacked under it (folded and unfolded), dragged into a group, out of it and to
another place; a group heading stands for all of its sheets."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402


@pytest.fixture
def win():
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.show()
    QApplication.processEvents()
    w.ctrl.settings.auto_recalculate = False
    p = w.ctrl.project
    for _ in range(4):
        p.add_sheet(None, 1000, 600)
    for k, s in enumerate(p.sheets):
        s.vectors += [VectorObject([shapes.rectangle(20 * i, 0, 10, 10)], p.layers[0].id) for i in range(k + 1)]
    w.ctrl.set_sheet_group([p.sheets[1].id, p.sheets[2].id], "Pleksi")
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def tops(panel):
    t = panel.tree
    return [t.topLevelItem(i) for i in range(t.topLevelItemCount())]


def test_the_sheets_of_a_group_are_stacked_under_it(win):
    panel, p = win.sheets_panel, win.ctrl.project
    heads = [it for it in tops(panel) if it.data(0, panel.GROUP_ROLE)]
    assert len(heads) == 1 and heads[0].text(0).startswith("▣ Pleksi") and "(2 sheets, 5 vectors)" in heads[0].text(0)
    assert [heads[0].child(k).data(0, panel.ID_ROLE) for k in range(2)] == [p.sheets[1].id, p.sheets[2].id]
    assert [it.data(0, panel.ID_ROLE) for it in panel.sheet_items()] == [s.id for s in p.sheets]
    assert len(tops(panel)) == 4                                     # (sheet 1, the group, sheets 4 and 5)


def test_a_folded_group_stays_folded_and_opens_for_the_sheet_that_is_shown(win):
    panel, p = win.sheets_panel, win.ctrl.project
    head = next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE))
    head.setExpanded(False)
    panel.refresh()
    head = next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE))
    assert not head.isExpanded()
    win.ctrl.set_sheet(p.sheets[2].id)
    head = next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE))
    assert head.isExpanded()


def test_a_selected_heading_is_all_of_its_sheets(win):
    panel, p = win.sheets_panel, win.ctrl.project
    panel.tree.clearSelection()
    next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE)).setSelected(True)
    assert panel.selected_ids() == [p.sheets[1].id, p.sheets[2].id]


def test_dragging_a_sheet_into_a_group_out_of_it_and_back_is_one_step_each(win):
    panel, c = win.sheets_panel, win.ctrl
    p = c.project
    ids = [s.id for s in p.sheets]
    tree = panel.tree
    # sheet 5 dropped onto the group (what the drop leaves in the tree)
    head = next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE))
    last = tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
    head.addChild(last)
    panel._dropped()
    assert [s.id for s in c.sheets_in("Pleksi")] == [ids[1], ids[2], ids[4]]
    assert [s.id for s in p.sheets] == [ids[0], ids[1], ids[2], ids[4], ids[3]]     # (it stands in the group now)
    # sheet 2 dragged out to the top
    head = next(it for it in tops(panel) if it.data(0, panel.GROUP_ROLE))
    out = head.takeChild(0)
    tree.insertTopLevelItem(0, out)
    panel._dropped()
    assert p.sheets[0].id == ids[1] and p.sheets[0].group == ""
    c.undo()
    assert [s.id for s in c.sheets_in("Pleksi")] == [ids[1], ids[2], ids[4]]
    c.undo()
    assert [s.id for s in p.sheets] == ids and [s.id for s in c.sheets_in("Pleksi")] == [ids[1], ids[2]]


def test_a_drop_that_changes_nothing_is_no_step(win):
    panel, c = win.sheets_panel, win.ctrl
    before = len(c.undo_stack._undo)
    panel._dropped()
    assert len(c.undo_stack._undo) == before
