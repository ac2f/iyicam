"""Jobs that outlive the phone: the last three jobs are kept (their results too, on disk), a finished one can be asked for again
by any device, the engine says which devices were given a result, and a restart of the engine does not lose a finished job.
"""
import gzip
import json
import os
import threading
import time

import pytest
from conftest import make_project

from iyicam.core import shapes
from iyicam.service import ServiceHost
from iyicam.service import jobs as J
from iyicam.service.errors import ServiceError
from iyicam.service.jobs import JobManager

PHONE = "phone-0123456789"
LAPTOP = "laptop-abcdef0123"


def meta(title="Nesting · plate", device=PHONE, label="Android · Chrome"):
    return {"title": title, "device": device, "device_label": label}


def quick(value):
    return lambda ctl: {"value": value}


def wait_done(m, jid, seconds=10.0):
    assert m.wait(jid, seconds).finished_event.is_set()


class Held:
    """A job that stays running until ``release()``: a nesting that takes minutes."""

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def __call__(self, ctl):
        self.started.set()
        while not self.gate.wait(0.02):
            ctl.check()
        return {"held": True}

    def release(self):
        self.gate.set()


# ---------------------------------------------------------------------------------------------
# what is kept
# ---------------------------------------------------------------------------------------------

def test_a_job_that_is_not_kept_is_as_before(tmp_path):
    m = JobManager(store=tmp_path / "jobs")
    jid = m.start("compute", quick(1))
    wait_done(m, jid)
    assert m.result(jid) == {"value": 1} and m.listing() == []
    assert not list((tmp_path / "jobs").glob("*"))                 # (nothing is written for it)
    m.ttl = -1
    m.start("compute", quick(2))                                    # (the next start forgets what is over)
    with pytest.raises(ServiceError) as e:
        m.status(jid)
    assert e.value.code == "job_not_found"


def test_what_a_device_says_about_a_job_is_made_safe():
    assert J.clean_meta(None) == {} and J.clean_meta("x") == {} and J.clean_meta(5) == {}
    m = J.clean_meta({"title": "a\nb\x00c" + "x" * 300, "device": "../etc/passwd", "device_label": "L" * 100, "extra": 1})
    assert "\n" not in m["title"] and "\x00" not in m["title"] and len(m["title"]) == 120
    assert m["device"] == "" and len(m["device_label"]) == 60 and set(m) == {"title", "device", "device_label"}
    assert J.clean_meta({"device": PHONE})["device"] == PHONE


def test_the_last_three_jobs_are_kept_and_the_older_ones_go_with_their_files(tmp_path):
    m = JobManager(store=tmp_path / "jobs")
    ids = []
    for i in range(5):
        jid = m.start("nest", quick(i), keep=meta(f"job {i}"))
        wait_done(m, jid)
        ids.append(jid)
        time.sleep(0.01)
    listing = m.listing(PHONE)
    assert [j["title"] for j in listing] == ["job 4", "job 3", "job 2"]               # newest first, three
    assert all(j["state"] == "done" and j["mine"] and j["has_result"] for j in listing)
    files = sorted(p.name for p in (tmp_path / "jobs").glob("*.result.json.gz"))
    assert files == sorted(f"{i}.result.json.gz" for i in ids[2:])                      # the two older results are gone
    for jid in ids[:2]:
        with pytest.raises(ServiceError):
            m.result(jid)
    assert m.result(ids[4]) == {"value": 4}


def test_a_job_that_is_not_over_is_never_pushed_out(tmp_path):
    m = JobManager(workers=4, store=tmp_path / "jobs")
    slow = Held()
    first = m.start("nest", slow, keep=meta("the long one"))
    assert slow.started.wait(5)
    for i in range(4):
        wait_done(m, m.start("nest", quick(i), keep=meta(f"quick {i}")))
        time.sleep(0.01)
    titles = [j["title"] for j in m.listing()]
    assert "the long one" in titles and len(titles) == 4                # (three newest and the one still running)
    slow.release()
    wait_done(m, first)
    assert [j["title"] for j in m.listing()][:3] == ["quick 3", "quick 2", "quick 1"] and len(m.listing()) == 3


def test_a_kept_job_does_not_wait_for_the_ttl_of_the_others(tmp_path):
    m = JobManager(ttl=0.0, store=tmp_path / "jobs")
    jid = m.start("nest", quick(1), keep=meta())
    wait_done(m, jid)
    time.sleep(0.02)
    m.start("compute", quick(2))
    assert m.result(jid) == {"value": 1}
    m.recent_ttl = 0.0                                                   # (a week is over)
    assert m.listing() == []
    with pytest.raises(ServiceError):
        m.status(jid)


