"""The page (``iyicam/web``): what can be checked without a browser.

The scripts that make the page (geom.js: the geometry of vectors, app.js: the shell, edit.js: the drawing tools) are
served, loaded in the right order, and every text the page asks for exists in both languages (a missing one would show
as its key on a phone).  What the tools do with a drawing is tried in a browser (docs/web.md); the engine's commands they
call are tested in test_service.py.
"""
import http.client
import re
import threading
from pathlib import Path

import pytest

from iyicam.service import server as srv

WEB = Path(srv.WEB_DIR)


@pytest.fixture
def web():
    s = srv.make_server("127.0.0.1", 0, key="ABCDEF-GHJKMN-PQRSTU-VWXYZ2", files=False)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, r.getheader("Content-Type", ""), body


@pytest.mark.parametrize("name", ["geom.js", "app.js", "edit.js", "app.css"])
def test_every_file_of_the_page_is_served(web, name):
    status, ctype, body = get(web, f"/static/{name}")
    assert status == 200 and body
    assert ("javascript" in ctype) == name.endswith(".js")


def test_the_scripts_load_in_the_order_they_need():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    order = [m.group(1) for m in re.finditer(r'<script src="/static/([a-z.]+)"', html)]
    assert order == ["geom.js", "app.js", "edit.js"]          # (app.js needs geom.js; edit.js needs both)


def _keys(block: str) -> set[str]:
    return set(re.findall(r'"([a-z0-9_]+\.[A-Za-z0-9_.]+)"\s*:', block))


def _texts():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    edit = (WEB / "edit.js").read_text(encoding="utf-8")
    tr_app, en_app = re.split(r"\n  en: \{", app.split("\n  tr: {", 1)[1], maxsplit=1)
    en_app = en_app.split("\n};", 1)[0]
    tr_edit = edit.split("Object.assign(TXT.tr, {", 1)[1].split("\n  });", 1)[0]
    en_edit = edit.split("Object.assign(TXT.en, {", 1)[1].split("\n  });", 1)[0]
    return app + edit, _keys(tr_app) | _keys(tr_edit), _keys(en_app) | _keys(en_edit)


def test_every_text_the_page_asks_for_exists_in_both_languages():
    source, tr, en = _texts()
    used = {k for k in re.findall(r'\bt\("([a-z0-9_]+\.[A-Za-z0-9_.]+)"', source) if not k.endswith(".")}
    used |= {"ed." + tool for tool in ("view", "select", "move", "line", "poly", "rect", "circle", "ellipse", "polygon",
                                       "nodes", "start")}          # (the tool bar asks for them as "ed." + name)
    used |= {"files.sort." + k for k in ("name", "date", "size")}  # (and the file window as "files.sort." + key)
    assert used, "the pattern found nothing: the test is wrong"
    assert not sorted(used - tr), f"missing in Turkish: {sorted(used - tr)}"
    assert not sorted(used - en), f"missing in English: {sorted(used - en)}"
    assert tr - en == set() or all(k.startswith("e.") for k in tr - en), sorted(tr - en)       # (the two have the same texts)


def test_edit_js_is_an_extension_not_a_requirement():
    """app.js works without edit.js (it asks ``window.IyiPage.editor`` and has the old drawing when it is missing)."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "window.IyiPage = {" in app and "editor: null" in app
    assert "window.IyiPage.editor" in app


def test_the_texts_that_are_made_from_a_name_exist_in_both_languages():
    """``t("ed.s." + key)``, ``t("ed." + key)`` and ``t("ref." + id)`` are not found by the pattern above: ask for them by name."""
    _, tr, en = _texts()
    names = {f"ed.s.{k}" for k in ("shift", "ctrl", "alt", "goto", "coord", "ref", "layers", "fold", "multi", "snap", "grid")}
    names |= {f"ed.{k}" for k in ("shift", "ctrl", "alt")} | {f"ref.{r}" for r in ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")}
    names |= {"ed.mod.once", "ed.mod.lock", "ed.mod.off", "help.mods", "help.coord", "lay.select", "lay.up", "lay.down", "lay.hidden"}
    assert not sorted(names - tr), f"missing in Turkish: {sorted(names - tr)}"
    assert not sorted(names - en), f"missing in English: {sorted(names - en)}"


def test_the_page_fits_what_can_be_seen():
    """A page that is ``100vh`` high reaches below the screen of a phone while the bar of the browser is there, and a bar of tabs
    that is ``position: fixed`` slides about when the keyboard comes: the page is fixed to the visual viewport instead, the tabs
    are the last thing of its column, and the view in the middle scrolls by itself."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "100vh" not in css
    assert re.search(r"#app \{[^}]*position: fixed", css)
    nav = re.search(r"nav\.tabs \{([^}]*)\}", css).group(1)
    assert "position: fixed" not in nav
    assert "visualViewport" in js and "--vv-h" in css
    assert re.search(r"main \{[^}]*overflow-y: auto", css)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "maximum-scale=1" in html                      # (a field the page zooms into would push the tabs off the screen)
    edit = (WEB / "edit.js").read_text(encoding="utf-8")
    assert "select.ed { font-size: 16px" in css           # (a field under 16 px makes a phone zoom in)
    assert 'class: "eddock"' in edit                      # (what is done to a selection is under the drawing: the drawing does not jump when it appears)


def test_shift_ctrl_and_alt_are_buttons_of_the_bars_and_count_like_the_keys():
    edit = (WEB / "edit.js").read_text(encoding="utf-8")
    assert "function mods(e)" in edit and "function releaseMods()" in edit
    assert 'key("shift"), key("ctrl"), key("alt")' in edit
    assert "e.shiftKey ||" not in edit.replace("!!(e && e.shiftKey)", "")        # (a gesture asks ``mods(e)``, never the event alone)
