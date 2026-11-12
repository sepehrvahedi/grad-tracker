"""Read a profile straight from any CV's text, so a new person needs nothing but a PDF.

Detects: how well the CV matches each kind of role (all Trackr categories across Tech, Finance and Engineering,
plus research), years of work experience (from date ranges outside education lines), the highest degree, and
the CV's main skills. Everything here is a default: settings in person.yaml always win.
"""

from __future__ import annotations

import re
from datetime import date

CATEGORY_PROTOTYPES = {
    # Tech
    "Software Engineering": "software engineer developer backend frontend full stack api microservices java python "
                            "kotlin go golang c++ typescript javascript distributed systems databases web services "
                            "scalable system design git",
    "Data Science": "data science data scientist statistics statistical modelling regression analysis pandas numpy sql "
                    "visualisation experiments hypothesis testing analytics forecasting r tableau",
    "AI and Machine Learning": "machine learning deep learning neural networks pytorch tensorflow jax llm nlp computer "
                               "vision model training inference transformers reinforcement learning",
    "Quant Developer": "quantitative developer trading systems low latency c++ algorithms competitive programming "
                       "market data exchange performance",
    "DevOps and Infrastructure": "kubernetes docker cloud aws gcp azure terraform ci cd infrastructure sre monitoring "
                                 "linux networking observability",
    "Cyber Security": "security cyber penetration testing vulnerability threat soc incident response cryptography "
                      "network security malware",
    "IT": "it support helpdesk service desk troubleshooting hardware windows active directory office 365 ticketing",
    "Tech Consulting": "consulting client stakeholder digital transformation business analysis requirements erp sap "
                       "salesforce implementation",
    # Finance
    "Trading and Quant": "quantitative trading research probability statistics stochastic calculus mathematics "
                         "derivatives options pricing alpha market making",
    "Buy-Side": "investment fund portfolio hedge fund asset allocation equities research",
    "Asset Management": "asset management portfolio investment fund equities fixed income client",
    "Bulge Bracket": "investment banking financial modelling valuation mergers acquisitions dcf markets sales trading",
    "Elite Boutique": "investment banking advisory mergers acquisitions financial modelling valuation",
    "Middle Market": "corporate finance mergers acquisitions due diligence valuation transaction",
    "Accounting and Audit": "accounting audit tax financial statements ifrs bookkeeping acca aca",
    "Big 4": "audit tax advisory consulting accounting assurance",
    "Pensions and Insurance": "actuarial insurance pensions risk underwriting reserving",
    "Consulting": "management consulting strategy business case stakeholder market research",
    "Real Estate": "real estate property valuation surveying",
    # Engineering
    "Construction & Infrastructure": "civil structural engineering construction site autocad revit bridges "
                                     "geotechnical highways rail",
    "Energy & Utilities": "energy power systems grid renewable nuclear electrical utilities water wind solar fusion",
    "Manufacturing & Product Design": "mechanical engineering cad solidworks manufacturing product design prototyping "
                                      "materials",
    "Aerospace & Defence": "aerospace aerodynamics avionics aircraft propulsion defence flight control cfd",
    "Multi-Sector Consultancy": "engineering consultancy design projects clients infrastructure",
    "Electronics & Digital Systems": "electronics embedded fpga circuit pcb vhdl verilog signal processing firmware",
    "Chemicals & Oil": "chemical engineering process plant petroleum reaction thermodynamics",
    "Automotive": "automotive vehicle powertrain battery chassis motorsport",
    "Scientific Research & Health": "scientific research laboratory medical devices biology physics health clinical "
                                    "imaging instrumentation",
    # derived from titles of non-Trackr listings
    "Research": "research scientist publications experiments physics simulation modelling theory thesis scientific "
                "computing numerical methods",
    "Quant": "quantitative research trading probability statistics stochastic mathematics",
}

SKILLS = [s.strip() for s in """
python, java, kotlin, go, golang, c++, c#, rust, scala, javascript, typescript, sql, r, matlab, julia, fortran,
pytorch, tensorflow, jax, scikit-learn, pandas, numpy, spark, kafka, kubernetes, docker, aws, gcp, azure, terraform,
linux, grpc, spring, react, node, machine learning, deep learning, reinforcement learning, computer vision, nlp, llm,
statistics, bayesian, probability, optimisation, optimization, simulation, monte carlo, numerical methods,
computational physics, quantum computing, quantum mechanics, quantum, condensed matter, particle physics,
astrophysics, cosmology, fluid dynamics, cfd, finite element, thermodynamics, electromagnetism, optics, photonics,
plasma, fusion, materials, chemistry, molecular dynamics, climate, weather, geophysics, signal processing,
control systems, distributed systems, microservices, backend, frontend, databases, data engineering, mlops, triton,
cuda, hpc, data analysis, data visualisation, tableau, excel, financial modelling, valuation, accounting, audit,
autocad, solidworks, revit, embedded, fpga, verilog, vhdl, physics
""".split(",") if s.strip()]

MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
DATE = rf"(?:(?:{MONTHS})\.?,?\s+)?(?:19|20)\d\d|\d{{1,2}}/(?:19|20)\d\d"  # PDF text often glues "MarketplaceDec 2025"
RANGE = re.compile(rf"({DATE})\s*(?:-|–|—|to|until)\s*({DATE}|present|current|now|ongoing|today)", re.I)
EDUCATION = re.compile(r"\b(universit|college|school|bsc|b\.sc|msc|m\.sc|meng|beng|ba|ma|mphil|phd|dphil|bachelor|"
                       r"master|degree|gcse|a-?levels?|ib diploma|coursework|modules|gpa|thesis)\b", re.I)
DEGREES = [("phd", re.compile(r"\b(ph\.?d|dphil|doctor of philosophy)\b", re.I)),
           ("msc", re.compile(r"\b(m\.?sc|meng|m\.eng|mphil|mres|msci|mmath|mphys|master'?s?|ma\b|mba)", re.I)),
           ("bsc", re.compile(r"\b(b\.?sc|beng|b\.eng|ba\b|bachelor'?s?|bs\b)", re.I))]
INTERNSHIP_LINE = re.compile(r"\b(intern|internship|placement|summer)\b", re.I)


MONTH_INDEX = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                             "nov", "dec"])}


def _month(text: str, end: bool) -> float | None:
    """'Jan 2021' / '03/2021' / '2021' / 'Present' -> year as a decimal (a bare year spans the whole year)."""
    t = text.strip().lower()
    if t in ("present", "current", "now", "ongoing", "today"):
        d = date.today()
        return d.year + (d.month - 1) / 12
    m = re.search(r"(\d{1,2})/((?:19|20)\d\d)", t)
    if m:
        return int(m.group(2)) + (int(m.group(1)) - 1) / 12
    y = re.search(r"(?:19|20)\d\d", t)
    if not y:
        return None
    mon = re.match(r"[a-z]{3}", t)
    idx = MONTH_INDEX.get(mon.group(0)) if mon else None
    return int(y.group(0)) + (idx if idx is not None else (11 if end else 0)) / 12


HEADING = re.compile(r"^\s*(work experience|professional experience|employment(?: history)?|experience|"
                     r"education|research(?: experience)?|projects?|selected projects|publications|skills|"
                     r"technical skills|awards|honou?rs|achievements|certifications|interests|volunteering|"
                     r"leadership|activities|teaching|languages|references)\s*:?\s*$", re.I)
WORK_HEADING = re.compile(r"experience|employment", re.I)


def experience_years(cv: str) -> float:
    """Time covered by job date ranges (overlaps merged). Only the work-experience section counts when the CV
    has one; education lines never count; internships and part-time jobs count half."""
    lines = cv.splitlines()
    headings = [(i, HEADING.match(l).group(1)) for i, l in enumerate(lines) if HEADING.match(l)]
    has_work_section = any(WORK_HEADING.search(h) and not h.lower().startswith("research") for _, h in headings)
    spans = []
    section = ""
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m:
            section = m.group(1).lower()
            continue
        if has_work_section and not (WORK_HEADING.search(section) and not section.startswith("research")):
            continue
        for a, b in RANGE.findall(line):
            context = " ".join(lines[max(0, i - 1):i + 1])
            if EDUCATION.search(line):
                continue
            s, e = _month(a, False), _month(b, True)
            if s is None or e is None or e < s or e - s > 15:
                continue
            half = INTERNSHIP_LINE.search(context) or re.search(r"part[\s-]?time", context, re.I)
            spans.append((s, e + 1 / 12, 0.5 if half else 1.0))
    total, cur_end = 0.0, None
    for s, e, w in sorted(spans):
        if cur_end is not None and s < cur_end:
            if e > cur_end:
                total += (e - cur_end) * w
                cur_end = e
            continue
        total += (e - s) * w
        cur_end = e
    return round(total, 1)


def highest_degree(cv: str) -> str:
    for name, rx in DEGREES:
        if rx.search(cv):
            return name
    return "bsc"


def main_skills(cv: str, n: int = 12) -> list[str]:
    """The skills from SKILLS the CV mentions most (single letters like "r" only as a listed skill)."""
    text = " " + re.sub(r"\s+", " ", cv.lower()) + " "
    counts = {}
    for phrase in SKILLS:
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9+#])"
        if len(phrase) == 1:
            pattern = r"(?<=[,(/] )" + re.escape(phrase) + r"(?=[,)/ ])"
        c = len(re.findall(pattern, text))
        if c:
            counts[phrase] = c
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))][:n]


def category_fit(cv: str, tfidf) -> dict:
    """35..85 per category from TF-IDF similarity between the CV and each category's description."""
    cv_vec = tfidf.vec(cv)
    sims = {c: tfidf.cosine(cv_vec, tfidf.vec(text)) for c, text in CATEGORY_PROTOTYPES.items()}
    top = max(sims.values()) or 1.0
    return {c: round(35 + 50 * (max(0.0, s) / top) ** 0.7) for c, s in sims.items()}


def detect(cv: str, tfidf) -> dict:
    return {"experience_years": experience_years(cv), "degree": highest_degree(cv),
            "main_skills": main_skills(cv), "category_fit": category_fit(cv, tfidf)}