# ---------------------------------------------------------------------------------------------
# a device that comes back
# ---------------------------------------------------------------------------------------------

def test_the_engine_says_who_was_given_a_result(tmp_path):
    m = JobManager(store=tmp_path / "jobs")
    jid = m.start("nest", quick(7), keep=meta())
    wait_done(m, jid)
    row = m.listing(PHONE)[0]
    assert row["mine"] and not row["delivered"] and row["deliveries"] == 0       # (finished and not given to the phone yet)
    other = m.listing(LAPTOP)[0]
    assert not other["mine"] and not other["delivered"]                          # (another device sees it, and can ask for it)
    assert m.result(jid, LAPTOP) == {"value": 7}
    assert m.listing(LAPTOP)[0]["delivered"] and not m.listing(PHONE)[0]["delivered"]
    assert m.result(jid, PHONE) == {"value": 7}
    assert m.listing(PHONE)[0]["delivered"] and m.listing(PHONE)[0]["deliveries"] == 2
    assert m.result(jid) == {"value": 7} and m.result(jid, "../bad") == {"value": 7}  # (no device: nothing is noted)
    assert m.listing("not a device id!")[0]["mine"] is False


def test_a_job_that_failed_or_was_cancelled_is_listed_with_why(tmp_path):
    m = JobManager(store=tmp_path / "jobs")

    def fails(ctl):
        raise ServiceError("nothing_to_nest", "there are no vectors to nest")

    bad = m.start("nest", fails, keep=meta("fails"))
    wait_done(m, bad)
    slow = Held()
    held = m.start("nest", slow, keep=meta("held"))
    assert slow.started.wait(5)
    rows = {r["title"]: r for r in m.listing(PHONE)}
    assert rows["fails"]["state"] == "error" and rows["fails"]["error"]["error"] == "nothing_to_nest" and not rows["fails"]["has_result"]
    assert rows["held"]["state"] == "running"
    m.cancel(held)
    wait_done(m, held)
    assert {r["title"]: r for r in m.listing(PHONE)}["held"]["state"] == "cancelled"
    with pytest.raises(ServiceError) as e:
        m.result(bad, PHONE)
    assert e.value.code == "nothing_to_nest"


def test_dismiss_takes_a_job_off_the_list_and_stops_one_that_runs(tmp_path):
    m = JobManager(store=tmp_path / "jobs")
    done = m.start("nest", quick(1), keep=meta("done"))
    wait_done(m, done)
    slow = Held()
    held = m.start("nest", slow, keep=meta("held"))
    assert slow.started.wait(5)
    assert m.dismiss(held)["ok"] and m.dismiss(done)["ok"]
    assert m.listing() == [] and not list((tmp_path / "jobs").glob("*.result.json.gz"))
    time.sleep(0.2)                                                      # (the one that ran has seen the cancel and stopped)
    with pytest.raises(ServiceError):
        m.dismiss(done)
    plain = m.start("compute", quick(1))
    with pytest.raises(ServiceError) as e:
        m.dismiss(plain)                                                 # (only the kept ones are on the list)
    assert e.value.code == "job_not_found"


# ---------------------------------------------------------------------------------------------
# a restart of the engine
# ---------------------------------------------------------------------------------------------

def test_a_finished_job_survives_a_restart_and_a_running_one_is_said_to_be_interrupted(tmp_path):
    store = tmp_path / "jobs"
    a = JobManager(store=store)
    done = a.start("nest", quick({"project": {"name": "plate"}}), keep=meta("finished"))
    wait_done(a, done)
    a.result(done, PHONE)
    slow = Held()
    running = a.start("nest", slow, keep=meta("running"))
    assert slow.started.wait(5)
    # (the engine stops here: a new manager reads the store)
    b = JobManager(store=store)
    rows = {r["title"]: r for r in b.listing(PHONE)}
    assert rows["finished"]["state"] == "done" and rows["finished"]["has_result"] and rows["finished"]["delivered"]
    assert rows["finished"]["mine"] and rows["finished"]["size"] > 0
    assert b.result(done, PHONE) == {"value": {"project": {"name": "plate"}}}
    assert rows["running"]["state"] == "error" and rows["running"]["error"]["error"] == "interrupted"
    with pytest.raises(ServiceError) as e:
        b.result(running)
    assert e.value.code == "interrupted"
    slow.release()                                                       # (the old one stops its thread)
    wait_done(a, running)


