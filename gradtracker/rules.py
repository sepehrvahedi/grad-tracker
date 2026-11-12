"""Local scorer: rates every position for a person with rules and text similarity. No LLM, no network.

It produces the same assessment as the old LLM scorer (role fit, competitiveness, career value, location, red
flags and a one-line reason for each), so the score formula, weights and UI are unchanged.

* Role fit: the person's fit for the role's categories (person.yaml `scoring.category_fit`), plus or minus the
  phrases they listed as great / good / weak / poor, plus how similar the listing is to their CV and to what they
  said they are looking for (TF-IDF cosine), plus a bonus for employer fields they like.
* Competitiveness: how selective the employer is (employers.yaml tier), adjusted for fit, seniority, degree
  and security-clearance requirements.
* Career value: employer reputation (tier) blended with role fit and field bonuses.
Tuned against 290 positions the LLM had scored (tests/fixtures/llm_benchmark.json).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import cvprofile, roles
from .dedupe import company_key
from .sources import ABROAD, categorize, is_uk

RULES_VERSION = "r1"

# tier -> (career prestige, competitiveness base: higher = easier to get in)
TIERS = {"elite": (95, 64), "top": (85, 72), "strong": (73, 79), "standard": (58, 85), "weak": (35, 90)}
TRACKR_HINTS = {  # tier/field for employers not in employers.yaml, from Trackr's categories
    "Trading and Quant": ("strong", "quant"), "Buy-Side": ("strong", "quant"), "Quant Developer": ("standard", "quant"),
    "Bulge Bracket": ("strong", "bank"), "Elite Boutique": ("strong", "bank"), "Asset Management": ("standard", "bank"),
    "Big 4": ("standard", "consulting"), "Tech Consulting": ("standard", "consulting"),
    "Scientific Research & Health": ("standard", "science"), "Energy & Utilities": ("standard", "energy"),
    "Aerospace & Defence": ("standard", "defence"), "AI and Machine Learning": ("standard", "ai"),
}
GENERIC_CATEGORY_FIT = {"Software Engineering": 60, "Data Science": 60, "AI and Machine Learning": 60, "Quant": 60,
                        "Quant Developer": 60, "Research": 55}
KEYWORD_POINTS = {"great": 20, "good": 10, "weak": -15, "poor": -30}
SKILL_POINTS = 6  # each of the CV's main skills found in a listing (unless person.yaml lists it already)

GRADUATE = re.compile(r"\b(graduate|grad|new grad|junior|entry|campus|trainee|analyst programme|early careers?|20[23]\d)\b", re.I)
GRAD_LABEL_BONUS = 4  # a role that says it is for graduates beats a look-alike that does not say who it is for
SENIOR = re.compile(r"\b(senior|sr|staff|principal|lead|manager|director|head|architect|ii|iii)\b", re.I)
PHD = re.compile(r"\bph\.?d\.?s?\b|\bdoctora", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d+\s*)?years?(?:'|’)?\s+(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+)?"
                   r"(?:experience|exp\b)", re.I)
CLEARANCE = re.compile(r"security clearance|\bsc clear|\bdv clear|developed vetting|national security|"
                       r"british (?:citizen|national)s? only|uk nationals? only|sole uk national|eligible for sc\b", re.I)
INTERN = re.compile(r"\b(intern|interns|internships?|placements?|summer|spring week|insight|off[\s-]?cycle|"
                    r"industrial year|work experience|kickstarter|discovery (?:day|programme)|open day|taster)\b", re.I)
# programmes only some people can apply to; person.yaml `scoring.eligible_groups` lists the ones that apply
RESTRICTED = {"women": re.compile(r"\b(women|woman|female|she\+?|girls?)\b", re.I),
              "ethnic minority": re.compile(r"\b(black|bame|ethnic minorit\w*|people of colou?r)\b", re.I),
              "neurodivergent": re.compile(r"\bneuro\s?diver\w*", re.I),
              "lgbtq": re.compile(r"\blgbt\w*\+?", re.I),
              "disability": re.compile(r"\b(disabilit\w*|disabled)\b", re.I),
              "social mobility": re.compile(r"\bsocial mobility\b", re.I),
              "veterans": re.compile(r"\b(veterans?|armed forces|service leavers?)\b", re.I)}
TIER_FIT = {"elite": 8, "top": 5, "strong": 0, "standard": -7, "weak": -12}  # engineering quality shows in the work
MONTH_YEAR = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d\d)\b", re.I)
MONTHS3 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
WORD = re.compile(r"[a-z][a-z0-9+#]{1,}")
STOP = set("""a an the and or of to in for on with at by from as is are be will you your our we us this that it its
our their they them he she his her can may such into about over under who what which when where how than then
also more most other some any all not no yes using use used work working role team teams join including
across within per new including etc via based help make strong good great well able""".split())


# ---------------------------------------------------------------- text similarity
def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall((text or "").lower()) if w not in STOP]


class TfIdf:
    def __init__(self, docs: list[str]):
        df = Counter()
        for d in docs:
            df.update(set(tokens(d)))
        n = max(1, len(docs))
        self.idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
        self.default_idf = math.log(1 + n) + 1

    def vec(self, text: str) -> dict:
        tf = Counter(tokens(text))
        v = {w: (1 + math.log(c)) * self.idf.get(w, self.default_idf) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    @staticmethod
    def cosine(a: dict, b: dict) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(w, 0.0) for w, x in a.items())


def job_text(op: dict) -> str:
    return " ".join([op.get("title", "")] * 3 + [" ".join(op.get("categories") or []),
                    " ".join(op.get("divisions") or []), op.get("snippet") or "", op.get("notes") or ""])


# ---------------------------------------------------------------- employers
class Employers:
    def __init__(self, path: Path | None):
        self.by_key: dict = {}
        self.domain_by_key: dict = {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path and path.exists() else {}
        for domain, names in ((data or {}).pop("domains", None) or {}).items():
            for name in names or []:
                self.domain_by_key.setdefault(company_key(str(name)), set()).add(domain)
        for tier, fields in (data or {}).items():
            for field, names in (fields or {}).items():
                for name in names or []:
                    self.by_key.setdefault(company_key(str(name)), (tier, field))

    def domains(self, op: dict) -> list[str]:
        return roles.domains(op, self.domain_by_key.get(company_key(op.get("company", ""))))

    def lookup(self, op: dict) -> tuple[str, str | None]:
        hit = self.by_key.get(company_key(op.get("company", "")))
        if hit:
            return hit
        for c in op.get("categories") or []:
            if c in TRACKR_HINTS:
                return TRACKR_HINTS[c]
        if op.get("source") == "jobs.ac.uk":
            return "standard", "academia"
        return "standard", None


# ---------------------------------------------------------------- person settings
@dataclass
class Prefs:
    category_fit: dict
    keywords: dict          # phrase -> points
    experience_years: float
    degree: str
    clearance_ok: bool | None
    employer_fields: dict
    start_year: int | None
    start: float | None
    eligible_groups: set
    domains: set
    fit_floor: int

    @classmethod
    def of(cls, person, detected: dict | None = None) -> "Prefs":
        """person.yaml `scoring` wins; anything it leaves out comes from the CV (`detected`)."""
        sc = dict(getattr(person, "scoring", None) or {})
        detected = detected or {}
        kws: dict = {s: SKILL_POINTS for s in detected.get("main_skills") or []}
        for group, pts in KEYWORD_POINTS.items():
            for phrase in sc.get(group) or []:
                kws[str(phrase).lower()] = pts
        avail = getattr(person, "available_from", "") or ""
        m = re.search(r"20(\d\d)", avail)
        mm = MONTH_YEAR.search(avail)
        clr = str(sc.get("security_clearance_ok", "unknown")).lower()
        return cls(
            category_fit={**GENERIC_CATEGORY_FIT, **(detected.get("category_fit") or {}), **(sc.get("category_fit") or {})},
            keywords=kws,
            experience_years=float(sc["experience_years"] if sc.get("experience_years") is not None
                                   else detected.get("experience_years") or 0),
            degree=str(sc.get("degree") or detected.get("degree") or "bsc").lower(),
            clearance_ok=True if clr in ("yes", "true") else False if clr in ("no", "false") else None,
            employer_fields={str(k): float(v) for k, v in (sc.get("employer_fields") or {}).items()},
            start_year=2000 + int(m.group(1)) if m else None,
            start=int(mm.group(2)) + MONTHS3.index(mm.group(1).lower()[:3]) / 12 if mm else None,
            eligible_groups={str(g).lower() for g in sc.get("eligible_groups") or []},
            domains={roles.resolve_domain(str(d)) or str(d).lower() for d in sc.get("preferred_domains") or []},
            fit_floor=int(sc.get("off_target_below", 42)),
        )


def fingerprint(person, employers_path: Path | None) -> str:
    blob = json.dumps({"v": RULES_VERSION, "scoring": getattr(person, "scoring", None),
                       "looking_for": getattr(person, "looking_for", ""), "start": getattr(person, "available_from", ""),
                       "employers": employers_path.read_text(encoding="utf-8") if employers_path and employers_path.exists() else ""},
                      sort_keys=True, default=str)
    return "rules-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- scoring
def _clamp(x: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(x))))


def _phrase_in(phrase: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def location_of(op: dict) -> str:
    text = " ".join([*(op.get("locations") or []), op.get("title", "")]).replace("|", " ")
    if re.search(r"\blondon\b", text, re.I) and not re.search(r"london,?\s*on\b", text, re.I):
        return "london"
    if is_uk(text):
        return "uk_other"
    if ABROAD.search(text):
        return "abroad"
    return "uk_other" if op.get("source") in ("trackr", "jobs.ac.uk") else "unknown"


class Scorer:
    def __init__(self, person, cv: str, corpus: list[dict], employers: Employers):
        self.employers = employers
        self.tfidf = TfIdf([job_text(o) for o in corpus] + [cv] + list(cvprofile.CATEGORY_PROTOTYPES.values()))
        self.detected = cvprofile.detect(cv, self.tfidf)
        self.p = Prefs.of(person, self.detected)
        self.cv_vec = self.tfidf.vec(cv)
        prefs_text = " ".join([getattr(person, "looking_for", "") or "", getattr(person, "fit_guide", "") or "",
                               " ".join(k for k, v in self.p.keywords.items() if v > 0)])
        self.pref_vec = self.tfidf.vec(prefs_text) if prefs_text.strip() else {}
        sims = [self._sims(o) for o in corpus] or [(0.0, 0.0, 0.0)]
        self.stats = [self._mean_sd([s[i] for s in sims]) for i in (0, 1, 2)]

    @staticmethod
    def _mean_sd(xs):
        m = sum(xs) / len(xs)
        return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0)

    def _sims(self, op):
        """(listing vs CV, listing vs what they are looking for, title vs CV)"""
        v = self.tfidf.vec(job_text(op))
        tv = self.tfidf.vec(op.get("title") or "")
        return (TfIdf.cosine(self.cv_vec, v), TfIdf.cosine(self.pref_vec, v) if self.pref_vec else 0.0,
                TfIdf.cosine(self.cv_vec, tv))

    def _z(self, i, x):
        m, sd = self.stats[i]
        return max(-2.0, min(2.0, (x - m) / sd))

    def assess(self, op: dict) -> dict:
        p = self.p
        title = (op.get("title") or "").lower()
        rest = " ".join([" ".join(op.get("divisions") or []), op.get("snippet") or "", op.get("notes") or ""]).lower()
        cats = list(dict.fromkeys([*(op.get("categories") or []), *(categorize(op.get("title", "")) if op.get("source") != "trackr" else [])]))
        tier, field = self.employers.lookup(op)
        prestige, selectivity = TIERS.get(tier, TIERS["standard"])

        # role fit
        known = [p.category_fit[c] for c in cats if c in p.category_fit]
        fit = sum(known) / len(known) if known else 55.0
        hits_title = {k: v for k, v in p.keywords.items() if _phrase_in(k, title)}
        hits_rest = {k: v / 2 for k, v in p.keywords.items() if k not in hits_title and _phrase_in(k, rest)}
        hits = {**hits_title, **hits_rest}
        ups = sorted((v for v in hits.values() if v > 0), reverse=True)
        downs = sorted(v for v in hits.values() if v < 0)
        up = min(22.0, (ups[0] + 0.3 * sum(ups[1:])) if ups else 0.0)          # strongest match counts fully
        down = max(-40.0, (downs[0] + 0.5 * sum(downs[1:])) if downs else 0.0)
        cv_sim, pref_sim, title_sim = self._sims(op)
        sim = max(-12.0, min(12.0, 3 * self._z(0, cv_sim) + 3 * self._z(1, pref_sim) + 3 * self._z(2, title_sim)))
        field_bonus = p.employer_fields.get(field or "", 0.0)
        in_domains = [d for d in self.employers.domains(op) if d in p.domains]
        domain_bonus = 12 if in_domains else 0
        labelled = GRAD_LABEL_BONUS if GRADUATE.search(title) else 0
        raw_fit = fit + up + down + sim + 0.6 * field_bonus + TIER_FIT.get(tier, 0) + domain_bonus + labelled
        fit = _clamp(raw_fit, 5, 98)

        # red flags
        flags = []
        if INTERN.search(title):
            flags.append("internship")
        years = [2000 + int(y) for y in re.findall(r"\b20(2\d)\b", title)]
        dated = [int(y) + MONTHS3.index(mo.lower()[:3]) / 12 for mo, y in MONTH_YEAR.findall(op.get("title") or "")]
        if (p.start_year and years and max(years) < p.start_year) or (p.start and dated and max(dated) < p.start - 0.1):
            flags.append("early_start")
        restricted = [g for g, rx in RESTRICTED.items() if rx.search(title) and g not in p.eligible_groups]
        needs_years = max([int(n) for n in YEARS.findall(rest + " " + title)] or [0])
        phd = bool(PHD.search(title)) and p.degree != "phd"
        too_senior = bool(SENIOR.search(title)) or phd or needs_years > p.experience_years + 1
        if too_senior:
            flags.append("too_senior")
        if fit < p.fit_floor or any(v <= -30 for v in hits_title.values()) or restricted:
            flags.append("off_target")
        clearance = bool(CLEARANCE.search(title + " " + rest)) or field == "defence"

        # competitiveness
        comp = selectivity + 0.12 * (fit - 60)
        if GRADUATE.search(title) and p.experience_years >= 2:
            comp += 3
        if too_senior:
            comp -= 20
        if clearance and p.clearance_ok is False:
            comp -= 25
        comp = _clamp(comp, 10, 97)

        # career value
        career = 0.55 * prestige + 0.45 * fit + field_bonus
        if tier == "weak":
            career = min(career, 40)
        career = _clamp(career, 5, 98)

        return {
            "role_fit": fit, "competitiveness": comp, "career_value": career, "location": location_of(op),
            "flags": flags,
            "why_fit": (f"Only open to {restricted[0]} applicants. " if restricted else "") +
                       (f"In your preferred domain ({in_domains[0]}). " if in_domains else "") +
                       self._why_fit(cats, hits, sim, field, field_bonus),
            "why_competitive": self._why_comp(tier, too_senior, phd, needs_years, clearance),
            "why_career": (f"{tier.capitalize()} employer" + (f" ({field})" if field else "") +
                           (f", a field you favour (+{field_bonus:g})" if field_bonus > 0 else "") + ".")[:140],
            "scorer": "rules",
            "rank": round(raw_fit + 0.1 * prestige, 2),   # unclamped, only used to order roles with the same score
        }

    def _why_fit(self, cats, hits, sim, field, field_bonus):
        parts = []
        good = [k for k, v in sorted(hits.items(), key=lambda x: -x[1]) if v > 0][:3]
        bad = [k for k, v in sorted(hits.items(), key=lambda x: x[1]) if v < 0][:2]
        if good:
            parts.append("matches " + ", ".join(good))
        if bad:
            parts.append("but " + ", ".join(bad))
        if not parts and cats:
            parts.append(", ".join(cats[:2]))
        parts.append("close to your CV" if sim >= 4 else "far from your CV" if sim <= -4 else "")
        text = "; ".join(x for x in parts if x)
        return (text[:1].upper() + text[1:])[:140] if text else "No strong signals either way."

    def _why_comp(self, tier, too_senior, phd, years, clearance):
        bits = {"elite": "Extremely selective employer", "top": "Very selective employer", "strong": "Selective employer",
                "standard": "Typical graduate selectivity", "weak": "Easy to get into"}[tier]
        extra = []
        if phd:
            extra.append("asks for a PhD")
        elif years > self.p.experience_years + 1:
            extra.append(f"asks for {years}+ years")
        elif too_senior:
            extra.append("senior title")
        if clearance and self.p.clearance_ok is False:
            extra.append("likely needs security clearance")
        return (bits + ("; " + ", ".join(extra) if extra else "") + ".")[:140]


def assess_all(ops: list[dict], person, cv: str, employers_path: Path | None,
               corpus: list[dict] | None = None) -> tuple[dict, dict]:
    """Scores every position for this person (about a millisecond each). Returns ({id: assessment}, detected)."""
    fp = fingerprint(person, employers_path)
    scorer = Scorer(person, cv, corpus or ops, Employers(employers_path))
    return {op["id"]: dict(scorer.assess(op), hash=fp) for op in ops}, scorer.detected
