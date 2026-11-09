import asyncio
import logging
import re
import time
from typing import Dict, Any, List, Tuple
import aiosqlite
import httpx

from app.database import DB_PATH, get_all_jobs, parse_published_date, normalize_job_url

logger = logging.getLogger("JobAgent.MarketAnalytics")

# Tassonomia delle competenze tecniche pre-compilata per prestazioni istantanee (<100ms su 2500+ offerte)
SKILL_PATTERNS_RAW = [
    ("Python", r"\bpython\b"),
    ("Java", r"\bjava\b(?!\s*script)"),
    ("C / C++", r"\b(c\+\+|cpp|embedded\s+c)\b"),
    ("C# / .NET", r"(\bc#|\b\.net\b|dotnet|asp\.net)"),
    ("JavaScript / TypeScript", r"\b(javascript|typescript|node\.?js|es6)\b"),
    ("SQL / RDBMS", r"\b(sql|postgresql|postgres|mysql|oracle\s+db|pl/sql|t-sql)\b"),
    ("NoSQL / MongoDB", r"\b(nosql|mongodb|redis|cassandra|dynamodb|elasticsearch)\b"),
    ("Machine Learning", r"\b(machine\s+learning|ml\s+engineer|xgboost|lightgbm|scikit-learn|sklearn)\b"),
    ("Deep Learning & Neural Nets", r"\b(deep\s+learning|neural\s+network|cnn|rnn|transformer)\b"),
    ("PyTorch / TensorFlow", r"\b(pytorch|tensorflow|keras|jax)\b"),
    ("LLM / GenAI / RAG", r"\b(llm|large\s+language\s+model|generative\s+ai|gen\s*ai|rag|langchain|llamaindex|openai|prompt\s+engineering|agentic)\b"),
    ("NLP", r"\b(nlp|natural\s+language\s+processing|text\s+mining|huggingface|bert)\b"),
    ("Computer Vision", r"\b(computer\s+vision|opencv|yolo|image\s+processing|3d\s+vision)\b"),
    ("Data Science & Pandas", r"\b(data\s+scien(?:ce|tist)|pandas|numpy|scipy|statistica|statistical)\b"),
    ("Big Data (Spark / Kafka)", r"\b(spark|pyspark|databricks|hadoop|kafka|airflow|snowflake|bigquery)\b"),
    ("BI (Power BI / Tableau)", r"\b(power\s*bi|tableau|qlik|looker|data\s+visualization|dax)\b"),
    ("AWS", r"\b(aws|amazon\s+web\s+services|sagemaker|ec2|s3|lambda)\b"),
    ("Azure", r"\b(azure|microsoft\s+cloud|azure\s+devops|azure\s+ml)\b"),
    ("Google Cloud (GCP)", r"\b(gcp|google\s+cloud|vertex\s+ai)\b"),
    ("Docker & Containers", r"\b(docker|container|podman)\b"),
    ("Kubernetes (K8s)", r"\b(kubernetes|k8s|openshift|helm)\b"),
    ("CI/CD & DevOps", r"\b(ci/cd|jenkins|gitlab\s+ci|github\s+actions|terraform|ansible|devops)\b"),
    ("Linux / Bash", r"\b(linux|ubuntu|redhat|unix|bash|shell\s+scripting)\b"),
    ("Git", r"\b(git|github|gitlab|bitbucket)\b"),
    ("FastAPI / Django / Flask", r"\b(fastapi|django|flask)\b"),
    ("Spring Boot", r"\b(spring\s*boot|spring\s+framework|hibernate|jakarta)\b"),
    ("React / Next.js", r"\b(react|react\.js|nextjs|next\.js)\b"),
    ("Angular / Vue", r"\b(angular|vue|vue\.js|nuxt)\b"),
    ("Mobile (Android / iOS / Flutter)", r"\b(android|kotlin|ios|swift|flutter|react\s+native)\b"),
    ("Rust / Go", r"\b(rust|golang|\bgo\s+developer)\b"),
    ("MATLAB / Simulink", r"\b(matlab|simulink|labview)\b"),
    ("Embedded / AUTOSAR / Firmware", r"\b(embedded|firmware|autosar|rtos|fpga|vhdl|plc|scada|can\s*bus)\b"),
    ("Robotics / ROS", r"\b(robotics|robotica|\bros\b|ros2|slam)\b"),
    ("Cybersecurity & Network", r"\b(cyber\s*security|siem|soc\b|penetration\s+test|iso\s*27001|iam|vulnerability|firewall)\b"),
    ("SAP / ERP / Salesforce", r"\b(\bsap\b|abap|s/4hana|salesforce|crm|dynamics\s*365)\b"),
    ("Agile / Scrum / PM", r"\b(agile|scrum|kanban|jira|pmp|prince2|itil)\b"),
]

