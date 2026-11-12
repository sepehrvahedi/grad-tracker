"""Trackr graduate programmes (Tech, Finance and Engineering lists), normalised to the tracker's shape.

This source is off by default (see defaults/sources.yaml). The helpers here are also used for every other source.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from .config import Settings

API_URL = "https://api.the-trackr.com/programmes"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; grad-tracker/0.1; +https://github.com/sepehrvahedi/grad-tracker)", "Accept": "application/json"}


def fetch_raw(s: Settings, session: requests.Session | None = None, industry: str = "Tech") -> list[dict]:
    params = {"region": s.trackr_region, "industry": industry,
              "season": s.trackr_season, "type": s.trackr_type}
    http = session or requests.Session()
    last: Exception | None = None
    for _ in range(3):
        try:
            r = http.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            body = r.json()
            items = body.get("programmes") if isinstance(body, dict) else body
            if not isinstance(items, list):
                raise ValueError("unexpected Trackr response shape")
            return items
        except (requests.RequestException, ValueError) as e:
            last = e
    raise RuntimeError(f"could not fetch Trackr {industry} programmes: {last}")


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


def clean_url(url: str | None) -> str | None:
    """Drop Trackr's utm/source tracking parameters; keep everything else."""
    if not url:
        return None
    parts = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() != "source"]
    return urlunparse(parts._replace(query=urlencode(query)))


def normalize(raw: dict, industry: str | None = None) -> dict:
    company = raw.get("company") or {}
    return {
        "id": raw["id"],
        "source": "trackr",
        "feed": f"trackr:{industry or raw.get('industry') or 'Tech'}",
        "industry": industry or raw.get("industry") or "Tech",
        "company": company.get("name") or raw.get("companyId") or "?",
        "title": (raw.get("name") or "").strip(),
        "url": clean_url(raw.get("url")),
        "categories": raw.get("categories") or [],
        "divisions": [d.split("|")[-1] for d in (raw.get("divisions") or [])],
        "locations": raw.get("locations") or [],
        "opening": _day(raw.get("openingDate")),
        "closing": _day(raw.get("closingDate")),
        "rolling": raw.get("rolling"),
        "cv": bool(raw.get("cv")),
        "cover_letter": raw.get("coverLetter") or "Unknown",
        "written_answers": raw.get("writtenAnswers") or "Unknown",
        "sponsors_visa": company.get("sponsorsVisa") or "Unknown",
        "notes": raw.get("notes") or "",
        "company_description": (company.get("description") or "").strip(),
        "snippet": "",
        "sources": [{"source": "trackr", "id": raw["id"], "url": clean_url(raw.get("url"))}],
    }


def status_of(op: dict, today: date | None = None) -> str:
    today_s = (today or date.today()).isoformat()
    if op.get("closing") and op["closing"] < today_s:
        return "closed"
    if not op.get("url") or (op.get("opening") and op["opening"] > today_s):
        return "upcoming"
    return "open"


def fetch(s: Settings, today: date | None = None, industries: tuple | None = None) -> list[dict]:
    seen: set = set()
    ops = []
    for industry in industries or ("Tech",):
        for r in fetch_raw(s, industry=industry):
            if r.get("id") and r["id"] not in seen:
                seen.add(r["id"])
                ops.append(normalize(r, industry))
    for op in ops:
        op["status"] = status_of(op, today)
    ops.sort(key=lambda o: (o["status"] != "open", o.get("closing") or "9999", o["company"].lower()))
    return ops
