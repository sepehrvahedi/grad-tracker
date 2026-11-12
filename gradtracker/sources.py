"""Every place the tracker looks for graduate roles, normalised to the same shape as Trackr programmes.

* Trackr: curated UK graduate programmes (Tech, Finance, Engineering lists). Switch off with `trackr: {enabled: false}`.
* Company job boards on Greenhouse, Lever and Ashby (public JSON APIs), listed in sources.yaml.
* jobs.ac.uk: research and data roles at universities and institutes (search pages; robots.txt allows them).
* Adzuna: UK job search API, only when ADZUNA_APP_ID / ADZUNA_APP_KEY are in .env.
* Graduate job boards (robots.txt allows each): Gradcracker search pages, TARGETjobs' search service,
  Higherin (formerly RateMyPlacement) search pages and SheCanCode's job board.

A source that fails keeps its previous positions, so one broken board never hides anything.
"""

from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Callable

import requests
import yaml

from . import trackr
from .config import Settings

UA = {"User-Agent": "Mozilla/5.0 (compatible; grad-tracker/0.1; +https://github.com/sepehrvahedi/grad-tracker)", "Accept": "application/json"}
SOURCE_LABELS = {"trackr": "Trackr", "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
                 "jobs.ac.uk": "jobs.ac.uk", "adzuna": "Adzuna", "gradcracker": "Gradcracker",
                 "targetjobs": "TARGETjobs", "higherin": "Higherin", "shecancode": "SheCanCode"}

GRAD = re.compile(r"\b(graduates?|grads?|new[\s-]?grads?|early[\s-]?careers?|entry[\s-]?level|junior|campus|"
                  r"university|trainee|residency|residents?|fellowship|20(?:26|27|28))\b", re.I)
INTERN = re.compile(r"\b(intern|interns|internships?|placements?|summer|spring|insight|off[\s-]?cycle|co-?op|"
                    r"work experience|apprentice(?:ship)?s?|industrial year)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr|staff|principal|lead|leader|manager|director|head|vp|vice president|chief|"
                    r"architect|partner|ii|iii|iv|expert|professor|lecturer|postdoc(?:toral)?)\b", re.I)
UK = re.compile(r"\b(united kingdom|uk|gb|great britain|england|scotland|wales|northern ireland|london|cambridge|"
                r"oxford|edinburgh|glasgow|manchester|bristol|reading|abingdon|harwell|culham|didcot|milton park|"
                r"birmingham|leeds|belfast|cardiff|bath|guildford|southampton|sheffield|newcastle|nottingham|york|"
                r"warwick|coventry|brighton|exeter|daresbury|swindon|farnborough|liverpool|dundee|st andrews|"
                r"aberdeen|leicester|surrey|durham|lancaster|norwich|plymouth|portsmouth|stevenage|bracknell)\b", re.I)
NOT_A_JOB_FOR_GRADS = re.compile(r"\b(recruit\w*|talent|sourcer|people partner|executive assistant|"
                                 r"office manager|receptionist)\b", re.I)
NOT_UK = re.compile(r"new york|cambridge,?\s*(ma|massachusetts|us|usa)\b|massachusetts|ontario|"
                    r"london,?\s*on\b|york,?\s*pa\b|bath,?\s*(me|ny)\b", re.I)


ABROAD = re.compile(r"\b(united arab emirates|uae|dubai|abu dhabi|qatar|doha|saudi|riyadh|china|hong kong|singapore|"
                    r"malaysia|kuala lumpur|australia|new zealand|canada|usa|united states|germany|netherlands|"
                    r"france|switzerland|sweden|denmark|norway|finland|ireland|dublin|spain|italy|belgium|austria|"
                    r"japan|korea|india|pakistan|egypt|south africa|nigeria|kenya|oman|kuwait|bahrain|turkey|"
                    r"israel|cyprus|greece|portugal|poland|czech|luxembourg|iceland|vietnam|hanoi|ho chi minh|suzhou|"
                    r"ningbo|zhejiang|xi'?an|haining|shenzhen|shanghai|beijing|guangzhou|kazakhstan|astana|uzbekistan|"
                    r"tashkent|thailand|bangkok|indonesia|jakarta|philippines|sri lanka|bangladesh|morocco|ghana|"
                    r"brazil|mexico|chile|argentina|colombia|mauritius|serbia|belgrade|hungary|budapest|romania|bucharest|"
                    r"bulgaria|sofia|croatia|zagreb|ukraine|kyiv|lithuania|vilnius|latvia|riga|estonia|tallinn)\b", re.I)


