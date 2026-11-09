import json
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup

from app.config import DATA_DIR, load_candidate_config
from app.database import deterministic_url_id

logger = logging.getLogger("JobAgent.XRaySignal")

SEEN_SIGNALS_FILE: Path = DATA_DIR / "seen_signals.json"

INVALID_COMPANY_PLACEHOLDERS = (
    "azienda tech",
    "x-ray",
    "linkedin company",
    "azienda non specificata",
    "post linkedin",
    "tech / azienda",
    "unknown",
    "n/d",
    "n/a",
    "confidenziale",
    "confidential",
)

ITALY_GEO_URN = '["103350119"]'


def clean_company_for_search(company: str) -> str:
    """
    Pulisce il nome dell'azienda da tag interni (es. '(Exp)', '(X-Ray)', 'S.p.A.', '& ...')
    e restituisce stringa vuota se il nome è un placeholder generico non ricercabile su LinkedIn.
    """
    if not company or not isinstance(company, str):
        return ""
    raw = company.strip()
    low = raw.lower()
    if any(p in low for p in INVALID_COMPANY_PLACEHOLDERS):
        return ""
    if low in {"azienda", "company", "lavoro", "italia", "italy", "remote", "remoto"}:
        return ""

    # Rimuovi note tra parentesi es. "PwC Italia (Exp)" -> "PwC Italia"
    cleaned = re.sub(r"\([^)]*\)", " ", raw)
    # Se è una coppia di brand separata da & o / (es. "Zucchetti & Tilby"), usa il brand principale
    if " & " in cleaned:
        cleaned = cleaned.split(" & ")[0]
    elif " / " in cleaned:
        cleaned = cleaned.split(" / ")[0]

    cleaned = re.sub(
        r"\b(s\.?r\.?l\.?|s\.?p\.?a\.?|s\.?c\.?a\.?r\.?l\.?|group|gruppo|italia|italy|europe|latam|global|holding|solutions|consulting|informatica|technologies|technology|part of .*)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—,.")
    if len(cleaned) < 2:
        cleaned = re.sub(r"\([^)]*\)", "", raw).strip()
    return cleaned if len(cleaned) >= 2 else ""


def build_networking_links(
    company: str,
    city: str = None,
    job: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """
    Genera link per la sezione Persone di LinkedIn e Google X-Ray per trovare
    CTO/Hiring Manager, Colleghi per Referral e Recruiter.
    Se l'offerta non ha un nome azienda valido/identificabile, restituisce None
    così da non mostrare tasti non funzionanti su Telegram.
    """
    clean_comp = clean_company_for_search(company)
    direct_recruiter_url = (job or {}).get("recruiter_url") if isinstance(job, dict) else None

    if not clean_comp and not direct_recruiter_url:
        return None

    cfg = load_candidate_config()
    search_prefs = cfg.get("search_preferences", {})
    profile = cfg.get("candidate_profile", {})

    if not city:
        cities = search_prefs.get("target_cities") or [profile.get("city", "Italia")]
        city = cities[0] if cities else "Italia"

    keywords = search_prefs.get("keywords") or ["AI Engineer", "Data Scientist", "Software Engineer", "Python"]
    primary_role = keywords[0] if keywords else "Software Engineer"
    secondary_role = keywords[1] if len(keywords) > 1 else "Data Scientist"

    geo_param = urllib.parse.quote(ITALY_GEO_URN)
    result: Dict[str, str] = {
        "clean_company": clean_comp or (company or "").strip(),
    }

    if clean_comp:
        # Query snelle compatibili con il motore /search/results/people/ di LinkedIn + filtro nativo geoUrn Italia
        hm_kw = f'"{clean_comp}" (CTO OR "Head of" OR "Engineering Manager" OR "Tech Lead")'
        result["hm_url"] = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={urllib.parse.quote(hm_kw)}&geoUrn={geo_param}&origin=FACETED_SEARCH"
        )

        ref_kw = f'"{clean_comp}" ("{primary_role}" OR "{secondary_role}" OR Engineer OR Developer)'
        result["ref_url"] = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={urllib.parse.quote(ref_kw)}&geoUrn={geo_param}&origin=FACETED_SEARCH"
        )

        hr_kw = f'"{clean_comp}" ("Talent Acquisition" OR Recruiter OR HR)'
        result["hr_url"] = direct_recruiter_url or (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={urllib.parse.quote(hr_kw)}&geoUrn={geo_param}&origin=FACETED_SEARCH"
        )

        xray_query = (
            f'site:it.linkedin.com/in/ "{clean_comp}" '
            f'("CTO" OR "Head of" OR "Talent Acquisition" OR "Tech Lead") "{city}"'
        )
        result["xray_url"] = f"https://www.google.com/search?q={urllib.parse.quote(xray_query)}"
    elif direct_recruiter_url:
        result["hr_url"] = direct_recruiter_url

    return result


