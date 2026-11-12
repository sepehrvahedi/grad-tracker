"""First run (no people yet), adding a person from an uploaded CV, and the command line used by scripts and assistants."""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request

import pytest

from gradtracker import cli
from gradtracker.config import SAMPLE_CV, load_settings
from gradtracker.people import create_person, load_people
from gradtracker.server import make_server
from gradtracker.service import Tracker
from conftest import TODAY


@pytest.fixture
def empty(tmp_path, monkeypatch):
    """A brand-new home folder: nobody set up, nothing fetched."""
    monkeypatch.setenv("GRADTRACKER_HOME", str(tmp_path))
    for k in ("GRADTRACKER_SCORER", "GRADTRACKER_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return load_settings()


def fetcher(ops):
    return lambda s, today, previous, progress=None: ([dict(o) for o in ops], {"test": len(ops)})


def test_first_run_view_asks_for_setup(empty):
    v = Tracker(empty).view()
    assert v["needs_setup"] and v["people"] == [] and v["positions"] == [] and not v["fetched"]


def test_create_person_writes_cv_and_preferences(empty):
    p = create_person(empty, "  Maya  O'Neil ", SAMPLE_CV.read_bytes(), looking_for='data roles: "physics" & more',
                      available_from="September 2027", needs_visa_sponsorship="yes")
    assert p.slug == "maya-o-neil" and p.name == "Maya O'Neil" and p.ready
    assert p.needs_visa_sponsorship is True and p.available_from == "September 2027"
    assert 'data roles: "physics" & more' in p.looking_for
    again = create_person(empty, "Maya O'Neil", SAMPLE_CV.read_bytes())
    assert again.slug == "maya-o-neil-2" and len(load_people(empty)) == 2


@pytest.mark.parametrize("name,cv,message", [
    ("", SAMPLE_CV.read_bytes(), "name"),
    ("Maya", b"not a pdf at all", "PDF"),
    ("Maya", b"%PDF-1.4 broken", "readable"),
])
def test_create_person_rejects_bad_input_and_leaves_nothing_behind(empty, name, cv, message):
    with pytest.raises(ValueError, match=message):
        create_person(empty, name, cv)
    assert list(empty.people_dir.iterdir()) == []


def test_new_person_is_scored_locally_without_any_key(empty, ops):
    t = Tracker(empty, fetcher=fetcher(ops))
    t.add_person("Alex", SAMPLE_CV.read_bytes(), needs_visa_sponsorship="no")
    t.update(progress=lambda m: None, today=TODAY)
    v = t.view(today=TODAY)
    scored = [r for r in v["positions"] if r["score"] is not None]
    assert not v["needs_setup"] and scored and v["meta"].get("scoring_error") is None
    assert all(r["parts"][0]["why"] for r in scored)                # every score explains itself


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def api(empty, ops):
    t = Tracker(empty, fetcher=fetcher(ops))
    server = make_server(t, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call, t
    server.shutdown()
    server.server_close()


def test_api_setup_with_uploaded_cv(api):
    call, t = api
    assert call("/api/data")[1]["needs_setup"]
    code, body = call("/api/people", {"name": "Maya", "cv_base64": base64.b64encode(SAMPLE_CV.read_bytes()).decode(),
                                      "available_from": "Sep 2027", "needs_visa_sponsorship": "no", "refresh": False})
    assert code == 201 and body["slug"] == "maya"
    code, view = call("/api/data")
    assert code == 200 and view["person"]["name"] == "Maya" and view["person"]["ready"]


def test_api_setup_with_sample_cv_and_bad_uploads(api):
    call, _ = api
    assert call("/api/people", {"name": "X", "cv_base64": "!!!"})[0] == 400
    assert call("/api/people", {"name": "X", "cv_base64": base64.b64encode(b"hello").decode()})[0] == 400
    code, body = call("/api/people", {"sample": True, "refresh": False})
    assert code == 201 and body["name"] == "Alex (sample)"


def test_api_refuses_posts_from_other_websites(api):
    call, _ = api
    code, body = call("/api/people", {"sample": True}, headers={"Origin": "https://evil.example"})
    assert code == 403 and "cross-site" in body["error"]
    assert call("/api/people", {"sample": True, "refresh": False}, headers={"Origin": "http://127.0.0.1:8787"})[0] == 201


# ------------------------------------------------------------------ command line
@pytest.fixture
def home(empty, ops, monkeypatch):
    t = Tracker(empty, fetcher=fetcher(ops))
    t.add_person("Alex", SAMPLE_CV.read_bytes(), needs_visa_sponsorship="no")
    t.update(progress=lambda m: None)
    return empty


def test_cli_top_json_is_ranked_and_explained(home, capsys):
    assert cli.main(["top", "--json", "--status", "any", "--include-upcoming", "-n", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    scores = [r["score"] for r in out["roles"]]
    assert out["person"] == "Alex" and scores == sorted(scores, reverse=True) and 0 < len(scores) <= 5
    assert all(r["id"] and r["company"] and "Role fit" in r["why"] for r in out["roles"])


def test_cli_status_show_and_table(home, capsys):
    assert cli.main(["top", "--status", "any", "--include-upcoming", "--json", "-n", "1"]) == 0
    first = json.loads(capsys.readouterr().out)["roles"][0]
    assert cli.main(["status", first["id"], "applied", "--note", "referral"]) == 0
    capsys.readouterr()
    assert cli.main(["show", first["id"]]) == 0
    shown = capsys.readouterr().out
    assert first["company"] in shown and "Role fit" in shown and "note: referral" in shown
    assert cli.main(["top", "--status", "applied", "--include-upcoming"]) == 0
    assert first["company"] in capsys.readouterr().out
    assert cli.main(["show", "no-such-id"]) == 1


def test_cli_add_and_setup_hint(empty, tmp_path, capsys):
    assert cli.main(["top"]) == 1 and "Nobody is set up" in capsys.readouterr().err
    assert cli.main(["add", "Maya", str(SAMPLE_CV), "--visa", "yes", "--available-from", "September 2027"]) == 0
    assert [p.name for p in load_people(empty)] == ["Maya"]
    assert cli.main(["add", "Bad", str(tmp_path / "missing.pdf")]) == 1