def is_uk(text: str) -> bool:
    """Any UK place, after removing look-alikes such as New York or Cambridge, MA."""
    return bool(UK.search(NOT_UK.sub(" ", text or "")))


def keep_title(title: str, all_levels: bool) -> bool:
    """Graduate-level, not an internship. Small teams (all_levels) keep every non-senior role."""
    if INTERN.search(title) or SENIOR.search(title) or NOT_A_JOB_FOR_GRADS.search(title):
        return False
    return all_levels or bool(GRAD.search(title))


def categorize(title: str) -> list[str]:
    t = title.lower()
    cats = []
    if re.search(r"\bquant(?!um)", t):
        cats.append("Quant")
    if re.search(r"data scien|machine learning|\bml\b|\bai\b|deep learning|research scien|applied scien|"
                 r"statistic|computational|modell?ing|simulation|data analy", t):
        cats.append("Data Science")
    if re.search(r"research", t) and "Data Science" not in cats:
        cats.append("Research")
    if re.search(r"engineer|developer|software|swe\b|programmer|technologist", t):
        cats.append("Software Engineering")
    return cats or ["Other"]


def _text(s: str | None, n: int = 700) -> str:
    s = html.unescape(html.unescape(s or ""))  # Greenhouse double-escapes its HTML
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:n]


def _op(source: str, feed: str, ext_id: str, company: str, title: str, url: str | None, locations: list,
        snippet: str = "", closing: str | None = None, opening: str | None = None, extra_cats: list | None = None) -> dict:
    op_id = f"{source}:{ext_id}"
    return {
        "id": op_id, "source": source, "feed": feed, "industry": None,
        "company": company, "title": title.strip(), "url": trackr.clean_url(url),
        "categories": list(dict.fromkeys([*(extra_cats or []), *categorize(title)])), "divisions": [],
        "locations": [l for l in locations if l], "opening": opening, "closing": closing, "rolling": None,
        "cv": True, "cover_letter": "Unknown", "written_answers": "Unknown", "sponsors_visa": "Unknown",
        "notes": "", "company_description": "", "snippet": snippet,
        "sources": [{"source": source, "id": op_id, "url": trackr.clean_url(url)}],
    }


def _get(http: requests.Session, url: str, **kw):
    r = http.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- company boards
def greenhouse(http: requests.Session, b: dict) -> list[dict]:
    jobs = _get(http, f"https://boards-api.greenhouse.io/v1/boards/{b['board']}/jobs").json().get("jobs", [])
    keep = [j for j in jobs if keep_title(j.get("title", ""), b.get("all_levels", False))
            and is_uk(" ".join([(j.get("location") or {}).get("name", "")] +
                               [o.get("name", "") for o in j.get("offices", []) or []]))]

    def detail(j):
        try:
            return _text(_get(http, f"https://boards-api.greenhouse.io/v1/boards/{b['board']}/jobs/{j['id']}")
                         .json().get("content"))
        except requests.RequestException:
            return ""
    with ThreadPoolExecutor(max_workers=6) as pool:
        snippets = list(pool.map(detail, keep))
    return [_op("greenhouse", f"greenhouse:{b['board']}", str(j["id"]), b["name"], j["title"], j.get("absolute_url"),
                [(j.get("location") or {}).get("name", "")], snippet, opening=(j.get("first_published") or "")[:10] or None)
            for j, snippet in zip(keep, snippets)]


def lever(http: requests.Session, b: dict) -> list[dict]:
    out = []
    for j in _get(http, f"https://api.lever.co/v0/postings/{b['board']}", params={"mode": "json"}).json():
        cat = j.get("categories") or {}
        where = " ".join([cat.get("location") or "", *(cat.get("allLocations") or []), j.get("country") or ""])
        if INTERN.search(cat.get("commitment") or "") or not keep_title(j.get("text", ""), b.get("all_levels", False)):
            continue
        if is_uk(where) or (j.get("country") or "").upper() == "GB":
            out.append(_op("lever", f"lever:{b['board']}", j["id"], b["name"], j["text"], j.get("hostedUrl"),
                           [cat.get("location") or ""], _text(j.get("descriptionPlain"))))
    return out


