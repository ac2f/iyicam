"""Making posts without TOML (iyicam.post.editing), the words of G-code (iyicam.post.guide) and the service
operations that give both to a page."""
import math
import re
import tomllib

import pytest

from iyicam.core import paths
from iyicam.core.registry import REGISTRY
from iyicam.post import editing as E
from iyicam.post import guide
from iyicam.post.engine import DEFAULT_BLOCKS, DEFAULT_OPTIONS
from iyicam.service import ServiceHost


@pytest.fixture
def host():
    from iyicam.builtins import load_user_posts
    from iyicam.service import ops
    ops.ensure_ready()
    REGISTRY.remove_source("user-posts")
    load_user_posts(REGISTRY)          # (the home of this test: nothing yet)
    yield ServiceHost()
    REGISTRY.remove_source("user-posts")


def draft_from(host, base="mach3", **changes):
    post = next(p for p in host.handle("posts")["posts"] if p["id"] == base)
    d = {**post, "id": "", "name": "My post", "extends": base}
    d["options"] = {**post["options"], **changes.pop("options", {})}
    d["blocks"] = {**post["blocks"], **changes.pop("blocks", {})}
    d.update(changes)
    return d


# ---------------------------------------------------------------------------------------------
# what every option is
# ---------------------------------------------------------------------------------------------

def test_every_option_and_block_of_the_engine_is_described():
    assert {o["key"] for o in E.OPTIONS} == set(DEFAULT_OPTIONS)         # (a new option must get its words)
    assert set(DEFAULT_BLOCKS) <= set(E.BLOCK_NAMES)
    for o in E.OPTIONS:
        assert o["label"]["en"] and o["label"]["tr"] and o["group"] in {g for g, _ in E.GROUPS}
        assert o["kind"] in ("bool", "int", "float", "str", "choice")
        if o["kind"] == "choice":
            assert o["default"] in [c[0] for c in o["choices"]]
    for b in E.BLOCKS:
        assert b["label"]["en"] and b["label"]["tr"] and b["when"]["tr"] and b["group"] in {g for g, _ in E.BLOCK_GROUPS}


def test_the_variables_named_are_the_ones_a_post_writes_with(host):
    """Every variable the form offers must exist for the engine (a chip that raises 'unknown variable' is a bug)."""
    names = {v["name"] for v in E.VARIABLES}
    d = draft_from(host, blocks={"header": " ".join("{" + n + "}" for n in names if not n.startswith(("tool_", "toolpath_",
                   "spindle", "coolant", "prev_", "feed_rate", "plunge")))})
    out = host.handle("post_preview", {"post": d})
    assert "error" not in out, out
    d = draft_from(host, blocks={"tool_change": " ".join("{" + n + "}" for n in names)})
    out = host.handle("post_preview", {"post": d})
    assert "error" not in out, out


# ---------------------------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------------------------

AWKWARD = ['plain', '', 'a "quote"', 'back\\slash', 'two\nlines', '\ttabbed', 'ends with quote"', '"""', 'ü ş ğ ı',
           '(comment) {file_name}\nG0 Z{safe_z}\n', '\nstarts with a newline', 'x\r\ny', "it's", 'a\\\nb']


@pytest.mark.parametrize("text", AWKWARD)
def test_the_toml_written_is_read_back_exactly(text):
    d = {"id": "x", "name": "n", "options": {"decimals": 4, "uppercase": False, "arc_max_sweep": 90.5, "rotary_letter": "B"},
         "blocks": {"header": text, "arc": text + "!"}}
    back = tomllib.loads(E.dump_toml(d))
    assert back["blocks"] == d["blocks"] and back["options"] == d["options"] and back["id"] == "x"


def test_a_name_with_awkward_letters_is_one_line_of_valid_toml():
    for name in ('My "post"', "back\\slash", "ü ş ğ", "tab\there"):
        back = tomllib.loads(E.dump_toml({"id": "x", "name": name, "description": name}))
        assert back["name"] == name


def test_ids_are_made_of_the_name():
    assert E.slug("Atölye Mach3 (şık)") == "atolye_mach3_sik"
    assert E.slug("ÇİĞDEM") == "cigdem"
    assert E.slug("***") == "custom"
    assert E.unique_id("Mach3", {"mach3"}) == "mach3_2"
    assert E.unique_id("Mach3", {"mach3", "mach3_2"}) == "mach3_3"


# ---------------------------------------------------------------------------------------------
# a draft
# ---------------------------------------------------------------------------------------------

