"""Tracker service: fetch every source, merge duplicates, score per person, and expose a view for the UI.

Positions are shared (data/opportunities.json). Each person (people/<name>/) has their own CV, scores,
statuses and notes. Positions anyone acted on are also snapshotted in data/archive.json, so an application
never disappears from the tracker when a listing is taken down or a source changes.
"""

from __future__ import annotations

import os
import threading
import traceback
from datetime import date
from typing import Callable

import yaml

from . import dedupe, roles, rules, sources
from .config import Settings
from .cv import cv_text
from .llm import LLM
from .people import Person, create_person, load_people
from .prompts import build_prompt, letter_need
from .scoring import (DEFAULT_WEIGHTS, WEIGHT_LABELS, assess, compute, content_hash, default_weights,
                      system_prompt)
from .store import STATUSES, State, now, read_json, write_json


class Tracker:
    def __init__(self, settings: Settings, llm_factory: Callable[[Settings], LLM] = LLM,
                 fetcher: Callable | None = None):
        """fetcher(settings, today, previous_ops) -> (ops, report); defaults to every configured source."""
        self.s = settings
        self.llm_factory = llm_factory
        self.fetcher = fetcher or (lambda s, today, previous, progress=print:
                                   sources.fetch_all(s, today, previous, progress))
        self._states: dict[str, State] = {}
        self._lock = threading.RLock()
        self.refresh_status = {"running": False, "message": "", "error": None, "finished_at": None}

    # ------------------------------------------------------------------ people
    def people(self) -> list[Person]:
        return load_people(self.s)

    def add_person(self, name: str, cv_bytes: bytes, **prefs) -> Person:
        """Create people/<name>/ from an uploaded CV (the first-run screen in the UI)."""
        with self._lock:
            return create_person(self.s, name, cv_bytes, **prefs)

    def person(self, slug: str | None = None) -> Person:
        people = self.people()
        if not people:
            raise KeyError("nobody set up yet: add a CV first")
        if not slug:
            return people[0]
        for p in people:
            if p.slug == slug:
                return p
        raise KeyError(slug)

    def state(self, person: Person) -> State:
        with self._lock:
            path = str(person.state_file)
            if path not in self._states:
                self._states[path] = State(person.state_file)
            return self._states[path]

    # ------------------------------------------------------------------ data
    def ops(self) -> list[dict]:
        return read_json(self.s.opportunities_file, [])

    def archive(self) -> dict:
        return read_json(self.s.archive_file, {})

    def op(self, op_id: str) -> dict:
        for o in self.ops():
            if o["id"] == op_id or op_id in (o.get("aliases") or []):
                return o
        archived = self.archive().get(op_id)
        if archived:
            return archived
        raise KeyError(op_id)

    def weights(self, person: Person) -> dict:
        return {**default_weights(person), **self.state(person).data["weights"]}

    def _snapshot(self, ops: list[dict]) -> None:
        """Keep a copy of every position someone acted on (status or note), keyed by every id it had."""
        people = [(p, self.state(p)) for p in self.people()]
        acted = {i for _, st in people for i in st.data["positions"] if st.acted_on(i)}
        if not acted:
            return
        arch = self.archive()
        for o in ops:
            ids = {o["id"], *(o.get("aliases") or [])}
            for i in ids & acted:
                arch[i] = dict(o, archived_at=now())
        write_json(self.s.archive_file, arch)

    # ------------------------------------------------------------------ update
    def update(self, progress: Callable[[str], None] = print, *, force: bool = False, today: date | None = None,
               only: str | None = None) -> dict:
        today = today or date.today()
        previous = self.ops()
        self._snapshot(previous)  # before anything is overwritten
        ops, report = self.fetcher(self.s, today, previous, progress)
        merged = dedupe.merge(ops)
        dedupe.share_visa_info(merged)
        merged.sort(key=lambda o: (o["status"] != "open", o.get("closing") or "9999", o["company"].lower()))
        write_json(self.s.opportunities_file, merged)
        self._snapshot(merged)
        n_dupes = len(ops) - len(merged)
        progress(f"{len(ops)} listings from {len(report)} feeds; {n_dupes} duplicates merged -> {len(merged)} positions.")

        summary = {"positions": len(merged), "listings": len(ops), "merged": n_dupes, "sources": report, "people": {}}
        llm = None
        for person in self.people():
            if only and person.slug != only:
                continue
            st = self.state(person)
            st.sync()
            for o in merged:
                if o.get("aliases"):
                    st.adopt_aliases(o["id"], o["aliases"])
            new_ids = st.mark_seen([o["id"] for o in merged if person.wants(o)], today.isoformat())
            st.data["meta"].update(fetched_at=now(), new_ids=new_ids, sources=report,
                                   listings=len(ops), merged=n_dupes)
            if not person.ready:
                st.data["meta"]["scoring_error"] = f"No CV yet: put a PDF at {person.cv_hint}"
                st.save()
                progress(f"{person.name}: no CV yet ({person.cv_hint}), skipped scoring.")
                continue
            if self.s.scorer != "llm":
                st.data["meta"].pop("scoring_error", None)
                st.save()
                n = self.score_locally(person, force=True)
                progress(f"{person.name}: scored {n} positions locally.")
                summary["people"][person.slug] = {"new": len(new_ids), "scored": n}
                continue
            llm = llm or self.llm_factory(self.s)
            active = [o for o in merged if o["status"] != "closed" and person.wants(o)]
            progress(f"{person.name}: scoring new or changed positions ({len(active)} active) with {self.s.model}...")
            errors: list = []
            n = assess(active, cv_text(str(person.cv)), llm, st, force=force, system=system_prompt(person),
                       progress=lambda d, t, who=person.name: progress(f"{who}: scoring {d}/{t}..."), errors=errors)
            missing = sum(1 for o in active if st.assessment(o["id"]) is None
                          or st.assessment(o["id"]).get("hash") != content_hash(o, system_prompt(person)))
            problem = (f"{missing} positions are not scored or use old scores. Last model error: {errors[-1][:300]}"
                       if missing and errors else None)
            if problem:
                progress(f"{person.name}: {problem}")
            st.data["meta"].update(last_scored=n, llm=llm.stats(), scoring_error=problem)
            st.save()
            summary["people"][person.slug] = {"new": len(new_ids), "scored": n}
        if llm:
            summary["llm"] = llm.stats()
        progress(f"Done: {summary['positions']} positions, {n_dupes} duplicates merged.")
        return summary

    def _local_fingerprint(self, person: Person) -> str:
        def stamp(path):
            try:
                st = os.stat(path)
                return f"{st.st_mtime_ns}:{st.st_size}"
            except (OSError, TypeError):
                return "-"
        return "|".join([rules.fingerprint(person, self.s.employers_file), str(person.cv), stamp(person.cv),
                         stamp(self.s.opportunities_file), repr(person.trackr_industries), repr(person.exclude),
                         repr(person.skip_sources)])

    def score_locally(self, person: Person, force: bool = False) -> int:
        """Re-scores everything for this person when the CV, person.yaml, employers.yaml or the positions changed
        (under a second, no network). Old LLM scores are kept under `llm_assessments`, never deleted."""
        if self.s.scorer == "llm" or not person.ready:
            return 0
        st = self.state(person)
        st.sync()
        fp = self._local_fingerprint(person)
        if not force and st.data["meta"].get("local_fingerprint") == fp:
            return 0
        ops = self.ops()
        active = [o for o in ops if o["status"] != "closed" and person.wants(o)]
        scored, detected = rules.assess_all(active, person, cv_text(str(person.cv)), self.s.employers_file, corpus=ops)
        with st.lock:
            old_llm = {k: v for k, v in st.data["assessments"].items() if v.get("scorer") != "rules"}
            if old_llm:
                st.data.setdefault("llm_assessments", {}).update(old_llm)
            st.data["assessments"] = scored
            st.data["meta"].update(local_fingerprint=fp, scoring_error=None, scorer="rules", detected=detected)
        st.save()
        self._write_detected(person, detected)
        return len(scored)

    @staticmethod
    def _write_detected(person: Person, detected: dict) -> None:
        body = {"experience_years": detected["experience_years"], "degree": detected["degree"],
                "main_skills": detected["main_skills"],
                "category_fit": dict(sorted(detected["category_fit"].items(), key=lambda kv: -kv[1]))}
        text = ("# Read from the CV by the local scorer. Not used if person.yaml `scoring` sets the same thing.\n"
                "# To change something, copy it into person.yaml under `scoring:` and edit it there.\n"
                + yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
        try:
            (person.folder / "detected.yaml").write_text(text, encoding="utf-8")
        except OSError:
            pass

    def start_refresh(self) -> bool:
        if self.refresh_status["running"]:
            return False

        def run():
            try:
                self.update(progress=lambda m: self.refresh_status.update(message=m))
                self.refresh_status.update(error=None)
            except Exception as e:  # surfaced in the UI
                self.refresh_status.update(error=f"{e.__class__.__name__}: {e}", message="Refresh failed")
                traceback.print_exc()
            finally:
                self.refresh_status.update(running=False, finished_at=now())

        self.refresh_status.update(running=True, message="Starting...", error=None)
        threading.Thread(target=run, daemon=True).start()
        return True

    # ------------------------------------------------------------------ view / actions
    def view(self, slug: str | None = None, today: date | None = None) -> dict:
        today = today or date.today()
        if not self.people():
            return {"needs_setup": True, "person": None, "people": [], "positions": [], "statuses": STATUSES,
                    "counts": {"total": 0, "open_todo": 0, "closing_week": 0, "applied": 0, "new": 0},
                    "meta": {}, "refresh": self.refresh_status, "home": str(self.s.home),
                    "fetched": self.s.opportunities_file.exists()}
        person = self.person(slug)
        self.score_locally(person)
        st = self.state(person)
        st.sync()
        w = self.weights(person)
        flag_text = {"early_start": f"starts before {person.available_from}"} if person.available_from else None
        new_ids = set(st.data["meta"].get("new_ids", []))
        ops = self.ops()
        current = {i for o in ops for i in [o["id"], *(o.get("aliases") or [])]}
        removed = [dict(o, status="closed", removed=True) for i, o in self.archive().items()
                   if i not in current and st.acted_on(i) and not st.data["positions"][i].get("merged_into")
                   and o.get("id") == i]
        employers = rules.Employers(self.s.employers_file)
        rows = []
        for o in ops + removed:
            pos = st.data["positions"].get(o["id"], {})
            if not person.wants(o) and not st.acted_on(o["id"]):
                continue
            a = st.assessment(o["id"])
            sc = compute(o, a, w, flag_text)
            days_left = (date.fromisoformat(o["closing"]) - today).days if o.get("closing") else None
            rows.append({
                **{k: o.get(k) for k in ("id", "company", "title", "url", "categories", "divisions", "opening",
                                         "closing", "cover_letter", "written_answers", "sponsors_visa", "notes",
                                         "company_description", "status", "source", "industry", "locations")},
                "sources": o.get("sources") or [{"source": o.get("source", "trackr"), "id": o["id"], "url": o.get("url")}],
                "removed": bool(o.get("removed")),
                "roles": roles.role_types(o),
                "domains": employers.domains(o),
                "snippet": (o.get("snippet") or "")[:400],
                "trackr_status": o["status"],
                "my_status": pos.get("status", "todo"),
                "note": pos.get("note", ""),
                "first_seen": pos.get("first_seen"),
                "is_new": o["id"] in new_ids,
                "days_left": days_left,
                "letter": letter_need(o),
                "score": sc["score"], "base": sc.get("base"), "parts": sc["parts"], "caps": sc["caps"],
                "rank": (a or {}).get("rank", 0),
                "location": (a or {}).get("location"),
            })
        rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), -(r["rank"] or 0), r["company"].lower()))
        counts = {
            "total": len(rows),
            "open_todo": sum(1 for r in rows if r["trackr_status"] == "open" and r["my_status"] == "todo"),
            "closing_week": sum(1 for r in rows if r["trackr_status"] == "open" and r["my_status"] == "todo"
                                and r["days_left"] is not None and 0 <= r["days_left"] <= 7),
            "applied": sum(1 for r in rows if r["my_status"] in ("applied", "interviewing", "offer")),
            "new": len(new_ids),
        }
        people = [{"slug": p.slug, "name": p.name, "ready": p.ready, "cv_hint": p.cv_hint} for p in self.people()]
        return {"person": {"slug": person.slug, "name": person.name, "ready": person.ready, "cv_hint": person.cv_hint},
                "people": people, "positions": rows, "weights": w, "weight_labels": WEIGHT_LABELS,
                "default_weights": default_weights(person), "statuses": STATUSES, "counts": counts,
                "source_labels": sources.SOURCE_LABELS, "role_types": roles.LABELS, "domain_types": roles.DOMAIN_LABELS,
                "meta": st.data["meta"], "refresh": self.refresh_status, "home": str(self.s.home),
                "needs_setup": False, "fetched": self.s.opportunities_file.exists()}

    def prompt(self, op_id: str, slug: str | None = None) -> str:
        person = self.person(slug)
        if not person.ready:
            raise ValueError(f"no CV for {person.name} yet ({person.cv_hint})")
        o = self.op(op_id)
        ml = bool(person.cv_ml and person.cv_ml.exists() and
                  {"AI and Machine Learning", "Data Science"} & set(o.get("categories") or []))
        cv = cv_text(str(person.cv_ml if ml else person.cv))
        return build_prompt(o, cv, person.public_facts(), name=person.name,
                            cv_variant="auto" if person.cv_ml else None,
                            closing=person.letter_closing)

    def _acting(self, slug: str | None, op_id: str) -> State:
        person = self.person(slug)
        st = self.state(person)
        st.sync()
        o = self.op(op_id)  # 404 for unknown ids
        if o["id"] != op_id and op_id in (o.get("aliases") or []):
            raise KeyError(op_id)
        return st

    def set_status(self, op_id: str, status: str, slug: str | None = None) -> None:
        st = self._acting(slug, op_id)
        st.set_status(op_id, status)
        st.save()
        self._snapshot(self.ops())

    def set_note(self, op_id: str, note: str, slug: str | None = None) -> None:
        st = self._acting(slug, op_id)
        st.set_note(op_id, note)
        st.save()
        self._snapshot(self.ops())

    def set_weights(self, weights: dict, slug: str | None = None) -> None:
        st = self.state(self.person(slug))
        st.sync()
        clean = {}
        for k, v in (weights or {}).items():
            if k in DEFAULT_WEIGHTS:
                clean[k] = max(-100.0, min(100.0, float(v)))
        st.data["weights"] = clean
        st.save()

    def reset_weights(self, slug: str | None = None) -> None:
        st = self.state(self.person(slug))
        st.sync()
        st.data["weights"] = {}
        st.save()