def get_boolean_xray_radar_links() -> Dict[str, str]:
    """
    Genera i link rapidi Booleani e X-Ray pronti all'uso in base ai ruoli e alle città attive.
    """
    cfg = load_candidate_config()
    search_prefs = cfg.get("search_preferences", {})
    profile = cfg.get("candidate_profile", {})

    cities = search_prefs.get("target_cities") or [profile.get("city", "Italia")]
    keywords = search_prefs.get("keywords") or ["AI Engineer", "Machine Learning", "Data Scientist", "Python"]

    primary_city = cities[0] if cities else "Torino"
    city_or = " OR ".join(f'"{c}"' for c in cities[:3])
    role_or = " OR ".join(f'"{k}"' for k in keywords[:3])
    geo_param = urllib.parse.quote(ITALY_GEO_URN)

    post_hm_q = (
        f'("stiamo cercando" OR "we are hiring" OR "cerchiamo") '
        f'({role_or}) ({primary_city} OR Italia)'
    )
    post_hm_url = (
        f"https://www.linkedin.com/search/results/content/"
        f"?keywords={urllib.parse.quote(post_hm_q)}&sortBy=%22date_posted%22&origin=FACETED_SEARCH"
    )

    post_ref_q = (
        f'("cerco per il mio team" OR "unisciti al nostro team" OR "hiring") '
        f'({role_or})'
    )
    post_ref_url = (
        f"https://www.linkedin.com/search/results/content/"
        f"?keywords={urllib.parse.quote(post_ref_q)}&sortBy=%22date_posted%22&origin=FACETED_SEARCH"
    )

    people_hm_q = f'{primary_city} (CTO OR "Head of AI" OR "Engineering Manager" OR "Tech Lead")'
    people_hm_url = (
        f"https://www.linkedin.com/search/results/people/"
        f"?keywords={urllib.parse.quote(people_hm_q)}&geoUrn={geo_param}&origin=FACETED_SEARCH"
    )

    people_hr_q = f'{primary_city} ("Talent Acquisition" OR "IT Recruiter" OR "Tech Recruiter")'
    people_hr_url = (
        f"https://www.linkedin.com/search/results/people/"
        f"?keywords={urllib.parse.quote(people_hr_q)}&geoUrn={geo_param}&origin=FACETED_SEARCH"
    )

    xray_ats_q = (
        'site:boards.greenhouse.io OR site:jobs.lever.co OR site:*.teamtailor.com '
        f'({role_or}) ({city_or} OR "Italy" OR "Remote")'
    )
    xray_ats_url = f"https://www.google.com/search?q={urllib.parse.quote(xray_ats_q)}"

    xray_wd_q = f'site:myworkdayjobs.com ({role_or}) ({city_or})'
    xray_wd_url = f"https://www.google.com/search?q={urllib.parse.quote(xray_wd_q)}"

    return {
        "post_hm_url": post_hm_url,
        "post_ref_url": post_ref_url,
        "people_hm_url": people_hm_url,
        "people_hr_url": people_hr_url,
        "xray_ats_url": xray_ats_url,
        "xray_wd_url": xray_wd_url,
    }