def good(**kw):
    d = {"name": "Mine", "extension": "nc", "options": {}, "blocks": {}}
    d.update(kw)
    return d


@pytest.mark.parametrize("draft, word", [
    (good(name=""), "name"), (good(name="a\nb"), "name"), (good(extension="no way"), "extension"),
    (good(options={"decimals": "many"}), "decimals"), (good(options={"decimals": 40}), "decimals"),
    (good(options={"units": "furlong"}), "units"), (good(options={"arc_max_sweep": 0.5}), "arc_max_sweep"),
    (good(options={"rotary_letter": "AB"}), "letter"), (good(blocks={"header": "{unclosed"}), "brace"),
    (good(blocks={"header": "}"}), "brace"), (good(blocks={"header": "{0}"}), "name of a variable"),
    (good(blocks={"header": "{file_name.__class__}"}), "name of a variable"),
    (good(blocks={"header": "{a[0]}"}), "name of a variable"), (good(blocks={"header": 5}), "text"),
    (good(id="Not A Slug"), "id"), (good(id="../x"), "id"), (good(id="a" * 61), "id"), ("a string", "object"),
    (good(options=[1]), "options"), (good(options={"bad key\nnewline": 1}), "name of an option"),
    (good(options={"plugin_option": [1]}), "plugin_option"), (good(blocks={"a b": "x"}), "name of a block"),
    (good(blocks={f"b{i}": "x" for i in range(101)}), "too many"),
    (good(blocks={"header": "x" * 9000}), "long"),
])
def test_a_draft_that_cannot_be_used_says_why(draft, word):
    with pytest.raises(E.PostEditError, match=word):
        E.clean_draft(draft)


def test_an_unknown_variable_is_a_warning_not_a_refusal():
    clean, warn = E.clean_draft(good(blocks={"header": "{file_name} {nothing_like_it}"}))
    assert any("nothing_like_it" in w for w in warn)
    assert E.clean_draft(good(blocks={"header": "{{literal}} {file_name:>10}"}))[1] == []     # braces doubled, a format


def test_a_header_that_contradicts_the_way_arcs_are_written_is_warned_about():
    mismatch = E.clean_draft(good(options={"ijk": "absolute"}, blocks={"header": "G90 G91.1"}))[1]
    assert len(mismatch) == 1 and "G90.1" in mismatch[0]
    assert len(E.clean_draft(good(options={"ijk": "incremental"}, blocks={"header": "G90.1"}))[1]) == 1
    assert E.clean_draft(good(options={"ijk": "absolute"}, blocks={"header": "G90 G90.1"}))[1] == []
    assert E.clean_draft(good(options={"ijk": "absolute", "arcs": "r"}, blocks={"header": "G91.1"}))[1] == []
    assert E.clean_draft(good(options={"ijk": "absolute"}))[1] == []        # (no header said: nothing to compare)


def test_a_draft_builds_a_post_that_writes():
    post = E.build_post(good(options={"decimals": 2, "line_numbers": True}, blocks={"header": "( {file_name} )"}))
    text = E.sample_files(post)[0][1]
    assert text.startswith("( sample )") and "N10" in text and re.search(r"X\d+\.\d{1,2}\b", text)


# ---------------------------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------------------------

def test_a_saved_post_keeps_only_what_differs_from_its_parent(host):
    d = draft_from(host, options={"decimals": 2, "line_numbers": True}, blocks={"footer": "M30"}, name="Atölye")
    out = host.handle("post_save", {"post": d})
    assert "error" not in out, out
    assert out["saved"] == "atolye" and out["file"] == "atolye.toml"
    written = tomllib.loads((paths.posts_dir() / "atolye.toml").read_text(encoding="utf-8"))
    assert written["extends"] == "mach3" and written["name"] == "Atölye"
    assert written["options"] == {"decimals": 2, "line_numbers": True}       # not the twenty others
    assert written["blocks"] == {"footer": "M30"}
    mine = next(p for p in out["posts"] if p["id"] == "atolye")
    assert mine["editable"] and not mine["builtin"] and mine["extends"] == "mach3"
    assert mine["options"]["line_ending"] == "crlf" and mine["options"]["decimals"] == 2      # (inherited and own)
    assert mine["blocks"]["header"].startswith("\n%") or "%" in mine["blocks"]["header"]       # (Mach3's header)
    assert REGISTRY.posts["atolye"].options["line_numbers"] is True


