"""New sources, duplicate merging, per-person scoring, and never losing a position you acted on."""

from __future__ import annotations

import json

import pytest

from gradtracker import dedupe, sources
from gradtracker.people import load_people
from gradtracker.scoring import content_hash, default_weights, system_prompt
from gradtracker.service import Tracker
from conftest import TODAY, FakeLLM, add_person, assessor


# ------------------------------------------------------------------ filters
@pytest.mark.parametrize("title,all_levels,keep", [
    ("Software Engineer, New Grad", False, True),
    ("Graduate Quantitative Researcher (2027 Start)", False, True),
    ("Software Engineer", False, False),
    ("Software Engineer", True, True),
    ("Senior Data Scientist", True, False),
    ("Machine Learning Intern", True, False),
    ("Summer Internship 2027", False, False),
    ("Campus Recruiter", False, False),
    ("Software Engineer II", True, False),
])
def test_keep_title(title, all_levels, keep):
    assert sources.keep_title(title, all_levels) is keep


@pytest.mark.parametrize("where,uk", [
    ("London, UK", True), ("Cambridge, England", True), ("Remote - United Kingdom", True),
    ("Cambridge, MA", False), ("New York", False), ("Abingdon", True),
])
def test_is_uk(where, uk):
    assert sources.is_uk(where) is uk


class FakeHTTP:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        for key, body in self.routes.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return FakeResp(body)
        raise AssertionError(f"unexpected url {url}")


class FakeResp:
    def __init__(self, body):
        self.body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_greenhouse_lever_ashby_keep_uk_grad_roles():
    http = FakeHTTP({
        "boards/acme/jobs/1": {"content": "&lt;p&gt;Build simulators&lt;/p&gt;"},
        "boards/acme/jobs": {"jobs": [
            {"id": 1, "title": "Graduate Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
             "location": {"name": "London"}},
            {"id": 2, "title": "Graduate Software Engineer", "absolute_url": "x", "location": {"name": "New York"}},
            {"id": 3, "title": "Staff Engineer", "absolute_url": "x", "location": {"name": "London"}}]},
        "lever.co": [
            {"id": "a" * 36, "text": "Software Engineer, New Grad", "hostedUrl": "https://jobs.lever.co/beta/" + "a" * 36,
             "categories": {"location": "London", "commitment": "Full-time"}, "descriptionPlain": "Go"},
            {"id": "b" * 36, "text": "Software Engineer, New Grad", "hostedUrl": "y",
             "categories": {"location": "London", "commitment": "Intern"}}],
        "ashbyhq.com": {"jobs": [
            {"id": "c", "title": "ML Scientist", "jobUrl": "https://jobs.ashbyhq.com/gamma/c", "location": "Remote",
             "address": {"postalAddress": {"addressCountry": "United Kingdom"}}, "descriptionPlain": "PDEs"},
            {"id": "d", "title": "ML Scientist", "jobUrl": "z", "location": "Paris", "isListed": True}]},
    })
    gh = sources.greenhouse(http, {"name": "Acme", "board": "acme"})
    assert [o["id"] for o in gh] == ["greenhouse:1"] and gh[0]["snippet"] == "Build simulators"
    lv = sources.lever(http, {"name": "Beta", "board": "beta"})
    assert len(lv) == 1 and lv[0]["source"] == "lever" and lv[0]["url"].startswith("https://jobs.lever.co")
    ab = sources.ashby(http, {"name": "Gamma", "board": "gamma", "all_levels": True})
    assert [o["id"] for o in ab] == ["ashby:c"] and "Data Science" in ab[0]["categories"]


JAC_PAGE = """
<div class="j-search-result__result ie-border-left" data-advert-id="111">
  <div class="j-search-result__text"><a href="/job/AB1/ml-data-scientist">ML Data Scientist</a>
  <div class="j-search-result__employer"><b>University of Oxford</b></div>
  <div>Location: Oxford </div></div>
  <span class="j-search-result__date-span j-search-result__date">Closes</span>
  <span class="j-search-result__date-span j-search-result__date--blue ">5 Jan</span>
</div>
<div class="j-search-result__result ie-border-left" data-advert-id="222">
  <div class="j-search-result__text"><a href="/job/AB2/postdoc">Postdoctoral Research Associate</a>
  <div class="j-search-result__employer"><b>UCL</b></div>
  <div>Location: London </div></div>
</div>
<div class="j-search-result__result ie-border-left" data-advert-id="333">
  <div class="j-search-result__text"><a href="/job/AB3/sim">Simulation Engineer</a>
  <div class="j-search-result__employer"><b>Emirates Aviation University</b></div>
  <div>Location: Dubai - United Arab Emirates </div></div>
</div>
<div id="pagination"></div>
"""


