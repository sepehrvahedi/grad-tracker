"""Paths and settings.

Your data (CVs, statuses, fetched roles) lives in a *home* folder, never inside the package:

* ``GRADTRACKER_HOME`` if it is set,
* the repository folder when you run from a git checkout,
* otherwise ``~/grad-tracker``.

Inside it: ``people/<name>/`` (a CV PDF and person.yaml), ``data/`` (fetched roles, statuses, notes),
and optional ``sources.yaml`` / ``employers.yaml`` that override the defaults shipped with the package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
DEFAULTS = PACKAGE / "defaults"
SAMPLE_CV = PACKAGE / "examples" / "sample-cv.pdf"


def default_home() -> Path:
    if os.environ.get("GRADTRACKER_HOME"):
        return Path(os.environ["GRADTRACKER_HOME"]).expanduser()
    checkout = PACKAGE.parent
    if (checkout / "pyproject.toml").exists() and (checkout / ".git").exists():
        return checkout
    return Path.home() / "grad-tracker"


def default_season(today: date | None = None) -> str:
    """Graduate programmes are advertised from late summer for the following year's intake."""
    today = today or date.today()
    return str(today.year + 1 if today.month >= 7 else today.year)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    home: Path = field(default_factory=default_home)

    # Scoring is local by default. "llm" is optional and needs an OpenAI-compatible endpoint.
    scorer: str = "rules"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    reasoning_effort: str = ""
    llm_timeout: float = 180.0
    llm_max_attempts: int = 5

    default_person: str = ""

    trackr_season: str = field(default_factory=default_season)
    trackr_type: str = "graduate-programmes"
    trackr_region: str = "UK"
    trackr_industries: tuple = ("Tech", "Finance", "Engineering")

    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def people_dir(self) -> Path:
        return self.home / "people"

    @property
    def sources_file(self) -> Path:
        mine = self.home / "sources.yaml"
        return mine if mine.exists() else DEFAULTS / "sources.yaml"

    @property
    def employers_file(self) -> Path:
        mine = self.home / "employers.yaml"
        return mine if mine.exists() else DEFAULTS / "employers.yaml"

    @property
    def opportunities_file(self) -> Path:
        return self.data_dir / "opportunities.json"

    @property
    def llm_cache_dir(self) -> Path:
        return self.data_dir / "llm-cache"

    @property
    def archive_file(self) -> Path:
        """Snapshots of positions you acted on, so they stay visible after a listing disappears."""
        return self.data_dir / "archive.json"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.people_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings(home: Path | None = None) -> Settings:
    s = Settings(home=Path(home).expanduser() if home else default_home())
    _load_dotenv(s.home / ".env")
    env = os.environ.get
    s.scorer = env("GRADTRACKER_SCORER", s.scorer).lower()
    s.api_key = env("GRADTRACKER_LLM_API_KEY", "")
    s.base_url = env("GRADTRACKER_LLM_BASE_URL", s.base_url)
    s.model = env("GRADTRACKER_LLM_MODEL", s.model)
    s.reasoning_effort = env("GRADTRACKER_LLM_REASONING_EFFORT", s.reasoning_effort)
    s.trackr_season = env("GRADTRACKER_SEASON", s.trackr_season)
    s.adzuna_app_id = env("ADZUNA_APP_ID", "")
    s.adzuna_app_key = env("ADZUNA_APP_KEY", "")
    s.ensure_dirs()
    return s
