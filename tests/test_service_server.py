"""Service end to end (fake Trackr + fake LLM) and the HTTP API over a real local socket."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from gradtracker.server import make_server
from gradtracker.service import Tracker
from conftest import TODAY, FakeLLM, assessor


def fetcher(ops):
    return lambda s, today, previous, progress=None: ([dict(o) for o in ops], {"trackr:Tech": len(ops)})


@pytest.fixture
def tracker(settings, ops):
    t = Tracker(settings, llm_factory=lambda s: FakeLLM(fn=assessor), fetcher=fetcher(ops))
    t.update(progress=lambda m: None, today=TODAY)
    return t


def test_update_scores_only_active_and_tracks_new(tracker):
    st = tracker.state(tracker.person())
    assert st.assessment("032gbd44r2") and st.assessment("future01")
    assert st.assessment("closed01") is None                       # closed: not worth scoring
    assert set(st.data["meta"]["new_ids"]) == {"032gbd44r2", "closed01", "future01"}
    tracker.update(progress=lambda m: None, today=TODAY)
    assert tracker.state(tracker.person()).data["meta"]["new_ids"] == []


def test_view_rows_and_counts(tracker):
    v = tracker.view(today=TODAY)
    rows = {r["id"]: r for r in v["positions"]}
    acturis = rows["032gbd44r2"]
    assert acturis["score"] is not None and acturis["letter"] == "optional" and acturis["days_left"] == 62
    assert acturis["parts"][0]["why"] == "backend role"
    assert rows["closed01"]["score"] is None
    assert v["positions"][-1]["score"] is None                     # unscored sink to the bottom
    assert v["counts"]["open_todo"] == 1 and v["counts"]["applied"] == 0
    assert set(v["statuses"]) == {"todo", "applied", "interviewing", "offer", "rejected", "skip"}


@pytest.fixture
def api(tracker):
    server = make_server(tracker, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw.decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    server.shutdown()
    server.server_close()


def test_api_serves_ui_and_static(api):
    code, html = api("/")
    assert code == 200 and "Grad Tracker" in html and "/static/app.js" in html
    assert api("/static/app.js")[0] == 200 and api("/static/style.css")[0] == 200
    assert api("/static/../service.py")[0] == 404                   # no path traversal
    assert api("/nope")[0] == 404


def test_api_status_note_persist(api, tracker):
    assert api("/api/status", {"id": "032gbd44r2", "status": "applied"})[0] == 200
    assert api("/api/note", {"id": "032gbd44r2", "note": "applied via referral"})[0] == 200
    _, data = api("/api/data")
    row = next(r for r in data["positions"] if r["id"] == "032gbd44r2")
    assert row["my_status"] == "applied" and row["note"] == "applied via referral"
    reloaded = json.loads(tracker.person().state_file.read_text())
    assert reloaded["positions"]["032gbd44r2"]["status"] == "applied"


def test_api_rejects_bad_input(api):
    assert api("/api/status", {"id": "032gbd44r2", "status": "maybe"})[0] == 400
    assert api("/api/status", {"id": "nope", "status": "applied"})[0] == 404
    assert api("/api/status", {"status": "applied"})[0] == 404


def test_api_prompt(api):
    code, data = api("/api/prompt?id=032gbd44r2")
    assert code == 200 and "Acturis" in data["prompt"] and "Output only the letter." in data["prompt"]
    assert api("/api/prompt?id=nope")[0] == 404


def test_api_weights_rerank_and_reset(api):
    _, before = api("/api/data")
    s0 = next(r["score"] for r in before["positions"] if r["id"] == "032gbd44r2")
    code, after = api("/api/weights", {"weights": {"visa_no": -60, "bogus": 5}})
    assert code == 200
    s1 = next(r["score"] for r in after["positions"] if r["id"] == "032gbd44r2")   # Acturis: visa No
    assert s1 == s0 - 45 and "bogus" not in after["weights"]
    _, reset = api("/api/weights", {"reset": True})
    assert next(r["score"] for r in reset["positions"] if r["id"] == "032gbd44r2") == s0


def test_api_refresh_runs_in_background(api):
    code, st = api("/api/refresh", {})
    assert code in (202, 409)
    for _ in range(50):
        _, st = api("/api/refresh")
        if not st["running"]:
            break
        time.sleep(0.1)
    assert not st["running"] and st["error"] is None
