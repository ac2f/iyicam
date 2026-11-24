"""The folders that are shared: what may be listed, read and written, and what never."""
import os

import pytest

from iyicam.share.rootfs import FsError, RootFS


@pytest.fixture
def fs(tmp_path):
    root = tmp_path / "jobs"
    (root / "sub").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / ".hidden" / "x.icam").write_text("secret")
    (root / "a.icam").write_text("{}")
    (root / "b.dxf").write_text("0\nEOF\n")
    (root / "prog.exe").write_text("MZ")
    (root / "notes.docx").write_text("no")
    (root / "sub" / "c.svg").write_text("<svg/>")
    other = tmp_path / "other"
    other.mkdir()
    (other / "outside.icam").write_text("outside")
    return RootFS([str(root)], max_bytes=1000), root, other


def code(call, *a, **k):
    with pytest.raises(FsError) as e:
        call(*a, **k)
    return e.value.code


def test_the_roots_are_named_by_their_last_part_and_never_show_the_disk(fs, tmp_path):
    f, root, _ = fs
    assert f.roots() == [{"name": "jobs", "path": "jobs"}]
    assert [e["name"] for e in f.list("")["entries"]] == ["jobs"]
    other = tmp_path / "x" / "jobs"
    other.mkdir(parents=True)
    two = RootFS([str(root), str(other), str(tmp_path / "missing")])
    assert [r["name"] for r in two.roots()] == ["jobs", "jobs-2"]       # (alike: told apart; a folder that is not there: left out)


def test_list_shows_folders_first_and_only_the_kinds_the_app_knows(fs):
    f, _root, _ = fs
    out = f.list("jobs")
    assert [(e["name"], e["dir"]) for e in out["entries"]] == [("sub", True), ("a.icam", False), ("b.dxf", False)]
    assert out["entries"][1]["path"] == "jobs/a.icam" and out["entries"][1]["size"] == 2
    assert [e["name"] for e in f.list("jobs/sub")["entries"]] == ["c.svg"]


def test_read_write_stat(fs):
    f, root, _ = fs
    assert f.read("jobs/a.icam") == b"{}"
    assert f.stat("jobs/sub")["dir"] is True and f.stat("jobs/a.icam")["size"] == 2
    out = f.write("jobs/sub/new.nc", b"G0 X0\n")
    assert out["path"] == "jobs/sub/new.nc" and (root / "sub" / "new.nc").read_bytes() == b"G0 X0\n"
    assert code(f.write, "jobs/sub/new.nc", b"x") == "file_exists"
    f.write("jobs/sub/new.nc", b"again", overwrite=True)
    assert (root / "sub" / "new.nc").read_bytes() == b"again"
    assert not [p for p in (root / "sub").iterdir() if p.name.endswith(".tmp")]       # (no scrap left behind)


@pytest.mark.parametrize("path", ["jobs/../other/outside.icam", "jobs/sub/../../other/outside.icam", "../x.icam", "jobs/./a.icam",
                                  "jobs/.hidden/x.icam", "jobs\\a.icam", "nothere/a.icam", "jobs/a.icam\0", "/etc/passwd"])
def test_paths_that_lead_out_or_are_odd_are_refused(fs, path):
    f, *_ = fs
    assert code(f.read, path) in ("path_not_allowed", "bad_path")
    assert code(f.stat, path) in ("path_not_allowed", "bad_path")
    assert code(f.write, path, b"x", True) in ("path_not_allowed", "bad_path")


def test_a_symbolic_link_cannot_lead_out(fs):
    f, root, other = fs
    os.symlink(other / "outside.icam", root / "link.icam")
    os.symlink(other, root / "linkdir")
    assert code(f.read, "jobs/link.icam") == "path_not_allowed"
    assert code(f.list, "jobs/linkdir") == "path_not_allowed"
    assert code(f.write, "jobs/linkdir/x.icam", b"x") == "path_not_allowed"
    assert "link.icam" not in [e["name"] for e in f.list("jobs")["entries"]] and "linkdir" not in [e["name"] for e in f.list("jobs")["entries"]]
    assert (other / "outside.icam").read_text() == "outside"


def test_only_known_files_are_read_and_no_program_is_written(fs):
    f, *_ = fs
    assert code(f.read, "jobs/prog.exe") == "unsupported_file" and code(f.read, "jobs/notes.docx") == "unsupported_file"
    for name in ("run.exe", "x.py", "x.sh", "x.bat", "x.js", "noextension", "x.html"):
        assert code(f.write, f"jobs/{name}", b"x") == "unsupported_file", name
    assert code(f.write, "jobs/.hidden.nc", b"x") == "path_not_allowed"
    assert code(f.write, "jobs", b"x") == "bad_path"               # (a root itself is a folder)
    assert code(f.write, "jobs/nofolder/x.nc", b"x") == "not_found"


def test_size_limits(fs):
    f, root, _ = fs
    (root / "big.icam").write_bytes(b"x" * 1001)
    assert code(f.read, "jobs/big.icam") == "file_too_large"
    assert code(f.write, "jobs/big.nc", b"x" * 1001) == "file_too_large"
    assert not (root / "big.nc").exists()


def test_the_state_folder_of_iyicam_is_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / "state"
    state.mkdir(parents=True)
    (state / "server.key").write_text("k")
    (state / "p.icam").write_text("{}")
    monkeypatch.setenv("IYICAM_HOME", str(state))
    f = RootFS([str(home)])
    assert code(f.read, "home/state/p.icam") == "path_not_allowed"      # (the home folder is shared, the state folder in it is not)
    assert RootFS([str(state / "inside")]).roots() == []
