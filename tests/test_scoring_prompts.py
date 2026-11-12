"""Explainable score formula, assessment batching/caching, and the cover-letter prompt."""

from __future__ import annotations

import json

from gradtracker.profile import public_facts
from gradtracker.prompts import BANNED, build_prompt, letter_need
from gradtracker.scoring import DEFAULT_WEIGHTS, assess, compute, content_hash, needs_assessment
from gradtracker.store import State
from conftest import PROFILE, assessor

A = {"role_fit": 90, "competitiveness": 80, "career_value": 70, "location": "london", "flags": [],
     "why_fit": "backend", "why_competitive": "strong", "why_career": "brand"}


def test_compute_weighted_base_and_adjustments():
    op = {"sponsors_visa": "Yes"}
    r = compute(op, A, DEFAULT_WEIGHTS)
    base = (90 * 50 + 80 * 20 + 70 * 30) / 100
    assert r["base"] == round(base, 1)
    assert r["score"] == round(base + DEFAULT_WEIGHTS["visa_yes"] + DEFAULT_WEIGHTS["loc_london"])
    labels = [p["label"] for p in r["parts"]]
    assert labels[:3] == ["Role fit", "Competitiveness", "Career value"] and "Sponsors visa" in labels
    assert sum(p["weight"] for p in r["parts"] if p["kind"] == "dim") == 100


def test_compute_visa_no_and_abroad_penalties():
    r = compute({"sponsors_visa": "No"}, dict(A, location="abroad"), DEFAULT_WEIGHTS)
    assert r["score"] == round(r["base"] + DEFAULT_WEIGHTS["visa_no"] + DEFAULT_WEIGHTS["loc_abroad"])


def test_compute_caps_apply_and_are_explained():
    r = compute({"sponsors_visa": "Yes"}, dict(A, flags=["internship", "early_start"]), DEFAULT_WEIGHTS,
                {"early_start": "starts before Sep 2027"})
    assert r["score"] == DEFAULT_WEIGHTS["cap_internship"]
    assert {c["why"] for c in r["caps"]} == {"internship / placement / insight programme", "starts before Sep 2027"}


def test_legacy_non_engineering_flag_still_caps():
    r = compute({"sponsors_visa": "Yes"}, dict(A, flags=["non_engineering"]), DEFAULT_WEIGHTS)
    assert r["score"] == DEFAULT_WEIGHTS["cap_off_target"] and r["caps"][0]["why"] == "not what you're looking for"


def test_user_weights_change_ranking():
    career_heavy = dict(DEFAULT_WEIGHTS, w_role_fit=0, w_competitiveness=0, w_career_value=100)
    a1 = dict(A, role_fit=95, career_value=40)
    a2 = dict(A, role_fit=60, career_value=95)
    op = {"sponsors_visa": "Yes"}
    assert compute(op, a1, DEFAULT_WEIGHTS)["score"] > compute(op, a2, DEFAULT_WEIGHTS)["score"]
    assert compute(op, a1, career_heavy)["score"] < compute(op, a2, career_heavy)["score"]


def test_compute_without_assessment():
    assert compute({}, None, DEFAULT_WEIGHTS)["score"] is None


def test_assess_batches_caches_and_rescores_on_change(settings, ops, fake_llm):
    state = State(settings.state_file)
    llm = fake_llm(fn=assessor)
    many = [dict(ops[0], id=f"p{i}", title=("Intern " if i == 3 else "Grad ") + str(i)) for i in range(15)]
    assert assess(many, "CV", llm, state) == 15
    assert len(llm.calls) == 2                                  # batches of 12
    assert state.assessment("p3")["flags"] == ["internship"]
    assert assess(many, "CV", llm, state) == 0 and len(llm.calls) == 2   # unchanged -> no calls
    changed = dict(many[0], notes="now also in Edinburgh")
    assert needs_assessment(changed, state)
    assert assess([changed], "CV", llm, state) == 1
    assert state.assessment("p0")["hash"] == content_hash(changed)


def test_assess_ignores_garbage_from_model(settings, ops, fake_llm):
    state = State(settings.state_file)
    llm = fake_llm(responses=[json.dumps([{"id": "032gbd44r2", "role_fit": "lots", "competitiveness": 250,
                                           "career_value": -3, "location": "mars", "flags": ["bogus", "internship"]},
                                          {"id": "unknown"}])])
    assess([ops[0]], "CV", llm, state)
    a = state.assessment("032gbd44r2")
    assert (a["role_fit"], a["competitiveness"], a["career_value"]) == (0, 100, 0)
    assert a["location"] == "unknown" and a["flags"] == ["internship"]


def test_letter_need():
    assert letter_need({"cover_letter": "Yes"}) == "required"
    assert letter_need({"cover_letter": "Optional"}) == "optional"
    assert letter_need({"cover_letter": "No"}) is None
    assert letter_need({}) == "unknown"


def test_build_prompt_content(ops):
    op = dict(ops[0], categories=["AI and Machine Learning"])
    p = build_prompt(op, "CV TEXT: raised throughput 54x; 1M+ events/day", public_facts(PROFILE))
    assert "Acturis" in p and op["title"] in p and "54x" in p and "1M+" in p
    assert "ML-platform version" in p
    assert "September 2027" in p and "Graduate visa" in p
    assert "never turn \"1M+\" into \"millions\"" in p
    assert all(b in p for b in ("delve", "passionate", "I am writing to express"))
    assert "Output only the letter." in p.splitlines()[-1]
    assert len(BANNED) >= 20


def test_prompt_dedupes_area_and_omits_contact_details(ops):
    op = dict(ops[0], categories=["Software Engineering"], divisions=["Software Engineering", "Back-end"])
    p = build_prompt(op, "CV", public_facts(PROFILE))
    assert "- Area: Software Engineering, Back-end" in p
    assert "alex.morgan@example.com" not in p and "+44" not in p
