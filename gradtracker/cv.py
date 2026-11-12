"""Read a CV PDF as plain text. Everything the scorer knows about a person comes from this and person.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader

ML_CATEGORIES = {"AI and Machine Learning", "Data Science"}


@lru_cache(maxsize=8)
def cv_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CV not found: {p}")
    try:
        reader = PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # pypdf raises several unrelated types on damaged files
        raise ValueError(f"{p.name} is not a readable PDF ({e.__class__.__name__})") from e
    if len(text.strip()) < 200:
        raise ValueError(f"no text could be read from {p.name}: is it a scan? Export the CV as a text PDF")
    return text


def pick_cv(op: dict, backend_pdf: Path, ml_pdf: Path) -> Path:
    """ML-platform variant for ML/data roles when it exists, backend variant otherwise."""
    if ML_CATEGORIES.intersection(op.get("categories", [])) and ml_pdf.exists():
        return ml_pdf
    return backend_pdf