def test_the_files_are_for_the_owner_only_and_hold_the_result_as_gzip(tmp_path):
    m = JobManager(store=tmp_path / "jobs")
    jid = m.start("nest", quick({"a": [1, 2, 3]}), keep=meta())
    wait_done(m, jid)
    f = tmp_path / "jobs" / f"{jid}.result.json.gz"
    assert json.loads(gzip.decompress(f.read_bytes())) == {"value": {"a": [1, 2, 3]}}
    assert (tmp_path / "jobs" / "index.json").exists() and not list((tmp_path / "jobs").glob("*.tmp"))
    if os.name == "posix":
        assert all((p.stat().st_mode & 0o077) == 0 for p in (tmp_path / "jobs").iterdir())


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"jobs": 5}', '{"jobs": [1, "x", {}]}',
                                     '{"jobs": [{"id": "../../x", "kind": "nest", "state": "done", "created": 1, "finished": 2}]}'])
def test_a_damaged_index_means_no_kept_jobs_nothing_more(tmp_path, content):
    store = tmp_path / "jobs"
    store.mkdir()
    (store / "index.json").write_text(content, encoding="utf-8")
    m = JobManager(store=store)
    assert m.listing() == []
    jid = m.start("nest", quick(1), keep=meta())
    wait_done(m, jid)
    assert JobManager(store=store).listing()[0]["id"] == jid          # (and it is written again as it should)


def test_a_result_that_is_gone_or_damaged_is_said_so_not_a_crash(tmp_path):
    store = tmp_path / "jobs"
    a = JobManager(store=store)
    jid = a.start("nest", quick(1), keep=meta())
    wait_done(a, jid)
    (store / f"{jid}.result.json.gz").write_bytes(b"not gzip")
    b = JobManager(store=store)
    with pytest.raises(ServiceError) as e:
        b.result(jid, PHONE)
    assert e.value.code == "job_not_found" and "could not be read" in e.value.message
    (store / f"{jid}.result.json.gz").unlink()
    c = JobManager(store=store)
    with pytest.raises(ServiceError) as e2:
        c.result(jid, PHONE)
    assert e2.value.code == "job_not_found"


def test_a_result_too_big_for_the_disk_stays_in_memory_only(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "MAX_STORED", 10)
    store = tmp_path / "jobs"
    m = JobManager(store=store)
    jid = m.start("nest", quick("x" * 100), keep=meta())
    wait_done(m, jid)
    assert m.result(jid, PHONE) == {"value": "x" * 100} and not list(store.glob("*.result.json.gz"))
    assert not JobManager(store=store).listing()[0]["has_result"]        # (after a restart it is there, without its result)


def test_a_folder_that_cannot_be_written_to_is_not_an_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    m = JobManager(store=blocker / "jobs")                                # (a "folder" that is a file)
    jid = m.start("nest", quick(3), keep=meta())
    wait_done(m, jid)
    assert m.result(jid, PHONE) == {"value": 3} and m.listing(PHONE)[0]["has_result"]


def test_without_a_store_the_jobs_are_kept_in_memory(tmp_path):
    m = JobManager()
    jid = m.start("nest", quick(5), keep=meta())
    wait_done(m, jid)
    assert m.result(jid, PHONE) == {"value": 5} and m.listing()[0]["state"] == "done"


# ---------------------------------------------------------------------------------------------
# through the service
# ---------------------------------------------------------------------------------------------

def nest_project():
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 60), shapes.rectangle(0, 0, 80, 50), shapes.circle((0, 0), 30)],
                              size=(500.0, 400.0))
    return p.to_dict()


def test_a_nesting_started_with_keep_is_there_after_a_restart_of_the_engine(tmp_path):
    host = ServiceHost(store=tmp_path / "jobs")
    out = host.handle("job.start", {"op": "nest", "payload": {"project": nest_project(), "settings": {"width": 1000, "height": 800,
                                                                                                         "iterations": 1}},
                                    "keep": meta("Nesting · plate"), "wait": 20000})
    assert out["state"] == "done" and out["result"]["project"]
    jid = out["id"]
    listing = host.handle("jobs", {"device": PHONE})
    assert listing["recent"] == 3 and [j["title"] for j in listing["jobs"]] == ["Nesting · plate"]
    assert listing["jobs"][0]["kind"] == "nest" and listing["jobs"][0]["mine"]
    assert listing["jobs"][0]["delivered"]            # (it was over within the call: the phone that started it has the result)
    assert not host.handle("jobs", {"device": LAPTOP})["jobs"][0]["delivered"]
    # a new engine on the same store (what a restart or a reboot is)
    again = ServiceHost(store=tmp_path / "jobs")
    row = again.handle("jobs", {"device": PHONE})["jobs"][0]
    assert row["id"] == jid and row["state"] == "done" and row["has_result"]
    got = again.handle("job.result", {"id": jid, "device": PHONE})
    assert got["project"]["sheets"] and got["placed"] == out["result"]["placed"] and got == out["result"]
    assert again.handle("jobs", {"device": PHONE})["jobs"][0]["delivered"]
    assert again.handle("job.dismiss", {"id": jid})["ok"] and again.handle("jobs", {})["jobs"] == []