def test_parse_jobs_ac_uk_skips_postdocs_abroad_and_infers_year():
    ops = sources.parse_jobs_ac_uk(JAC_PAGE, TODAY)
    assert [o["title"] for o in ops] == ["ML Data Scientist"]
    assert ops[0]["closing"] == "2027-01-05" and ops[0]["company"] == "University of Oxford"
    assert ops[0]["url"] == "https://www.jobs.ac.uk/job/AB1/ml-data-scientist"


def test_fetch_all_keeps_previous_positions_when_a_source_fails(settings, ops):
    (settings.home / "sources.yaml").write_text("trackr: {industries: [Tech]}\n"
                                     "boards: [{name: Acme, ats: greenhouse, board: acme}]\n"
                                     "adzuna: {queries: [graduate]}\n")
    old = {"id": "greenhouse:9", "source": "greenhouse", "feed": "greenhouse:acme", "title": "Grad", "company": "Acme",
           "status": "open"}
    http = FakeHTTP({"boards/acme": ConnectionError("down")})
    got, report = sources.fetch_all(settings, TODAY, previous=[old], http=http,
                                    trackr_fetch=lambda s, today, inds: [dict(o) for o in ops])
    assert old in got and "kept 1" in report["greenhouse:acme"]
    assert report["trackr:Tech"] == len(ops) and report["adzuna"].startswith("off")


# ------------------------------------------------------------------ merging
def op(i, source, company, title, url=None, **kw):
    return {"id": i, "source": source, "company": company, "title": title, "url": url, "status": "open",
            "categories": [], "locations": [], "sources": [{"source": source, "id": i, "url": url}], **kw}


def test_merge_same_role_across_sources_keeps_trackr_id():
    ops = [
        op("gh:1", "greenhouse", "Stripe", "Software Engineer, New Grad", "https://stripe.com/jobs/1?gh_jid=555"),
        op("t1", "trackr", "Stripe", "Software Engineer, New Grad", "https://boards.greenhouse.io/stripe/jobs/555"),
        op("t2", "trackr", "Five Rings", "Campus Full Time 2027 - Software Developer"),
        op("gh:2", "greenhouse", "Five Rings LLC", "Campus Full Time 2027 - Software Developer - London"),
        op("gh:3", "greenhouse", "Five Rings LLC", "Campus Full Time 2027 - Quantitative Trader - London"),
        op("t3", "trackr", "JPMorgan", "Software Engineer Program"),
        op("t4", "trackr", "JPMorgan", "Software Engineer Program"),          # two Trackr entries stay apart
        op("a:1", "ashby", "Faculty", "Machine Learning Engineer"),
        op("a:2", "ashby", "Faculty", "Machine Learning Engineer"),           # same posting in two cities
        op("a:3", "ashby", "Faculty", "Data Scientist"),
    ]
    merged = {o["id"]: o for o in dedupe.merge(ops)}
    assert set(merged) == {"t1", "t2", "gh:3", "t3", "t4", "a:1", "a:3"}
    assert merged["t1"]["aliases"] == ["gh:1"] and {s["source"] for s in merged["t1"]["sources"]} == {"trackr", "greenhouse"}
    assert merged["t2"]["aliases"] == ["gh:2"] and merged["a:1"]["aliases"] == ["a:2"]


def test_share_visa_info_from_trackr():
    ops = [op("t1", "trackr", "Riverlane Ltd", "Graduate Scheme", sponsors_visa="Yes"),
           op("gh:1", "greenhouse", "Riverlane", "Quantum Error Correction Scientist", sponsors_visa="Unknown")]
    dedupe.share_visa_info(ops)
    assert ops[1]["sponsors_visa"] == "Yes"


# ------------------------------------------------------------------ people
def test_people_preferences_and_template(settings):
    add_person(settings, "maya", extra="trackr_industries: [Tech, Engineering]\n"
                                       "exclude: [Engineering:Construction & Infrastructure]\nneeds_visa_sponsorship: no\n")
    (settings.people_dir / "newbie").mkdir()
    people = {p.slug: p for p in load_people(settings)}
    assert list(people)[0] == "alex" and not people["maya"].ready
    assert (settings.people_dir / "newbie" / "person.yaml").exists() and people["newbie"].name == "Newbie"
    maya = people["maya"]
    assert maya.wants({"source": "trackr", "industry": "Engineering", "categories": ["Energy & Utilities"]})
    assert not maya.wants({"source": "trackr", "industry": "Engineering", "categories": ["Construction & Infrastructure"]})
    assert not maya.wants({"source": "trackr", "industry": "Finance", "categories": ["Trading and Quant"]})
    assert not people["alex"].wants({"source": "jobs.ac.uk"})
    assert default_weights(maya)["visa_no"] == 0 and default_weights(people["alex"])["visa_no"] < 0