class XRayScraper:
    """
    Esegue ricerche X-Ray sugli ATS aziendali (Workday CXS JSON, Greenhouse JSON, DuckDuckGo X-Ray)
    usando dinamicamente le città e le posizioni configurate dall'utente.
    """

    async def scrape_xray_jobs(self) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        cfg = load_candidate_config()
        search_prefs = cfg.get("search_preferences", {})
        target_cities = search_prefs.get("target_cities") or [
            cfg.get("candidate_profile", {}).get("city", "Italia")
        ]
        keywords = search_prefs.get("keywords") or [
            "AI Engineer", "Machine Learning", "Data Scientist", "Python", "Software Engineer"
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        }

        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
            # --- 1. X-RAY DIRETTO SU WORKDAY ATS ---
            workday_tenants = [
                ("Banca Sella", "https://sellagroup.wd3.myworkdayjobs.com/wday/cxs/sellagroup/ID01/jobs", "https://sellagroup.wd3.myworkdayjobs.com/it-IT/ID01"),
                ("Thales Alenia Space", "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/jobs", "https://thales.wd3.myworkdayjobs.com/en-US/Careers"),
                ("PwC Italia (Exp)", "https://pwc.wd3.myworkdayjobs.com/wday/cxs/pwc/Global_Experienced_Careers/jobs", "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers"),
                ("PwC Italia (Junior/Campus)", "https://pwc.wd3.myworkdayjobs.com/wday/cxs/pwc/Global_Campus_Careers/jobs", "https://pwc.wd3.myworkdayjobs.com/Global_Campus_Careers"),
            ]

            for comp_name, api_url, public_base in workday_tenants:
                for city_q in target_cities[:3]:
                    try:
                        resp = await client.post(
                            api_url,
                            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": city_q},
                            headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": headers["User-Agent"]},
                        )
                        if resp.status_code == 200:
                            postings = resp.json().get("jobPostings", [])
                            for p in postings:
                                title = p.get("title", "")
                                ext_path = p.get("externalPath", "")
                                loc_txt = p.get("locationsText", city_q)
                                if not title or not ext_path:
                                    continue
                                full_url = f"{public_base.rstrip('/')}{ext_path}"
                                if full_url in seen_urls:
                                    continue
                                seen_urls.add(full_url)

                                posted_on = str(p.get("postedOn") or "")
                                discovered.append({
                                    "external_id": deterministic_url_id("xray_wd", full_url),
                                    "portal": "X-Ray Workday ATS",
                                    "company": comp_name,
                                    "title": title,
                                    "location": loc_txt if city_q.lower() in loc_txt.lower() else f"{city_q} ({loc_txt})",
                                    "url": full_url,
                                    "published_at": posted_on,
                                    "description": f"{title} presso {comp_name} - Sede: {loc_txt} ({city_q}) - {posted_on}",
                                    "is_easy_apply": False,
                                })
                    except Exception as e:
                        logger.debug(f"Workday X-Ray warning ({comp_name}): {e}")

            # --- 2. X-RAY DIRETTO SU GREENHOUSE ATS ---
            greenhouse_boards = ["canonical", "datadog", "elastic", "gitlab", "cloudflare", "mozilla"]
            role_tokens = [k.lower() for k in keywords] + ["ai", "machine learning", "data", "python", "software"]
            city_tokens = ["italy", "italia", "remote"] + [c.lower() for c in target_cities]

            for board in greenhouse_boards:
                try:
                    resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
                    if resp.status_code == 200:
                        jobs = resp.json().get("jobs", [])
                        for j in jobs:
                            title = j.get("title", "")
                            loc_name = j.get("location", {}).get("name", "")
                            abs_url = j.get("absolute_url", "")
                            t_low = title.lower()
                            l_low = loc_name.lower()

                            if not any(k in l_low for k in city_tokens):
                                continue
                            if not any(rk in t_low for rk in role_tokens):
                                continue
                            if abs_url in seen_urls:
                                continue
                            seen_urls.add(abs_url)

                            discovered.append({
                                "external_id": f"xray_gh_{j.get('id') or deterministic_url_id('gh', abs_url)}",
                                "portal": "X-Ray Greenhouse ATS",
                                "company": board.title(),
                                "title": title,
                                "location": f"Full Remote Italia ({loc_name})",
                                "url": abs_url,
                                "published_at": str(j.get("updated_at") or "")[:10],
                                "description": f"{title} presso {board.title()} ({loc_name}) - Full Remote in Italia.",
                                "is_easy_apply": True,
                            })
                except Exception as e:
                    logger.debug(f"Greenhouse X-Ray warning ({board}): {e}")

            # --- 3. X-RAY MOTORE DI RICERCA (Post Hiring Manager & Lever/Teamtailor) ---
            primary_city = target_cities[0] if target_cities else "Italia"
            primary_roles = " OR ".join(keywords[:3])
            ddg_queries = [
                (
                    "X-Ray ATS (Lever/Teamtailor)",
                    f"site:jobs.lever.co OR site:boards.greenhouse.io ({primary_roles}) ({primary_city} OR Italy)",
                ),
                (
                    "LinkedIn Post Hiring Manager",
                    f'site:linkedin.com/posts ("stiamo cercando" OR "we are hiring" OR "cerco per il mio team") ({primary_roles}) {primary_city}',
                ),
            ]
            for portal_label, q in ddg_queries:
                try:
                    resp = await client.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": q},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        for r in soup.select("div.result, div.web-result")[:8]:
                            a_el = r.select_one("a.result__a")
                            if not a_el:
                                continue
                            raw_href = a_el.get("href", "")
                            if "uddg=" in raw_href:
                                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                                href = parsed_qs.get("uddg", [raw_href])[0]
                            else:
                                href = raw_href
                            if not href or not href.startswith("http") or href in seen_urls:
                                continue
                            seen_urls.add(href)

                            title = a_el.get_text(" ", strip=True)
                            snippet_el = r.select_one("a.result__snippet, div.result__snippet")
                            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""

                            # Tenta di estrarre il nome reale dell'azienda dall'URL ATS (es. jobs.lever.co/azienda)
                            inferred_comp = "Post LinkedIn (X-Ray)"
                            parsed_u = urllib.parse.urlparse(href)
                            host_low = parsed_u.netloc.lower()
                            path_parts = [p for p in parsed_u.path.strip("/").split("/") if p]
                            if ("lever.co" in host_low or "greenhouse.io" in host_low) and path_parts:
                                slug = path_parts[0].replace("-", " ").replace("_", " ").strip()
                                if slug and slug.lower() not in {"jobs", "boards", "embed", "v1"}:
                                    inferred_comp = slug.title()
                            elif "teamtailor.com" in host_low:
                                sub = host_low.split(".teamtailor.com")[0].split(".")[-1]
                                if sub and sub not in {"www", "jobs", "career", "careers"}:
                                    inferred_comp = sub.replace("-", " ").title()

                            discovered.append({
                                "external_id": deterministic_url_id("xray_web", href),
                                "portal": portal_label,
                                "company": inferred_comp,
                                "title": title[:130],
                                "location": f"{primary_city} / Remote Italia",
                                "url": href,
                                "description": f"{title}\n{snippet}\nSede: {primary_city} / Remote",
                                "is_easy_apply": False,
                            })
                except Exception as e:
                    logger.debug(f"DDG X-Ray warning: {e}")

        logger.info(f"[X-Ray] Scansione completata: estratti {len(discovered)} annunci da ATS e X-Ray!")
        return discovered


