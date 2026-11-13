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


@pytest.mark.parametrize("name", ["geom.js", "app.js", "edit.js", "post.js", "preview.js", "jobs.js", "app.css"])
def test_every_file_of_the_page_is_served(web, name):
    status, ctype, body = get(web, f"/static/{name}")
    assert status == 200 and body
    assert ("javascript" in ctype) == name.endswith(".js")


def test_the_scripts_load_in_the_order_they_need():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    order = [m.group(1) for m in re.finditer(r'<script src="/static/([a-z.]+)"', html)]
    assert order == ["geom.js", "app.js", "edit.js", "post.js", "preview.js", "jobs.js"]   # (app.js needs geom.js; edit.js and post.js need both)


def _keys(block: str) -> set[str]:
    return set(re.findall(r'"([a-z0-9_]+\.[A-Za-z0-9_.]+)"\s*:', block))


def _texts():
    """The page's own texts (app.js) and those the other scripts add (``Object.assign(TXT.tr, {...})``)."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    extra = [(WEB / n).read_text(encoding="utf-8") for n in ("edit.js", "post.js", "preview.js", "jobs.js")]
    tr_app, en_app = re.split(r"\n  en: \{", app.split("\n  tr: {", 1)[1], maxsplit=1)
    en_app = en_app.split("\n};", 1)[0]
    tr, en = _keys(tr_app), _keys(en_app)
    for text in extra:
        tr_block = re.findall(r"Object\.assign\(TXT\.tr, \{(.*?)\n  \}\);", text, re.S)
        en_block = re.findall(r"Object\.assign\(TXT\.en, \{(.*?)\n  \}\);", text, re.S)
        assert tr_block and en_block, "a script of the page has no texts of its own in both languages"
        tr |= _keys("\n".join(tr_block))
        en |= _keys("\n".join(en_block))
    return app + "".join(extra), tr, en


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


def test_post_js_is_an_extension_not_a_requirement():
    """The same for the post editor and the G-code words: without post.js the page shows the G-code as plain text."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "post: null" in app and app.count("window.IyiPage.post") >= 2
    assert "pre.textContent" in app                                   # (the plain view is still there)
    post = (WEB / "post.js").read_text(encoding="utf-8")
    assert "if (!B) return;" in post and "B.post = {" in post
    # every operation the script calls is one the page may call
    from iyicam.service.api import API_OPS
    assert set(re.findall(r'api\("([a-z_]+)"', post)) <= API_OPS


def test_the_overlay_that_hides_a_button_really_hides_it():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css)      # (button { display: inline-flex } would win)


def test_jobs_js_is_an_extension_not_a_requirement():
    """The page works without jobs.js (it asks ``window.IyiPage.jobs``); with it a nesting is a kept job."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "jobs: null" in app and app.count("window.IyiPage.jobs") >= 3
    jobs = (WEB / "jobs.js").read_text(encoding="utf-8")
    assert "if (!B) return;" in jobs and "B.jobs = {" in jobs
    from iyicam.service.api import API_OPS
    assert set(re.findall(r'api\("([A-Za-z_.]+)"', jobs)) <= API_OPS
    assert {"jobs", "job.result", "job.cancel", "job.dismiss"} <= set(re.findall(r'api\("([A-Za-z_.]+)"', jobs))


def test_a_job_outlives_the_connection_of_the_page():
    """What makes a nesting go on and be found again: the page keeps asking while the connection is lost and asks the person
    before it takes a result that came while it was away; the job is kept at the engine, nesting is a kept job."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    run = app[app.index("async function runJob"):app.index("// ---", app.index("async function runJob"))]
    assert "lostConnection(e)" in run and "dropped" in run and "JobDeferred" in run and "opts.deferrable" in run
    assert 'addEventListener("online"' in app and "visibilitychange" in app          # (a phone that wakes polls at once)
    assert "keep," in run or "keep }" in run or "keep:" in run
    edit = (WEB / "edit.js").read_text(encoding="utf-8")
    assert "keep: { title:" in edit and "deferrable: true" in edit and "B.jobApply.nest" in edit
    assert "JobDeferred" in edit and "B.jobs.offer" in edit


def test_the_texts_of_the_jobs_exist_in_both_languages():
    """``t("job." + state)`` is not found by the pattern of the test above: ask for the states by name."""
    _, tr, en = _texts()
    names = {f"job.{k}" for k in ("queued", "running", "done", "error", "cancelled", "title", "btn", "none", "hint", "mine", "get",
                                  "cancel", "drop", "send", "later", "offline", "finishedTitle", "finished", "askNote", "failed",
                                  "applied", "interrupted", "noapply")} | {"e.job_not_found", "e.interrupted"}
    assert not sorted(names - tr), f"missing in Turkish: {sorted(names - tr)}"
    assert not sorted(names - en), f"missing in English: {sorted(names - en)}"


def test_preview_js_is_an_extension_that_changes_the_order_by_the_engines_own_calculation():
    """The preview tab is added to the page by preview.js (the page works without it); a change of the cut order is calculated by
    the engine on a copy of the project, shown, and only then written into the project."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "EXTRA_TABS" in app and "SCENE_HOOKS" in app and "S.pv.custom" in app and "async function recompute" in app
    pv = (WEB / "preview.js").read_text(encoding="utf-8")
    assert "if (!B || !G) return;" in pv and "B.EXTRA_TABS.push" in pv and "B.SCENE_HOOKS.push" in pv
    assert 'runJob("compute"' in pv and "toolpaths: [tp]" in pv                       # (only the toolpath being changed is calculated)
    assert "auto_order: false" in pv and "applyDraft" in pv and "B.recompute()" in pv
    # the copy of the project: the project itself is changed only by Apply
    run = pv[pv.index("async function runPreview"):pv.index("async function applyDraft")]
    assert "S.project.toolpaths.map" in run and "tp.sequence =" not in run
    assert "tp.sequence =" in pv[pv.index("async function applyDraft"):]


def test_the_texts_of_the_preview_exist_in_both_languages():
    _, tr, en = _texts()
    names = {"tab.prev"} | {f"pv.{k}" for k in ("need", "calc", "notyet", "paths", "show", "rapids", "arrows", "numbers", "starts", "depth", "play",
                                                "step", "all", "order", "orderNone", "pick", "picking", "done", "up", "down", "first", "last",
                                                "reverse", "strategy", "reorder", "direction", "startp", "draft", "apply", "drop", "computing",
                                                "compare", "moved", "rule", "applied", "cutof", "same", "choose", "stats", "total")}
    assert not sorted(names - tr), f"missing in Turkish: {sorted(names - tr)}"
    assert not sorted(names - en), f"missing in English: {sorted(names - en)}"