def test_the_operations_are_checked(tmp_path):
    host = ServiceHost(store=tmp_path / "jobs")
    assert host.handle("job.start", {"op": "nest", "payload": {}, "keep": 5})["error"] == "bad_request"
    assert host.handle("job.dismiss", {"id": "nope"})["error"] == "job_not_found"
    assert host.handle("jobs", {"device": 5})["jobs"] == []
    assert host.handle("job.start", {"op": "import", "payload": {"name": "x.dxf", "data_b64": ""}, "keep": meta(),
                                     "wait": 5000}).get("state") in ("done", "error")
    assert host.handle("jobs", {})["jobs"] == []                           # (only a long calculation is kept)


def test_the_page_can_call_the_new_operations():
    from iyicam.service.api import API_OPS
    assert {"jobs", "job.dismiss"} <= API_OPS


def test_a_default_service_keeps_its_jobs_in_the_user_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    host = ServiceHost()
    assert host.jobs.store == tmp_path / "home" / "jobs"
    assert ServiceHost(store=None).jobs.store is None and ServiceHost(store=False).jobs.store is None


# ---------------------------------------------------------------------------------------------
# through the web server and through a hub
# ---------------------------------------------------------------------------------------------

def _settings():
    return {"width": 1000, "height": 800, "iterations": 1}


def test_the_web_server_gives_the_new_operations_to_a_signed_in_browser(tmp_path):
    import http.client

    from iyicam.service import server as srv
    key = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"
    s = srv.make_server("127.0.0.1", 0, key=key, service=ServiceHost(store=tmp_path / "jobs"), files=False)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        cookie = {}

        def call(path, body):
            conn = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=60)
            conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json", **cookie})
            r = conn.getresponse()
            data = json.loads(r.read() or b"null")
            sc = r.getheader("Set-Cookie")
            if sc:
                cookie["Cookie"] = sc.split(";", 1)[0]
            conn.close()
            return r.status, data

        assert call("/api/jobs", {})[0] == 401                                    # (not without the key)
        assert call("/api/login", {"key": key})[0] == 200
        status, out = call("/api/job.start", {"op": "nest", "payload": {"project": nest_project(), "settings": _settings()},
                                              "keep": meta("through the server"), "wait": 20000})
        assert status == 200 and out["state"] == "done"
        status, listing = call("/api/jobs", {"device": LAPTOP})
        assert status == 200 and listing["jobs"][0]["title"] == "through the server" and not listing["jobs"][0]["delivered"]
        status, got = call("/api/job.result", {"id": out["id"], "device": LAPTOP})
        assert status == 200 and got["project"]
        assert call("/api/jobs", {"device": LAPTOP})[1]["jobs"][0]["delivered"] is True
        assert call("/api/job.dismiss", {"id": out["id"]})[0] == 200 and call("/api/jobs", {})[1]["jobs"] == []
        assert call("/api/job.dismiss", {"id": out["id"]})[0] == 404                   # (job_not_found)
    finally:
        s.shutdown()
        s.server_close()


def test_a_kept_job_runs_at_the_agent_and_waits_there_for_a_phone_that_comes_back(tmp_path):
    """The phone starts a nesting through the hub and is gone (a new session, nothing in its hands but its device id): the job
    went on at the agent, and the phone, back, is told what it has not received and asks for it."""
    from test_hub import Setup
    folder = tmp_path / "files"
    folder.mkdir()
    setup = Setup(tmp_path, folder)
    try:
        setup.agent()
        phone = setup.browser()
        status, started = phone.api("job.start", {"op": "nest", "payload": {"project": nest_project(), "settings": _settings()},
                                                  "keep": meta("through the hub")})
        assert status == 200 and started["state"] in ("queued", "done")
        # the phone is gone: its cookie and its page are lost, the device id is what it keeps
        back = setup.browser()
        deadline = time.time() + 30
        row = None
        while time.time() < deadline:
            row = back.api("jobs", {"device": PHONE})[1]["jobs"][0]
            if row["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert row["state"] == "done" and row["mine"] and row["title"] == "through the hub" and row["has_result"]
        if started["state"] == "queued":
            assert not row["delivered"]                                         # (finished and not given: the page asks)
        status, got = back.api("job.result", {"id": row["id"], "device": PHONE})
        assert status == 200 and got["project"]["sheets"]
        assert back.api("jobs", {"device": PHONE})[1]["jobs"][0]["delivered"]
        assert not back.api("jobs", {"device": LAPTOP})[1]["jobs"][0]["delivered"]  # (another device can still ask for it)
    finally:
        setup.close()
