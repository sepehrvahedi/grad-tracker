"""Merge the same role found in several sources into one position.

Two listings are the same role when they point at the same job-board posting (Greenhouse / Lever / Ashby id),
or when the company matches and the titles match after dropping years, places and filler words.
The kept position is the one from the most trusted source (Trackr first, so your existing statuses keep
their ids); the others become its `aliases`, and their links are listed under `sources`.
"""

from __future__ import annotations

import re

PRIORITY = {"trackr": 0, "greenhouse": 1, "lever": 1, "ashby": 1, "adzuna": 2, "jobs.ac.uk": 2}

COMPANY_FILLER = {"ltd", "limited", "plc", "inc", "llc", "llp", "lp", "group", "holdings", "the", "uk", "co",
                  "company", "corporation", "corp", "technologies", "technology", "capital", "trading", "markets",
                  "international", "global", "labs", "lab", "careers", "and", "europe", "london", "management"}
TITLE_FILLER = {"full", "time", "fulltime", "ft", "program", "programme", "programs", "programmes", "scheme", "the",
                "and", "of", "in", "for", "a", "an", "to", "uk", "london", "cambridge", "oxford", "united", "kingdom",
                "england", "emea", "europe", "start", "starting", "new", "grad", "grads", "graduate", "graduates",
                "university", "campus", "entry", "level", "early", "career", "careers", "role", "opportunity",
                "hybrid", "remote", "office", "based", "position", "job", "september", "summer", "autumn", "fixed",
                "term", "permanent", "contract", "m", "f", "x", "d"}
TITLE_SYNONYMS = {"developer": "engineer", "dev": "engineer", "engineering": "engineer", "engineers": "engineer",
                  "quantitative": "quant", "analysts": "analyst", "scientists": "scientist", "researcher": "research",
                  "programmer": "engineer", "swe": "software"}

ATS_KEYS = [
    (re.compile(r"greenhouse\.io/.*?/jobs/(\d+)"), "gh"),
    (re.compile(r"[?&]gh_jid=(\d+)"), "gh"),
    (re.compile(r"jobs\.lever\.co/[^/]+/([0-9a-f-]{36})"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/[^/?#]+/([0-9a-f-]{36})"), "ashby"),
]


def company_key(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    kept = [t for t in tokens if t not in COMPANY_FILLER]
    return " ".join(kept or tokens)


def title_tokens(title: str) -> frozenset:
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    out = set()
    for t in tokens:
        if t in TITLE_FILLER or re.fullmatch(r"20\d\d|\d{1,2}", t):
            continue
        out.update(TITLE_SYNONYMS.get(t, t).split())
    return frozenset(out)


def url_keys(op: dict) -> set:
    keys = set()
    for link in [op.get("url"), *[s.get("url") for s in op.get("sources") or []]]:
        for rx, kind in ATS_KEYS:
            m = rx.search(link or "")
            if m:
                keys.add(f"{kind}:{m.group(1)}")
    return keys


def same_title(a: frozenset, b: frozenset) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.8


def _absorb(primary: dict, other: dict) -> None:
    primary.setdefault("aliases", [])
    primary["aliases"] = list(dict.fromkeys(primary["aliases"] + [other["id"]] + other.get("aliases", [])))
    seen = {s.get("id") for s in primary["sources"]}
    primary["sources"] += [s for s in other.get("sources") or [] if s.get("id") not in seen]
    for k in ("url", "closing", "opening", "snippet", "company_description"):
        if not primary.get(k) and other.get(k):
            primary[k] = other[k]
    primary["locations"] = list(dict.fromkeys([*(primary.get("locations") or []), *(other.get("locations") or [])]))
    if primary.get("source") != "trackr":
        primary["categories"] = list(dict.fromkeys([*primary["categories"], *(other.get("categories") or [])]))
    if primary.get("status") != "open" and other.get("status") == "open" and not primary.get("closing"):
        primary["status"] = "open"


def merge(ops: list[dict]) -> list[dict]:
    """Same role, one position. Never merges two Trackr programmes (Trackr already lists each once)."""
    ordered = sorted(ops, key=lambda o: PRIORITY.get(o.get("source"), 3))
    kept: list[dict] = []
    by_key: dict = {}
    by_company: dict = {}
    for op in ordered:
        op = dict(op, sources=list(op.get("sources") or [{"source": op.get("source"), "id": op["id"], "url": op.get("url")}]),
                  aliases=list(op.get("aliases") or []))
        keys = url_keys(op)
        tokens = title_tokens(op["title"])
        match = next((by_key[k] for k in keys if k in by_key), None)
        if match is not None and match["id"] != op["id"] and match.get("source") == op.get("source") == "trackr":
            match = None
        if match is None:
            for cand in by_company.get(company_key(op["company"]), []):
                same_source = cand.get("source") == op.get("source")
                if same_source and op.get("source") == "trackr":
                    continue
                if same_title(cand["_tokens"], tokens) and (not same_source or cand["_tokens"] == tokens):
                    match = cand
                    break
        if match is not None and match["id"] != op["id"]:
            _absorb(match, op)
            for k in keys:
                by_key.setdefault(k, match)
            continue
        if match is not None:
            continue  # the same id twice
        op["_tokens"] = tokens
        kept.append(op)
        for k in keys:
            by_key.setdefault(k, op)
        by_company.setdefault(company_key(op["company"]), []).append(op)
    for op in kept:
        op.pop("_tokens", None)
    return kept


def share_visa_info(ops: list[dict]) -> None:
    """Trackr knows whether a company sponsors visas; copy that onto the same company's other listings."""
    known = {company_key(o["company"]): o["sponsors_visa"] for o in ops
             if o.get("source") == "trackr" and o.get("sponsors_visa") in ("Yes", "No")}
    for o in ops:
        if o.get("sponsors_visa") in (None, "", "Unknown"):
            o["sponsors_visa"] = known.get(company_key(o["company"]), "Unknown")
