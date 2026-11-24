"""Opening files fast: the reader of CorelDRAW files, and the engine taking bytes without base64 / answering a quick job at once.

(The file window, the import cache of the old server and the gzip of the old hub are gone with them: the browser reads files
itself now, see tests/webapp.)"""
import base64
import struct

import pytest

from iyicam.formats import cdr_native as cn
from iyicam.service import ServiceHost

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
       b'<rect x="5" y="5" width="90" height="40" fill="none" stroke="black"/></svg>')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "state"))

    class E:
        pass
    e = E()
    e.host = ServiceHost()
    return e


def test_the_engine_takes_the_bytes_of_a_file_without_base64(env):
    a = env.host.handle("import", {"name": "x.svg", "data": SVG})
    b = env.host.handle("import", {"name": "x.svg", "data_b64": base64.b64encode(SVG).decode()})
    assert "error" not in a and len(a["project"]["sheets"][0]["vectors"]) == len(b["project"]["sheets"][0]["vectors"]) == 1


# -- a quick job answers in the call that starts it ------------------------------------------------------------------------

def test_a_job_that_is_over_in_time_answers_in_the_call_that_starts_it(env):
    payload = {"name": "x.svg", "data_b64": base64.b64encode(SVG).decode()}
    out = env.host.handle("job.start", {"op": "import", "payload": payload, "wait": 20000})
    assert out["state"] == "done" and len(out["result"]["project"]["sheets"][0]["vectors"]) == 1
    assert env.host.handle("job.status", {"id": out["id"]})["error"] == "job_not_found"       # (nothing is kept for later)
    bad = env.host.handle("job.start", {"op": "import", "payload": {"name": "x.dxf", "data_b64": base64.b64encode(b"nonsense").decode()}, "wait": 20000})
    assert bad["state"] == "error" and isinstance(bad["error"].get("error"), str)
    plain = env.host.handle("job.start", {"op": "import", "payload": payload})                 # (without ``wait``: as before)
    assert plain["state"] == "queued" and env.host.handle("job.status", {"id": plain["id"]})["id"] == plain["id"]


# -- the server: gzip requests and the files of the page -----------------------------------------------------------------

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"




# -- the reader of CorelDRAW files ---------------------------------------------------------------------------------------

def test_the_reader_reads_its_numbers_as_it_did_and_stops_at_the_end():
    data = struct.pack("<BHIhid", 7, 513, 0xDEADBEEF, -2, -70000, 1.5) + b"\x01\x02\x03"
    r = cn._R(data)
    assert (r.u8(), r.u16(), r.u32(), r.s16(), r.s32(), r.f64()) == (7, 513, 0xDEADBEEF, -2, -70000, 1.5)
    assert r.p == len(data) - 3 and not r.end()
    with pytest.raises(cn._Short):
        r.u32()
    assert r.p == len(data) - 3                                              # (a failed read moves nothing)
    assert list(cn._Parser._types(r, 3)) == [1, 2, 3] and r.end()
    with pytest.raises(cn._Short):
        cn._Parser._types(cn._R(b"\x01"), 2)


def test_a_path_is_read_in_one_go_to_the_same_points():
    pts = [(10.5, -3.25), (0.0, 7.0), (12.345, 0.001)]
    for bits, div, fmt in ((32, 254000.0, "<i"), (16, 1000.0, "<h")):
        raw = b"".join(struct.pack(fmt, round(v * div)) for p in pts for v in p)
        parser = cn._Parser(object(), [])
        parser.prec = bits
        got = parser._points(cn._R(raw), len(pts))
        want = [(round(x * div) / div, round(y * div) / div) for x, y in pts]
        assert got == want
    parser = cn._Parser(object(), [])
    with pytest.raises(cn.Unsupported):                                       # (the numbers before the version are not read)
        parser._points(cn._R(b"\0" * 16), 2)
    parser.prec = 32
    with pytest.raises(cn._Short):
        parser._points(cn._R(b"\0" * 7), 1)

