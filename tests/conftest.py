from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
RAW = json.loads((FIXTURES / "trackr_sample.json").read_text())
TODAY = date(2026, 10, 20)

# A fictional profile.yaml: the sensitive parts must never reach a prompt.
PROFILE = {
    "personal": {"first_name": "Alex", "last_name": "Morgan", "email": "alex.morgan@example.com",
                 "phone": "+44 7000 000000", "city": "Manchester"},
    "work_authorisation": {"after_graduation": "Eligible for the UK Graduate visa from September 2027"},
    "education": {"current_degree": "MSc Computer Science", "graduation_date": "September 2027"},
    "availability": {"earliest_start_date": "September 2027"},
    "extra_facts": ["Winner of a university hackathon in 2024."],
}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADTRACKER_HOME", str(tmp_path))
    monkeypatch.setenv("GRADTRACKER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("GRADTRACKER_LLM_MODEL", "test-model")
    from gradtracker.config import SAMPLE_CV, load_settings
    s = load_settings()
    s.default_person = "alex"
    s.cv_pdf = SAMPLE_CV                               # the fictional CV shipped with the package
    s.state_file = s.data_dir / "tracker.json"
    s.scorer = "llm"  # these tests drive the optional LLM path with a fake model; test_rules.py covers local scoring
    add_person(s, "alex", cv=s.cv_pdf, extra="needs_visa_sponsorship: yes\navailable_from: Sep 2027\n"
                                            "trackr_industries: [Tech, Finance]\nskip_sources: [jobs.ac.uk]\n")
    return s


def add_person(s, slug, cv=None, extra=""):
    folder = s.people_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "person.yaml").write_text(f"name: {slug.title()}\ncv: {cv or 'cv.pdf'}\n{extra}", encoding="utf-8")
    return folder


@pytest.fixture
def ops():
    from gradtracker import trackr
    out = [trackr.normalize(r) for r in RAW]
    for o in out:
        o["status"] = trackr.status_of(o, TODAY)
    return out


class FakeLLM:
    """Deterministic stand-in for gradtracker.llm.LLM."""

    def __init__(self, responses=None, fn=None):
        self.responses = list(responses or [])
        self.fn = fn
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.fn(messages) if self.fn else self.responses.pop(0)

    def json(self, messages, **kw):
        from gradtracker.llm import extract_json
        return extract_json(self.chat(messages))

    def stats(self):
        return f"{len(self.calls)} fake calls"


def assessor(messages):
    """Fake model: scores every programme in the batch from its title."""
    progs = json.loads(messages[1]["content"].split("PROGRAMMES:\n", 1)[1])
    out = []
    for p in progs:
        intern = "intern" in p["title"].lower()
        out.append({"id": p["id"], "role_fit": 90, "competitiveness": 80, "career_value": 70, "location": "london",
                    "flags": ["internship"] if intern else [], "why_fit": "backend role", "why_competitive": "strong",
                    "why_career": "good brand"})
    return json.dumps(out)


@pytest.fixture
def fake_llm():
    return FakeLLM
