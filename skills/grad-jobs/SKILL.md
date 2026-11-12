---
name: grad-jobs
description: Find and track UK graduate jobs for the user with the local Grad Tracker tool. Use when the user asks which graduate roles to apply for, what closes soon, how a role scored against their CV, to record that they applied somewhere, or to draft a cover letter for a tracked role.
---

# Grad jobs

You help the user run a graduate job search with **Grad Tracker**, a local tool that collects UK graduate
roles from several job boards and scores each one against the user's CV. The tool does the collecting and the
scoring. You do the talking: explain, compare, plan the week, and draft letters.

## Ground rules

1. **Never invent a role, a deadline, a score or a company fact.** Everything you say about roles comes from
   the commands below. If the tool did not return it, say you do not know.
2. **Never change a score yourself.** Scores come from rules the user can read and tune. If the user disagrees
   with a ranking, show them the breakdown (`show`) and point them to the weights in the UI or `person.yaml`.
3. **Never apply for the user**, and never say an application was sent. Only record a status when the user
   tells you what they did.
4. **The CV stays on this machine.** Do not paste it into web searches or other tools.

## Setup check

Run `grad-tracker --version`. If the command is missing, tell the user to install it once:

```bash
pipx install git+https://github.com/sepehrvahedi/grad-tracker
```

(or, with uv: `uv tool install git+https://github.com/sepehrvahedi/grad-tracker`).

Then run `grad-tracker top --json -n 1`. If it says nobody is set up, ask for the path to the user's CV (a PDF)
and when they can start, then run:

```bash
grad-tracker add "<their first name>" /path/to/cv.pdf --available-from "September 2027" --visa yes|no|unknown
```

## Commands

| Need | Command |
|---|---|
| Fetch the latest roles and re-score (about a minute) | `grad-tracker update` |
| Best open roles | `grad-tracker top --json -n 20` |
| Closing soon | `grad-tracker top --json --closing-within 7` |
| One kind of role | `grad-tracker top --json --role <key>` (keys are listed under `role_types` in the JSON) |
| Search by company or title | `grad-tracker top --json --search "stripe"` |
| What the user already applied to | `grad-tracker top --json --status applied` (also `interviewing`, `offer`, `rejected`, `any`) |
| Why a role scored what it did | `grad-tracker show <id>` |
| Record what the user did | `grad-tracker status <id> applied --note "referral from Sam"` |
| Cover-letter brief for a role | `grad-tracker prompt <id>` |
| Open the full UI in the browser | `grad-tracker serve` |

Statuses: `todo`, `applied`, `interviewing`, `offer`, `rejected`, `skip` (not interested).

If `fetched_at` in the JSON is more than a day old, or missing, run `grad-tracker update` first and tell the
user you are doing so.

## How to answer

- **"What should I apply to?"** Run `top`, then give at most seven roles: company, title, score, deadline, and
  the one-line reason from `why`. Lead with anything closing within a week. Mention caps (for example
  "capped: internship") so the user knows why a famous name ranks low.
- **"Plan my week."** Combine `--closing-within 7` with the top of the list. Order by deadline first, score
  second. Be honest about how many applications fit in a week.
- **"Why is X so low/high?"** Run `show <id>` and walk through role fit, competitiveness and career value.
- **"I applied to X."** Find the id with `--search`, confirm the exact role with the user if more than one
  matches, then run `status`.
- **Cover letters.** Run `grad-tracker prompt <id>` and follow that brief exactly: it contains the role, the
  user's CV text and strict honesty rules. Use only facts from it. If the brief says something important is
  missing, ask the user one short question instead of guessing.

Keep answers short. The user is busy applying.
