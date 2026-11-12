"""Role types for the quick filters in the UI ("Software engineering", "Data science", ...).

A position can be several types (an "ML Engineer" is both machine learning and software engineering).
Types come from the title first, then from Trackr's categories when the title says nothing specific.
"""

from __future__ import annotations

import re

# key, label, title pattern, Trackr categories that imply it
FAMILIES = [
    ("swe", "Software engineering",
     r"software|developer|\bswe\b|back[\s-]?end|front[\s-]?end|full[\s-]?stack|programmer|\bsde\b|"
     r"application engineer|product engineer|technologist|campus engineer|technology graduate",
     {"Software Engineering"}),
    ("data", "Data science",
     r"data scien|statistic|decision scien|data (?:&|and) ai|insight analyst|actuarial data",
     {"Data Science"}),
    ("ml", "Machine learning & AI",
     r"machine learning|\bml\b|\bai\b|deep learning|\bllms?\b|computer vision|\bnlp\b|applied scien|"
     r"artificial intelligence|research resident",
     {"AI and Machine Learning"}),
    ("quant_research", "Quant research & trading",
     r"\bquant(?:itative)?\b(?: \w+)? (?:research|trad|analyst|strateg)|\btrader\b|trading analyst|"
     r"algorithm developer|crypto researcher|\bstrats?\b|^graduate quant$",
     {"Trading and Quant"}),
    ("quant_dev", "Quant development",
     r"\bquant(?:itative)? (?:develop|software|engineer)|trading (?:systems|technolog|software)|\bc\+\+",
     {"Quant Developer"}),
    ("science", "Research & physics",
     r"scientist|physic|simulation|computational|quantum|research engineer|\br&d\b|\bcfd\b|"
     r"modell?ing|materials|plasma|fusion|nuclear|laborator|research associate|research assistant",
     {"Scientific Research & Health", "Research"}),
    ("analytics", "Data analysis & engineering",
     r"data analy|data engineer|analytics|business intelligence|\bbi\b|reporting analyst|data management",
     set()),
    ("infra", "Cloud, DevOps & infrastructure",
     r"devops|\bsre\b|site reliability|infrastructure|cloud|platform engineer|network|systems engineer|"
     r"production engineer",
     {"DevOps and Infrastructure"}),
    ("security", "Cyber security",
     r"cyber|security|\bsoc\b|penetration|threat",
     {"Cyber Security"}),
    ("hardware", "Hardware & electronics",
     r"hardware|electronic|embedded|firmware|silicon|\bfpga\b|\basic\b|\bsoc\b architecture|chip|"
     r"electrical engineer|semiconductor",
     {"Electronics & Digital Systems"}),
    ("engineering", "Civil, mechanical & other engineering",
     r"civil|structural|mechanical|chemical engineer|aerospace|manufactur|process engineer|geotech|"
     r"design engineer|building services|mechatronic|automotive|graduate engineer\b",
     {"Construction & Infrastructure", "Energy & Utilities", "Manufacturing & Product Design", "Aerospace & Defence",
      "Multi-Sector Consultancy", "Chemicals & Oil", "Automotive"}),
    ("consulting", "Consulting & IT",
     r"consult|\bit\b support|service desk|helpdesk|business analyst|technology analyst|\bsap\b|erp|servicenow",
     {"Tech Consulting", "IT", "Consulting", "Big 4"}),
    ("finance", "Finance & banking",
     r"investment bank|\bm&a\b|corporate finance|audit|account|actuar|risk analyst|underwrit|wealth|"
     r"asset management|markets analyst|sales (?:and|&) trading",
     {"Bulge Bracket", "Elite Boutique", "Middle Market", "Asset Management", "Buy-Side", "Accounting and Audit",
      "Pensions and Insurance", "Real Estate"}),
]
LABELS = [{"key": k, "label": label} for k, label, _, _ in FAMILIES]
_COMPILED = [(k, re.compile(rx, re.I), cats) for k, _, rx, cats in FAMILIES]


def role_types(op: dict) -> list[str]:
    title = op.get("title") or ""
    by_title = [k for k, rx, _ in _COMPILED if rx.search(title)]
    cats = set(op.get("categories") or [])
    by_cat = [k for k, _, implied in _COMPILED if implied & cats]
    if by_title:
        # the title decides; categories only add types the title does not contradict
        extra = [k for k in by_cat if k not in by_title and k in ("data", "ml", "quant_dev", "infra", "science")
                 and not {"finance", "consulting", "engineering", "analytics"} & set(by_title)]
        return by_title + extra
    if by_cat:
        return by_cat
    snippet = (op.get("snippet") or "")[:300]
    return [k for k, rx, _ in _COMPILED if rx.search(snippet)][:2] or ["other"]