def test_scoring_prompt_follows_the_person(settings):
    add_person(settings, "maya", extra="looking_for: AI x Physics graduate roles\noff_target: not a data role\n")
    maya = {p.slug: p for p in load_people(settings)}["maya"]
    prompt = system_prompt(maya)
    assert "AI x Physics graduate roles" in prompt and "not a data role" in prompt
    assert "never use this flag" in prompt                      # no available_from -> no early_start cap
    o = {"title": "Data Scientist", "company": "PhysicsX"}
    assert content_hash(o, prompt) != content_hash(o, system_prompt(None))


# ------------------------------------------------------------------ service: people, merges, archive
def make_tracker(settings, listings):
    box = {"ops": listings}
    t = Tracker(settings, llm_factory=lambda s: FakeLLM(fn=assessor),
                fetcher=lambda s, today, previous, progress=None: ([dict(o) for o in box["ops"]], {"test": len(box["ops"])}))
    return t, box


def test_second_person_without_cv_is_listed_but_not_scored(settings, ops):
    add_person(settings, "maya")
    t, _ = make_tracker(settings, ops)
    t.update(progress=lambda m: None, today=TODAY)
    v = t.view("maya", today=TODAY)
    assert not v["person"]["ready"] and all(r["score"] is None for r in v["positions"])
    assert "people/maya/cv.pdf" in v["meta"]["scoring_error"]
    assert t.view(today=TODAY)["person"]["slug"] == "alex"
    t.set_status("032gbd44r2", "applied", "maya")
    assert t.view("alex", today=TODAY)["counts"]["applied"] == 0      # statuses are per person
    assert t.view("maya", today=TODAY)["counts"]["applied"] == 1


def test_status_follows_a_role_when_trackr_lists_it_later(settings):
    board = op("greenhouse:1", "greenhouse", "PhysicsX", "Graduate ML Engineer",
               "https://boards.greenhouse.io/physicsx/jobs/1")
    t, box = make_tracker(settings, [board])
    t.update(progress=lambda m: None, today=TODAY)
    t.set_status("greenhouse:1", "applied")
    t.set_note("greenhouse:1", "referral from Ali")
    box["ops"] = [board, op("trk1", "trackr", "PhysicsX", "Graduate ML Engineer 2027", industry="Tech")]
    t.update(progress=lambda m: None, today=TODAY)
    rows = {r["id"]: r for r in t.view(today=TODAY)["positions"]}
    assert set(rows) == {"trk1"}
    assert rows["trk1"]["my_status"] == "applied" and rows["trk1"]["note"] == "referral from Ali"
    assert {s["source"] for s in rows["trk1"]["sources"]} == {"trackr", "greenhouse"}


def test_applied_position_stays_visible_after_its_listing_disappears(settings, ops):
    t, box = make_tracker(settings, ops)
    t.update(progress=lambda m: None, today=TODAY)
    t.set_status("032gbd44r2", "applied")
    box["ops"] = [o for o in ops if o["id"] != "032gbd44r2"]
    t.update(progress=lambda m: None, today=TODAY)
    rows = {r["id"]: r for r in t.view(today=TODAY)["positions"]}
    assert rows["032gbd44r2"]["removed"] and rows["032gbd44r2"]["my_status"] == "applied"
    assert rows["032gbd44r2"]["company"] == "Acturis"
    t.set_status("032gbd44r2", "interviewing")                          # still editable
    assert t.view(today=TODAY)["counts"]["applied"] == 1


def test_scoring_failure_is_reported_not_silent(settings, ops):
    from gradtracker.llm import LLMError

    class Broke(FakeLLM):
        def json(self, messages, **kw):
            raise LLMError("endpoint rejected the request (403): insufficient user quota")
    t = Tracker(settings, llm_factory=lambda s: Broke(),
                fetcher=lambda s, today, previous, progress=None: ([dict(o) for o in ops], {}))
    t.update(progress=lambda m: None, today=TODAY)
    err = t.view(today=TODAY)["meta"]["scoring_error"]
    assert "not scored" in err and "insufficient user quota" in err


# ------------------------------------------------------------------ graduate job boards
@pytest.mark.parametrize("text,iso", [
    ("November 16th, 2026", "2026-11-16"), ("30th November 2026", "2026-11-30"), ("1 Dec 2026", "2026-12-01"),
    ("Ongoing", None), (None, None),
])
def test_parse_long_date(text, iso):
    assert sources.parse_long_date(text) == iso


