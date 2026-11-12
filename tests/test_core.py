"""Trackr normalisation, LLM client (retries, cache, JSON), state and profile."""

from __future__ import annotations

import copy
from datetime import date
from types import SimpleNamespace

import pytest

from gradtracker import trackr
from gradtracker.llm import LLM, LLMError, extract_json
from gradtracker.profile import load_profile, public_facts
from gradtracker.store import State
from conftest import PROFILE, RAW, TODAY


# ------------------------------------------------------------------ trackr
def test_normalize_and_clean_url():
    op = trackr.normalize(RAW[0])
    assert op["company"] == "Acturis" and op["divisions"] == ["Back-end"] and op["closing"] == "2026-12-21"
    assert "utm_" not in op["url"] and "source=" not in op["url"] and "ref=42" in op["url"]
    assert op["cover_letter"] == "Optional" and op["sponsors_visa"] == "No"


def test_status_of_open_closed_upcoming():
    by_id = {r["id"]: trackr.normalize(r) for r in RAW}
    assert trackr.status_of(by_id["032gbd44r2"], TODAY) == "open"
    assert trackr.status_of(by_id["closed01"], TODAY) == "closed"
    assert trackr.status_of(by_id["future01"], TODAY) == "upcoming"


def test_fetch_uses_api(settings, monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"programmes": RAW, "groups": []}
    monkeypatch.setattr(trackr.requests.Session, "get", lambda self, *a, **k: Resp())
    ops = trackr.fetch(settings, today=TODAY)
    assert ops[0]["status"] == "open" and len(ops) == 3


def test_fetch_raises_after_retries(settings, monkeypatch):
    def boom(self, *a, **k):
        raise trackr.requests.ConnectionError("offline")
    monkeypatch.setattr(trackr.requests.Session, "get", boom)
    with pytest.raises(RuntimeError, match="could not fetch"):
        trackr.fetch_raw(settings)


# ------------------------------------------------------------------ llm
@pytest.mark.parametrize("text,expected", [
    ('[{"id": "a", "score": 80}]', [{"id": "a", "score": 80}]),
    ('```json\n{"x": 1}\n```', {"x": 1}),
    ('Sure! Here it is: [1, 2, 3] hope that helps', [1, 2, 3]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_rejects_prose():
    with pytest.raises(ValueError):
        extract_json("no json here")


class FlakyClient:
    def __init__(self, outputs):
        self.outputs, self.kwargs = list(outputs), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.kwargs.append(kw)
        out = self.outputs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))],
                               usage=SimpleNamespace(cost=0.001))


def test_llm_retries_empty_then_succeeds_and_caches(settings):
    client = FlakyClient(["", "  ", "hello"])
    settings.reasoning_effort = "low"
    llm = LLM(settings, client=client, sleep=lambda s: None)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hello"
    assert len(client.kwargs) == 3 and client.kwargs[0]["reasoning_effort"] == "low"
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hello"  # disk cache
    assert len(client.kwargs) == 3 and llm.cache_hits == 1
    assert "3 API calls" in llm.stats()


def test_llm_gives_up_after_max_attempts(settings):
    settings.llm_max_attempts = 3
    with pytest.raises(LLMError):
        LLM(settings, client=FlakyClient(["", "", ""]), sleep=lambda s: None).chat([{"role": "user", "content": "x"}])


def test_llm_json_repairs_once(settings):
    llm = LLM(settings, client=FlakyClient(["not json", '{"ok": true}']), sleep=lambda s: None)
    assert llm.json([{"role": "user", "content": "give json"}]) == {"ok": True}


def test_llm_requires_key(settings):
    settings.api_key = ""
    with pytest.raises(LLMError):
        LLM(settings)


# ------------------------------------------------------------------ state / profile
def test_state_roundtrip_and_validation(settings):
    st = State(settings.state_file)
    assert st.mark_seen(["a", "b"], "2026-10-20") == ["a", "b"]
    assert st.mark_seen(["a", "c"], "2026-10-21") == ["c"]
    st.set_status("a", "applied")
    st.set_note("a", "referral from Amir")
    st.save()
    st2 = State(settings.state_file)
    assert st2.status("a") == "applied" and st2.status("zzz") == "todo"
    assert st2.data["positions"]["a"]["note"] == "referral from Amir"
    assert st2.data["positions"]["a"]["first_seen"] == "2026-10-20"
    with pytest.raises(ValueError):
        st2.set_status("a", "maybe")


def test_public_facts_never_leak_sensitive_fields(settings):
    prof = copy.deepcopy(PROFILE)
    assert load_profile(settings.home / "missing.yaml") == {}
    prof["personal"]["address_line_1"] = "10 Secret Road"
    prof["personal"]["nationality"] = "Secretland"
    prof["preferences"] = {"salary_expectation": "£99,999"}
    prof["diversity"] = {"gender": "Prefer not to say"}
    facts = str(public_facts(prof))
    for secret in ("Secret Road", "Secretland", "99,999", "Prefer not to say"):
        assert secret not in facts
    assert "September 2027" in facts and "hackathon" in facts


def test_state_picks_up_changes_from_another_process(settings):
    import os
    server_view = State(settings.state_file)
    server_view.set_status("a", "applied")
    server_view.save()
    cli = State(settings.state_file)                 # e.g. `grad-tracker update` in another terminal
    cli.set_assessment("a", {"role_fit": 90})
    cli.save()
    st = os.stat(settings.state_file)
    os.utime(settings.state_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # make the change visible
    server_view.sync()
    server_view.set_note("a", "still here")
    server_view.save()
    final = State(settings.state_file)
    assert final.assessment("a") == {"role_fit": 90}     # not overwritten by the stale copy
    assert final.status("a") == "applied" and final.data["positions"]["a"]["note"] == "still here"