COMPILED_SKILLS: List[Tuple[str, re.Pattern]] = [
    (label, re.compile(pat, re.IGNORECASE)) for label, pat in SKILL_PATTERNS_RAW
]

RE_CAT_AI = re.compile(
    r"\b(ai\b|artificial\s+intelligence|intelligenza\s+artificiale|machine\s+learning|\bml\b|deep\s+learning|data\s+scien|nlp|computer\s+vision|llm|gen\s*ai|generative\s+ai|neural|algorithm)\b",
    re.I,
)
RE_CAT_DATA = re.compile(
    r"\b(data\s+engineer|data\s+analyst|business\s+intelligence|\bbi\b|big\s+data|etl|data\s+governance|data\s+architect|analytics|power\s*bi|tableau|databricks)\b",
    re.I,
)
RE_CAT_CLOUD = re.compile(
    r"\b(cyber\s*security|security|soc\b|pentest|iam|cloud\s+engineer|devops|devsecops|sre|site\s+reliability|system\s+engineer|sistemista|network\s+engineer|infrastructure|kubernetes|aws|azure)\b",
    re.I,
)
RE_CAT_EMBEDDED = re.compile(
    r"\b(embedded|firmware|hardware|automotive|mechatronic|mecatron|elettronic|electronic|fpga|plc|automation|robotics|validation\s+engineer|testing\s+engineer|powertrain|calibration|adas)\b",
    re.I,
)
RE_CAT_SW = re.compile(
    r"\b(software|developer|sviluppatore|programmatore|backend|back-end|frontend|front-end|full\s*stack|python|java\b|\.net|c\+\+|c#|react|angular|node|web\s+dev|mobile|android|ios|rust|golang|architect)\b",
    re.I,
)
RE_CAT_CONSULT = re.compile(
    r"\b(functional\s+analyst|analista\s+funzionale|business\s+analyst|project\s+manager|\bpm\b|product\s+owner|product\s+manager|scrum\s+master|consultant|consulente|sap\b|erp|crm|salesforce|pmo|audit|risk|tax|advisory)\b",
    re.I,
)

RE_SEN_STAGE = re.compile(r"\b(stage|stagista|intern|internship|tirocinio|tirocinante|tesi|thesis|academy)\b", re.I)
RE_SEN_JR = re.compile(r"\b(junior|\bjr\.?\b|entry\s*level|early\s*career|graduate|neolaureat[oi]|apprendistato|trainee|young)\b", re.I)
RE_SEN_SR = re.compile(r"\b(senior|\bsr\.?\b|lead|principal|staff|director|head\s+of|manager|vp|chief|experienced|expert)\b", re.I)

COMPILED_CITIES: List[Tuple[str, re.Pattern]] = [
    ("Torino", re.compile(r"\b(torino|turin|volpiano|orbassano|ivrea|grugliasco|rivoli|collegno|moncalieri|chivasso|settimo|santena|piemonte)\b", re.I)),
    ("Milano", re.compile(r"\b(milano|milan|assago|segrate|monza|sesto\s+san\s+giovanni|cinisello|rho|rozzano|lombardia)\b", re.I)),
    ("Roma", re.compile(r"\b(roma|rome|pomezia|frascati|eur|lazio)\b", re.I)),
    ("Bologna", re.compile(r"\b(bologna|casalecchio|imola|modena|reggio\s+emilia|parma|emilia)\b", re.I)),
    ("Firenze", re.compile(r"\b(firenze|florence|prato|pisa|pontedera|siena|toscana)\b", re.I)),
    ("Napoli", re.compile(r"\b(napoli|naples|pomigliano|caserta|salerno|campania)\b", re.I)),
    ("Padova / Veneto", re.compile(r"\b(padova|padua|venezia|verona|vicenza|treviso|mestre|veneto)\b", re.I)),
    ("Genova", re.compile(r"\b(genova|genoa|erzelli|morego|savona|la\s+spezia|liguria)\b", re.I)),
    ("Bari / Puglia", re.compile(r"\b(bari|molfetta|lecce|taranto|brindisi|puglia)\b", re.I)),
]