def test_a_saved_post_follows_a_later_change_of_its_parent(host):
    """Only differences are written, so what the post does not change stays the parent's."""
    out = host.handle("post_save", {"post": draft_from(host, options={"decimals": 2})})
    assert "error" not in out
    assert "line_ending" not in tomllib.loads(next(paths.posts_dir().glob("*.toml")).read_text(encoding="utf-8")).get("options", {})


def test_saving_again_changes_the_same_file(host):
    d = draft_from(host, name="Shop")
    first = host.handle("post_save", {"post": d})
    d2 = {**next(p for p in first["posts"] if p["id"] == "shop")}
    d2["options"] = {**d2["options"], "comments": False}
    d2["name"] = "Shop (no comments)"
    second = host.handle("post_save", {"post": d2})
    assert "error" not in second and second["saved"] == "shop"
    assert [f.name for f in paths.posts_dir().glob("*.toml")] == ["shop.toml"]            # not a second file
    assert REGISTRY.posts["shop"].name == "Shop (no comments)" and REGISTRY.posts["shop"].options["comments"] is False


def test_two_posts_of_one_name_do_not_overwrite_each_other(host):
    a = host.handle("post_save", {"post": draft_from(host, name="Same")})
    b = host.handle("post_save", {"post": draft_from(host, name="Same", options={"decimals": 1})})
    assert (a["saved"], b["saved"]) == ("same", "same_2")
    assert REGISTRY.posts["same"].options["decimals"] != 1 and REGISTRY.posts["same_2"].options["decimals"] == 1


def test_a_post_that_comes_with_the_program_is_never_overwritten(host):
    out = host.handle("post_save", {"post": draft_from(host, id="mach3", name="Hacked")})
    assert out["error"] == "invalid_post" and "comes with the program" in out["message"]
    assert REGISTRY.posts["mach3"].name == "Mach3 mm" and not list(paths.posts_dir().glob("*"))


def test_a_post_cannot_be_based_on_itself_or_on_nothing_that_exists(host):
    host.handle("post_save", {"post": draft_from(host, name="Base")})
    out = host.handle("post_save", {"post": draft_from(host, id="base", name="Base", extends="base")})
    assert out["error"] == "invalid_post" and "itself" in out["message"]
    out = host.handle("post_save", {"post": draft_from(host, name="Orphan", extends="no_such_post")})
    assert "error" not in out
    assert "extends" not in tomllib.loads((paths.posts_dir() / "orphan.toml").read_text(encoding="utf-8"))


def test_a_post_can_be_based_on_a_post_of_the_user(host):
    host.handle("post_save", {"post": draft_from(host, name="Parent", options={"decimals": 2})})
    child = draft_from(host, base="parent", name="Child", options={"line_numbers": True})
    out = host.handle("post_save", {"post": child})
    assert "error" not in out
    t = tomllib.loads((paths.posts_dir() / "child.toml").read_text(encoding="utf-8"))
    assert t["extends"] == "parent" and t["options"] == {"line_numbers": True}
    assert REGISTRY.posts["child"].options["decimals"] == 2 and REGISTRY.posts["child"].options["line_numbers"]


def test_what_the_editor_does_not_know_stays_in_the_file(host):
    paths.posts_dir().mkdir(parents=True, exist_ok=True)
    (paths.posts_dir() / "plug.toml").write_text('id = "plug"\nname = "Plug"\nextends = "mach3"\n[options]\n'
                                                 'plugin_option = 7\ndecimals = 2\n', encoding="utf-8")
    host.handle("posts")
    from iyicam.builtins import load_user_posts
    REGISTRY.remove_source("user-posts")
    load_user_posts(REGISTRY)
    d = next(p for p in host.handle("posts")["posts"] if p["id"] == "plug")
    d["options"] = {**d["options"], "decimals": 3}
    host.handle("post_save", {"post": d})
    t = tomllib.loads((paths.posts_dir() / "plug.toml").read_text(encoding="utf-8"))
    assert t["options"]["plugin_option"] == 7 and t["options"]["decimals"] == 3


def test_a_post_can_be_deleted_only_by_its_maker(host):
    host.handle("post_save", {"post": draft_from(host, name="Gone")})
    assert (paths.posts_dir() / "gone.toml").exists()
    out = host.handle("post_delete", {"id": "gone"})
    assert out["deleted"] == "gone" and not (paths.posts_dir() / "gone.toml").exists()
    assert "gone" not in REGISTRY.posts and all(p["id"] != "gone" for p in out["posts"])
    assert host.handle("post_delete", {"id": "mach3"})["error"] == "invalid_post"
    assert host.handle("post_delete", {"id": "never_was"})["error"] == "unknown_post"
    assert "mach3" in REGISTRY.posts


