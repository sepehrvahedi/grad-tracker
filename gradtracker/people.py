"""People the tracker finds jobs for: one folder per person in people/, holding person.yaml and a CV PDF.

Everyone shares the fetched positions; each person has their own CV, preferences, scores and statuses.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Settings
from .profile import load_profile, public_facts

TEMPLATE = """\
# Who this tracker scores jobs for. Edit freely; scores update on the next refresh.
name: {name}
cv: cv.pdf                 # the CV in this folder (any .pdf here works if this file is missing)
summary: ""                # optional one-line summary of the candidate
looking_for: >
  {looking_for}
fit_guide: ""              # what counts as high vs low fit, e.g. "data science = high; IT support = low"
off_target: not the kind of role this candidate is looking for
available_from: {available_from}         # e.g. "September 2027"; "" if it does not matter
needs_visa_sponsorship: {visa}   # yes / no / unknown
trackr_industries: [Tech, Finance, Engineering]
exclude: []                # Trackr industries or categories to ignore, e.g. "Finance:Middle Market"
skip_sources: []           # e.g. [jobs.ac.uk]
letter_closing: ""         # last sentence of cover letters, e.g. "I can start in September."
facts: {{}}                 # extra facts for cover letters (never put addresses or ID numbers here)
# Scoring works from the CV alone (see detected.yaml after a refresh). Anything here overrides it:
scoring: {{}}
#   great: [phrases that make a role ideal]      good: [...]      weak: [...]      poor: [phrases to avoid]
#   category_fit: {{Data Science: 85}}            experience_years: 1      degree: msc
#   employer_fields: {{science: 10, ai: 5}}       security_clearance_ok: unknown
"""


DEFAULT_LOOKING_FOR = "A graduate (entry-level) role in the UK."
PDF_LIMIT = 15 * 1024 * 1024


def render_template(name: str, *, looking_for: str = "", available_from: str = "", visa: str = "unknown") -> str:
    """person.yaml for a new person. Values are written as YAML scalars, so any text is safe."""
    q = lambda v: yaml.safe_dump(str(v), default_style='"', allow_unicode=True, width=10_000).strip()
    return TEMPLATE.format(name=q(name), available_from=q(available_from),
                           visa=visa if visa in ("yes", "no", "unknown") else "unknown",
                           looking_for=" ".join((looking_for or DEFAULT_LOOKING_FOR).split()))


def create_person(s: Settings, name: str, cv_bytes: bytes, *, looking_for: str = "", available_from: str = "",
                  needs_visa_sponsorship: str = "unknown") -> "Person":
    """Add a person from the UI: a folder in people/ with their CV and a person.yaml."""
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("a name is required")
    if not cv_bytes.startswith(b"%PDF"):
        raise ValueError("the CV must be a PDF file")
    if len(cv_bytes) > PDF_LIMIT:
        raise ValueError("the CV is larger than 15 MB")
    s.people_dir.mkdir(parents=True, exist_ok=True)
    base = slug = _slug(name)
    n = 2
    while (s.people_dir / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    folder = s.people_dir / slug
    folder.mkdir()
    try:
        (folder / "cv.pdf").write_bytes(cv_bytes)
        from .cv import cv_text
        cv_text(str(folder / "cv.pdf"))  # fails early on scanned or empty PDFs
        (folder / "person.yaml").write_text(
            render_template(name, looking_for=looking_for, available_from=" ".join(str(available_from or "").split())[:40],
                            visa=str(needs_visa_sponsorship or "unknown").lower()), encoding="utf-8")
    except Exception:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
        raise
    return load_person(folder, s)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"


def _tri(v) -> bool | None:
    s = str(v).strip().lower()
    return True if s in ("yes", "true", "1") else False if s in ("no", "false", "0") else None


@dataclass
class Person:
    slug: str
    name: str
    folder: Path
    cv: Path | None
    cv_ml: Path | None = None
    summary: str = ""
    looking_for: str = ""
    fit_guide: str = ""
    off_target: str = "not the kind of role this candidate is looking for"
    available_from: str = ""
    needs_visa_sponsorship: bool | None = None
    trackr_industries: list = field(default_factory=lambda: ["Tech"])
    exclude: list = field(default_factory=list)
    skip_sources: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    scoring: dict = field(default_factory=dict)
    letter_closing: str = ""
    facts: dict = field(default_factory=dict)
    profile_file: Path | None = None
    state_file: Path | None = None

    @property
    def ready(self) -> bool:
        return bool(self.cv and self.cv.exists())

    @property
    def cv_hint(self) -> str:
        return f"people/{self.folder.name}/cv.pdf"

    def public_facts(self) -> dict:
        facts = public_facts(load_profile(self.profile_file)) if self.profile_file else {}
        facts.update(self.facts or {})
        return facts

    def wants(self, op: dict) -> bool:
        """False for sources/industries/categories this person excluded (those are never scored)."""
        if op.get("source") in self.skip_sources:
            return False
        if op.get("source") == "trackr":
            industry = op.get("industry") or "Tech"
            if industry not in self.trackr_industries or industry in self.exclude:
                return False
            cats = op.get("categories") or []
            if cats and all(c in self.exclude or f"{industry}:{c}" in self.exclude for c in cats):
                return False
        return True

    def fingerprint(self) -> str:
        blob = repr((self.summary, self.looking_for, self.fit_guide, self.off_target, self.available_from))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _resolve(folder: Path, value) -> Path | None:
    if not value:
        return None
    p = Path(str(value)).expanduser()
    return (p if p.is_absolute() else folder / p).resolve()


def load_person(folder: Path, s: Settings) -> Person:
    cfg_path = folder / "person.yaml"
    if not cfg_path.exists():
        cfg_path.write_text(render_template(folder.name.replace("-", " ").title()), encoding="utf-8")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{cfg_path} must contain a YAML mapping")
    slug = _slug(folder.name)
    cv = _resolve(folder, cfg.get("cv"))
    if not (cv and cv.exists()):
        pdfs = sorted(folder.glob("*.pdf"))
        cv = pdfs[0].resolve() if pdfs else cv
    state = _resolve(folder, cfg.get("state")) or s.data_dir / "people" / slug / "tracker.json"
    return Person(
        slug=slug, name=str(cfg.get("name") or folder.name), folder=folder, cv=cv,
        cv_ml=_resolve(folder, cfg.get("cv_ml")),
        summary=str(cfg.get("summary") or "").strip(), looking_for=str(cfg.get("looking_for") or "").strip(),
        fit_guide=str(cfg.get("fit_guide") or "").strip(),
        off_target=str(cfg.get("off_target") or Person.off_target).strip(),
        available_from=str(cfg.get("available_from") or "").strip(),
        needs_visa_sponsorship=_tri(cfg.get("needs_visa_sponsorship")),
        trackr_industries=list(cfg.get("trackr_industries") or ["Tech"]),
        exclude=[str(x) for x in cfg.get("exclude") or []],
        skip_sources=[str(x) for x in cfg.get("skip_sources") or []],
        weights=dict(cfg.get("weights") or {}), scoring=dict(cfg.get("scoring") or {}), letter_closing=str(cfg.get("letter_closing") or "").strip(),
        facts=dict(cfg.get("facts") or {}), profile_file=_resolve(folder, cfg.get("profile")),
        state_file=state,
    )


def load_people(s: Settings) -> list[Person]:
    """Every folder in people/ is a person; the default person comes first, then by folder name."""
    s.people_dir.mkdir(parents=True, exist_ok=True)
    folders = [f for f in sorted(s.people_dir.iterdir()) if f.is_dir() and not f.name.startswith(".")]
    people = [load_person(f, s) for f in folders]
    if s.default_person:
        people.sort(key=lambda p: p.slug != s.default_person)
    return people
