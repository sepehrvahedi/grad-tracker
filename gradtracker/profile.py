"""Optional extra facts about a person (profile.yaml). Only the non-sensitive ones are ever put in a prompt."""

from __future__ import annotations

from pathlib import Path

import yaml

# Sections that may appear in a cover-letter prompt. Address, nationality, salary and diversity answers: never.
PUBLIC = {
    "personal": ("first_name", "last_name", "city"),
    "work_authorisation": ("after_graduation", "requires_sponsorship_in_future"),
    "education": ("current_degree", "graduation_date", "previous_degree", "previous_grade", "expected_grade"),
    "availability": ("earliest_start_date",),
}


def load_profile(path: Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def public_facts(profile: dict) -> dict:
    out: dict = {}
    for section, keys in PUBLIC.items():
        vals = {k: (profile.get(section) or {}).get(k) for k in keys}
        vals = {k: v for k, v in vals.items() if v not in (None, "")}
        if vals:
            out[section] = vals
    extra = [f for f in profile.get("extra_facts") or [] if f]
    if extra:
        out["extra_facts"] = extra
    return out