# ---------------------------------------------------------------------------------------------
# trying it
# ---------------------------------------------------------------------------------------------

def test_the_preview_shows_what_a_change_does(host):
    plain = host.handle("post_preview", {"post": draft_from(host)})["files"][0]["text"]
    numbered = host.handle("post_preview", {"post": draft_from(host, options={"line_numbers": True})})["files"][0]["text"]
    assert "N10 " not in plain and "N10 " in numbered
    cycles = host.handle("post_preview", {"post": draft_from(host, options={"drill_cycles": True})})["files"][0]["text"]
    assert "G83" in cycles and "G83" not in plain


def test_the_preview_says_what_is_wrong_instead_of_failing(host):
    out = host.handle("post_preview", {"post": draft_from(host, blocks={"linear": "{G} {NOPE}"})})
    assert out["error"] == "post_failed" and "NOPE" in out["message"]
    out = host.handle("post_preview", {"post": draft_from(host, options={"decimals": "x"})})
    assert out["error"] == "invalid_post" and "decimals" in out["message"]
    out = host.handle("post_preview", {"post": draft_from(host, blocks={"linear": "{G} {X"})})
    assert out["error"] == "invalid_post"


def test_every_post_that_comes_with_the_program_writes_the_sample(host):
    for p in host.handle("posts")["posts"]:
        out = host.handle("post_preview", {"post": p})
        assert "error" not in out, (p["id"], out)
        assert all(f["text"].strip() for f in out["files"])


def test_the_gcode_operation_can_use_a_draft_that_is_not_saved(host):
    from conftest import add_tp, end_mill, make_project
    from iyicam.core import shapes
    p, s, objs = make_project([shapes.rectangle(10, 10, 50, 30, corner_radius=5)])
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    d = draft_from(host, options={"line_numbers": True, "decimals": 1})
    out = host.handle("gcode", {"project": p.to_dict(), "post_draft": d})
    assert out["ok"], out
    assert "N10 " in out["files"][0]["text"]
    assert host.handle("gcode", {"project": p.to_dict(), "post_draft": {**d, "name": ""}})["error"] == "invalid_post"
    assert not list(paths.posts_dir().glob("*"))                                # (nothing was written)


# ---------------------------------------------------------------------------------------------
# the words of G-code
# ---------------------------------------------------------------------------------------------

def test_the_glossary_is_in_both_languages_and_has_the_arc_words():
    en, tr = guide.glossary("en"), guide.glossary("tr")
    assert [g["word"] for g in en] == [g["word"] for g in tr]
    assert all(g["text"] for g in en + tr) and all(g["text"] != e["text"] for g, e in zip(tr, en))
    words = {g["word"] for g in en}
    assert {"I", "J", "K", "R", "F", "G2", "G3", "G90", "G91.1", "M3", "M30"} <= words


def lines_of(text):
    return text.replace("\r\n", "\n").split("\n")


def test_an_arc_is_explained_with_its_centre_as_a_place():
    prog = ["G21 G90 G91.1", "G0 X0 Y0", "G1 X10 F500", "G2 X20 Y0 I5 J0", "G3 X0 Y0 I-10 J0"]
    a = guide.explain(prog, 3, "en")
    assert a["arc"] == {"cw": True, "start": [10.0, 0.0], "end": [20.0, 0.0], "centre": [15.0, 0.0], "radius": 5.0}
    assert "clockwise" in a["summary"] and "X15 Y0" in a["summary"] and "radius 5" in a["summary"]
    assert [w["word"] for w in a["words"]] == ["G2", "X20", "Y0", "I5", "J0"]
    b = guide.explain(prog, 4, "en")
    assert b["arc"]["centre"] == [10.0, 0.0] and b["arc"]["radius"] == 10.0 and not b["arc"]["cw"]
    assert "counter" in b["summary"]
    tr = guide.explain(prog, 3, "tr")
    assert "saat yönünde" in tr["summary"] and "yarıçap 5" in tr["summary"] and "merkez" in tr["summary"]
    assert any("MERKEZ" in w["text"] for w in tr["words"] if w["word"] == "I5")