RE_RAL_RANGE = re.compile(
    r"(?:eur|€)?\s*([1-9]\d)(?:[\.,](\d{3}))?\s*(?:k|€|eur|\/yr|\/anno|\s*€\/yr|\s*€\/anno)?\s*(?:-|–|to|a)\s*(?:eur|€)?\s*([1-9]\d)(?:[\.,](\d{3}))?\s*(?:k|€|eur|\/anno|\/yr)?",
    re.I,
)
RE_RAL_SINGLE = re.compile(
    r"(?:ral|retribuzione|compenso|stipendio|salary)?[:\s]*(?:eur|€)?\s*([1-9]\d)(?:[\.,](\d{3}))?\s*(?:k|€|eur|\s*\/anno|\s*\/yr)",
    re.I,
)

# Benchmark salariali di mercato tecnologico italiano (€ lordi annui per livello Mid-level)
CATEGORY_BASE_SALARY = {
    "AI, Machine Learning & Data Science": 42500,
    "Cloud, DevOps & Cybersecurity": 40500,
    "Software, Backend & Full-Stack": 38000,
    "Data Engineering & BI / Analytics": 37500,
    "Embedded, Automotive & Hardware/IoT": 36500,
    "IT Consulting, PM & Functional": 35000,
    "Altri Ruoli / Operations & Engineering": 31000,
}

SENIORITY_MULTIPLIER = {
    "Stage / Internship": 0.28,      # ~9.600€ - 12.000€ (800€-1000€/mese)
    "Junior / Entry Level": 0.74,    # ~28.000€ - 31.500€
    "Mid-Level / Regular": 1.00,     # ~35.000€ - 42.500€
    "Senior / Lead / Manager": 1.38, # ~48.000€ - 60.000€
}

CITY_SALARY_MULTIPLIER = {
    "Milano": 1.08,
    "Roma": 1.03,
    "Torino": 1.00,
    "Bologna": 1.02,
    "Full Remote": 1.05,
    "Estero / Internazionale": 1.35,
    "Padova / Veneto": 0.99,
    "Genova": 0.98,
    "Firenze": 0.98,
    "Napoli": 0.94,
    "Bari / Puglia": 0.93,
    "Altre Sedi Italia": 0.96,
}

# Cache in memoria per risposte istantanee
_ANALYTICS_CACHE: Dict[str, Any] = {"ts": 0.0, "count": -1, "data": None}


def parse_explicit_salary(estimated_ral: str, desc: str) -> Tuple[int, int, int]:
    """
    Estrae i valori di retribuzione espressi esplicitamente nell'annuncio o nella stima AI.
    Restituisce (valore_medio, valore_min, valore_max) in Euro, oppure (0, 0, 0) se non specificata.
    """
    raw_text = f"{estimated_ral or ''} {desc[:500]}".strip()
    if not raw_text:
        return 0, 0, 0
    t = raw_text.lower().replace("\xa0", " ")
    if any(k in t for k in ["non valutata", "non specificata", "n/d", "da definire"]) and len(estimated_ral.strip()) < 20:
        raw_text = desc[:500].strip()
        t = raw_text.lower().replace("\xa0", " ")

    if "stage retribuito" in t or "tirocinio retribuito" in t:
        return 10800, 9600, 12000

    # Rimuovi eventuali centesimi decimali (,00 o .00)
    t_clean = re.sub(r"[,.]00\b", "", t)

    # 1. Range esplicito: es. 28.000€ - 33.000€ o EUR 33.000 - EUR 49.000
    m_range = RE_RAL_RANGE.search(t_clean)
    if m_range:
        v1 = int(m_range.group(1)) * 1000 if not m_range.group(2) else int(m_range.group(1)) * 1000 + int(m_range.group(2)[:3])
        v2 = int(m_range.group(3)) * 1000 if not m_range.group(4) else int(m_range.group(3)) * 1000 + int(m_range.group(4)[:3])
        if 15000 <= v1 <= 150000 and 15000 <= v2 <= 150000:
            low, high = min(v1, v2), max(v1, v2)
            return round((low + high) / 2), low, high

    # 2. Singola cifra con indicatore monetario esplicito
    m_single = RE_RAL_SINGLE.search(t_clean)
    if m_single:
        v = int(m_single.group(1)) * 1000 if not m_single.group(2) else int(m_single.group(1)) * 1000 + int(m_single.group(2)[:3])
        if 15000 <= v <= 150000:
            return v, round(v * 0.92), round(v * 1.08)

    return 0, 0, 0


