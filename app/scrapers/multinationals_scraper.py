import asyncio
import html
import logging
import re
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any
import httpx
from bs4 import BeautifulSoup

from app.config import BASE_DIR, load_candidate_config
from app.database import deterministic_url_id
from app.site_catalog import get_mapped_sites
from app.stealth_browser import StealthBrowserManager, human_delay, human_scroll
from app.ai_matcher import GeminiMatcher

logger = logging.getLogger("JobAgent.Multinationals")

GENERIC_LINK_TEXTS = {
    "dettagli",
    "vedi dettagli",
    "scopri di più",
    "scopri",
    "find out more",
    "read more",
    "read more >",
    "learn more",
    "view more",
    "view job",
    "view roles",
    "view details",
    "apply",
    "apply now",
    "candidati",
    "candidati ora",
    "avviso pubblico",
    "scarica",
    "download",
    "apri",
    "info",
    "leggi tutto",
    "more info",
}

NAV_IGNORE_TEXTS = {
    "home",
    "homepage",
    "chi siamo",
    "about",
    "about us",
    "servizi",
    "prodotti",
    "soluzioni",
    "contatti",
    "contact",
    "contact us",
    "privacy",
    "privacy policy",
    "informativa sulla privacy",
    "informativa candidati",
    "cookie",
    "cookie policy",
    "note legali",
    "lavora con noi",
    "work with us",
    "carriere",
    "careers",
    "posizioni aperte",
    "open positions",
    "all our jobs",
    "jobs",
    "welfare",
    "certificazioni",
    "partner",
    "news",
    "blog",
    "eventi",
    "login",
    "accedi",
    "area riservata",
    "english",
    "italiano",
    "it",
    "en",
    "fr",
    "de",
    "job cart",
    "linkedin",
    "facebook",
    "instagram",
    "youtube",
    "twitter",
    "tiktok",
    "academy",
    "formazione",
    "sostenibilità",
    "governance",
    "investitori",
    "investor relations",
    "media",
    "stampa",
    "candidatura spontanea",
    "invia una candidatura spontanea",
    "annunci di lavoro",
}


def _humanize_slug(url_or_path: str) -> str:
    """Ricava un titolo leggibile dallo slug dell'URL quando il testo del link è generico (es. 'Dettagli')."""
    parsed = urllib.parse.urlparse(url_or_path)
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    if not path:
        return ""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    slug = parts[-1]
    # Se l'ultimo segmento è un UUID (es. 3ed90ccb-59bc-45ea-a6e5-77b6e36cdb20), non è un titolo leggibile
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", slug):
        return ""
    # Se l'ultimo segmento è solo un ID numerico (es. /jobs/business-manager/2026-35669), usa il penultimo
    if re.fullmatch(r"[\d\-_]+", slug) and len(parts) >= 2:
        slug = parts[-2]
    slug = re.sub(r"\.(html?|pdf|php|aspx?)$", "", slug, flags=re.IGNORECASE)
    # Rimuovi eventuale ID/timestamp numerico iniziale (es. 8447629-internship-data o 28102026040843cybersecurity)
    slug = re.sub(r"^\d{4,}[-_]*", "", slug)
    slug = slug.replace("-", " ").replace("_", " ")
    slug = re.sub(r"\s+", " ", slug).strip()
    return slug.title() if slug else ""


