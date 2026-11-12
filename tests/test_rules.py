"""Local scoring without any LLM: CV detection, rules, roles/domains, and agreement with the old LLM scores."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from gradtracker import cvprofile, roles, rules
from gradtracker.people import load_people
from gradtracker.scoring import compute, default_weights
from gradtracker.service import Tracker
from conftest import TODAY, add_person

ROOT = Path(__file__).resolve().parent.parent
EMPLOYERS = ROOT / "gradtracker" / "defaults" / "employers.yaml"
BENCHMARK = ROOT / "tests" / "fixtures" / "benchmark"

PHYSICS_CV = """Jane Doe
EDUCATION
MSc Physics, Imperial College London 2025 - 2026
BSc Physics, University of Manchester 2022 - 2025
EXPERIENCE
Research Intern, UK Atomic Energy Authority  Jun 2025 - Sep 2025
Built PyTorch surrogate models of plasma turbulence; Monte Carlo uncertainty quantification.
Data Science Intern, Met Office  Jul 2024 - Sep 2024
Weather forecast post-processing with machine learning, Python, pandas, statistics.
SKILLS
Python, PyTorch, NumPy, pandas, scikit-learn, machine learning, statistics, computational physics, simulation, CFD
"""


def op(i, company, title, categories=(), industry="Tech", **kw):
    return {"id": i, "company": company, "title": title, "categories": list(categories), "industry": industry,
            "source": kw.pop("source", "trackr"), "status": "open", "locations": ["UK|London|London"], **kw}


# ------------------------------------------------------------------ CV detection
def test_experience_counts_jobs_not_education_and_halves_internships():
    cv = ("EXPERIENCE\nData Analyst, Acme  June 2023 - Dec 2024\nSummer Intern, Beta  Jun 2022 – Sep 2022\n"
          "Software Engineer (part-time)MarketplaceDec 2025 – Nov 2026\n"
          "EDUCATION\nBSc Physics, University of Leeds 2019 - 2022\nRESEARCH EXPERIENCE\nThesis project 2021 - 2022")
    years = cvprofile.experience_years(cv)
    assert 2.1 <= years <= 2.3            # 1.6 + 0.17 (intern, half) + 0.5 (part-time, half); no "Mar 2025" misread


def test_degree_skills_and_categories_from_cv():
    assert cvprofile.highest_degree(PHYSICS_CV) == "msc"
    assert cvprofile.highest_degree("PhD in Astrophysics, UCL") == "phd"
    skills = cvprofile.main_skills(PHYSICS_CV)
    assert "machine learning" in skills and "pytorch" in skills
    tf = rules.TfIdf([PHYSICS_CV] + list(cvprofile.CATEGORY_PROTOTYPES.values()))
    fit = cvprofile.category_fit(PHYSICS_CV, tf)
    assert fit["AI and Machine Learning"] > fit["Construction & Infrastructure"]
    assert max(fit.values()) == 85 and min(fit.values()) >= 35


# ------------------------------------------------------------------ roles and domains
@pytest.mark.parametrize("title,cats,expected", [
    ("Graduate Software Engineer", ["Software Engineering"], "swe"),
    ("Graduate Data Scientist", [], "data"),
    ("Graduate Quantitative Researcher (2027 Start)", [], "quant_research"),
    ("Quantum Error Correction Researcher", ["Research"], "science"),
    ("Graduate Civil Engineer", ["Construction & Infrastructure"], "engineering"),
    ("Graduate Hires", ["Consulting"], "consulting"),
])
def test_role_types(title, cats, expected):
    assert expected in roles.role_types({"title": title, "categories": cats})


def test_quantum_is_not_quant():
    assert "quant_research" not in roles.role_types({"title": "Quantum Scientist", "categories": []})
    assert "finance" not in roles.domains({"title": "Quantum Scientist", "company": "Riverlane"})


def test_domains_and_typed_domain_names():
    emp = rules.Employers(EMPLOYERS)
    assert "physics" in emp.domains(op("1", "PhysicsX", "Machine Learning Engineer"))       # by employer
    assert "physics" in emp.domains(op("2", "Acme", "Graduate CFD Engineer"))               # by title
    assert "finance" in emp.domains(op("3", "Acme", "Graduate Analyst", ["Trading and Quant"], "Finance"))
    assert "physics" not in emp.domains(op("4", "Uni", "Research Associate in Physical Activity"))
    assert roles.resolve_domain("Physics") == "physics" and roles.resolve_domain("quantum") == "physics"
    assert roles.resolve_domain("banking") == "finance" and roles.resolve_domain("knitting") is None


# ------------------------------------------------------------------ scoring
@pytest.fixture
def friend(settings):
    add_person(settings, "maya", extra=(
        "available_from: September 2027\nlooking_for: graduate data science, preferably physics\n"
        "scoring:\n  preferred_domains: [physics]\n  great: [data scientist, simulation]\n"
        "  poor: [sales, civil engineer]\n  eligible_groups: [women]\n  employer_fields: {science: 12}\n"))
    return {p.slug: p for p in load_people(settings)}["maya"]


def scores(person, cv, ops):
    sc = rules.Scorer(person, cv, ops, rules.Employers(EMPLOYERS))
    w = default_weights(person)
    return {o["id"]: (compute(o, a := sc.assess(o), w)["score"], a) for o in ops}


def test_physics_data_scientist_ranks_physics_roles_first(friend):
    ops = [op("phys", "PhysicsX", "Graduate Data Scientist - Simulation", source="greenhouse"),
           op("ds", "Tesco", "Graduate Data Scientist", ["Data Science"]),
           op("swe", "Tesco", "Graduate Software Engineer", ["Software Engineering"]),
           op("civil", "Skanska", "Graduate Civil Engineer", ["Construction & Infrastructure"], "Engineering"),
           op("sales", "Acme", "Graduate Sales Executive")]
    s = scores(friend, PHYSICS_CV, ops)
    order = sorted(s, key=lambda k: -s[k][0])
    assert order[:2] == ["phys", "ds"] and set(order[-2:]) == {"civil", "sales"}
    assert "preferred domain" in s["phys"][1]["why_fit"]
    assert "off_target" in s["sales"][1]["flags"]


def test_flags_seniority_start_date_and_eligibility(friend):
    sep = dataclasses.replace(friend, scoring=dict(friend.scoring, eligible_groups=[]))
    ops = [op("senior", "Acme", "Senior Data Scientist"),
           op("phd", "HRT", "Algorithm Developer – 2027 PhDs"),
           op("june", "NCR", "Graduate Data Scientist - June 2027 Start"),
           op("aug", "Expedia", "Graduate Data Scientist - August 2027"),
           op("women", "Optiver", "Women in Tech Data Programme"),
           op("intern", "Acme", "Data Science Summer Internship")]
    s = scores(sep, PHYSICS_CV, ops)
    flags = {k: set(v[1]["flags"]) for k, v in s.items()}
    assert "too_senior" in flags["senior"] and "too_senior" in flags["phd"]
    assert "early_start" in flags["june"] and "early_start" not in flags["aug"]
    assert "off_target" in flags["women"] and "internship" in flags["intern"]
    assert "off_target" not in {f for f in scores(friend, PHYSICS_CV, ops[4:5])["women"][1]["flags"]}


# ------------------------------------------------------------------ agreement with the old LLM scores
def _spearman(a, b):
    def rank(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5


@pytest.mark.parametrize("configured,minimum", [(True, 0.78), (False, 0.6)])
def test_local_scores_agree_with_llm_benchmark(configured, minimum, settings):
    """290 roles an LLM scored for one real CV: the local ranking must stay close to it."""
    from gradtracker.cv import cv_text
    from gradtracker.people import load_person
    person = load_person(BENCHMARK, settings)
    if not configured:
        person = dataclasses.replace(person, scoring={})
    bench = json.loads((BENCHMARK / "llm_scores.json").read_text())
    ops = [dict(r, source="trackr", snippet="", status="open") for r in bench]
    sc = rules.Scorer(person, cv_text(str(person.cv)), ops, rules.Employers(EMPLOYERS))
    w = default_weights(person)
    ours = [compute(o, sc.assess(o), w)["score"] for o in ops]
    llm = [compute(o, r["llm"], w)["score"] for o, r in zip(ops, bench)]
    assert _spearman(ours, llm) >= minimum


# ------------------------------------------------------------------ service
def test_tracker_scores_locally_and_rescores_when_preferences_change(settings, ops):
    settings.scorer = "rules"
    folder = add_person(settings, "maya", cv=settings.cv_pdf, extra="looking_for: software engineering\n")
    t = Tracker(settings, llm_factory=lambda s: (_ for _ in ()).throw(AssertionError("no LLM allowed")),
                fetcher=lambda s, today, previous, progress=None: ([dict(o) for o in ops], {"test": len(ops)}))
    t.update(progress=lambda m: None, today=TODAY)
    v = t.view("maya", today=TODAY)
    row = next(r for r in v["positions"] if r["id"] == "032gbd44r2")
    assert row["score"] is not None and row["roles"] and v["role_types"] and v["domain_types"]
    assert (folder / "detected.yaml").exists() and "experience_years" in (folder / "detected.yaml").read_text()
    before = row["score"]
    (folder / "person.yaml").write_text((folder / "person.yaml").read_text() +
                                        "scoring:\n  poor: [developer, engineer, software]\n")
    after = next(r for r in t.view("maya", today=TODAY)["positions"] if r["id"] == "032gbd44r2")["score"]
    assert after < before                                 # no refresh needed: edits apply on the next page load