def estimate_market_salary(role_cat: str, seniority: str, city_hub: str, skills: List[str]) -> Tuple[int, int, int]:
    """
    Calcola la stima di benchmark di mercato della RAL (avg, min, max)
    combinando categoria professionale, livello di esperienza, polo geografico e competenze ad alta domanda.
    """
    base = CATEGORY_BASE_SALARY.get(role_cat, 35000)
    sen_mult = SENIORITY_MULTIPLIER.get(seniority, 1.0)
    city_mult = CITY_SALARY_MULTIPLIER.get(city_hub, 1.0)

    # Surplus per skill ad alta complessità / scarsità
    premium_mult = 1.0
    premium_skills = {"LLM / GenAI / RAG", "PyTorch / TensorFlow", "Kubernetes (K8s)", "Rust / Go", "Cybersecurity & Network"}
    if any(s in premium_skills for s in skills):
        premium_mult += 0.05

    val_mid = base * sen_mult * city_mult * premium_mult
    val_avg = int(round(val_mid, -2))

    if seniority == "Stage / Internship":
        val_min = 9600
        val_max = 12000
    else:
        val_min = int(round(val_avg * 0.88, -2))
        val_max = int(round(val_avg * 1.14, -2))

    return val_avg, val_min, val_max


def classify_role_category(title: str, desc_short: str) -> str:
    if RE_CAT_AI.search(title):
        return "AI, Machine Learning & Data Science"
    if RE_CAT_DATA.search(title):
        return "Data Engineering & BI / Analytics"
    if RE_CAT_CLOUD.search(title):
        return "Cloud, DevOps & Cybersecurity"
    if RE_CAT_EMBEDDED.search(title):
        return "Embedded, Automotive & Hardware/IoT"
    if RE_CAT_SW.search(title):
        return "Software, Backend & Full-Stack"
    if RE_CAT_CONSULT.search(title):
        return "IT Consulting, PM & Functional"

    if RE_CAT_AI.search(desc_short):
        return "AI, Machine Learning & Data Science"
    if RE_CAT_SW.search(desc_short):
        return "Software, Backend & Full-Stack"

    return "Altri Ruoli / Operations & Engineering"


def classify_seniority(title: str, desc_short: str) -> str:
    header = desc_short[:260].lower()
    if RE_SEN_STAGE.search(title) or "seniority: internship" in header or "contratto: internship" in header:
        return "Stage / Internship"
    if (
        RE_SEN_JR.search(title)
        or "seniority: early career" in header
        or "students/graduates" in header
        or "studenti/laureati" in header
    ):
        return "Junior / Entry Level"
    if (
        RE_SEN_SR.search(title)
        or "seniority: senior level" in header
        or "seniority: dirigenti" in header
        or "seniority: executives" in header
    ):
        return "Senior / Lead / Manager"
    if RE_SEN_JR.search(desc_short):
        return "Junior / Entry Level"
    return "Mid-Level / Regular"


def classify_work_mode(title: str, location: str, desc_short: str) -> str:
    comb = f"{title} {location} {desc_short}".lower()
    if any(k in comb for k in ("full remote", "fully remote", "100% remote", "da remoto", "telelavoro", "(remote)", "- remote")):
        return "Remoto"
    if any(k in comb for k in ("ibrido", "ibrida", "hybrid", "smart working", "smartworking", "(hybrid)")):
        return "Ibrido"
    if "remote" in location.lower():
        return "Remoto"
    return "In Sede"


def classify_city_hub(title: str, location: str, desc_short: str) -> str:
    loc_text = f"{location} {title}"
    for label, pat in COMPILED_CITIES:
        if pat.search(loc_text):
            return label
    for label, pat in COMPILED_CITIES:
        if pat.search(desc_short):
            return label

    loc_low = loc_text.lower()
    if "remote" in loc_low or "remoto" in loc_low:
        return "Full Remote"
    if any(
        k in loc_low
        for k in (
            "germany", "france", "spain", "uk", "united kingdom", "usa", "united states",
            "poland", "romania", "india", "china", "brazil", "sweden", "austria", "switzerland",
            "munich", "paris", "london", "berlin", "madrid", "graz", "bucharest", "sofia",
        )
    ):
        return "Estero / Internazionale"

    return "Altre Sedi Italia"


