"""Copy-ready prompts for any AI assistant: a cover letter built only from your CV, written to not sound generated."""

from __future__ import annotations

import yaml

from .cv import ML_CATEGORIES

NEEDS_LETTER = {"Yes": "required", "Optional": "optional", "Unknown": "unknown"}

BANNED = [
    "I am writing to express", "I am excited", "I am thrilled", "passionate", "passion for", "delve",
    "leverage", "synergy", "cutting-edge", "fast-paced", "dynamic environment", "a testament to",
    "I believe I would be a great fit", "I am confident that", "unique opportunity", "esteemed",
    "seamlessly", "robust", "spearheaded", "tapestry", "landscape", "navigate", "journey", "elevate",
    "Furthermore", "Moreover", "Additionally", "In conclusion", "Thank you for considering my application",
]


def letter_need(op: dict) -> str | None:
    """'required' | 'optional' | 'unknown' when a letter is wanted, None when Trackr says No."""
    return NEEDS_LETTER.get(op.get("cover_letter") or "Unknown")


def build_prompt(op: dict, cv_text: str, facts: dict, *, name: str | None = None, cv_variant: str | None = "auto",
                 closing: str | None = None) -> str:
    """cv_variant: "auto" names the CV variant (ML-platform or backend) for people with two CVs; None leaves it out."""
    need = letter_need(op) or "not requested"
    if cv_variant == "auto":
        cv_variant = "ML-platform" if ML_CATEGORIES.intersection(op.get("categories", [])) else "backend"
    cv_label = f"My CV ({cv_variant} version)" if cv_variant else "My CV"
    closing = closing or ""
    last = (f"one or two sentences: {closing}" if closing else
            "one or two sentences on when I can start, only if the facts above say so")
    banned = ", ".join(f'"{b}"' for b in BANNED)
    about = op.get("company_description") or "(no description in the listing)"
    notes = op.get("notes") or "none"
    snippet = f"\n- From the job description: {op['snippet']}" if op.get("snippet") else ""
    area_parts = list(dict.fromkeys([*(op.get("categories") or []), *(op.get("divisions") or [])]))
    area = ", ".join(area_parts) or "n/a"
    return f"""Write a cover letter for me for the role below. I will paste it as plain text into an application form.

## The role
- Company: {op['company']}
- Programme: {op['title']}
- Area: {area}
- Application link: {op.get('url') or 'n/a'}
- Cover letter: {need}. Written answers: {op.get('written_answers', 'Unknown')}. Closes: {op.get('closing') or 'not stated'}.
- Notes from the listing: {notes}
- About the company: {about}{snippet}

## About me (the only facts you may use)
{cv_label}, extracted from the PDF:
<cv>
{cv_text.strip()}
</cv>

Other facts:
<facts>
{yaml.safe_dump(facts, sort_keys=False, allow_unicode=True).strip()}
</facts>

## What I want
1. 250 to 350 words, British English, plain text. Start with "Dear Hiring Team," and end with "Kind regards," and my name{f" ({name})" if name else ""} on the next line. No subject line, no address block, no date.
2. Paragraph 1: name the programme and give ONE specific, concrete reason I want to work at {op['company']} in particular, tied to what they actually build or do. If you can browse, check their website or engineering blog for one real detail; if you cannot, use the description above. Never invent facts about the company.
3. Paragraphs 2 and 3: pick the two or three achievements from my CV that matter most for THIS role and explain them through the problem, what I did, and the result. Use the numbers exactly as written in my CV (never round up, never turn "1M+" into "millions").
4. Last paragraph: {last} Then a plain closing line. No summary of the letter.

## How it must sound
Write like a confident specialist talking to a peer, not like a template:
- Short, concrete sentences in first person. Vary sentence length. Prefer verbs like built, cut, rewrote, owned, measured.
- Every sentence should be specific to me or to {op['company']}; delete any sentence that could appear in anyone's letter.
- No lists of three adjectives, no rhetorical questions, no em dashes, no exclamation marks, no generic praise ("industry leader", "innovative company").
- Do not repeat my CV line by line; explain why the work matters for this role.
- Never use these words or phrases: {banned}.

## Before you answer
Check silently that every claim about me is in the CV or facts above and every claim about the company is true.
If something important is missing (for example why I chose this company beyond the description), ask me one short question instead of guessing.
Output only the letter."""
