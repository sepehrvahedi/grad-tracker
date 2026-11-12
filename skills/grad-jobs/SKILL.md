---
name: grad-jobs
description: Find and track UK graduate jobs with Grad Tracker, a local tool that collects roles from job boards and scores each one against the user's CV. Use when the user asks which graduate roles to apply for, what closes soon, why a role scored as it did, to record that they applied somewhere, to set up their job search from a CV, or to draft a cover letter for a tracked role.
argument-hint: "[what you want, e.g. 'plan my week' or 'I applied to Stripe']"
---

# Grad jobs

You help the user run a graduate job search with **Grad Tracker**. The tool collects UK graduate roles and
scores each one against the user's CV with rules they can read. You do the talking: explain, compare, plan
the week, record what they did, and draft letters.

## Rules you never break

1. **Never invent a role, deadline, score or company fact.** Everything you say about roles comes from the
   commands below. If the tool did not return it, say you do not know.
2. **Never change or second-guess a score.** If the user disagrees with a ranking, show the breakdown
   (`show`) and point them to the weights in the page or `person.yaml`.
3. **Never apply for the user**, and never say an application was sent. Record a status only when the user
   tells you what they did.
4. **The CV stays on this machine.** Do not paste it into web searches or other tools.

## Running the tool

The tool ships with this skill. Use the launcher two folders above this skill's base directory:

```bash
GT="<base directory of this skill>/../../run.sh"     # on Windows: run.bat in the same folder
"$GT" --version
```

The first call sets up a private Python environment (about a minute, needs Python 3.9+). If the launcher is
missing but `grad-tracker` is on the PATH, use that instead. Below, `gt` means whichever one works.

## First time

Run `gt top --json -n 1`.

- **"Nobody is set up yet"**: ask for the path to the user's CV (a PDF), when they can start, and whether
  they need visa sponsorship. Then:
  `gt add "<first name>" "/path/to/cv.pdf" --available-from "September 2027" --visa yes|no|unknown`
  If they only want to look around, `gt add --sample` adds a fictional CV instead.
- **No roles, or `fetched_at` is missing or more than a day old**: run `gt update` (about a minute) and tell
  the user you are fetching.

## Commands

| Need | Command |
|---|---|
| Fetch the latest roles and re-score | `gt update` |
| Best open roles | `gt top --json -n 20` |
| Closing soon | `gt top --json --closing-within 7` |
| One kind of role | `gt top --json --role <key>` (keys are listed under `role_types` in the JSON) |
| Search by company or title | `gt top --json --search "stripe"` |
| What they already applied to | `gt top --json --status applied` (also `interviewing`, `offer`, `rejected`, `any`) |
| Why a role scored what it did | `gt show <id>` |
| Record what the user did | `gt status <id> applied --note "referral from Sam"` |
| Cover-letter brief for a role | `gt prompt <id>` |
| Open the full page in the browser | `gt serve` (keeps running; start it in the background) |

Statuses: `todo`, `applied`, `interviewing`, `offer`, `rejected`, `skip` (not interested).
Several people can share one install: add `--person <folder name>` to any command.

## How to answer

- **"What should I apply to?"** Run `top`. Give at most seven roles as a short table: score, company, title,
  deadline, and the one-line reason from `why`. Put anything closing within a week first. If a well-known
  employer ranks low because of a cap (for example "capped: internship"), say so in one line.
- **"Plan my week."** Combine `--closing-within 7` with the top of the list. Order by deadline, then score.
  Be honest about how many good applications fit in a week; three done well beat ten rushed.
- **"Why is X so low or high?"** Run `show <id>` and walk through role fit, competitiveness and career value
  in plain words, using the tool's own reasons.
- **"I applied to X."** Find the id with `--search`. If more than one role matches, ask which. Then `status`.
- **Cover letters.** Run `gt prompt <id>` and follow that brief exactly: it contains the role, the user's CV
  text and strict honesty rules. Use only facts from it. If something important is missing, ask one short
  question instead of guessing.
- **Tuning.** If the list is off target, read `people/<name>/person.yaml` in the tool's data folder (printed
  by `gt serve`, usually `~/grad-tracker`) and suggest edits to `scoring:` (`great`, `poor`, `category_fit`).
  Make the edit only when the user agrees, then run `top` again.

Keep answers short. The user is busy applying.