def _compute_analytics_sync(raw_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    enriched_jobs = []
    company_counts: Dict[str, Dict[str, Any]] = {}

    for j in raw_jobs:
        comp = (j.get("company") or "Azienda Non Specificata").strip()
        title = (j.get("title") or "").strip()
        loc = (j.get("location") or "").strip()
        desc = j.get("description") or ""
        desc_short = desc[:650]
        disc_raw = j.get("discovered_at") or ""
        disc_date = disc_raw[:10] if len(disc_raw) >= 10 else ""
        pub_date = j.get("published_at") or disc_date

        role_cat = classify_role_category(title, desc_short)
        seniority = classify_seniority(title, desc_short)
        work_mode = classify_work_mode(title, loc, desc_short)
        city_hub = classify_city_hub(title, loc, desc_short)

        skill_haystack = f"{title} {j.get('matching_skills') or ''} {j.get('missing_skills') or ''} {desc[:900]}"
        skills = [label for label, pat in COMPILED_SKILLS if pat.search(skill_haystack)]

        # Estrazione RAL (esplicita da annuncio o calcolata da benchmark di mercato)
        exp_avg, exp_min, exp_max = parse_explicit_salary(j.get("estimated_ral") or "", desc_short)
        mkt_avg, mkt_min, mkt_max = estimate_market_salary(role_cat, seniority, city_hub, skills)

        if exp_avg > 0:
            final_ral = exp_avg
            final_min = exp_min
            final_max = exp_max
            is_explicit = True
        else:
            final_ral = mkt_avg
            final_min = mkt_min
            final_max = mkt_max
            is_explicit = False

        score = int(j.get("match_score") or 0)
        missing_raw = j.get("missing_skills") or ""
        missing_list = [
            s.strip()
            for s in missing_raw.split(",")
            if s.strip() and len(s.strip()) <= 38 and "nessuno" not in s.lower()
        ]

        if comp not in company_counts:
            company_counts[comp] = {"name": comp, "total": 0, "matched": 0, "max_score": 0}
        company_counts[comp]["total"] += 1
        if score >= 60:
            company_counts[comp]["matched"] += 1
        if score > company_counts[comp]["max_score"]:
            company_counts[comp]["max_score"] = score

        enriched_jobs.append({
            "id": j["id"],
            "company": comp,
            "portal": j.get("portal") or "",
            "title": title,
            "location": loc,
            "url": j.get("url") or "",
            "published_at": pub_date,
            "discovered_at": disc_date,
            "role_category": role_cat,
            "seniority": seniority,
            "work_mode": work_mode,
            "city_hub": city_hub,
            "skills": skills,
            "missing_skills": missing_list,
            "match_score": score,
            "status": j.get("status") or "DISCOVERED",
            "applied_at": j.get("applied_at") or "",
            "is_applied": (j.get("status") or "") in ["APPLIED", "AUTO_APPLIED", "APPROVED_APPLIED", "MANUAL_APPLIED"] or bool(j.get("applied_at")),
            "estimated_ral": j.get("estimated_ral") or f"{final_min:,}€ - {final_max:,}€".replace(",", "."),
            "ral_numeric": final_ral,
            "ral_min": final_min,
            "ral_max": final_max,
            "is_explicit_ral": is_explicit,
            "ai_reasoning": (j.get("ai_reasoning") or "")[:240],
        })

    sorted_companies = sorted(
        company_counts.values(),
        key=lambda x: (x["total"], x["matched"]),
        reverse=True,
    )

    return {
        "total_jobs": len(enriched_jobs),
        "total_companies": len(sorted_companies),
        "companies": sorted_companies,
        "jobs": enriched_jobs,
    }


def invalidate_analytics_cache():
    _ANALYTICS_CACHE["ts"] = 0
    _ANALYTICS_CACHE["data"] = None


async def build_market_analytics_dataset(force_refresh: bool = False) -> Dict[str, Any]:
    raw_jobs = await get_all_jobs(limit=10000)
    now = time.time()
    if (
        not force_refresh
        and _ANALYTICS_CACHE["data"] is not None
        and _ANALYTICS_CACHE["count"] == len(raw_jobs)
        and (now - _ANALYTICS_CACHE["ts"]) < 15.0
    ):
        return _ANALYTICS_CACHE["data"]

    dataset = await asyncio.to_thread(_compute_analytics_sync, raw_jobs)
    _ANALYTICS_CACHE["ts"] = now
    _ANALYTICS_CACHE["count"] = len(raw_jobs)
    _ANALYTICS_CACHE["data"] = dataset
    return dataset


async def backfill_published_dates_from_live_apis() -> int:
    """
    Arricchisce lo storico del database interrogando i feed JSON rapidi (AshbyHQ, Greenhouse,
    Workday CXS, Amazon) che restituiscono la vera data di pubblicazione storica ('publishedAt',
    'updated_at', 'postedOn', 'posted_date') e aggiornando i record corrispondenti in jobs.db.
    """
    updated_count = 0
    url_to_pubdate: Dict[str, str] = {}

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for board in ["satispay", "docebo"]:
            try:
                r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
                if r.status_code == 200:
                    for j in r.json().get("jobs", []):
                        ju = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{j.get('id', '')}"
                        pub = str(j.get("publishedAt") or "")[:10]
                        if ju and pub:
                            url_to_pubdate[normalize_job_url(ju)] = pub
            except Exception as e:
                logger.debug(f"Ashby backfill skip ({board}): {e}")

        for board in ["canonical", "datadog", "elastic", "gitlab", "cloudflare", "mozilla"]:
            try:
                r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
                if r.status_code == 200:
                    for j in r.json().get("jobs", []):
                        ju = j.get("absolute_url") or ""
                        pub = str(j.get("updated_at") or "")[:10]
                        if ju and pub:
                            url_to_pubdate[normalize_job_url(ju)] = pub
            except Exception as e:
                logger.debug(f"Greenhouse backfill skip ({board}): {e}")

        workday_endpoints = [
            ("https://sellagroup.wd3.myworkdayjobs.com/wday/cxs/sellagroup/ID01/jobs", "https://sellagroup.wd3.myworkdayjobs.com/it-IT/ID01"),
            ("https://leonardocompany.wd3.myworkdayjobs.com/wday/cxs/leonardocompany/LeonardoCareerSite/jobs", "https://leonardocompany.wd3.myworkdayjobs.com/it-IT/LeonardoCareerSite"),
            ("https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/jobs", "https://thales.wd3.myworkdayjobs.com/en-US/Careers"),
        ]
        for api_ep, pub_base in workday_endpoints:
            try:
                r = await client.post(
                    api_ep,
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
                if r.status_code == 200:
                    for p in r.json().get("jobPostings", []):
                        ext_path = p.get("externalPath", "")
                        posted_on = p.get("postedOn", "")
                        if ext_path and posted_on:
                            full_u = f"{pub_base.rstrip('/')}{ext_path}"
                            url_to_pubdate[normalize_job_url(full_u)] = parse_published_date(posted_on)
            except Exception as e:
                logger.debug(f"Workday backfill skip ({api_ep}): {e}")

        try:
            r = await client.get("https://www.amazon.jobs/en/search.json?country=ITA&result_limit=50&sort=recent")
            if r.status_code == 200:
                for item in r.json().get("jobs", []):
                    jp = item.get("job_path", "")
                    pd = item.get("posted_date", "")
                    if jp and pd:
                        full_u = f"https://www.amazon.jobs{jp}"
                        url_to_pubdate[normalize_job_url(full_u)] = parse_published_date(pd)
        except Exception as e:
            logger.debug(f"Amazon backfill skip: {e}")

    if not url_to_pubdate:
        return 0

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, url, discovered_at FROM jobs") as cursor:
            all_rows = await cursor.fetchall()

        updates = []
        for r_id, u, disc_at in all_rows:
            norm_u = normalize_job_url(u or "")
            if norm_u in url_to_pubdate:
                updates.append((url_to_pubdate[norm_u], r_id))

        if updates:
            await db.executemany("UPDATE jobs SET published_at = ? WHERE id = ?", updates)
            await db.commit()
            updated_count = len(updates)

    _ANALYTICS_CACHE["ts"] = 0.0
    logger.info(f"[MarketAnalytics] Aggiornate {updated_count} date di pubblicazione reali tramite API.")
    return updated_count
