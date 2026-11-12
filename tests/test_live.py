"""Live checks against the real job boards (run with: pytest -m live). No keys needed."""

from __future__ import annotations

import pytest
import requests

from gradtracker import sources

pytestmark = pytest.mark.live


def test_a_graduate_board_lists_roles():
    jobs = sources.gradcracker({"max_pages": 1, "searches": ["technology-it/software-systems-graduate-jobs"]})
    assert jobs and all(j["source"] == "gradcracker" and j["url"] and j["title"] for j in jobs)


def test_a_public_company_board_answers():
    jobs = sources.greenhouse(requests.Session(), {"name": "Stripe", "board": "stripe", "all_levels": True})
    assert all(j["source"] == "greenhouse" and j["company"] == "Stripe" for j in jobs)