class SignalScout:
    """
    Signal-Based Scouting (Mercato Nascosto):
    Monitora Google News RSS sulle città configurate dall'utente per intercettare:
    - Round di finanziamento startup AI / tech
    - Nuovi centri R&D, aperture sedi o piani di assunzione
    - Vittorie di bandi o progetti tecnologici.
    """

    def _load_seen(self) -> set:
        if SEEN_SIGNALS_FILE.exists():
            try:
                with open(SEEN_SIGNALS_FILE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                pass
        return set()

    def _save_seen(self, seen: set):
        try:
            with open(SEEN_SIGNALS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(seen)[-300:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Errore salvataggio seen_signals: {e}")

    async def find_market_signals(self, ignore_seen: bool = False) -> List[Dict[str, Any]]:
        cfg = load_candidate_config()
        target_cities = cfg.get("search_preferences", {}).get("target_cities") or [
            cfg.get("candidate_profile", {}).get("city", "Italia")
        ]
        city_or = " OR ".join(f'"{c}"' for c in target_cities[:4])
        primary_city = target_cities[0] if target_cities else "Italia"

        queries = [
            f'({city_or}) AND ("startup" OR "finanziamento" OR "round" OR "assunzioni" OR "nuovo centro") AND ("Intelligenza Artificiale" OR "AI" OR "tech" OR "software" OR "digital") when:14d',
            f'({city_or}) AND ("AI4I" OR "Fondazione LINKS" OR "Reply" OR "Teoresi" OR "Leonardo" OR "Banca Sella" OR "Centrico" OR "Italdesign" OR "Intesa Sanpaolo" OR "Zucchetti" OR "Satispay" OR "Docebo") AND ("AI" OR "assunzioni" OR "ricerca" OR "progetto" OR "innovazione") when:14d',
        ]

        seen = set() if ignore_seen else self._load_seen()
        new_signals: List[Dict[str, Any]] = []

        target_entities = [
            "AI4I", "I3P", "OGR", "Reply", "Teoresi", "Leonardo", "Banca Sella", "Centrico",
            "LINKS", "Italdesign", "Stellantis", "Comau", "Arduino", "Qualcomm", "Thales",
            "Intesa Sanpaolo", "Reale Mutua", "Zucchetti", "Tilby", "NTT DATA", "Capgemini",
            "Accenture", "Prima Assicurazioni", "New Altitude", "CSI Piemonte", "Synesthesia",
            "Visualitics", "Gecal", "Altair", "EnginSoft", "Ferrero", "Akkodis", "Almaviva", "Dana",
            "aizoon", "SPEA", "Satispay", "Docebo", "Proxima", "AGM Solutions", "F2 Informatica",
            "Amaris", "CGM Consulting", "Bit", "Sirma", "AVL", "Dumarey", "Marelli", "SKF", "Iren",
        ]

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for q in queries:
                rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=it&gl=IT&ceid=IT:it"
                try:
                    resp = await client.get(rss_url)
                    if resp.status_code != 200:
                        continue

                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:12]:
                        title = item.find("title").text if item.find("title") is not None else ""
                        link = item.find("link").text if item.find("link") is not None else ""
                        pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""

                        if not title or not link:
                            continue

                        sig_id = deterministic_url_id("sig", title[:60])
                        if sig_id in seen:
                            continue

                        t_lower = title.lower()
                        if any(bad in t_lower for bad in ["stagione sportiva", "diocesi", "mostra", "danza", "teatro", "calcio", "partita"]):
                            continue

                        seen.add(sig_id)

                        detected_entity = f"Tech / Azienda ({primary_city})"
                        for ent in target_entities:
                            if ent.lower() in t_lower:
                                detected_entity = ent
                                break
                        else:
                            if " - " in title:
                                detected_entity = title.rsplit(" - ", 1)[-1].strip()

                        net_links = build_networking_links(detected_entity, primary_city) or {}

                        new_signals.append({
                            "id": sig_id,
                            "title": title,
                            "url": link,
                            "pub_date": pub_date[:16] if pub_date else "Recente",
                            "entity": detected_entity,
                            "hm_url": net_links.get("hm_url"),
                            "ref_url": net_links.get("ref_url"),
                            "xray_url": net_links.get("xray_url"),
                        })

                        if len(new_signals) >= 5:
                            break
                except Exception as e:
                    logger.warning(f"Errore lettura RSS segnali: {e}")

                if len(new_signals) >= 5:
                    break

        self._save_seen(seen)
        return new_signals