def test_absolute_centres_and_radius_words():
    prog = ["G90.1", "G0 X0 Y0", "G2 X20 Y0 I10 J0"]
    a = guide.explain(prog, 2)
    assert a["arc"]["centre"] == [10.0, 0.0] and a["arc"]["radius"] == 10.0
    assert guide.explain(["G0 X0 Y0", "G2 X20 Y0 I10 J0"], 1, arc_absolute=True)["arc"]["centre"] == [10.0, 0.0]
    r = guide.explain(["G0 X0 Y0", "G3 X10 Y0 R-8"], 1)
    assert r["arc"]["radius"] == 8 and "more than half" in r["summary"]


def test_modes_and_units_are_followed_through_the_lines():
    prog = ["G20", "G90", "G0 X0 Y0", "G91", "G0 X1 Y1", "G1 X1", "G90", "G1 X5"]
    out = guide.explain(prog, 5)
    assert out["at"] == [2.0, 1.0, None] and " in" in out["summary"]           # (incremental: from where it was)
    assert guide.explain(prog, 7)["at"][0] == 5.0
    assert guide.explain(["G91", "G0 X1"], 1)["at"] == [None, None, None]        # (a start that is not known stays so)
    assert guide.explain(["G21", "G0 X1"], 1)["summary"].count("mm") == 1
    assert guide.explain(["G1 X1"], 0, inch=True)["summary"].count(" in") >= 1


@pytest.mark.parametrize("line, word", [
    ("%", "marker"), ("", "empty"), ("(a comment)", "comment"), ("; also", "comment"), ("N10", "Line number"),
    ("M5", "Spindle stop"), ("M30", "End of the program"), ("T1 M6", "Tool"), ("S18000 M3", "18000"),
    ("G4 P2", "Wait 2"), ("G98 G81 X1 Y2 Z-5 R2 F300", "Drilling cycle"), ("G1X10.5Y3F2000", "straight line"),
    ("g1 x10", "straight line"), ("G0 Z5 (lift)", "Rapid"),
])
def test_lines_of_every_kind_are_explained(line, word):
    out = guide.explain([line], 0)
    text = out["summary"] + " ".join(w["text"] for w in out["words"])
    assert word.lower() in text.lower(), (line, out)


def test_the_words_of_a_line_without_spaces_are_split():
    out = guide.explain(["G2X10Y3.5I-2.25J0F800"], 0)
    assert [w["word"] for w in out["words"]] == ["G2", "X10", "Y3.5", "I-2.25", "J0", "F800"]


def test_garbage_does_not_raise():
    for line in ["G", "X", "G999", "M999", "G1 X", "X1..2", "((((", "G2 I", "G2 X1 Y1", "ÿ ü ğ", "G1 X1e5", "\x00\x01"]:
        guide.explain([line], 0)
        guide.explain(["G2 X1 Y1 I1 J1", line], 1)
    with pytest.raises(IndexError):
        guide.explain(["G0"], 3)


def _posts_that_write_arcs():
    return [{"arcs": "ijk", "ijk": "incremental"}, {"arcs": "ijk", "ijk": "absolute"}, {"arcs": "r"},
            {"arcs": "ijk", "units": "inch", "decimals": 4}, {"arcs": "ijk", "modal_coords": False, "modal_motion": False},
            {"arcs": "ijk", "line_numbers": True, "g_leading_zero": True, "integer_point": True},
            {"arcs": "ijk", "arc_max_sweep": 60.0}]


@pytest.mark.parametrize("opts", _posts_that_write_arcs())
def test_the_explanation_of_every_arc_the_engine_writes_is_a_true_circle(host, opts):
    """The explainer reads the G-code the way a controller does: the end of every arc must lie on the circle it says
    (the centre and radius it gives), whatever the post's way of writing numbers, units and arcs."""
    d = draft_from(host, "linuxcnc", options=opts)
    if opts.get("ijk") == "absolute":              # (what the post writes, the header must tell the controller)
        d["blocks"]["header"] = d["blocks"]["header"].replace("G91.1", "G90.1")
        assert E.clean_draft(d)[1] == []
    post = E.build_post(d)
    lines = lines_of(E.sample_files(post)[0][1])
    inch = post.options["units"] == "inch"
    arcs = 0
    for i, ln in enumerate(lines):
        if not re.search(r"G0?[23](?!\d)", ln):
            continue
        out = guide.explain(lines, i, "en", inch=inch, arc_absolute=post.options["ijk"] == "absolute")
        a = out["arc"]
        assert a is not None, ln
        arcs += 1
        if "radius" in a and a["end"] is not None and a.get("centre"):
            assert abs(math.dist(a["end"], a["centre"]) - a["radius"]) < 0.01 * (1 if inch else 1), (ln, a)
            assert abs(math.dist(a["start"], a["centre"]) - a["radius"]) < 0.01, (ln, a)
    assert arcs >= 5