def ashby(http: requests.Session, b: dict) -> list[dict]:
    out = []
    for j in _get(http, f"https://api.ashbyhq.com/posting-api/job-board/{b['board']}").json().get("jobs", []):
        if j.get("isListed") is False or (j.get("employmentType") or "").lower() == "intern":
            continue
        if not keep_title(j.get("title", ""), b.get("all_levels", False)):
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        places = [j.get("location") or "", addr.get("addressCountry") or "", addr.get("addressLocality") or ""]
        for sl in j.get("secondaryLocations") or []:
            places.append(sl.get("location") or "")
            places.append(((sl.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        if is_uk(" ".join(places)):
            out.append(_op("ashby", f"ashby:{b['board']}", j["id"], b["name"], j["title"], j.get("jobUrl"),
                           [j.get("location") or ""], _text(j.get("descriptionPlain")),
                           opening=(j.get("publishedAt") or "")[:10] or None))
    return out


BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


# ---------------------------------------------------------------- jobs.ac.uk
JAC_ITEM = re.compile(r'<div class="j-search-result__result[^"]*"[^>]*data-advert-id="(\d+)".*?'
                      r'<a href="(/job/[^"]+)">\s*(.*?)\s*</a>.*?'
                      r'j-search-result__employer">\s*<b>(.*?)</b>.*?'
                      r'Location:\s*(.*?)\s*</div>(.*?)(?=<div class="j-search-result__result|<div id="pagination|$)', re.S)
JAC_CLOSE = re.compile(r'Closes</span>\s*<span[^>]*>\s*(\d{1,2} \w{3})\s*<', re.S)
JAC_SKIP = re.compile(r"\b(fellow|fellowship|phd|studentship|professor|lecturer|postdoc|postdoctoral|senior|"
                      r"principal|head|director|manager|chair|reader|chief|distin?gu?i\w*|faculty position|post-?doc)\b", re.I)


def _closing_from_day_month(text: str, today: date) -> str | None:
    try:
        d = datetime.strptime(f"{text} {today.year}", "%d %b %Y").date()
    except ValueError:
        return None
    if (today - d).days > 60:  # "5 Jan" seen in November means next year
        d = d.replace(year=today.year + 1)
    return d.isoformat()


def parse_jobs_ac_uk(page: str, today: date) -> list[dict]:
    out = []
    for advert, path, title, employer, place, rest in JAC_ITEM.findall(page):
        title, employer, place = (_text(x, 200) for x in (title, employer, place))
        abroad = ABROAD.search(place) or ABROAD.search(employer)
        if JAC_SKIP.search(title) or INTERN.search(title) or (abroad and not is_uk(place)):
            continue
        close = JAC_CLOSE.search(rest)
        out.append(_op("jobs.ac.uk", "jobs.ac.uk", advert, employer, title, "https://www.jobs.ac.uk" + path,
                       [place], closing=_closing_from_day_month(close.group(1), today) if close else None))
    return out


def jobs_ac_uk(http: requests.Session, queries: list[str], today: date) -> list[dict]:
    out: dict = {}
    for q in queries:
        page = _get(http, "https://www.jobs.ac.uk/search/", params={"keywords": q, "pageSize": 50}).text
        for op in parse_jobs_ac_uk(page, today):
            out.setdefault(op["id"], op)
    return list(out.values())


# ---------------------------------------------------------------- adzuna (optional)
def adzuna(http: requests.Session, s: Settings, queries: list[str]) -> list[dict]:
    out: dict = {}
    for q in queries:
        data = _get(http, "https://api.adzuna.com/v1/api/jobs/gb/search/1", params={
            "app_id": s.adzuna_app_id, "app_key": s.adzuna_app_key, "what": q, "results_per_page": 50,
            "category": "graduate-jobs", "max_days_old": 45, "content-type": "application/json"}).json()
        for j in data.get("results", []):
            title = _text(j.get("title"), 200)
            if INTERN.search(title) or SENIOR.search(title):
                continue
            op = _op("adzuna", "adzuna", str(j["id"]), (j.get("company") or {}).get("display_name") or "?", title,
                     j.get("redirect_url"), [(j.get("location") or {}).get("display_name", "")],
                     _text(j.get("description")), opening=(j.get("created") or "")[:10] or None)
            out.setdefault(op["id"], op)
    return list(out.values())


# ---------------------------------------------------------------- graduate job boards
MONTHS = "january february march april may june july august september october november december".split()


def parse_long_date(text: str | None) -> str | None:
    """'November 16th, 2026', '30th November 2026' or '16 Nov 2026' -> '2026-11-16'."""
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", (text or "").lower().replace(",", " "))
    m = re.search(r"(\d{1,2})\s+([a-z]{3,})\s+(\d{4})", t) or re.search(r"([a-z]{3,})\s+(\d{1,2})\s+(\d{4})", t)
    if not m:
        return None
    a, b, year = m.groups()
    day, month = (a, b) if a.isdigit() else (b, a)
    idx = next((i for i, name in enumerate(MONTHS) if name.startswith(month[:3])), None)
    if idx is None:
        return None
    try:
        return date(int(year), idx + 1, int(day)).isoformat()
    except ValueError:
        return None


def _grad_board_title(title: str) -> bool:
    """These boards only list early-career roles, so keep every title that isn't an internship or senior."""
    return not (INTERN.search(title) or SENIOR.search(title) or NOT_A_JOB_FOR_GRADS.search(title))


GC_START = re.compile(r'<article\s+wire:key="\d+"\s+data-search-result=')  # the hidden decoy card has no data-search-result
GC_LINK = re.compile(r'href="(https://www\.gradcracker\.com/hub/\d+/([^/"]+)/graduate-job/(\d+)/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>')
GC_FIELD = re.compile(r"\|\s*(Deadline|Location|Starting|Salary)\s*\|\s*([^|]+?)\s*(?=\|)")


def parse_gradcracker(page: str) -> list[dict]:
    out = []
    starts = [m.start() for m in GC_START.finditer(page)] + [len(page)]
    for a, b in zip(starts, starts[1:]):
        block = page[a:b]
        link = GC_LINK.search(block)
        if not link:
            continue
        url, emp_slug, job_id, title = link.groups()
        title = _text(title, 200)
        employer = re.search(r'<img[^>]+alt="([^"]+)"', block)
        employer = html.unescape(employer.group(1)) if employer else emp_slug.replace("-", " ").title()
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", re.sub(r"<svg.*?</svg>", "", block, flags=re.S)))
        flat = re.sub(r"(\|\s*)+", "| ", html.unescape(flat))
        fields = dict(GC_FIELD.findall(flat))
        if not _grad_board_title(title):
            continue
        place = fields.get("Location", "")
        if ABROAD.search(place) and not is_uk(place):
            continue
        snippet = "; ".join(f"{k}: {v}" for k, v in fields.items())
        out.append(_op("gradcracker", "gradcracker", job_id, employer, title, url, [place], snippet,
                       closing=parse_long_date(fields.get("Deadline"))))
    return out


def _get_page_plain(url: str) -> str:
    """Gradcracker answers urllib but rejects requests' TLS client, so fetch it the plain way."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def gradcracker(cfg: dict, fetch: Callable[[str], str] = _get_page_plain) -> list[dict]:
    out: dict = {}
    for path in cfg.get("searches") or []:
        for n in range(1, int(cfg.get("max_pages", 3)) + 1):
            url = f"https://www.gradcracker.com/search/{path}" + (f"?page={n}" if n > 1 else "")
            got = parse_gradcracker(fetch(url))
            for op in got:
                out.setdefault(op["id"], op)
            if len(got) < 20:
                break
            time.sleep(1)  # be gentle with a site that lets us read it
    return list(out.values())


TJ_SEARCH = "https://targetjobs.co.uk/ext/svc/inferno-search-service-1-0/search"
TJ_EMPLOYER = re.compile(r'"hiringOrganization"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"')


def _tj_employer(http: requests.Session, url: str) -> str | None:
    """The search hides some employers; the job page's structured data names them."""
    try:
        r = http.get(url, headers=UA, timeout=20)
        m = TJ_EMPLOYER.search(r.text) if r.ok else None
        return html.unescape(m.group(1)).strip() if m else None
    except requests.RequestException:
        return None


def targetjobs(http: requests.Session, cfg: dict) -> list[dict]:
    def group(name, values):
        return {"conjunction": "OR", "conditions": [{"name": name, "value": v, "operator": "="} for v in values]}
    groups = [group("opportunity_type", ["Graduate job"]), group("sectors", cfg.get("sectors") or ["Technology"]),
              {"conjunction": "OR", "conditions": [{"name": "application_deadline_date", "value": ["0", "NOW"],
                                                    "operator": "NOT BETWEEN"}]},
              group("type", ["opportunity"])]
    headers = {"User-Agent": UA["User-Agent"], "Accept": "application/json",
               "Origin": "https://targetjobs.co.uk", "Referer": "https://targetjobs.co.uk/graduate-jobs/it"}
    out: dict = {}
    offset, total = 0, None
    while total is None or offset < min(total, int(cfg.get("max_results", 600))):
        body = {"fields": ["nid", "title", "url", "location", "city", "parent_organisation_title",
                           "application_deadline_date", "opportunity_start_date", "sectors", "abstract"],
                "keys": [""], "groupBy": None, "conditionGroup": {"conjunction": "AND", "groups": groups},
                "facets": [], "sort": [{"field": "application_deadline_date", "value": "asc"}],
                "conditions": [], "limit": 100, "offset": offset, "includePromoted": False}
        r = http.post(TJ_SEARCH, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json().get("search") or {}
        total = int(data.get("result_count") or 0)
        docs = data.get("documents") or []
        for d in docs:
            title = _text(d.get("title"), 200)
            place = d.get("location") or ", ".join(d.get("city") or [])
            if not title or not _grad_board_title(title) or (ABROAD.search(place) and not is_uk(place)):
                continue
            deadline = d.get("applicationDeadline")
            closing = datetime.utcfromtimestamp(deadline).date().isoformat() if isinstance(deadline, (int, float)) else None
            path = d.get("path") or d.get("url") or ""
            url = path if path.startswith("http") else f"https://targetjobs.co.uk{path}" if path else None
            company = (d.get("organisation") or {}).get("title") or d.get("parent_organisation_title")
            if not company and url:
                company = _tj_employer(http, url)
                time.sleep(0.5)
            op = _op("targetjobs", "targetjobs", str(d.get("nid")), company or "?", title, url, [place],
                     _text(d.get("abstract"), 400), closing=closing)
            out.setdefault(op["id"], op)
        if not docs:
            break
        offset += len(docs)
    return list(out.values())


HI_STATE = re.compile(r"window\.__RMP_SEARCH_RESULTS_INITIAL_STATE__\s*=\s*")


def parse_higherin(page: str) -> tuple[list[dict], int]:
    m = HI_STATE.search(page)
    if not m:
        return [], 0
    import json
    state, _ = json.JSONDecoder().raw_decode(page[m.end():])
    out = []
    for j in state.get("data") or []:
        title = _text(j.get("jobTitle"), 200)
        place = j.get("jobLocationNames") or ""
        if not title or not _grad_board_title(title) or (ABROAD.search(place) and not is_uk(place)):
            continue
        out.append(_op("higherin", "higherin", str(j.get("id") or j.get("jobId")), j.get("companyName") or "?", title,
                       j.get("url"), [place], f"{j.get('jobTypeName') or ''}; salary: {j.get('salary') or '?'}",
                       closing=parse_long_date(j.get("deadline"))))
    return out, int((state.get("meta") or {}).get("totalResults") or 0)


def higherin(http: requests.Session, cfg: dict) -> list[dict]:
    out: dict = {}
    for slug in cfg.get("roles") or ["software-engineering"]:
        for n in range(1, int(cfg.get("max_pages", 5)) + 1):
            page = _get(http, f"https://higherin.com/search-jobs/graduates/{slug}", params={"page": n}).text
            got, total = parse_higherin(page)
            for op in got:
                out.setdefault(op["id"], op)
            if n * 20 >= total:
                break
    return list(out.values())


SCC_ITEM = re.compile(r'<li class="post-(\d+) job_listing.*?<a href="([^"]+)".*?<h3>(.*?)</h3>\s*<div class="company">(.*?)</div>'
                      r'.*?<div class="location">(.*?)</div>.*?(?:<time datetime="([\d-]+)")?', re.S)


def parse_shecancode(fragment: str) -> list[dict]:
    out = []
    for post, url, title, company, place, posted in SCC_ITEM.findall(fragment):
        title, company, place = _text(title, 200), _text(company, 120), _text(place, 120)
        if not keep_title(title, False) or not is_uk(place):  # mixed-seniority board: graduate/junior titles only
            continue
        out.append(_op("shecancode", "shecancode", post, company or "?", title, url, [place], opening=posted or None))
    return out


def shecancode(http: requests.Session, cfg: dict) -> list[dict]:
    out: dict = {}
    for n in range(1, int(cfg.get("max_pages", 5)) + 1):
        r = http.post("https://shecancode.io/jm-ajax/get_listings/", headers={"User-Agent": UA["User-Agent"]},
                      data={"per_page": 100, "page": n}, timeout=30)
        r.raise_for_status()
        data = r.json()
        for op in parse_shecancode(data.get("html") or ""):
            out.setdefault(op["id"], op)
        if n >= int(data.get("max_num_pages") or 0):
            break
    return list(out.values())


# ---------------------------------------------------------------- all together
def load_config(s: Settings) -> dict:
    if not s.sources_file.exists():
        return {}
    cfg = yaml.safe_load(s.sources_file.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{s.sources_file} must contain a YAML mapping")
    return cfg


def fetch_all(s: Settings, today: date | None = None, previous: list[dict] | None = None,
              progress: Callable[[str], None] = lambda m: None, http: requests.Session | None = None,
              trackr_fetch: Callable = trackr.fetch) -> tuple[list[dict], dict]:
    """Returns (positions from every source, report {feed: count or error})."""
    today = today or date.today()
    cfg = load_config(s)
    http = http or requests.Session()
    previous = previous or []
    ops: list[dict] = []
    report: dict = {}

    def keep_previous(feed_prefix: str, error: Exception) -> None:
        old = [o for o in previous if (o.get("feed") or f"trackr:{o.get('industry') or 'Tech'}").startswith(feed_prefix)]
        ops.extend(old)
        report[feed_prefix] = f"failed ({error.__class__.__name__}), kept {len(old)} from last run"

    tcfg = cfg.get("trackr") or {}
    industries = () if tcfg.get("enabled") is False else tuple(tcfg.get("industries") or s.trackr_industries)
    if tcfg.get("season"):
        s.trackr_season = str(tcfg["season"])
    for industry in industries:
        progress(f"Fetching Trackr {industry}...")
        try:
            got = trackr_fetch(s, today, (industry,))
            ops.extend(got)
            report[f"trackr:{industry}"] = len(got)
        except Exception as e:
            keep_previous(f"trackr:{industry}", e)

    boards = [b for b in cfg.get("boards") or [] if b.get("ats") in BOARDS and b.get("board") and b.get("name")]
    if boards:
        progress(f"Fetching {len(boards)} company job boards...")

        def one(b):
            try:
                return b, BOARDS[b["ats"]](http, b), None
            except Exception as e:
                return b, [], e
        with ThreadPoolExecutor(max_workers=8) as pool:
            for b, got, err in pool.map(one, boards):
                feed = f"{b['ats']}:{b['board']}"
                if err:
                    keep_previous(feed, err)
                else:
                    ops.extend(got)
                    report[feed] = len(got)

    queries = (cfg.get("jobs_ac_uk") or {}).get("queries") or []
    if queries:
        progress("Fetching jobs.ac.uk...")
        try:
            got = jobs_ac_uk(http, queries, today)
            ops.extend(got)
            report["jobs.ac.uk"] = len(got)
        except Exception as e:
            keep_previous("jobs.ac.uk", e)

    grad_boards = [("gradcracker", lambda c: gradcracker(c)), ("targetjobs", lambda c: targetjobs(http, c)),
                   ("higherin", lambda c: higherin(http, c)), ("shecancode", lambda c: shecancode(http, c))]
    for name, run in grad_boards:
        c = cfg.get(name)
        if not c or c.get("enabled") is False:
            continue
        progress(f"Fetching {SOURCE_LABELS[name]}...")
        try:
            got = run(c)
            ops.extend(got)
            report[name] = len(got)
        except Exception as e:
            keep_previous(name, e)

    queries = (cfg.get("adzuna") or {}).get("queries") or []
    if queries and s.adzuna_app_id and s.adzuna_app_key:
        progress("Fetching Adzuna...")
        try:
            got = adzuna(http, s, queries)
            ops.extend(got)
            report["adzuna"] = len(got)
        except Exception as e:
            keep_previous("adzuna", e)
    elif queries:
        report["adzuna"] = "off (add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env)"

    for op in ops:
        if op.get("source") != "trackr":
            op["status"] = trackr.status_of(op, today) if op.get("closing") else "open"
    return ops, report
