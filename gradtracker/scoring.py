"""Explainable priority score, per person.

The LLM only judges three things per position (each 0-100 with a one-line reason) plus location and red flags,
against that person's CV and what they are looking for (people/<name>/person.yaml).
The final score is a transparent formula with weights YOU control in the UI:

    base  = weighted average of role fit, competitiveness and career value
    score = base + visa adjustment + location adjustment, then capped by red flags (internship, ...)
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .llm import LLM, LLMError
from .store import State

DEFAULT_WEIGHTS: dict = {
    # relative importance of the three AI judgements (any positive numbers; normalised)
    "w_role_fit": 50,
    "w_competitiveness": 20,
    "w_career_value": 30,
    # added to the base score (points)
    "visa_yes": 0, "visa_unknown": -4, "visa_no": -15,
    "loc_london": 0, "loc_uk_other": -6, "loc_abroad": -25, "loc_unknown": -2,
    # hard maximums when a red flag applies
    "cap_internship": 25, "cap_early_start": 35, "cap_off_target": 40, "cap_too_senior": 45,
}

WEIGHT_LABELS = {
    "w_role_fit": "Role fit (weight)", "w_competitiveness": "Competitiveness (weight)",
    "w_career_value": "Career value (weight)",
    "visa_yes": "Sponsors visa", "visa_unknown": "Visa unknown", "visa_no": "No visa sponsorship",
    "loc_london": "London", "loc_uk_other": "UK outside London", "loc_abroad": "Outside UK", "loc_unknown": "Location unknown",
    "cap_internship": "Cap: internship/placement", "cap_early_start": "Cap: starts before you're available",
    "cap_off_target": "Cap: not what you're looking for", "cap_too_senior": "Cap: needs more experience",
}

FLAGS = {"internship": "cap_internship", "early_start": "cap_early_start", "off_target": "cap_off_target",
         "too_senior": "cap_too_senior"}
LEGACY_FLAGS = {"non_engineering": "off_target"}
FLAG_TEXT = {"internship": "internship / placement / insight programme",
             "early_start": "starts before you're available", "off_target": "not what you're looking for",
             "too_senior": "needs more experience than you have"}

BATCH = 12
PROMPT_VERSION = "v4"

SYSTEM_TEMPLATE = """You assess UK job opportunities for ONE candidate. Be strict and calibrated; use the full 0-100 range.
Candidate: {summary}
Looking for: {looking_for}

For EACH position return:
- role_fit (0-100): how closely the day-to-day work matches the candidate's skills AND what they are looking
  for.{fit_guide}
- competitiveness (0-100): how strong their application would be vs. typical applicants (experience, degree, skills).
- career_value (0-100): long-term value of this job for the candidate's direction (quality and reputation of the
  team, learning, compensation, UK career growth). Elite employers in their field high; generic schemes medium.
- location: "london" | "uk_other" | "abroad" | "unknown" (infer from title, location, notes, description).
- flags: list from ["internship", "early_start", "off_target", "too_senior"] that apply
  (internship = internships, placements, spring/summer/insight programmes; early_start = {early_start};
  off_target = {off_target}; too_senior = asks for clearly more experience or a higher degree than the
  candidate has).