class MultinationalsScraper:
    """
    Raccoglie offerte con selettori dedicati, espansione 'Load More' e paginazione numerata ('1, 2, 3 / Next') per:
    - Workday ATS (Sella, Leonardo, Thales, PwC)
    - SAP SuccessFactors (Intesa Sanpaolo, CNH Industrial, Dana, EY, KPMG)
    - Oracle Cloud HCM & Oracle Taleo (Reale Group, Altair Engineering)
    - AshbyHQ (Satispay, Docebo con paginazione numerata)
    - Allibo ATS (Bit Software Solutions)
    - Eightfold AI (Arduino / Qualcomm)
    - Avature & Cornerstone ATS (Deloitte, MSC)
    - In-recruiting / Intervieweb (Zucchetti & Tilby, Fondazione LINKS)
    - Factorial ATS (Synesthesia), Join.com (Visualitics), Gecal, CSI Piemonte, EnginSoft, Akkodis, Ferrero, Almaviva
    - aizoOn, SPEA, Proxima Group, AGM Solutions, F2 Informatica, Amaris Consulting, CGM Consulting, Sirma
    - Stellantis & Comau API, Amazon Jobs API
    """

    def __init__(self, ai_matcher: GeminiMatcher):
        self.ai_matcher = ai_matcher

    async def scrape_all(self) -> List[Dict[str, Any]]:
        mapped = get_mapped_sites()
        if not mapped:
            logger.warning("Nessun sito mappato in career_sites_mapping.json")
            return []

        logger.info(f"[Scraper] Avvio scansione mirata su {len(mapped)} portali ufficiali...")

        # Raggruppa i task per non saturare la memoria (pool di 4 alla volta)
        all_jobs: List[Dict[str, Any]] = []
        site_items = list(mapped.items())
        batch_size = 4

        for i in range(0, len(site_items), batch_size):
            batch = site_items[i : i + batch_size]
            tasks = [self._scrape_single_company(name, data) for name, data in batch]
            results_nested = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results_nested:
                if isinstance(res, list):
                    all_jobs.extend(res)
                elif isinstance(res, Exception):
                    logger.warning(f"Errore su un portale: {res}")

        logger.info(f"[Scraper] Scansione mirata completata: {len(all_jobs)} annunci trovati.")
        return all_jobs

    async def _scrape_single_company(self, name: str, site_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        site_type = site_data.get("type", "custom_career")
        url = site_data.get("iframe_url") or site_data.get("mapped_url", "")
        default_location = site_data.get("default_location", "Italia")
        if not url:
            return []

        # 1. Amazon Jobs via API veloce
        if site_type == "amazon_portal":
            return await self._scrape_amazon_api()

        # 2. Stellantis & Comau via API JSON ufficiale CWS
        if site_type == "stellantis":
            return await self._scrape_stellantis_api(name)

        # 3. Bit Software Solutions (Allibo ATS connector + pagina web)
        if site_type == "bit_allibo":
            return await self._scrape_bit_allibo(name, url, default_location)

        # 4. F2 Informatica (card H2 + H4 con bottoni modali 'Scopri')
        if site_type == "f2informatica":
            return await self._scrape_f2informatica(name, url, default_location)

        # 5. Proxima Group via HTTPX diretto (evita ERR_HTTP2_PROTOCOL_ERROR di Chromium)
        if site_type == "proxima":
            return await self._scrape_proxima_httpx(name, url, default_location)

        # 6. Dumarey ADP (API JSON webapi/api/v1/vacancy + fallback browser)
        if site_type == "dumarey_adp":
            return await self._scrape_dumarey_adp(name, url, default_location)

        # 7. LinkedIn cluster/ricerche dirette vengono già gestite dal modulo LinkedIn con sessione autenticata
        if site_type == "linkedin_search":
            return []

        # 6. Portali Browser (con supporto a 'Load More', paginazione numerata 1-2-3-4 e arricchimento Ashby)
        results = await self._scrape_browser_portal(name, url, site_type, default_location)

        # Se è un portale Ashby (Satispay o Docebo), arricchisci le sedi e descrizioni anche tramite API AshbyHQ
        ashby_board = site_data.get("ashby_board")
        if ashby_board:
            api_jobs = await self._scrape_ashby_api(name, ashby_board)
            existing_urls = {r["url"].split("?")[0].rstrip("/") for r in results}
            # Aggiorna location/descrizione dei job già estratti dalla pagina paginata e aggiungi eventuali mancanti
            api_by_url = {j["url"].split("?")[0].rstrip("/"): j for j in api_jobs}
            for r in results:
                norm_u = r["url"].split("?")[0].rstrip("/")
                if norm_u in api_by_url:
                    r["location"] = api_by_url[norm_u]["location"]
                    r["description"] = api_by_url[norm_u]["description"]
            for norm_u, aj in api_by_url.items():
                if norm_u not in existing_urls:
                    results.append(aj)

        return results

    async def _scrape_amazon_api(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        url = "https://www.amazon.jobs/en/search.json?country=ITA&result_limit=25&sort=recent"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("jobs", []):
                        job_id = item.get("id_icims") or item.get("id")
                        job_path = item.get("job_path", "")
                        results.append({
                            "external_id": f"amazon_{job_id}",
                            "portal": "Amazon Jobs",
                            "company": "Amazon",
                            "title": item.get("title", ""),
                            "location": item.get("normalized_location", "Italy"),
                            "url": f"https://www.amazon.jobs{job_path}",
                            "description": f"{item.get('description_short', '')}\n{item.get('basic_qualifications', '')}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"Amazon API error: {e}")
        return results

    async def _scrape_ashby_api(self, name: str, board: str) -> List[Dict[str, Any]]:
        """Recupera metadati completi (sede, modalità, descrizione) da AshbyHQ (Satispay, Docebo)."""
        results: List[Dict[str, Any]] = []
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(api_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    for j in resp.json().get("jobs", []):
                        title = j.get("title", "")
                        loc = j.get("location", "Italia")
                        workplace = j.get("workplaceType", "")
                        is_remote = j.get("isRemote", False)
                        job_url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{j.get('id', '')}"
                        desc_plain = j.get("descriptionPlain") or f"{title} - {loc} ({workplace})"
                        loc_str = f"{loc} ({workplace})" if workplace else loc
                        if (workplace.lower() == "remote" or (is_remote and not workplace)) and "remote" not in loc_str.lower():
                            loc_str = f"{loc_str} - Remote"
                        results.append({
                            "external_id": f"ashby_{board}_{j.get('id') or deterministic_url_id('ashby', job_url)}",
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": loc_str,
                            "url": job_url,
                            "description": f"{title}\nSede: {loc_str}\n{desc_plain[:2500]}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Ashby API warning: {e}")
        return results

    async def _scrape_proxima_httpx(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """Estrae eventuali annunci reali da Proxima Group via HTTPX ignorando i placeholder 'Lorem ipsum'."""
        results: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a_el in soup.select("h3 a[href], a[href*='offerta'], a[href*='job']"):
                        title = a_el.get_text(" ", strip=True)
                        href = a_el.get("href", "")
                        if not title or len(title) < 4 or "lorem ipsum" in title.lower() or title.lower() in GENERIC_LINK_TEXTS:
                            continue
                        full_url = urllib.parse.urljoin(url, href)
                        results.append({
                            "external_id": deterministic_url_id("proxima", full_url),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": default_location,
                            "url": full_url,
                            "description": title,
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Errore HTTPX Proxima: {e}")
        return results

    async def _scrape_dumarey_adp(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """Interroga l'API JSON ADP di Dumarey (ex Punch Torino) per tutte le vacancies attive."""
        results: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
                await client.get("https://dumarey.careers.adp.com/webapi/api/v1/token", headers={"User-Agent": "Mozilla/5.0"})
                resp = await client.post(
                    "https://dumarey.careers.adp.com/webapi/api/v1/vacancy",
                    json={"limit": 50, "start": 0, "includeParametrics": True, "filter": []},
                    headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for v in data.get("data", []):
                        vid = str(v.get("id") or v.get("reference") or "")
                        title = v.get("title") or v.get("jobTitle") or ""
                        loc = v.get("location") or v.get("city") or default_location
                        slug = v.get("slug") or vid
                        job_url = f"https://dumarey.careers.adp.com/vacancies/{slug}" if slug else url
                        desc = v.get("description") or v.get("summary") or f"{title} - {loc}"
                        if not title:
                            continue
                        results.append({
                            "external_id": f"dumarey_{vid}" if vid else deterministic_url_id("dumarey", job_url),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": str(loc),
                            "url": job_url,
                            "description": f"{title}\nSede: {loc}\n{desc[:2500]}",
                            "is_easy_apply": False,
                        })
                    logger.info(f"[{name}] Estratti {len(results)} annunci da ADP API.")
        except Exception as e:
            logger.warning(f"[{name}] Errore ADP API Dumarey: {e}")
        return results

    async def _scrape_bit_allibo(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """Estrae gli annunci di Bit Software Solutions dal connettore Allibo ATS integrato nella pagina."""
        results: List[Dict[str, Any]] = []
        connector_url = "https://joblink.allibo.com/ats3/connector.aspx?DM=1929&DMN=4M50wRVFM67sgK6GoX6N&SG=2&FT=154&LN=IT"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
                resp = await client.get(connector_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    raw_js = resp.text
                    # Decodifica sequenze unicode \u0026 ed entità HTML
                    unescaped = raw_js.encode("utf-8").decode("unicode_escape", errors="ignore")
                    unescaped = html.unescape(unescaped)
                    soup = BeautifulSoup(unescaped, "html.parser")
                    for adv in soup.select("div.AHRP_adv"):
                        a_el = adv.select_one("a.AHRP_subj")
                        det_el = adv.select_one("p.AHRP_det")
                        if not a_el:
                            continue
                        title = a_el.get_text(" ", strip=True)
                        loc_txt = det_el.get_text(" ", strip=True) if det_el else default_location
                        onclick = a_el.get("onclick", "")
                        m = re.search(r"(https?://[^\s'\"\\]+)", onclick)
                        job_url = html.unescape(m.group(1)) if m else url
                        results.append({
                            "external_id": deterministic_url_id("bit", job_url),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": loc_txt.replace("Sede di lavoro:", "").strip() or default_location,
                            "url": job_url,
                            "description": f"{title} presso {name} - {loc_txt}",
                            "is_easy_apply": False,
                        })
                    logger.info(f"[{name}] Estratti {len(results)} annunci da Allibo ATS.")
        except Exception as e:
            logger.warning(f"[{name}] Errore Allibo ATS: {e}")
        return results

    async def _scrape_f2informatica(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """Estrae le card di lavoro su F2 Informatica (che usano H2 + H4 e bottoni modali 'Scopri')."""
        results: List[Dict[str, Any]] = []
        mgr = StealthBrowserManager(profile_name="site_f2informatica")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                await human_delay(3.0, 4.5)
                await self._click_load_more_buttons(page, max_clicks=3)

                cards = await page.eval_on_selector_all(
                    "h2",
                    """els => els.map(h2 => {
                        const title = (h2.innerText || '').trim();
                        const parent = h2.parentElement;
                        const desc = parent ? (parent.innerText || '').trim() : title;
                        return { title, desc };
                    })""",
                )
                for c in cards:
                    title = c.get("title", "").strip()
                    desc = c.get("desc", "").strip()
                    if not title or len(title) < 4 or title.lower() in NAV_IGNORE_TEXTS:
                        continue
                    job_url = f"{url.rstrip('/')}/#{urllib.parse.quote(title.lower().replace(' ', '-'))}"
                    results.append({
                        "external_id": deterministic_url_id("f2", job_url),
                        "portal": name,
                        "company": name,
                        "title": title[:140],
                        "location": default_location,
                        "url": job_url,
                        "description": f"{title}\n{desc}",
                        "is_easy_apply": False,
                    })
                logger.info(f"[{name}] Estratti {len(results)} annunci.")
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping F2 Informatica: {e}")
        return results

    async def _scrape_stellantis_api(self, name: str) -> List[Dict[str, Any]]:
        """Interroga l'API ufficiale Talent Cloud di Stellantis & Comau filtrando le sedi italiane o remote."""
        results: List[Dict[str, Any]] = []
        api_url = "https://jobsapi-google.m-cloud.io/api/job/search"
        queries = ["Turin", "Torino", "Italy", "Comau", "AI", "Software", "Data"]
        seen_ids = set()

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                for q in queries:
                    params = {
                        "pageSize": 50,
                        "offset": 0,
                        "companyName": "companies/16115603-6c1b-4c45-b544-238a4e6c51b3",
                        "query": q,
                    }
                    resp = await client.get(api_url, params=params, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    for hit in data.get("searchResults", []):
                        j = hit.get("job", {})
                        jid = str(j.get("id") or j.get("ref") or "")
                        if not jid or jid in seen_ids:
                            continue
                        country = (j.get("primary_country") or "").upper()
                        city = j.get("primary_city") or ""
                        title = j.get("title") or ""
                        desc_raw = j.get("description") or ""
                        # Accetta posizioni in Italia o con Torino/Comau nel testo
                        if country not in ("IT", "ITA", "ITALY") and not any(
                            k in f"{city} {title} {desc_raw[:400]}".lower()
                            for k in ("torino", "turin", "italy", "italia", "comau", "grugliasco")
                        ):
                            continue
                        seen_ids.add(jid)
                        job_url = j.get("url") or j.get("seo_url") or "https://careers.stellantis.com/job-search-results/"
                        loc_str = f"{city}, {country}".strip(", ") or "Italia"
                        desc = desc_raw or f"{title} - {loc_str}"
                        results.append({
                            "external_id": f"stellantis_{jid}",
                            "portal": name,
                            "company": j.get("brand") or name,
                            "title": title[:140],
                            "location": loc_str,
                            "url": job_url,
                            "description": desc[:3500],
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Errore API Stellantis: {e}")
        return results

    async def _click_load_more_buttons(self, page, max_clicks: int = 5) -> None:
        """
        Individua e clicca eventuali pulsanti 'Load More / Mostra altri / Carica altri / Show More Positions / See more'
        per espandere tutti gli annunci presenti nella pagina prima dell'estrazione.
        """
        load_more_selectors = [
            "button.show-more-positions",
            "#tile-more-results-link",
            "#tile-more-results",
            "button#tile-more-results-link",
            "button#tile-more-results",
            "a.more-link.button",
            "button[data-automation-id='loadMoreJobs']",
            "button:has-text('More Search Results')",
            "button:has-text('Show More Positions')",
            "button:has-text('Show more')",
            "button:has-text('See more')",
            "button:has-text('Load more')",
            "button:has-text('Load More')",
            "button:has-text('Mostra altri')",
            "button:has-text('Mostra di più')",
            "button:has-text('Carica altri')",
            "button:has-text('Carica di più')",
            "button:has-text('Vedi altri')",
            "button:has-text('Visualizza altri')",
            "button:has-text('More jobs')",
            "button:has-text('More Jobs')",
            "a:has-text('Mostra altri')",
            "a:has-text('Carica altri')",
            "a:has-text('Load more')",
            "a:has-text('Show More')",
        ]

        for click_idx in range(max_clicks):
            await human_scroll(page)
            clicked = False
            for sel in load_more_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        # Evita il bottone cookie 'Show more' di CookieYes (cky-show-desc-btn)
                        cls_attr = (await btn.get_attribute("class")) or ""
                        if "cky-" in cls_attr or "cookie" in cls_attr.lower() or "iubenda" in cls_attr.lower():
                            continue
                        logger.info(f"[Pagination] Click #{click_idx + 1} su pulsante carica altri ({sel})")
                        await btn.scroll_into_view_if_needed(timeout=3000)
                        await btn.click(timeout=5000)
                        await human_delay(2.0, 3.5)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break

    async def _go_to_next_numbered_page(self, page, next_page_num: int) -> bool:
        """
        Gestisce la paginazione numerata (es. Docebo '1, 2, 3, 4', AGM Solutions '?page=2', aizoOn 'Next'):
        1. Cerca prima il pulsante/link numerico corrispondente a `next_page_num` (es. '2', '3', '4').
        2. Se non trova il numero esatto, cerca il pulsante 'Next' / '›' / '→' attivo.
        Restituisce True se ha effettuato il cambio pagina, False se non ci sono altre pagine.
        """
        num_str = str(next_page_num)
        numbered_selectors = [
            f"button.careers-list__pagination-number:text-is('{num_str}')",
            f".pagination a.page-link:text-is('{num_str}')",
            f".pagination a:text-is('{num_str}')",
            f"a[href*='startrow=']:text-is('{num_str}')",
            f".page-numbers:text-is('{num_str}')",
            f"[class*='pagination'] button:text-is('{num_str}')",
            f"[class*='pagination'] a:text-is('{num_str}')",
            f"nav button:text-is('{num_str}')",
            f"nav a:text-is('{num_str}')",
        ]

        for sel in numbered_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    cls_attr = (await loc.get_attribute("class")) or ""
                    if "is-current" in cls_attr or "active" in cls_attr or "disabled" in cls_attr:
                        continue
                    logger.info(f"[Numbered Pagination] Click su pagina #{next_page_num} ({sel})")
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    await loc.click(timeout=5000)
                    await human_delay(2.2, 3.5)
                    return True
            except Exception:
                continue

        # Fallback: pulsante Next / Freccia pagina successiva (es. aizoOn 'Next', Docebo '→', AGM '›')
        next_btn_selectors = [
            "button.careers-list__pagination-next:not([disabled])",
            "button:has-text('Next'):not([disabled])",
            "button:has-text('Successiva'):not([disabled])",
            "a.page-link:text-is('›')",
            "a.next.page-numbers",
            "[class*='pagination'] a[rel='next']",
        ]
        for sel in next_btn_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    is_disabled = await loc.get_attribute("disabled")
                    cls_attr = (await loc.get_attribute("class")) or ""
                    if is_disabled is not None or "disabled" in cls_attr:
                        continue
                    logger.info(f"[Next Page Pagination] Click su pagina successiva #{next_page_num} ({sel})")
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    await loc.click(timeout=5000)
                    await human_delay(2.2, 3.5)
                    return True
            except Exception:
                continue

        return False

    def _get_selector_for_type(self, site_type: str) -> str:
        """Restituisce il selettore CSS ottimale per ciascuna famiglia di portale/ATS."""
        if site_type == "workday":
            return "a[data-automation-id='jobTitle'], a.css-19uc56f, a[href*='/job/']"
        if site_type == "successfactors":
            return "a.jobTitle-link, tr.data-row a[href*='/job/'], a[href*='/job/']"
        if site_type == "taleo":
            return "h4.oracletaleocwsv2-head-title a, a[href*='viewRequisition'], div.oracletaleocwsv2-accordion-head a"
        if site_type == "qualcomm":
            return "a[href*='/careers/job/'], a.position-card, div.position-card a"
        if site_type == "oracle_cloud":
            return "a.job-title-link, a[href*='job-details'], a[href*='requisition-item']"
        if site_type == "avature":
            return "a.jobTitle, a[href*='/JobDetail/'], a[href*='/job/']"
        if site_type == "cornerstone":
            return "a[data-tag='jobTitle'], a[href*='requisition']"
        if site_type == "akkodis":
            return "a[href*='/lavora-con-noi/jobs/'], a[href*='/jobs/']"
        if site_type == "ferrero":
            return "a[href*='/it/jobs/'], a[href*='/jobs/']"
        if site_type == "csi_piemonte":
            return "a[href*='avvisi_di_selezione'][href$='.pdf']"
        if site_type == "factorial":
            return "a[href*='/job_posting/'], li.factorial-jobs-item a, a[href*='job']"
        if site_type == "visualitics":
            return "a[href*='join.com/companies/visualitics/'], a[href*='join.com']"
        if site_type == "enginsoft":
            return "a[href*='careers/'][href$='.html']"
        if site_type == "gecal":
            return "main a[href], div a[href]"
        if site_type == "inrecruiting":
            return "a[href*='Annuncio'], a[href*='annunci'], a[href*='job'], a[href*='detail'], div.job-item a, h3 a, h4 a"
        if site_type == "almaviva":
            return "a[href*='posizioni-aperte'], a[href*='job'], a[href*='dettaglio'], div.card a, article a"
        if site_type == "aizoon":
            return "a[href*='/careers/job/']"
        if site_type == "spea":
            return "a[href*='/it/job/'], a[href*='/job/']"
        if site_type == "ashby":
            return "a[href*='/satispay/'], a[class*='_container_']"
        if site_type == "docebo":
            return "a[href*='jobs.ashbyhq.com/docebo/'], #open-roles a[href*='ashbyhq']"
        if site_type == "agm":
            return "a[href*='/jobs/show/']"
        if site_type == "amaris":
            return "a[href^='/jobs/']"
        if site_type == "cgm":
            return "a[href*='/offerta-lavoro/']"
        if site_type == "sirma":
            return "a.jobs-list-item, a[href*='/jobs/job?jobId=']"
        if site_type == "proxima":
            return "a[href*='offerta'], a[href*='job'], a[href*='position'], h3 a"
        if site_type == "iren":
            return "a[href*='jobid=']"
        if site_type == "ai4i":
            return "a.column-link, a[href*='position'], a[href*='call-for'], a[href*='ncoreplat.com']"
        return (
            "a[href*='job'], a[href*='career'], a[href*='opportunit'], a[href*='detail'], "
            "a[href*='position'], a[href*='vacancy'], a[href*='annunci'], div.card a, tr td a"
        )

    async def _extract_items_from_current_view(
        self,
        page,
        name: str,
        url: str,
        site_type: str,
        default_location: str,
        seen_urls: set,
        results: List[Dict[str, Any]],
    ) -> int:
        """Estrae tutte le offerte visibili nella vista/pagina corrente e le aggiunge a `results`."""
        selector = self._get_selector_for_type(site_type)
        raw_items = await page.eval_on_selector_all(
            selector,
            """els => els.map(el => {
                const txt = (el.innerText || el.textContent || '').trim();
                const href = el.getAttribute('href') || '';
                const rawCard = el.closest('li, tr, article, .card, .job-card, .job-item, .oracletaleocwsv2-accordion, .position-card, .row');
                const card = (rawCard && (rawCard.innerText || '').length < 450)
                             ? rawCard
                             : (el.parentElement?.parentElement || el.parentElement);
                let cardTitle = '';
                let cardText = '';
                if (card && (card.innerText || '').length < 500) {
                    const h = card.querySelector('h1, h2, h3, h4, h5, .title, .job-title, .jobTitle, strong');
                    if (h) cardTitle = (h.innerText || '').trim();
                    cardText = (card.innerText || '').trim().slice(0, 450);
                }
                return { txt, href, cardTitle, cardText };
            })""",
        )

        added_on_page = 0
        for item in raw_items:
            txt = (item.get("txt") or "").strip()
            href = (item.get("href") or "").strip()
            card_title = (item.get("cardTitle") or "").strip()
            card_text = (item.get("cardText") or "").strip()

            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            if site_type == "enginsoft" and href.startswith("careers/"):
                full_url = urllib.parse.urljoin("https://www.enginsoft.com/", href)
            else:
                full_url = urllib.parse.urljoin(url, href)

            if full_url in seen_urls or full_url.rstrip("/") == url.rstrip("/"):
                continue

            low_txt = txt.lower().strip()
            low_url = full_url.lower()

            # Ignora eventuali placeholder 'Lorem ipsum' (es. su Proxima Group)
            if "lorem ipsum" in low_txt or "lorem ipsum" in card_text.lower():
                continue

            # Filtri e risoluzione titoli specifici per portale
            if site_type == "csi_piemonte":
                if any(skip in low_txt or skip in low_url for skip in ("esito", "proroga", "modulo", "graduatoria")):
                    continue
                title = _humanize_slug(full_url) or card_title or txt
            elif site_type == "aizoon":
                # Su aizoOn il testo della card ha: Linea 0 = Categoria, Linea 1 = Titolo, Linea 2 = Sede · Modalità
                lines = [ln.strip() for ln in card_text.splitlines() if ln.strip()]
                if len(lines) >= 2:
                    title = lines[1]
                elif card_title and card_title.lower() not in GENERIC_LINK_TEXTS:
                    title = card_title
                else:
                    title = txt
            elif site_type == "spea":
                # Su SPEA il testo è tipo 'Engineering Software Engineer ITALIA +'
                clean_spea = re.sub(r"\s*ITALIA\s*\+?\s*$", "", txt, flags=re.IGNORECASE).strip()
                title = clean_spea or _humanize_slug(full_url)
            elif site_type == "gecal":
                parsed_path = urllib.parse.urlparse(full_url).path.strip("/")
                if not parsed_path or "/" in parsed_path:
                    continue
                if low_txt in NAV_IGNORE_TEXTS or any(
                    nav in low_url
                    for nav in (
                        "privacy",
                        "cookie",
                        "contatti",
                        "chi-siamo",
                        "servizi",
                        "prodotti",
                        "soluzioni",
                        "academy",
                        "welfare",
                        "certificazioni",
                        "news",
                        "blog",
                        "lavoraconnoi",
                        "gecal-group",
                        "home",
                    )
                ):
                    continue
                title = txt.splitlines()[0].strip()
            else:
                if low_txt in NAV_IGNORE_TEXTS:
                    continue
                if (
                    not txt
                    or len(txt) < 4
                    or low_txt in GENERIC_LINK_TEXTS
                    or any(low_txt.startswith(g) for g in GENERIC_LINK_TEXTS)
                ):
                    slug_title = _humanize_slug(full_url)
                    if slug_title and len(slug_title) >= 4:
                        title = slug_title
                    elif card_title and card_title.lower() not in GENERIC_LINK_TEXTS:
                        title = card_title.splitlines()[0].strip()
                    else:
                        title = ""
                else:
                    title = txt.splitlines()[0].strip()

            if not title or len(title) < 4 or title.lower() in NAV_IGNORE_TEXTS or "lorem ipsum" in title.lower():
                continue

            seen_urls.add(full_url)
            ext_id = deterministic_url_id("job", full_url)

            combined_desc = f"{title}\n{txt}\n{card_text}".strip()

            inferred_location = default_location
            low_combined = combined_desc.lower()
            if site_type == "aizoon":
                lines = [ln.strip() for ln in card_text.splitlines() if ln.strip()]
                if len(lines) >= 3:
                    inferred_location = lines[2]
            elif site_type == "iren":
                m_loc = re.search(r"\(([^)]+)\)\s*$", title)
                if m_loc:
                    inferred_location = m_loc.group(1).title()
            else:
                detected_cities = []
                for c_label, c_pat in [
                    ("Torino", r"\b(torino|turin|volpiano|orbassano|ivrea|grugliasco)\b"),
                    ("Milano", r"\b(milano|milan|assago|segrate|monza)\b"),
                    ("Roma", r"\b(roma|rome|pomezia)\b"),
                    ("Bologna", r"\b(bologna|casalecchio|imola)\b"),
                    ("Firenze", r"\b(firenze|florence)\b"),
                    ("Napoli", r"\b(napoli|naples)\b"),
                    ("Padova", r"\b(padova|padua)\b"),
                    ("Genova", r"\b(genova|genoa)\b"),
                    ("Bari", r"\b(bari|molfetta)\b"),
                ]:
                    if re.search(c_pat, low_combined):
                        detected_cities.append(c_label)
                if detected_cities:
                    inferred_location = ", ".join(detected_cities) + ", Italia"
                elif "full remote" in low_combined or "telelavoro" in low_combined:
                    inferred_location = "Full Remote (Italia)"

            results.append({
                "external_id": ext_id,
                "portal": name,
                "company": name,
                "title": title[:140],
                "location": inferred_location,
                "url": full_url,
                "description": combined_desc[:2500],
                "is_easy_apply": False,
            })
            added_on_page += 1

            if len(results) >= 60:
                break

        return added_on_page

    async def _scrape_browser_portal(
        self, name: str, url: str, site_type: str, default_location: str = "Italia"
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen_urls = set()
        mgr = StealthBrowserManager(profile_name=f"site_{abs(hash(name)) % 1000}")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                logger.info(f"[{name}] Apertura portale ({site_type}): {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.5, 5.5)

                # 1. Espandi eventuali pulsanti 'Load More / Mostra altri / See more' nella vista corrente
                await self._click_load_more_buttons(page, max_clicks=5)

                # 2. Estrai annunci dalla Pagina 1
                await self._extract_items_from_current_view(
                    page, name, url, site_type, default_location, seen_urls, results
                )

                # 3. Naviga eventuali pagine numerate successive (2, 3, 4, 5, 6) o pulsante 'Next' (es. Docebo, AGM, aizoOn)
                max_pages = 6
                for next_page_num in range(2, max_pages + 1):
                    if len(results) >= 60:
                        break
                    moved = await self._go_to_next_numbered_page(page, next_page_num)
                    if not moved:
                        break
                    added = await self._extract_items_from_current_view(
                        page, name, url, site_type, default_location, seen_urls, results
                    )
                    if added == 0:
                        break

                logger.info(f"[{name}] Estratti {len(results)} annunci validi (inclusa paginazione).")
        except Exception as e:
            logger.warning(f"Errore durante lo scraping su {name} ({url}): {e}")
        return results

    async def apply_standard_ats_form(self, job: Dict[str, Any]) -> bool:
        config = load_candidate_config()
        profile = config.get("candidate_profile", {})
        cv_path: Path = BASE_DIR / profile.get("cv_pdf_path", "data/cv.pdf")

        mgr = StealthBrowserManager(profile_name="ats_apply")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=35000)
                await human_delay(2.5, 4.5)

                field_map = {
                    "#first_name, input[name*='first_name']": profile.get("first_name", ""),
                    "#last_name, input[name*='last_name']": profile.get("last_name", ""),
                    "#email, input[type='email']": profile.get("email", ""),
                    "#phone, input[type='tel']": profile.get("phone", ""),
                    "input[name*='linkedin'], input[id*='linkedin']": profile.get("linkedin_url", ""),
                }

                for selector, value in field_map.items():
                    loc = page.locator(selector).first
                    if await loc.count() > 0 and value:
                        await loc.fill(value)
                        await human_delay(0.5, 1.0)

                file_input = page.locator("input[type='file']").first
                if await file_input.count() > 0 and cv_path.exists():
                    await file_input.set_input_files(str(cv_path))
                    await human_delay(1.5, 2.5)

                submit = page.locator("#submit_app, button[type='submit']").first
                if await submit.count() > 0:
                    await submit.click()
                    await human_delay(3.0, 5.0)
                    return True
        except Exception as e:
            logger.error(f"Errore compilazione form ATS per {job['url']}: {e}")
        return False