# ---------------------------------------------------------------------------------------------
# the operation
# ---------------------------------------------------------------------------------------------

def test_the_service_explains_a_line(host):
    prog = ["G21 G90 G91.1", "G0 X0 Y0", "G1 X10 F500", "G2 X20 Y0 I5 J0"]
    out = host.handle("gcode_explain", {"lines": prog, "index": 3, "lang": "tr", "post": "mach3"})
    assert "error" not in out and out["arc"]["centre"] == [15.0, 0.0] and "saat yönünde" in out["summary"]
    assert out["index"] == 3 and host.handle("gcode_explain", {"lines": prog})["index"] == 3      # (the last line by default)
    for bad in ({}, {"lines": []}, {"lines": "G1"}, {"lines": [1]}, {"lines": prog, "index": 9},
                {"lines": prog, "index": True}, {"lines": ["G1"] * 5001}):
        assert host.handle("gcode_explain", bad)["error"] == "bad_request"


def test_the_posts_the_page_may_call_are_in_the_interface():
    from iyicam.service.api import API_OPS
    assert {"posts", "post_save", "post_delete", "post_preview", "gcode_explain"} <= API_OPS


def test_a_saved_post_is_in_the_schema_and_makes_gcode(host):
    from conftest import add_tp, end_mill, make_project
    from iyicam.core import shapes
    host.handle("post_save", {"post": draft_from(host, name="Shop", options={"decimals": 1})})
    assert any(p["id"] == "shop" for p in host.handle("schema")["posts"])
    p, s, objs = make_project([shapes.rectangle(10, 10, 50, 30)])
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    out = host.handle("gcode", {"project": p.to_dict(), "post": "shop"})
    assert out["ok"] and out["files"][0]["name"].endswith(".tap")


def test_the_explainer_of_a_long_program_gives_what_a_fresh_read_gives():
    prog = ["G21 G90 G91.1", "G0 X0 Y0"]
    for i in range(1, 700):
        prog += [f"G1 X{i}", f"G2 X{i + 0.5} Y0 I0.25 J0" if i % 3 == 0 else f"G1 Y{i % 7}"]
    ex = guide.Explainer(prog, "en", every=64)
    for i in (5, 400, 401, 63, 64, 65, 1000, 3, 1390, 700):        # (forwards, backwards, on a checkpoint)
        assert ex.at(i) == guide.explain(prog, i, "en"), i
    with pytest.raises(IndexError):
        ex.at(len(prog))


def test_following_a_program_without_sentences_gives_the_same_state():
    prog = ["G21", "G90", "G0 X1 Y2 Z3", "G91", "G1 X1 Y1", "G90", "G98 G81 X9 Y9 Z-5 R2 F100", "G1 Z5"]
    a, b = guide.State(), guide.State()
    for ln in prog:
        guide.explain_line(ln, a)
        guide.explain_line(ln, b, track=True)
    assert a == b and a.x == 9 and a.y == 9 and a.z == 5


def test_the_id_of_a_file_written_by_hand_can_be_saved_again(host):
    paths.posts_dir().mkdir(parents=True, exist_ok=True)
    (paths.posts_dir() / "Old-Style.toml").write_text('id = "Old-Style"\nname = "Old"\nextends = "mach3"\n', encoding="utf-8")
    from iyicam.builtins import load_user_posts
    REGISTRY.remove_source("user-posts")
    load_user_posts(REGISTRY)
    d = next(p for p in host.handle("posts")["posts"] if p["id"] == "Old-Style")
    d["options"] = {**d["options"], "decimals": 2}
    out = host.handle("post_save", {"post": d})
    assert "error" not in out and out["saved"] == "Old-Style" and out["file"] == "Old-Style.toml"
    assert [f.name for f in paths.posts_dir().glob("*.toml")] == ["Old-Style.toml"]


def test_a_post_that_would_not_read_back_is_not_written(host, monkeypatch):
    monkeypatch.setattr(E, "dump_toml", lambda d: "this is = not [toml")
    out = host.handle("post_save", {"post": draft_from(host, name="Broken")})
    assert out["error"] == "invalid_post" and "cannot be written" in out["message"]
    assert not list(paths.posts_dir().glob("*"))