- why_fit, why_competitive, why_career: max 14 words each, concrete.
Calibration (important): across a typical list, aim for medians around 60 on every dimension. Reserve 90+
for the top ~5%. Competitiveness must reflect real selectivity: employers that hire well under 1% of applicants
(top quant/HFT, the most selective big tech and AI labs) rarely exceed 75 even for a strong candidate; a generic
graduate scheme where the candidate clearly stands out can be 80+.
Return ONLY a JSON array of objects with keys: id, role_fit, competitiveness, career_value, location, flags,
why_fit, why_competitive, why_career."""


def system_prompt(person=None) -> str:
    """The scoring instructions for one person (people.Person); generic when person is None."""
    get = lambda k: (getattr(person, k, "") or "").strip()
    start = get("available_from")
    return SYSTEM_TEMPLATE.format(
        summary=get("summary") or "see the CV below.",
        looking_for=get("looking_for") or "a full-time graduate role in the UK.",
        fit_guide=f" Guide: {get('fit_guide')}." if get("fit_guide") else "",
        early_start=(f'the job starts before {start}, e.g. an earlier intake' if start
                     else "never use this flag (the start date does not matter)"),
        off_target=get("off_target") or "not the kind of role the candidate is looking for",
    )


DEFAULT_SYSTEM = system_prompt(None)


def default_weights(person=None) -> dict:
    """DEFAULT_WEIGHTS, adjusted for the person: no visa points for someone who never needs sponsorship."""
    w = dict(DEFAULT_WEIGHTS)
    if person is not None and getattr(person, "needs_visa_sponsorship", None) is False:
        w.update(visa_yes=0, visa_unknown=0, visa_no=0)
    w.update({k: v for k, v in (getattr(person, "weights", None) or {}).items() if k in DEFAULT_WEIGHTS})
    return w


def content_hash(op: dict, system: str = DEFAULT_SYSTEM) -> str:
    keys = ("title", "company", "categories", "divisions", "notes", "company_description", "opening", "closing",
            "snippet", "locations")
    blob = json.dumps({k: op.get(k) for k in keys}, sort_keys=True, ensure_ascii=False) + PROMPT_VERSION + system
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _brief(op: dict) -> dict:
    b = {"id": op["id"], "company": op["company"], "title": op["title"], "categories": op.get("categories") or [],
         "divisions": (op.get("divisions") or [])[:4], "location": (op.get("locations") or [])[:3],
         "notes": (op.get("notes") or "")[:200], "about": (op.get("company_description") or "")[:300]}
    if op.get("snippet"):
        b["description"] = op["snippet"][:450]
    return b


def _clamp(v, lo=0, hi=100) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def _parse(item: dict) -> dict:
    loc = str(item.get("location", "unknown")).lower()
    flags = [LEGACY_FLAGS.get(f, f) for f in (item.get("flags") or [])]
    flags = [f for f in dict.fromkeys(flags) if f in FLAGS]
    return {
        "role_fit": _clamp(item.get("role_fit")), "competitiveness": _clamp(item.get("competitiveness")),
        "career_value": _clamp(item.get("career_value")),
        "location": loc if loc in ("london", "uk_other", "abroad", "unknown") else "unknown",
        "flags": flags,
        "why_fit": str(item.get("why_fit", ""))[:140], "why_competitive": str(item.get("why_competitive", ""))[:140],
        "why_career": str(item.get("why_career", ""))[:140],
    }


def _assess_batch(batch: list[dict], cv: str, llm: LLM, system: str = DEFAULT_SYSTEM) -> dict[str, dict]:
    ids = {o["id"] for o in batch}
    result = llm.json([
        {"role": "system", "content": system},
        {"role": "user", "content": f"CANDIDATE CV:\n{cv[:6000]}\n\nPROGRAMMES:\n{json.dumps([_brief(o) for o in batch], ensure_ascii=False)}"},
    ])
    if isinstance(result, dict):
        result = next((v for v in result.values() if isinstance(v, list)), [])
    out = {}
    for item in result if isinstance(result, list) else []:
        if isinstance(item, dict) and item.get("id") in ids:
            out[item["id"]] = _parse(item)
    return out


def needs_assessment(op: dict, state: State, system: str = DEFAULT_SYSTEM) -> bool:
    a = state.assessment(op["id"])
    return a is None or a.get("hash") != content_hash(op, system)


def assess(ops: list[dict], cv: str, llm: LLM, state: State, *, force: bool = False, workers: int = 4,
           progress: Callable[[int, int], None] = lambda d, t: None, system: str = DEFAULT_SYSTEM,
           errors: list | None = None) -> int:
    """Scores what is new or changed; returns how many were attempted. Model errors go into `errors`."""
    errors = errors if errors is not None else []
    todo = [o for o in ops if force or needs_assessment(o, state, system)]
    by_id = {o["id"]: o for o in todo}
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done = 0

    def store(results: dict) -> None:
        for op_id, a in results.items():
            a["hash"] = content_hash(by_id[op_id], system)
            state.set_assessment(op_id, a)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_assess_batch, b, cv, llm, system): b for b in batches}
        for fut in as_completed(futures):
            try:
                store(fut.result())
            except LLMError as e:
                errors.append(str(e))  # retried one by one below
            state.save()
            done += len(futures[fut])
            progress(done, len(todo))
    for op in [o for o in todo if needs_assessment(o, state, system)]:
        try:
            store(_assess_batch([op], cv, llm, system))
        except LLMError as e:
            errors.append(str(e))
            if "quota" in str(e).lower():
                break  # no point retrying the rest one by one
    state.save()
    return len(todo)


def compute(op: dict, a: dict | None, weights: dict, flag_text: dict | None = None) -> dict:
    """Final score + human-readable breakdown. Pure function (no I/O)."""
    flag_text = {**FLAG_TEXT, **(flag_text or {})}
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    if not a:
        return {"score": None, "parts": [], "caps": []}
    dims = [("role_fit", "Role fit", "why_fit", "w_role_fit"),
            ("competitiveness", "Competitiveness", "why_competitive", "w_competitiveness"),
            ("career_value", "Career value", "why_career", "w_career_value")]
    total_w = sum(max(0.0, float(w[k])) for *_, k in dims) or 1.0
    base = sum(a[key] * max(0.0, float(w[wk])) for key, _, _, wk in dims) / total_w
    parts = [{"label": label, "value": a[key], "weight": round(max(0.0, float(w[wk])) / total_w * 100),
              "points": round(a[key] * max(0.0, float(w[wk])) / total_w, 1), "why": a.get(why, ""), "kind": "dim"}
             for key, label, why, wk in dims]

    visa = {"Yes": "visa_yes", "No": "visa_no"}.get(op.get("sponsors_visa"), "visa_unknown")
    visa_from = "Trackr" if op.get("source") in (None, "trackr") or op.get("sponsors_visa") in ("Yes", "No") else "listing"
    loc = {"london": "loc_london", "uk_other": "loc_uk_other", "abroad": "loc_abroad"}.get(a.get("location"), "loc_unknown")
    adj = float(w[visa]) + float(w[loc])
    parts.append({"label": WEIGHT_LABELS[visa], "points": float(w[visa]), "kind": "adj",
                  "why": f"{visa_from}: sponsors visa = {op.get('sponsors_visa') or 'unknown'}"})
    parts.append({"label": WEIGHT_LABELS[loc], "points": float(w[loc]), "kind": "adj",
                  "why": f"location: {a.get('location', 'unknown').replace('_', ' ')}"})

    score = base + adj
    caps = []
    for flag in dict.fromkeys(LEGACY_FLAGS.get(f, f) for f in a.get("flags", [])):
        if flag not in FLAGS:
            continue
        cap = float(w[FLAGS[flag]])
        caps.append({"label": WEIGHT_LABELS[FLAGS[flag]], "max": cap, "why": flag_text[flag]})
        score = min(score, cap)
    return {"score": _clamp(score), "base": round(base, 1), "parts": parts, "caps": caps}
