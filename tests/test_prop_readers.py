"""The readers of files on anything (Hypothesis): a damaged or foreign file says so with the reader's own error, it never
crashes with another one or hangs; and what one writes the other reads back."""
import math
import struct

import pytest
from hypothesis import HealthCheck, example, given, settings, strategies as st

from iyicam.formats import artcam
from iyicam.formats.cfb import MAGIC, CfbError, CompoundFile

SET = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@st.composite
def cfb_like(draw):
    """Bytes that start like a compound file (so the reader goes past the first check), with random tables."""
    head = bytearray(draw(st.binary(min_size=512, max_size=512)))
    head[0:8] = MAGIC
    struct.pack_into("<HH", head, 0x1E, draw(st.sampled_from([9, 12, 7])), 6)
    body = draw(st.binary(min_size=0, max_size=4096))
    return bytes(head) + body


@SET
@given(st.one_of(st.binary(max_size=2048), cfb_like()))
@example(MAGIC + b"\0" * 504)
def test_a_compound_file_reader_says_what_is_wrong(data):
    try:
        cf = CompoundFile(data)
    except CfbError:
        return
    for p in cf.paths()[:20]:
        if not p.endswith("/"):
            try:
                cf.read(p)
            except CfbError:
                pass


@SET
@given(data=st.one_of(st.binary(max_size=4096), cfb_like()))
def test_an_artcam_reader_says_what_is_wrong(data, tmp_path_factory):
    f = tmp_path_factory.mktemp("a") / "x.art"
    f.write_bytes(data)
    with pytest.raises(artcam.ArtCamError):
        artcam.read_file(f)


@st.composite
def damaged_artcam(draw):
    """A real-looking ArtCAM model (tests/artcam_builder.py) with one stream cut short, padded or with bytes flipped."""
    from artcam_builder import circle_line, compound_file, model_streams, rect_line
    streams = model_streams([{"name": "k1", "contours": [(1, rect_line(10, 10, 100, 50, 2.5)), (2, circle_line(300, 100, 30))]},
                             {"name": "k2", "contours": [(3, rect_line(400, 10, 80, 40, 2.5))]}],
                            version=draw(st.sampled_from([2008, 2018])))
    key = draw(st.sampled_from(sorted(streams)))
    raw = bytearray(streams[key])
    how = draw(st.sampled_from(["cut", "flip", "pad"]))
    if how == "cut":
        raw = raw[:draw(st.integers(0, len(raw)))]
    elif how == "flip" and raw:
        for _ in range(draw(st.integers(1, 8))):
            i = draw(st.integers(0, len(raw) - 1))
            raw[i] = draw(st.integers(0, 255))
    else:
        raw += draw(st.binary(min_size=1, max_size=64))
    streams[key] = bytes(raw)
    return compound_file(streams)


@settings(max_examples=150, deadline=None)
@given(data=damaged_artcam())
def test_a_damaged_artcam_model_is_read_as_far_as_it_goes_or_said_to_be_damaged(data, tmp_path_factory):
    from iyicam.formats.report import ImportReport
    f = tmp_path_factory.mktemp("a") / "x.art"
    f.write_bytes(data)
    try:
        p = artcam.read_project(f, ImportReport())
    except artcam.ArtCamError:
        return
    for s in p.sheets:                                   # (what it read is sound: finite numbers, a plate of a size)
        assert s.width > 0 and s.height > 0 and math.isfinite(s.width) and math.isfinite(s.height)
        for v in s.vectors:
            for c in v.contours:
                assert all(math.isfinite(x) and math.isfinite(y) for x, y in c.flatten(0.5))
    for t in p.toolpaths:
        assert math.isfinite(t.params["cut_depth"]) and math.isfinite(t.tool.diameter) and t.tool.diameter > 0


@SET
@given(st.text(max_size=400))
@example("G2 X10 Y0 I0 J0")
@example("G1 X1e400 Y-1e400")
@example("G0 X nan")
def test_the_gcode_reader_reads_any_text(text):
    from iyicam.cam.gcode_reader import read_gcode
    tps = read_gcode(text)
    for tp in tps:
        for m in tp.moves:
            for k in ("x", "y", "z"):
                v = getattr(m, k, 0.0)
                assert v == v, (text, m)                 # (no NaN reaches the moves)


@SET
@given(st.recursive(st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text(max_size=5)),
                    lambda ch: st.one_of(st.lists(ch, max_size=4), st.dictionaries(st.text(max_size=8), ch, max_size=5)),
                    max_leaves=30))
def test_a_project_file_of_anything_is_said_to_be_wrong(junk):
    from iyicam.service import ops
    from iyicam.service.errors import ServiceError
    payload = {"format": "iyicam-project", **junk} if isinstance(junk, dict) else junk
    try:
        ops.load_project(payload)
    except ServiceError:
        pass