# ---------------------------------------------------------------- domains ("physics", "finance", ...)
# key, label, synonyms people may type, keyword pattern, Trackr categories/industries that imply it
DOMAINS = [
    ("physics", "Physics & physical sciences", ["physics", "physical sciences", "science", "quantum", "fusion"],
     r"physics|physicist|physical scien|quantum|plasma|fusion|nuclear|photonic|optics|optical|laser|astro|cosmolog|particle|semiconductor|"
     r"\bcfd\b|fluid dynamic|simulation|materials? scien|metrolog|accelerator|synchrotron|spectroscop|geophysic|"
     r"instrumentation|electromagnet|thermodynamic|computational (?:science|physics|modell?ing)",
     set()),
    ("finance", "Finance & trading", ["finance", "financial", "banking", "bank", "trading", "quant", "fintech", "investment"],
     r"financ|\bbank|trading|trader|\bquant(?!um)|invest|asset management|wealth|insurance|actuar|fintech|payments?|"
     r"hedge fund|capital markets|\bcredit\b|private equity|brokerage|exchange",
     {"Finance"}),
    ("health", "Healthcare & biotech", ["health", "healthcare", "medical", "medicine", "biotech", "bio", "pharma", "biology"],
     r"health|medic|clinical|pharma|biotech|biolog|drug|genom|protein|hospital|\bnhs\b|life science|"
     r"bioinformatic|neuro|cancer|patient",
     set()),
    ("energy", "Energy & climate", ["energy", "climate", "sustainability", "renewables", "environment", "weather"],
     r"\benergy|power (?:systems|grid|station)|\bgrid\b|renewable|wind|solar|oil|\bgas\b|climate|weather|carbon|"
     r"sustainab|utilit|net zero|decarboni|hydrogen|battery|environment",
     {"Energy & Utilities"}),
    ("defence", "Defence, aerospace & space", ["defence", "defense", "aerospace", "space", "aviation"],
     r"defen[cs]e|aerospace|aircraft|\bspace\b|satellite|missile|military|avionic|naval|submarine",
     {"Aerospace & Defence"}),
    ("transport", "Transport & automotive", ["transport", "automotive", "cars", "rail", "mobility", "logistics"],
     r"automotive|vehicle|\brail|transport|motorsport|autonomous driving|self-driving|logistic|mobility|highways",
     {"Automotive"}),
    ("ai", "AI labs & AI products", ["ai", "artificial intelligence", "machine learning", "ml", "llm"],
     r"\bai\b|artificial intelligence|machine learning|\bllms?\b|deep learning|generative",
     {"AI and Machine Learning"}),
    ("tech", "Tech & software products", ["tech", "software", "saas", "internet", "startup"],
     r"\bsaas\b|platform|software company|start-?up|cloud|developer tools|e-?commerce|marketplace",
     {"Startups"}),
    ("construction", "Construction & infrastructure", ["construction", "civil", "infrastructure", "property", "built environment"],
     r"construction|civil engineer|structural|infrastructure project|building|property|real estate|architect",
     {"Construction & Infrastructure", "Real Estate"}),
    ("public", "Government, public sector & academia", ["government", "public sector", "academia", "university", "research institute", "civil service"],
     r"\buniversity\b|government|public sector|civil service|\bcouncil\b|ministry|\bnhs\b|research council|institute",
     set()),
    ("consumer", "Retail, media & consumer", ["retail", "media", "gaming", "games", "consumer", "e-commerce"],
     r"retail|consumer|\bmedia\b|gaming|\bgames?\b|entertainment|broadcast|fashion|food|grocer|e-?commerce|music|sport",
     set()),
]
DOMAIN_LABELS = [{"key": k, "label": label, "synonyms": syn} for k, label, syn, _, _ in DOMAINS]
_DOMAINS = [(k, re.compile(rx, re.I), cats) for k, _, _, rx, cats in DOMAINS]


def domains(op: dict, employer_domains: set | None = None) -> list[str]:
    """Domains of a position: from the employer list (employers.yaml `domains:`), Trackr category/industry,
    the title and company name, or at least three separate keywords in the descriptions."""
    found = list(employer_domains or [])
    strong = " ".join([op.get("title") or "", op.get("company") or ""])
    weak = " ".join([op.get("company_description") or "", op.get("snippet") or "", " ".join(op.get("divisions") or [])])
    tags = set(op.get("categories") or []) | {op.get("industry") or ""}
    for k, rx, implied in _DOMAINS:
        if k in found:
            continue
        if implied & tags or rx.search(strong) or len({m.lower() for m in rx.findall(weak)}) >= 3:
            found.append(k)
    if op.get("source") == "jobs.ac.uk" and "public" not in found:
        found.append("public")
    return found


def resolve_domain(text: str) -> str | None:
    """'Physics' / 'physical sciences' / 'quantum' -> 'physics'; unknown words -> None (plain text search)."""
    t = (text or "").strip().lower()
    for k, label, syn, _, _ in DOMAINS:
        if t in (k, label.lower(), *syn):
            return k
    return None