GC_PAGE = """
<article wire:key="hp" aria-hidden="true"><h2><a href="https://www.gradcracker.com/hub/9000001/featured-graduate-employer/graduate-job/1/graduate-software-engineer" rel="nofollow">Graduate Software Engineer</a></h2></article>
<article wire:key="87052" data-search-result="87052" class="x"><figure><a href="https://www.gradcracker.com/hub/294/wtw"><img alt="WTW" /></a></figure>
<h2><a href="https://www.gradcracker.com/hub/294/wtw/graduate-job/87052/2027-software-developer" class="y">2027 Software Developer Graduate Programme</a></h2>
<dt>Deadline</dt><dd>November 20th, 2026</dd><dt>Location</dt><dd>Reigate (Surrey)</dd><dt>Starting</dt><dd>September 2027</dd></article>
<article wire:key="1" data-search-result="1"><h2><a href="https://www.gradcracker.com/hub/5/acme/graduate-job/1/summer-internship">Software Summer Internship</a></h2></article>
"""


def test_parse_gradcracker_skips_the_hidden_decoy_and_internships():
    ops = sources.parse_gradcracker(GC_PAGE)
    assert [(o["id"], o["company"], o["closing"], o["locations"]) for o in ops] == [
        ("gradcracker:87052", "WTW", "2026-11-20", ["Reigate (Surrey)"])]


def test_parse_higherin_reads_embedded_results():
    page = ('<script>window.__RMP_SEARCH_RESULTS_INITIAL_STATE__ = {"data":[{"id":46520,"jobTitle":"Technology '
            'Consultant Graduate Programme 2027","jobTypeName":"Graduate Scheme","deadline":"30th November 2026",'
            '"url":"https://higherin.com/jobs/46520/x/y","jobLocationNames":"Hatfield, London","companyName":"Computacenter"}],'
            '"meta":{"totalResults":1}};window.__OTHER__ = 1</script>')
    ops, total = sources.parse_higherin(page)
    assert total == 1 and ops[0]["id"] == "higherin:46520" and ops[0]["closing"] == "2026-11-30"


def test_parse_shecancode_keeps_uk_junior_roles_only():
    item = ('<li class="post-{p} job_listing x"><a href="https://shecancode.io/job/{p}/"><div class="position">'
            '<h3>{t}</h3><div class="company"><strong>{c}</strong></div></div><div class="location">{l}</div>'
            '<ul class="meta"><li class="date"><time datetime="2026-11-12">x</time></li></ul></a></li>')
    html_ = (item.format(p=1, t="Junior Software Engineer", c="Acme", l="London, UK")
             + item.format(p=2, t="Senior Engineer", c="Acme", l="London")
             + item.format(p=3, t="Graduate Developer", c="Acme", l="Anywhere"))
    assert [o["id"] for o in sources.parse_shecancode(html_)] == ["shecancode:1"]


def test_targetjobs_pages_through_results():
    class R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    class HTTP:
        calls = 0

        def post(self, url, json, headers, timeout):
            HTTP.calls += 1
            assert headers["Origin"] == "https://targetjobs.co.uk"
            docs = [{"nid": json["offset"] + 1, "title": "Graduate Software Engineer", "path": "/jobs/x",
                     "location": "London", "organisation": {"title": "Acme"}, "applicationDeadline": 1794614400}]
            return R({"search": {"result_count": 2, "documents": docs}})
    ops = sources.targetjobs(HTTP(), {"sectors": ["Technology"]})
    assert HTTP.calls == 2 and [o["id"] for o in ops] == ["targetjobs:1", "targetjobs:2"]
    assert ops[0]["url"] == "https://targetjobs.co.uk/jobs/x" and ops[0]["closing"] == "2026-11-14"


def test_targetjobs_reads_a_hidden_employer_from_the_job_page(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)

    class R:
        ok = True
        text = '<script>{"hiringOrganization":{"@type":"Organization","name":"Seagate Technology"}}</script>'
        def __init__(self, data=None): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    class HTTP:
        def post(self, url, json, headers, timeout):
            docs = [{"nid": 7, "title": "Graduate Engineer", "path": "/jobs/y", "location": "Derry", "organisation": None}]
            return R({"search": {"result_count": 1, "documents": docs}})

        def get(self, url, headers, timeout):
            assert url == "https://targetjobs.co.uk/jobs/y"
            return R()
    [op] = sources.targetjobs(HTTP(), {})
    assert op["company"] == "Seagate Technology"
