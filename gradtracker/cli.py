"""Command line.

    grad-tracker                 fetch every source, score, open the UI
    grad-tracker demo            the same, with a fictional sample CV (try it before adding yours)
    grad-tracker serve           open the UI on the roles fetched last time
    grad-tracker update          fetch and score only
    grad-tracker add NAME CV.pdf add a person from the terminal (the UI can do this too; --sample for a fictional CV)
    grad-tracker top             the best open roles as a table (--json for scripts and AI assistants)
    grad-tracker show ID         one role with its score breakdown
    grad-tracker status ID applied   record what you did
    grad-tracker prompt ID       a cover-letter prompt for that role
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .config import SAMPLE_CV, load_settings
from .llm import LLMError
from .server import make_server
from .service import Tracker
from .store import STATUSES

COMMANDS = ("run", "demo", "serve", "update", "add", "top", "show", "status", "prompt")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grad-tracker", description=__doc__.split("\n\n")[0].strip(),
                                epilog=__doc__.split("\n\n", 1)[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"grad-tracker {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="folder for your CVs and data (default: this checkout, or ~/grad-tracker)")
    common.add_argument("--person", help="whose list (folder name in people/); default: the first person")
    sub = p.add_subparsers(dest="command")

    for name, text in (("run", "fetch, score and open the UI (the default)"),
                       ("demo", "run with the fictional sample CV"),
                       ("serve", "open the UI without fetching")):
        c = sub.add_parser(name, parents=[common], help=text)
        c.add_argument("--port", type=int, default=8787)
        c.add_argument("--no-open", action="store_true", help="do not open the browser")
        c.add_argument("--no-fetch", action="store_true", help="skip fetching (same as serve)")
        c.add_argument("--rescore", action="store_true", help="re-score every open position")

    c = sub.add_parser("update", parents=[common], help="fetch and score, no UI")
    c.add_argument("--rescore", action="store_true")

    c = sub.add_parser("add", parents=[common], help="add a person from a CV PDF")
    c.add_argument("name", nargs="?", help="their first name")
    c.add_argument("cv", nargs="?", type=Path, help="path to their CV (PDF)")
    c.add_argument("--sample", action="store_true", help="add the fictional sample CV instead, to look around")
    c.add_argument("--looking-for", default="")
    c.add_argument("--available-from", default="", help='e.g. "September 2027"')
    c.add_argument("--visa", choices=("yes", "no", "unknown"), default="unknown", help="do you need visa sponsorship?")

    c = sub.add_parser("top", parents=[common], help="the best roles as a table or JSON")
    c.add_argument("-n", "--limit", type=int, default=20)
    c.add_argument("--min", type=int, default=0, dest="min_score", help="minimum score")
    c.add_argument("--role", help="role type key, e.g. software, data, quant (see --json output for keys)")
    c.add_argument("--search", help="text that must appear in the company or title")
    c.add_argument("--closing-within", type=int, metavar="DAYS")
    c.add_argument("--status", default="todo", help="your status: todo (default), applied, interviewing, ... or any")
    c.add_argument("--include-upcoming", action="store_true", help="also roles that have not opened yet")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("show", parents=[common], help="one role with its score breakdown")
    c.add_argument("id")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("status", parents=[common], help="set your status on a role")
    c.add_argument("id")
    c.add_argument("status", choices=tuple(STATUSES))
    c.add_argument("--note")

    c = sub.add_parser("prompt", parents=[common], help="cover-letter prompt for a role")
    c.add_argument("id")
    return p


def _brief(r: dict) -> dict:
    keys = ("id", "score", "company", "title", "url", "closing", "days_left", "locations", "roles", "domains",
            "sponsors_visa", "letter", "my_status", "is_new")
    out = {k: r.get(k) for k in keys}
    out["listing"] = r.get("trackr_status")
    out["why"] = {p["label"]: p.get("why", "") for p in r.get("parts", []) if p.get("kind") == "dim"}
    out["caps"] = [c["why"] for c in r.get("caps", [])]
    return out


def _select(view: dict, a) -> list[dict]:
    rows = []
    q = (a.search or "").lower()
    for r in view["positions"]:
        if r["score"] is None or r["score"] < a.min_score:
            continue
        if a.status != "any" and r["my_status"] != a.status:
            continue
        if r["trackr_status"] == "closed" or (r["trackr_status"] == "upcoming" and not a.include_upcoming):
            continue
        if a.role and a.role not in (r.get("roles") or []):
            continue
        if q and q not in f"{r['company']} {r['title']}".lower():
            continue
        if a.closing_within is not None and not (r["days_left"] is not None and 0 <= r["days_left"] <= a.closing_within):
            continue
        rows.append(r)
    return rows[:max(0, a.limit)]


def _table(rows: list[dict]) -> str:
    if not rows:
        return "No roles match. Try `grad-tracker update` first, or loosen the filters."
    lines = [f"{'SCORE':>5}  {'CLOSES':<10}  {'ID':<22}  ROLE"]
    for r in rows:
        left = "" if r["days_left"] is None else f" ({r['days_left']}d)"
        lines.append(f"{r['score']:>5}  {(r['closing'] or '-'):<10}  {r['id'][:22]:<22}  {r['company']}: {r['title']}{left}")
    return "\n".join(lines)


def _show(r: dict) -> str:
    out = [f"{r['company']}: {r['title']}", f"score {r['score']}   id {r['id']}", f"apply: {r.get('url') or 'not open yet'}",
           f"closes: {r.get('closing') or 'not stated'}   visa sponsorship: {r.get('sponsors_visa')}   "
           f"cover letter: {r.get('letter') or 'not asked'}", ""]
    for p in r.get("parts", []):
        if p["kind"] == "dim":
            out.append(f"  {p['label']:<16} {p['value']:>3}  (weight {p['weight']}%)  {p.get('why', '')}")
        else:
            out.append(f"  {p['label']:<16} {p['points']:+g}  {p.get('why', '')}")
    out += [f"  capped at {c['max']:g}: {c['why']}" for c in r.get("caps", [])]
    if r.get("note"):
        out += ["", f"note: {r['note']}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a in COMMANDS for a in argv) and not any(a in ("-h", "--help", "--version") for a in argv):
        argv.insert(0, "run")
    a = build_parser().parse_args(argv)
    s = load_settings(a.home)
    tracker = Tracker(s)
    say = lambda m: print(m, flush=True)

    if a.command == "add":
        if a.sample:
            a.name, cv_path = a.name or "Alex (sample)", SAMPLE_CV
            a.available_from, a.visa = a.available_from or "September 2027", "no" if a.visa == "unknown" else a.visa
        elif not a.name or not a.cv:
            print('Usage: grad-tracker add "Name" path/to/cv.pdf   (or: grad-tracker add --sample)', file=sys.stderr)
            return 1
        else:
            cv_path = a.cv
        if not cv_path.exists():
            print(f"No such file: {cv_path}", file=sys.stderr)
            return 1
        a.cv = cv_path
        try:
            p = tracker.add_person(a.name, a.cv.read_bytes(), looking_for=a.looking_for,
                                   available_from=a.available_from, needs_visa_sponsorship=a.visa)
        except ValueError as e:
            print(f"Could not add {a.name}: {e}", file=sys.stderr)
            return 1
        say(f"Added {p.name}. Preferences are in {p.folder / 'person.yaml'}. Next: grad-tracker update")
        return 0

    if a.command in ("top", "show", "status", "prompt"):
        try:
            if a.command == "status":
                tracker.set_status(a.id, a.status, a.person)
                if a.note is not None:
                    tracker.set_note(a.id, a.note, a.person)
                say(f"{a.id}: {STATUSES[a.status]}")
                return 0
            if a.command == "prompt":
                say(tracker.prompt(a.id, a.person))
                return 0
            view = tracker.view(a.person)
            if view["needs_setup"]:
                print("Nobody is set up yet. Run `grad-tracker` and drop in a CV, or `grad-tracker add NAME CV.pdf`.",
                      file=sys.stderr)
                return 1
            if a.command == "top":
                rows = _select(view, a)
                if a.json:
                    say(json.dumps({"person": view["person"]["name"], "fetched_at": view["meta"].get("fetched_at"),
                                    "role_types": [x["key"] for x in view["role_types"]],
                                    "roles": [_brief(r) for r in rows]}, ensure_ascii=False, indent=1))
                else:
                    say(_table(rows))
                return 0
            row = next((r for r in view["positions"] if r["id"] == a.id), None)
            if row is None:
                raise KeyError(a.id)
            say(json.dumps(row, ensure_ascii=False, indent=1) if a.json else _show(row))
            return 0
        except KeyError as e:
            print(f"Unknown role or person: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1

    if a.command == "demo" and not any(p.ready for p in tracker.people()):
        tracker.add_person("Alex (sample)", SAMPLE_CV.read_bytes(), available_from="September 2027",
                           needs_visa_sponsorship="no", looking_for="A graduate software engineering role in the UK.")
        say("Added the fictional sample CV. Add your own from the UI whenever you like.")

    fetch = a.command in ("run", "demo", "update") and not getattr(a, "no_fetch", False)
    if a.command == "update" or (fetch and tracker.people()):
        try:
            tracker.update(progress=say, force=a.rescore, only=a.person)
        except LLMError as e:
            print(f"Scoring failed ({e}). The last scores are kept.", file=sys.stderr)
        except Exception as e:
            print(f"Update failed: {e.__class__.__name__}: {e}. The last data is kept.", file=sys.stderr)
        if a.command == "update":
            return 0

    try:
        server = make_server(tracker, a.port)
    except OSError:
        print(f"Port {a.port} is busy (is Grad Tracker already running?). Try --port {a.port + 1}.", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{a.port}/"
    say(f"Grad Tracker is running at {url}  (Ctrl+C to stop). Your data: {s.home}")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say("\nStopped. Your statuses and notes are saved.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
