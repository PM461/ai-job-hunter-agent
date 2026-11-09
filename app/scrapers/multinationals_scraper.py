import asyncio
import html
import json
import logging
import re
import hashlib
import urllib.parse
from urllib.parse import quote_plus, urljoin
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import httpx
from bs4 import BeautifulSoup

from app.config import BASE_DIR, load_candidate_config
from app.database import deterministic_url_id, record_scraper_health
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


STOPWORDS_SEARCH = {
    "and", "or", "of", "in", "for", "the", "to", "with", "at", "by",
    "di", "da", "in", "con", "su", "per", "tra", "fra", "il", "lo", "la", "i", "gli", "le", "un", "una",
}


def expand_search_keywords(raw_keywords: List[str], max_terms: int = 18) -> List[str]:
    """
    Espande la lista delle posizioni cercate dal candidato generando:
    1. Le diciture esatte (es. 'AI Engineer', 'Machine Learning Engineer', 'Data Scientist', 'Python Developer')
    2. Le diciture ridotte / scomposte (es. da 'IA Engineer' o 'AI Engineer' -> 'IA', 'AI', 'Engineer',
       da 'Machine Learning Engineer' -> 'Machine Learning', 'Machine', 'Learning', 'Engineer',
       da 'Data Scientist' -> 'Data', 'Scientist', ecc.).
    Utile per tutti i form di ricerca e filtri testuali sui portali aziendali.
    """
    if not raw_keywords:
        raw_keywords = ["AI Engineer", "Data Scientist", "Software Engineer", "Python Developer"]

    expanded: List[str] = []
    seen_lower: set[str] = set()

    def _add(term: str):
        t = (term or "").strip()
        if not t or len(t) < 2:
            return
        low = t.lower()
        if low in STOPWORDS_SEARCH or low in seen_lower:
            return
        seen_lower.add(low)
        expanded.append(t)

    # 1. Diciture esatte
    for kw in raw_keywords:
        _add(str(kw))

    # 2. Diciture ridotte intermedie (es. 'Machine Learning' da 'Machine Learning Engineer', e variante AI <-> IA)
    joined_low = " ".join(str(k).lower() for k in raw_keywords)
    if re.search(r"\b(ai|ia|artificial intelligence|intelligenza artificiale)\b", joined_low):
        _add("AI")
        _add("IA")

    for kw in raw_keywords:
        words = [w.strip() for w in re.split(r"[\s/\-,()]+", str(kw)) if w.strip()]
        if len(words) >= 3:
            _add(" ".join(words[:2]))
            _add(" ".join(words[-2:]))

    # 3. Singoli token / radici ridotte (es. 'Engineer', 'Data', 'Developer', 'Python', 'Scientist', 'Software')
    for kw in raw_keywords:
        words = [w.strip() for w in re.split(r"[\s/\-,()]+", str(kw)) if w.strip()]
        for w in words:
            _add(w)
            if w.lower() == "ai":
                _add("IA")
            elif w.lower() == "ia":
                _add("AI")

    return expanded[:max_terms]


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
        if not url or site_type == "linkedin_search":
            return []

        try:
            jobs = await self._scrape_single_company_raw(name, site_data)
        except Exception as err:
            logger.warning(f"[{name}] Errore durante lo scraping: {err}")
            await record_scraper_health(
                company=name,
                site_type=site_type,
                portal_url=url,
                status="DOM_CHANGED",
                jobs_count=0,
                diagnostic_message=f"⚠️ Eccezione durante l'estrazione ({str(err)[:140]}). Verificare selettori DOM o aggiornare lo scraper.",
                needs_scraper_update=True,
            )
            return []

        if len(jobs) > 0:
            await record_scraper_health(
                company=name,
                site_type=site_type,
                portal_url=url,
                status="HEALTHY",
                jobs_count=len(jobs),
                diagnostic_message=f"✅ DOM integro e offerte in chiaro ({len(jobs)} posizioni estratte).",
                needs_scraper_update=False,
            )
        else:
            # Se sono state trovate 0 offerte, verifica automaticamente se la pagina ha cambiato DOM o se le offerte non sono più in chiaro
            await self.verify_page_dom_and_visibility(name, site_data, scraped_jobs_count=0)

        return jobs

    async def verify_page_dom_and_visibility(
        self,
        name: str,
        site_data: Dict[str, Any],
        scraped_jobs_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Verifica se una pagina aziendale ha cambiato struttura DOM, se le offerte non sono più in chiaro
        (es. offuscate, spostate dietro nuovi selettori/iframe o bloccate da protezioni anti-bot)
        o se bisogna aggiornare lo scraper, registrando l'esito in `scraper_health` per il portale localhost.
        """
        site_type = site_data.get("type", "custom_career")
        url = site_data.get("iframe_url") or site_data.get("mapped_url", "")

        # Regole di firma DOM / API attese per ciascuna famiglia di scraper
        expected_signatures: Dict[str, Dict[str, Any]] = {
            "accenture": {
                "selectors": [".rad-filters-vertical", ".rad-card-vertical", "[data-cmp-is='jobsearch']"],
                "keywords": ["findjobs", "careers", "jobsearch", "area of interest"],
                "label": "Filtri Area of Interest / Card `.rad-card-vertical` o API `findjobs`",
            },
            "capgemini": {
                "selectors": ["a.table-tr", ".card-default", "button.filters-more", "[href*='join-us']"],
                "keywords": ["experience_level", "country_code", "join-us", "macs/v1/jobs"],
                "label": "Tabella/Card offerte `a.table-tr` o REST `/wp-json/macs/v1/jobs`",
            },
            "nttdata": {
                "selectors": ["#search-job-keyword", "#search-job-location", "#search-job-family", "a[href*='/posizioni-aperte/']"],
                "keywords": ["search-job-keyword", "posizioni-aperte", "job-family"],
                "label": "Form `#search-job-keyword`, `#search-job-location`, `#search-job-family` e card `.listing-cards-item`",
            },
            "prima": {
                "selectors": ["#__NEXT_DATA__", "#department", "a[href*='workable.com']"],
                "keywords": ["__NEXT_DATA__", "workable", "department", "offerte-lavoro"],
                "label": "Stato Next.js `#__NEXT_DATA__`, selettore `#department` e link Workable",
            },
            "lutech": {
                "selectors": [
                    "#tcl-careers-filters_select--posizione-offerta",
                    "#tcl-careers-filters_select--sede",
                    "tr.tcl-careers-table_row",
                ],
                "keywords": ["tcl-careers-filters_select", "tcl-careers-table"],
                "label": "5 selettori `#tcl-careers-filters_select--*` e righe tabella `tr.tcl-careers-table_row`",
            },
            "bip": {
                "selectors": [".repeater-grid", ".text-title-md", "a[href*='oraclecloud.com']"],
                "keywords": ["repeater-grid", "oraclecloud.com", "Filter for"],
                "label": "Barra filtri `Seniority/Location`, griglia `.repeater-grid` e modali Oracle Cloud",
            },
            "pwc": {
                "selectors": ["#typehead", "#CategoryAccordion", "#CityAccordion", "#ManagementLevelAccordion"],
                "keywords": ["phApp", "refineSearch", "CategoryAccordion", "ManagementLevelAccordion"],
                "label": "Motore Phenom `window.phApp.ddo` / `/widgets` e accordion `Category / City / ManagementLevel`",
            },
            "ey": {
                "selectors": ["tr.data-row", "a.jobTitle-link", "select#optionsFacetsDD_country", "a#options-search"],
                "keywords": ["optionsFacetsDD_country", "optionsFacetsDD_city", "jobTitle-link"],
                "label": "Portale 1 Yello (`input.js-search-text`) + Portale 2 SuccessFactors (`#options-search`, `tr.data-row`)",
            },
            "leonardo": {
                "selectors": ["[data-automation-id='keywordSearchInput']", "[data-automation-id='jobTitle']"],
                "keywords": ["LeonardoCareerSite", "myworkdayjobs", "wday/cxs"],
                "label": "Workday CXS `/wday/cxs/leonardocompany/LeonardoCareerSite/jobs` e filtri Sede/Categoria/Tipo impiego",
            },
            "wsp": {
                "selectors": ["a[href*='/jobs/']", "#jobs_list_container"],
                "keywords": ["phApp", "teamtailor", "wsp"],
                "label": "Menu laterale Paese/Città WSP + card posizioni aperte",
            },
            "orbyta": {
                "selectors": ["a[href*='annuncio']", "a[href*='vacancy']", ".vacancy-item", "table"],
                "keywords": ["intervieweb", "orbyta", "career"],
                "label": "Schede Sedi / Data In-recruiting (`intervieweb.it`) e lista annunci",
            },
            "altherias": {
                "selectors": [".e-loop-item", "h2", "h3", "a[href*='/carriere/']"],
                "keywords": ["altherias", "carriere", "elementor"],
                "label": "Griglia card Elementor `.e-loop-item` su `/it/carriere/`",
            },
            "mareum": {
                "selectors": ["a[href*='/offerte-di-lavoro/']", ".page-numbers"],
                "keywords": ["offerte-di-lavoro", "page-numbers", "mareum"],
                "label": "Griglia offerte Mareum e paginazione numerica `.page-numbers`",
            },
            "shakers": {
                "selectors": ["#jobs_list_container", "a[href*='/jobs/']"],
                "keywords": ["shakersworks", "jobs_list_container", "teamtailor"],
                "label": "Griglia offerte a fine pagina `#jobs_list_container` (`a[href*='/jobs/']`)",
            },
            "successfactors": {
                "selectors": ["tr.data-row", "a.jobTitle-link", ".jobTitle"],
                "keywords": ["jobTitle-link", "data-row", "search"],
                "label": "Tabella SAP SuccessFactors (`tr.data-row` / `a.jobTitle-link`)",
            },
            "inrecruiting": {
                "selectors": ["a[href*='annuncio']", "a[href*='vacancy']", ".vacancy", "table", "div"],
                "keywords": ["intervieweb", "annunci", "career"],
                "label": "Portale In-recruiting (`intervieweb.it`) con lista annunci in chiaro",
            },
        }

        status = "HEALTHY"
        needs_update = False
        diag_msg = ""
        dom_fp = ""
        jobs_cnt = scraped_jobs_count if scraped_jobs_count is not None else 0

        try:
            async with httpx.AsyncClient(
                timeout=18.0,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=0, max_connections=2),
            ) as client:
                # Casi con endpoint JSON / API dedicati diretti
                if site_type in ("leonardo", "workday") and "myworkdayjobs.com" in url:
                    m_wd = re.match(r"https?://([^/]+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url)
                    if m_wd:
                        tenant = m_wd.group(1).split(".")[0]
                        site_id = m_wd.group(2)
                        cxs_endpoint = f"https://{m_wd.group(1)}.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}/jobs"
                        r_wd = await client.post(
                            cxs_endpoint,
                            json={"appliedFacets": {}, "limit": 5, "offset": 0, "searchText": ""},
                            headers={"Content-Type": "application/json", "Accept": "application/json"},
                        )
                        if r_wd.status_code == 200 and "jobPostings" in r_wd.json():
                            tot = int(r_wd.json().get("total") or len(r_wd.json().get("jobPostings", [])))
                            dom_fp = f"workday_cxs_ok_total_{tot}"
                            if tot > 0 and jobs_cnt == 0 and scraped_jobs_count is not None:
                                status = "WARNING_EMPTY"
                                diag_msg = f"ℹ️ Portale Workday attivo ({tot} offerte globali in chiaro), ma 0 compatibili coi filtri correnti."
                            elif tot == 0:
                                status = "DOM_CHANGED"
                                needs_update = True
                                diag_msg = "⚠️ L'endpoint Workday non restituisce più offerte in chiaro (0 jobPostings). Verificare URL o aggiornare lo scraper."
                            else:
                                status = "HEALTHY"
                                jobs_cnt = jobs_cnt or tot
                                diag_msg = f"✅ Struttura Workday CXS integra e offerte in chiaro ({tot} annunci disponibili)."
                        else:
                            status = "DOM_CHANGED"
                            needs_update = True
                            diag_msg = f"🚨 Endpoint Workday modificato o non più accessibile (HTTP {r_wd.status_code}). Aggiornare lo scraper."
                        await record_scraper_health(name, site_type, url, status, jobs_cnt, diag_msg, dom_fp, needs_update)
                        return {
                            "company": name,
                            "site_type": site_type,
                            "portal_url": url,
                            "status": status,
                            "needs_scraper_update": needs_update,
                            "diagnostic_message": diag_msg,
                        }

                if site_type == "pwc":
                    r_pwc = await client.post(
                        "https://jobs-it.pwc.com/widgets",
                        json={
                            "lang": "en_it",
                            "deviceType": "desktop",
                            "country": "it",
                            "pageName": "search-results",
                            "ddoKey": "refineSearch",
                            "from": 0,
                            "jobs": True,
                            "counts": True,
                            "size": 5,
                            "pageId": "page11",
                            "siteType": "external",
                            "keywords": "",
                            "global": True,
                            "selected_fields": {},
                        },
                        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                    )
                    if r_pwc.status_code == 200 and "refineSearch" in r_pwc.json():
                        tot = int(r_pwc.json()["refineSearch"].get("totalHits") or 0)
                        dom_fp = f"pwc_phenom_ok_{tot}"
                        status = "HEALTHY" if tot > 0 else "DOM_CHANGED"
                        needs_update = tot == 0
                        jobs_cnt = jobs_cnt or tot
                        diag_msg = (
                            f"✅ DOM/API Phenom PwC integro e offerte in chiaro ({tot} posizioni attive)."
                            if tot > 0
                            else "🚨 Il portale PwC non espone più offerte in `refineSearch`. Aggiornare lo scraper."
                        )
                    else:
                        status = "DOM_CHANGED"
                        needs_update = True
                        diag_msg = f"🚨 Struttura `/widgets` di PwC cambiata (HTTP {r_pwc.status_code}). Aggiornare lo scraper."
                    await record_scraper_health(name, site_type, url, status, jobs_cnt, diag_msg, dom_fp, needs_update)
                    return {
                        "company": name,
                        "site_type": site_type,
                        "portal_url": url,
                        "status": status,
                        "needs_scraper_update": needs_update,
                        "diagnostic_message": diag_msg,
                    }

                # Verifica HTTP + Analisi DOM HTML della pagina
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                        "Connection": "close",
                    },
                )
                http_code = resp.status_code
                html_body = resp.text or ""
                low_body = html_body.lower()

                if http_code >= 400:
                    # Alcuni siti (es. Capgemini o portali protetti da WAF su HTTPX) richiedono Chromium reale: verifichiamo se abbiamo già offerte nel DB prima di dare falso allarme, oppure segnaliamo il blocco
                    if http_code in (403, 503) and any(
                        cf in low_body for cf in ("cloudflare", "cf-browser-verification", "just a moment", "attention required", "access denied")
                    ):
                        status = "HTTP_ERROR"
                        needs_update = True
                        diag_msg = f"🚨 Pagina protetta da Challenge Anti-Bot/Cloudflare (HTTP {http_code}): le offerte non sono in chiaro via HTTP diretto. Verificare sessione browser o aggiornare lo scraper."
                    else:
                        status = "HTTP_ERROR"
                        needs_update = True
                        diag_msg = f"🚨 URL portale non raggiungibile o rimosso (HTTP {http_code}). Aggiornare l'URL o lo scraper."
                else:
                    soup = BeautifulSoup(html_body, "html.parser")
                    # Calcola impronta strutturale DOM (tag e classi dei contenitori principali)
                    structural_tags = [
                        f"{el.name}.{'.'.join(sorted(el.get('class', []))[:2])}"
                        for el in soup.find_all(["main", "section", "table", "form", "article", "ul"], limit=35)
                    ]
                    dom_fp = hashlib.md5("|".join(structural_tags).encode("utf-8")).hexdigest()[:12]

                    sig = expected_signatures.get(site_type)
                    if sig:
                        matched_sels = [sel for sel in sig["selectors"] if soup.select_one(sel) is not None]
                        matched_kws = [kw for kw in sig["keywords"] if kw.lower() in low_body]

                        if not matched_sels and not matched_kws:
                            status = "DOM_CHANGED"
                            needs_update = True
                            diag_msg = (
                                f"🚨 DOM cambiato o offerte non più in chiaro: non sono stati trovati i selettori attesi "
                                f"({sig['label']}). Aggiornare lo scraper per {name}."
                            )
                        else:
                            if scraped_jobs_count == 0:
                                status = "WARNING_EMPTY"
                                needs_update = False
                                diag_msg = (
                                    f"ℹ️ Struttura DOM verificata ({sig['label']}), ma 0 offerte corrispondono ai filtri attuali."
                                )
                            else:
                                status = "HEALTHY"
                                needs_update = False
                                diag_msg = f"✅ DOM integro e selettori verificati ({sig['label']})."
                    else:
                        # Verifica generica che le offerte siano in chiaro o ci sia un contenitore annunci/SPA valido
                        clear_job_nodes = soup.select(
                            "a[href*='job'], a[href*='career'], a[href*='lavor'], a[href*='posizion'], "
                            "a[href*='vacanc'], a[href*='annunc'], a[href*='opening'], tr, article, .job, .career"
                        )
                        has_spa_root = any(
                            spa_k in low_body
                            for spa_k in ("__next_data__", "ng-version", "data-reactroot", "id=\"app\"", "id=\"root\"", "workday", "oraclecloud", "teamtailor", "greenhouse", "lever.co", "factorial", "ashby")
                        )
                        if len(clear_job_nodes) == 0 and not has_spa_root and len(html_body) < 1500:
                            status = "DOM_CHANGED"
                            needs_update = True
                            diag_msg = (
                                f"🚨 Le offerte su {name} non risultano più in chiaro nel DOM (nessun nodo offerta o contenitore ATS rilevato). "
                                f"Bisogna aggiornare lo scraper."
                            )
                        elif scraped_jobs_count == 0:
                            status = "WARNING_EMPTY"
                            needs_update = False
                            diag_msg = f"ℹ️ Pagina raggiungibile ({len(clear_job_nodes)} nodi rilevati nel DOM), 0 offerte estratte nell'ultimo ciclo."
                        else:
                            status = "HEALTHY"
                            needs_update = False
                            diag_msg = f"✅ Pagina raggiungibile e struttura DOM valida ({len(clear_job_nodes)} elementi rilevati)."
        except Exception as exc:
            status = "HTTP_ERROR"
            needs_update = True
            diag_msg = f"🚨 Impossibile verificare il DOM di {name} ({str(exc)[:120]}). Controllare URL o aggiornare lo scraper."

        await record_scraper_health(
            company=name,
            site_type=site_type,
            portal_url=url,
            status=status,
            jobs_count=jobs_cnt,
            diagnostic_message=diag_msg,
            dom_fingerprint=dom_fp,
            needs_scraper_update=needs_update,
        )
        return {
            "company": name,
            "site_type": site_type,
            "portal_url": url,
            "status": status,
            "needs_scraper_update": needs_update,
            "diagnostic_message": diag_msg,
            "dom_fingerprint": dom_fp,
        }

    async def verify_all_portals_dom_health(self, target_company: Optional[str] = None) -> Dict[str, Any]:
        """
        Esegue una scansione diagnostica rapida su tutti i portali (o su una singola azienda)
        per verificare se hanno cambiato DOM, se le offerte non sono più in chiaro o se occorre aggiornare lo scraper.
        """
        from app.database import get_all_scraper_health, get_scraper_health_summary

        sites = get_mapped_sites()
        existing_health = {r["company"]: r for r in await get_all_scraper_health()}

        items_to_check = [
            (comp, s_data)
            for comp, s_data in sites.items()
            if s_data.get("type") != "linkedin_search"
            and (not target_company or comp.lower() == target_company.lower())
        ]

        sem = asyncio.Semaphore(6)

        async def _check_one(c_name: str, s_data: Dict[str, Any]):
            async with sem:
                prev_cnt = existing_health.get(c_name, {}).get("last_jobs_count")
                return await self.verify_page_dom_and_visibility(c_name, s_data, scraped_jobs_count=prev_cnt)

        await asyncio.gather(*[_check_one(c, sd) for c, sd in items_to_check], return_exceptions=True)
        return await get_scraper_health_summary()

    async def _scrape_single_company_raw(self, name: str, site_data: Dict[str, Any]) -> List[Dict[str, Any]]:
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

        # 7. Accenture (Filtri mirati Area of Interest + Location via ElasticSearch findjobs + fallback DOM interattivo)
        if site_type == "accenture":
            return await self._scrape_accenture(name, url, default_location)

        # 8. Capgemini & Capgemini Engineering (Filtri Experience Level, Ruolo/Professional Communities, Località + Paginazione)
        if site_type == "capgemini":
            return await self._scrape_capgemini(name, url, default_location)

        # 9. WSP in Italia (Filtro laterale Paese=Italia & Città + Teamtailor WSP Italy)
        if site_type == "wsp":
            return await self._scrape_wsp(name, url, default_location)

        # 10. Orbyta (In-recruiting con doppia scheda Sede + Data e paginazione AJAX)
        if site_type == "orbyta":
            return await self._scrape_orbyta(name, url, default_location)

        # 11. Altherias (Grid cards WordPress/Elementor)
        if site_type == "altherias":
            return await self._scrape_altherias(name, url, default_location)

        # 12. Mareum (Grid cards con scorrimento pagine numerate /page/1..N/)
        if site_type == "mareum":
            return await self._scrape_mareum(name, url, default_location)

        # 13. Shakers (Grid cards a fine pagina su Teamtailor)
        if site_type == "shakers":
            return await self._scrape_shakers(name, url, default_location)

        # 14. NTT DATA (Form Search con diciture esatte + ridotte, Topic, Location, Job Family + Paginazione)
        if site_type == "nttdata":
            return await self._scrape_nttdata(name, url, default_location)

        # 15. Prima Assicurazioni (Filtro Paese=Italia, Dipartimento, Toggle Remoto + Mostra di più)
        if site_type == "prima":
            return await self._scrape_prima(name, url, default_location)

        # 16. Lutech (5 selettori: Posizione, Seniority/Profilo, Modalità, Sede, Settore + Tabella offerte)
        if site_type == "lutech":
            return await self._scrape_lutech(name, url, default_location)

        # 17. BIP (Menu a tendina Seniority, Location + Search con diciture esatte/ridotte + Pagine numerate)
        if site_type == "bip":
            return await self._scrape_bip(name, url, default_location)

        # 18. PwC (Form di ricerca posizione esatte/ridotte + Category, City, Management Level, Keywords + Pagine numeriche)
        if site_type == "pwc":
            return await self._scrape_pwc(name, url, default_location)

        # 19. EY (2 portali: Studenti/Primo Impiego Yello con scroll dinamico + Esperienza SuccessFactors con More options e paginazione)
        if site_type == "ey":
            return await self._scrape_ey(name, url, site_data, default_location)

        # 20. Leonardo S.p.A. (Workday con ricerca rapida esatte/ridotte, Sede/Paese/Uffici, Categoria d'impiego, Tipo di impiego e pagine numeriche)
        if site_type == "leonardo":
            return await self._scrape_leonardo(name, url, default_location)

        # 21. LinkedIn cluster/ricerche dirette vengono già gestite dal modulo LinkedIn con sessione autenticata
        if site_type == "linkedin_search":
            return []

        # 6. Portali Browser (con supporto a 'Load More', paginazione numerata 1-2-3-4 e arricchimento Ashby)
        results = await self._scrape_browser_portal(name, url, site_type, default_location)

        # Se è un portale Ashby (Satispay o Docebo), arricchisci le sedi e descrizioni anche tramite API AshbyHQ
        ashby_board = site_data.get("ashby_board")
        if ashby_board:
            api_jobs = await self._scrape_ashby_api(name, ashby_board)
            existing_urls = {r["url"].split("?")[0].rstrip("/") for r in results}
            # Aggiorna location/descrizione/published_at dei job già estratti dalla pagina paginata e aggiungi eventuali mancanti
            api_by_url = {j["url"].split("?")[0].rstrip("/"): j for j in api_jobs}
            for r in results:
                norm_u = r["url"].split("?")[0].rstrip("/")
                if norm_u in api_by_url:
                    r["location"] = api_by_url[norm_u]["location"]
                    r["description"] = api_by_url[norm_u]["description"]
                    r["published_at"] = api_by_url[norm_u].get("published_at", "")
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
                            "published_at": str(item.get("posted_date") or ""),
                            "description": f"{item.get('description_short', '')}\n{item.get('basic_qualifications', '')}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"Amazon API error: {e}")
        return results

    async def _scrape_ashby_api(self, name: str, board: str) -> List[Dict[str, Any]]:
        """Recupera metadati completi (sede, modalità, data pubblicazione, descrizione) da AshbyHQ (Satispay, Docebo)."""
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
                            "published_at": str(j.get("publishedAt") or "")[:10],
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

    def _resolve_accenture_filter_params(self) -> Dict[str, Any]:
        """
        Mappa dinamicamente le preferenze del candidato (target_cities, keywords, seniority)
        nei filtri ufficiali Accenture ('location.keyword', 'areaOfInterestTitle.keyword', 'jobKeyword').
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]

        # Mappa città italiane nei valori esatti di Accenture 'location.keyword'
        city_map = {
            "torino": "Torino",
            "turin": "Torino",
            "milano": "Milano",
            "milan": "Milano",
            "roma": "Roma",
            "rome": "Roma",
            "bologna": "Bologna",
            "napoli": "Napoli",
            "naples": "Napoli",
            "padova": "Padova",
            "padua": "Padova",
            "bari": "Bari",
            "modena": "Modena",
            "cagliari": "Cagliari",
            "verona": "Verona",
            "firenze": "Firenze",
            "florence": "Firenze",
            "genova": "Genova",
            "genoa": "Genova",
        }
        accenture_locations: List[str] = []
        for rc in raw_cities:
            for k, acc_city in city_map.items():
                if k in rc and acc_city not in accenture_locations:
                    accenture_locations.append(acc_city)

        if not accenture_locations:
            accenture_locations = ["Torino"]

        # Aree di interesse tecnologiche/AI/Engineering di Accenture
        accenture_areas = [
            "Artificial Intelligence (AI) & Data Science",
            "Software Engineering",
            "Technology",
            "Cloud",
            "Security",
            "Engineering & Manufacturing",
            "Emerging Technology",
            "Programming Languages",
            "Research & Development",
        ]

        # Eventuali aree aggiuntive in base alle keyword del profilo
        kw_joined = " ".join(raw_keywords).lower()
        if any(x in kw_joined for x in ("consul", "analyst", "functional", "pm", "project")):
            accenture_areas.append("Consulting")

        return {
            "locations": accenture_locations,
            "areas": accenture_areas,
            "keywords": expand_search_keywords(raw_keywords, max_terms=14),
        }

    def _parse_accenture_elastic_item(
        self, item: Dict[str, Any], name: str, preferred_cities: List[str]
    ) -> Dict[str, Any] | None:
        """Converte un singolo record JSON di Accenture findjobs nel formato standard del database."""
        title = (item.get("title") or "").strip()
        guid = str(item.get("guid") or item.get("requisitionId") or "").strip()
        if not title or not guid:
            return None

        raw_detail_url = item.get("jobDetailUrl") or ""
        if "{0}" in raw_detail_url:
            job_url = raw_detail_url.replace("{0}", "it-it")
        elif raw_detail_url.startswith("http"):
            job_url = raw_detail_url
        else:
            job_url = f"https://www.accenture.com/it-it/careers/jobdetails?id={guid}&title={urllib.parse.quote_plus(title)}"

        raw_locs = item.get("location") or []
        if isinstance(raw_locs, str):
            raw_locs = [raw_locs]
        # Ordina le sedi rispettando l'ordine esatto di priorità delle target_cities del candidato (es. Torino prima di Bari)
        ordered_locs = sorted(
            [str(loc).strip() for loc in raw_locs if loc],
            key=lambda c: (preferred_cities.index(c) if c in preferred_cities else 999, c),
        )
        loc_str = ", ".join(ordered_locs) if ordered_locs else (item.get("feedCity") or "Italia")
        remote_type = (item.get("remoteType") or "").strip()
        if remote_type and remote_type.lower() not in loc_str.lower():
            loc_str = f"{loc_str} ({remote_type})"

        update_date = str(item.get("updateDate") or "")[:10]
        job_type_desc = (item.get("jobTypeDescription") or "").strip()
        yoe = (item.get("yearsOfExperience") or "").strip()
        career_lvl = (item.get("careerLevel") or "").strip()
        families = ", ".join(item.get("jobFamilyGroup") or [])
        skills = ", ".join(item.get("workdaySkill") or item.get("skill") or [])
        ai_summary = (item.get("azureopenaiSummary") or "").strip()
        overview = (item.get("jobOverview") or item.get("staticExtractiveSummary") or "").strip()
        qual_clean = (item.get("qualificationClean") or "").strip()
        desc_clean = (item.get("jobDescriptionClean") or "").strip()

        meta_header = f"{title} | Sede: {loc_str}"
        if job_type_desc:
            meta_header += f" | Seniority: {job_type_desc}"
        if yoe:
            meta_header += f" ({yoe} anni di esperienza)"
        if career_lvl:
            meta_header += f" | Livello: {career_lvl}"
        if families:
            meta_header += f" | Area: {families}"
        if skills:
            meta_header += f"\nCompetenze: {skills}"

        body_parts = [p for p in (ai_summary, overview, qual_clean, desc_clean) if p]
        full_desc = meta_header + "\n\n" + "\n\n".join(body_parts)

        return {
            "external_id": f"accenture_{guid}",
            "portal": name,
            "company": name,
            "title": title[:140],
            "location": loc_str,
            "url": job_url,
            "published_at": update_date,
            "description": full_desc[:3800],
            "is_easy_apply": False,
        }

    async def _scrape_accenture(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Accenture Italia (https://www.accenture.com/it-it/careers/jobsearch):
        1. Applica i parametri di ricerca nei campi filtro corretti:
           - Location (`location.keyword`, es. Torino e altre target_cities del profilo)
           - Area of Interest (`areaOfInterestTitle.keyword`, es. Artificial Intelligence (AI) & Data Science,
             Software Engineering, Technology, Cloud, Security, Engineering & Manufacturing)
           - Keyword di ricerca (`jobKeyword` dal profilo)
        2. Esegue la paginazione completa su `POST /api/accenture/elastic/findjobs` per estrarre tutti gli annunci pertinenti.
        3. In caso di fallback o blocco API, esegue l'interazione diretta via browser sui checkbox DOM
           (`#location-text-field`, `location.keyword.<City>`, `.apply-location-filters-button`,
           `areaOfInterestTitle.keyword.*`) e naviga tutte le pagine numerate.
        """
        params = self._resolve_accenture_filter_params()
        target_locations = params["locations"]
        target_areas = params["areas"]
        keywords = params["keywords"]

        results: List[Dict[str, Any]] = []
        seen_guids: set[str] = set()
        api_url = "https://www.accenture.com/api/accenture/elastic/findjobs"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": url,
            "Origin": "https://www.accenture.com",
        }

        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                # 1. Ricerca filtrata per Location (es. Torino) + Aree di Interesse Tech/AI/Data/Engineering con paginazione completa
                faceted_filters = [
                    {
                        "fieldName": "location.keyword",
                        "items": target_locations,
                        "multiSelect": len(target_locations) > 1,
                    },
                    {
                        "fieldName": "areaOfInterestTitle.keyword",
                        "items": target_areas,
                        "multiSelect": True,
                    },
                ]
                page_size = 100
                start_index = 0
                while start_index < 300:
                    files = {
                        "startIndex": (None, str(start_index)),
                        "maxResultSize": (None, str(page_size)),
                        "jobKeyword": (None, ""),
                        "jobCountry": (None, "Italia"),
                        "jobLanguage": (None, "it"),
                        "countrySite": (None, "it-it"),
                        "sortBy": (None, "2"),
                        "searchType": (None, "vectorSearch"),
                        "enableQueryBoost": (None, "true"),
                        "minScore": (None, "0.6"),
                        "getFeedbackJudgmentEnabled": (None, "true"),
                        "useCleanEmbedding": (None, "true"),
                        "score": (None, "true"),
                        "totalHits": (None, "true"),
                        "debugQuery": (None, "false"),
                        "jobFilters": (None, json.dumps(faceted_filters)),
                    }
                    resp = await client.post(api_url, files=files, headers=headers)
                    if resp.status_code != 200:
                        break
                    payload = resp.json()
                    items = payload.get("data") or []
                    if not items:
                        break
                    for item in items:
                        parsed = self._parse_accenture_elastic_item(item, name, target_locations)
                        if parsed and parsed["external_id"] not in seen_guids:
                            seen_guids.add(parsed["external_id"])
                            results.append(parsed)
                    total_hits_obj = payload.get("totalHits") or {}
                    total_hits = int(total_hits_obj.get("total", 0)) if isinstance(total_hits_obj, dict) else 0
                    start_index += len(items)
                    if start_index >= total_hits or len(items) < page_size:
                        break

                # 2. Ricerca per Location + Keyword specifiche ed espanse/ridotte del candidato (es. 'AI Engineer', 'AI', 'IA', 'Engineer')
                loc_only_filter = [
                    {
                        "fieldName": "location.keyword",
                        "items": target_locations,
                        "multiSelect": len(target_locations) > 1,
                    }
                ]
                for kw in keywords[:10]:
                    files_kw = {
                        "startIndex": (None, "0"),
                        "maxResultSize": (None, "40"),
                        "jobKeyword": (None, kw),
                        "jobCountry": (None, "Italia"),
                        "jobLanguage": (None, "it"),
                        "countrySite": (None, "it-it"),
                        "sortBy": (None, "1"),
                        "searchType": (None, "vectorSearch"),
                        "enableQueryBoost": (None, "true"),
                        "minScore": (None, "0.65"),
                        "getFeedbackJudgmentEnabled": (None, "true"),
                        "useCleanEmbedding": (None, "true"),
                        "score": (None, "true"),
                        "totalHits": (None, "true"),
                        "debugQuery": (None, "false"),
                        "jobFilters": (None, json.dumps(loc_only_filter)),
                    }
                    resp_kw = await client.post(api_url, files=files_kw, headers=headers)
                    if resp_kw.status_code == 200:
                        for item in resp_kw.json().get("data") or []:
                            parsed = self._parse_accenture_elastic_item(item, name, target_locations)
                            if parsed and parsed["external_id"] not in seen_guids:
                                seen_guids.add(parsed["external_id"])
                                results.append(parsed)

            if results:
                logger.info(
                    f"[{name}] Estratti {len(results)} annunci filtrati (Sedi: {target_locations}, Aree Tech/AI: {len(target_areas)})."
                )
                return results
        except Exception as e:
            logger.warning(f"[{name}] Errore API findjobs Accenture, avvio fallback DOM interattivo: {e}")

        # 3. Fallback Browser DOM interattivo: compila il campo location, spunta i checkbox di città e aree di interesse e pagina i risultati
        return await self._scrape_accenture_browser_filters(name, url, target_locations, target_areas)

    async def _scrape_accenture_browser_filters(
        self, name: str, url: str, target_locations: List[str], target_areas: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Interagisce direttamente con i filtri laterali della pagina Accenture:
        - Compila `#location-text-field`, seleziona `location.keyword.<City>` e clicca `.apply-location-filters-button`
        - Seleziona i checkbox delle Aree di Interesse (`areaOfInterestTitle.keyword.*`)
        - Scorre tutte le pagine numerate raccogliendo le job card (`a[href*='/careers/jobdetails']`).
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        mgr = StealthBrowserManager(profile_name="site_accenture")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.5, 5.0)

                # Applica i filtri Location (inserimento testo + checkbox città + pulsante Filtra)
                await page.evaluate(
                    """(cities) => {
                        const locInput = document.getElementById('location-text-field');
                        for (const city of cities) {
                            if (locInput) {
                                locInput.value = city;
                                locInput.dispatchEvent(new Event('input', { bubbles: true }));
                            }
                            const cb = document.getElementById('location.keyword.' + city);
                            if (cb && !cb.checked) cb.click();
                        }
                        const applyBtn = document.querySelector('.apply-location-filters-button');
                        if (applyBtn) applyBtn.click();
                    }""",
                    target_locations,
                )
                await human_delay(3.0, 4.0)

                # Applica i filtri Area of Interest (checkbox sulla sinistra)
                area_ids = [
                    "areaOfInterestTitle.keyword." + a.replace(" ", "")
                    for a in target_areas
                ]
                await page.evaluate(
                    """(ids) => {
                        for (const id of ids) {
                            const cb = document.getElementById(id);
                            if (cb && !cb.checked) cb.click();
                        }
                    }""",
                    area_ids,
                )
                await human_delay(3.5, 4.5)

                for page_num in range(1, 10):
                    cards = await page.eval_on_selector_all(
                        "a[href*='/careers/jobdetails']",
                        """els => els.map(a => {
                            const href = a.getAttribute('href') || '';
                            const card = a.closest('.rad-filters-vertical__job-card') || a.parentElement?.parentElement;
                            const titleEl = card ? card.querySelector('h3, .rad-filters-vertical__job-card-title') : null;
                            const title = titleEl ? titleEl.innerText.trim() : a.innerText.trim();
                            const text = card ? card.innerText.trim() : title;
                            return { href, title, text };
                        })""",
                    )
                    added = 0
                    for c in cards:
                        href = (c.get("href") or "").strip()
                        title = (c.get("title") or "").strip()
                        if not href or "/careers/jobdetails" not in href or not title:
                            continue
                        full_url = urllib.parse.urljoin(url, href)
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)
                        results.append({
                            "external_id": deterministic_url_id("accenture", full_url),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": ", ".join(target_locations) + ", Italia",
                            "url": full_url,
                            "description": f"{title}\n{c.get('text', '')}"[:2500],
                            "is_easy_apply": False,
                        })
                        added += 1
                    if added == 0:
                        break
                    moved = await self._go_to_next_numbered_page(page, page_num + 1)
                    if not moved:
                        break
        except Exception as e:
            logger.warning(f"[{name}] Errore fallback browser Accenture: {e}")
        return results

    def _resolve_capgemini_filter_params(self) -> Dict[str, Any]:
        """
        Mappa dinamicamente le preferenze del candidato (target_seniority, keywords, target_cities)
        nei checkbox/parametri di filtro di Capgemini (/it-it/join-us/ e cg-jobstream-api):
        - experience_level (Livello di esperienza: Students/Graduates​, Studenti/Laureati, Experienced Professionals, Professionisti esperti, Dirigenti/ Manager, Executives)
        - professional_communities (Ruolo: Data & AI, Software Engineering, Cybersecurity, Cloud Infrastructure Management, Architecture, Products & Systems Engineering, ecc.)
        - location (Località: Torino, Bari, Milano, Roma, Bologna, Modena, Napoli, Padova, Firenze, Genova, ecc.)
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        profile = cfg.get("candidate_profile", {})
        sen_cfg = profile.get("target_seniority", {})
        accepted_tiers = [str(t).strip().lower() for t in sen_cfg.get("accepted_tiers", ["stage", "junior", "mid", "senior"])]
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]

        # 1. Livelli di esperienza (checkbox id="<val>-experience_level")
        exp_levels: List[str] = []
        if "stage" in accepted_tiers or "junior" in accepted_tiers:
            exp_levels.extend(["Students/Graduates\u200b", "Studenti/Laureati"])
        if "mid" in accepted_tiers or "senior" in accepted_tiers:
            exp_levels.extend(["Experienced Professionals", "Professionisti esperti"])
        if "senior" in accepted_tiers:
            exp_levels.extend(["Dirigenti/ Manager", "Executives"])
        if not exp_levels:
            exp_levels = [
                "Students/Graduates\u200b",
                "Studenti/Laureati",
                "Experienced Professionals",
                "Professionisti esperti",
            ]

        # 2. Ruolo / Professional Communities (checkbox id="<val>-professional_communities")
        roles = [
            "Data & AI",
            "Software Engineering",
            "Cybersecurity",
            "Cloud Infrastructure Management",
            "Architecture",
            "Products & Systems Engineering",
            "Manufacturing & Operations Engineering",
            "SaaS Solutions",
            "Quality Engineering & Testing",
            "Business Analysis",
            "Delivery Excellence",
            "Strategy & Transformation",
        ]

        # 3. Località (checkbox id="<val>-location")
        city_map = {
            "torino": ["Torino"],
            "turin": ["Torino"],
            "bari": ["Bari"],
            "milano": ["Milano"],
            "milan": ["Milano"],
            "roma": ["Roma", "Rome (Roma)"],
            "rome": ["Roma", "Rome (Roma)"],
            "bologna": ["Bologna"],
            "modena": ["Modena"],
            "napoli": ["Napoli", "Pomigliano DArco"],
            "naples": ["Napoli", "Pomigliano DArco"],
            "padova": ["Padova"],
            "padua": ["Padova"],
            "venezia": ["Marcon VE", "Marcon"],
            "firenze": ["Firenze"],
            "florence": ["Firenze"],
            "genova": ["Genova"],
            "genoa": ["Genova"],
            "pisa": ["Pisa"],
            "salerno": ["Salerno"],
            "trieste": ["Trieste"],
            "palermo": ["Palermo"],
            "piacenza": ["Piacenza"],
        }
        locations: List[str] = []
        for rc in raw_cities:
            for k, mapped_list in city_map.items():
                if k in rc:
                    for mc in mapped_list:
                        if mc not in locations:
                            locations.append(mc)
        if not locations:
            locations = ["Torino"]

        return {
            "experience_levels": exp_levels,
            "roles": roles,
            "locations": locations,
            "keywords": raw_keywords[:8],
        }

    def _parse_capgemini_item(
        self, item: Dict[str, Any], default_name: str, preferred_cities: List[str]
    ) -> Dict[str, Any] | None:
        """Converte un record JSON di Capgemini (cg-jobstream-api) nel formato standard del database."""
        title = (item.get("title") or "").strip()
        ref = str(item.get("ref") or item.get("id") or "").strip()
        if not title or not ref:
            return None

        source = (item.get("source") or "SAP_BTP").strip().lower()
        apply_url = (item.get("apply_job_url") or "").strip()
        if item.get("ref"):
            job_url = f"https://www.capgemini.com/it-it/jobs/{item['ref']}+{source}"
        elif apply_url:
            job_url = apply_url
        else:
            job_url = f"https://www.capgemini.com/it-it/jobs/{ref}"

        brand = (item.get("brand") or default_name).strip()
        raw_loc_str = (item.get("location") or "Italia").strip()
        loc_parts = [p.strip() for p in raw_loc_str.split(",") if p.strip()]
        ordered_locs = sorted(
            loc_parts,
            key=lambda c: (preferred_cities.index(c) if c in preferred_cities else 999, c),
        )
        loc_str = ", ".join(ordered_locs) if ordered_locs else raw_loc_str

        exp_lvl = (item.get("experience_level") or "").replace("\u200b", "").strip()
        role_comm = (item.get("professional_communities") or item.get("department") or "").strip()
        contract = (item.get("contract_type") or "").strip()
        pub_date = str(item.get("updated_at") or item.get("indexed_at") or "")[:10]
        desc_plain = (item.get("description_stripped") or "").strip()

        meta_header = f"{title} | Brand: {brand} | Sede: {loc_str}"
        if exp_lvl:
            meta_header += f" | Seniority: {exp_lvl}"
        if role_comm:
            meta_header += f" | Ruolo: {role_comm}"
        if contract:
            meta_header += f" | Contratto: {contract}"
        if apply_url:
            meta_header += f"\nCandidatura diretta SAP: {apply_url}"

        full_desc = f"{meta_header}\n\n{desc_plain}" if desc_plain else meta_header

        return {
            "external_id": f"capgemini_{ref}",
            "portal": default_name,
            "company": brand if brand else default_name,
            "title": title[:140],
            "location": loc_str,
            "url": job_url,
            "published_at": pub_date,
            "description": full_desc[:3800],
            "is_easy_apply": False,
        }

    async def _scrape_capgemini(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Capgemini & Capgemini Engineering Italia (https://www.capgemini.com/it-it/join-us/):
        1. Applica i filtri di:
           - Livello di esperienza (`experience_level`, es. Students/Graduates​, Studenti/Laureati, Experienced Professionals)
           - Ruolo (`professional_communities`, es. Data & AI, Software Engineering, Cybersecurity, Cloud, Products & Systems Engineering)
           - Località (`location`, es. Torino, Bari e altre target_cities del profilo)
        2. Esegue la paginazione completa (`page=1, 2, 3...`) sull'endpoint ufficiale `cg-jobstream-api.azurewebsites.net/api/job-search`
           e in fallback interagisce direttamente via browser con i checkbox e la paginazione numerata a fondo pagina.
        """
        params = self._resolve_capgemini_filter_params()
        exp_levels = params["experience_levels"]
        roles = params["roles"]
        locations = params["locations"]
        keywords = params["keywords"]

        results: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        api_url = "https://cg-jobstream-api.azurewebsites.net/api/job-search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.capgemini.com/it-it/join-us/",
            "Origin": "https://www.capgemini.com",
        }

        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                # 1. Ricerca filtrata per Località + Livello di Esperienza + Ruolo (con paginazione completa)
                page_num = 1
                page_size = 50
                while page_num <= 10:
                    query_params = {
                        "country_code": "it-it",
                        "page": page_num,
                        "size": page_size,
                        "location": ",".join(locations),
                        "experience_level": ",".join(exp_levels),
                        "professional_communities": ",".join(roles),
                    }
                    resp = await client.get(api_url, params=query_params, headers=headers)
                    if resp.status_code != 200:
                        break
                    payload = resp.json()
                    items = payload.get("data") or []
                    if not items:
                        break
                    for item in items:
                        parsed = self._parse_capgemini_item(item, name, locations)
                        if parsed and parsed["external_id"] not in seen_ids:
                            seen_ids.add(parsed["external_id"])
                            results.append(parsed)
                    total = int(payload.get("total") or 0)
                    if page_num * page_size >= total or len(items) < page_size:
                        break
                    page_num += 1

                # 2. Ricerca completa per i livelli Junior/Laureati ('Students/Graduates​,Studenti/Laureati') e per le community core Data & AI / Software Engineering in Italia
                extra_passes = [
                    {
                        "country_code": "it-it",
                        "page": 1,
                        "size": 50,
                        "experience_level": "Students/Graduates\u200b,Studenti/Laureati",
                        "professional_communities": ",".join(roles),
                    },
                    {
                        "country_code": "it-it",
                        "page": 1,
                        "size": 60,
                        "experience_level": ",".join(exp_levels),
                        "professional_communities": "Data & AI,Software Engineering,Cybersecurity,Cloud Infrastructure Management,Architecture",
                    },
                ]
                for ep in extra_passes:
                    resp_ep = await client.get(api_url, params=ep, headers=headers)
                    if resp_ep.status_code == 200:
                        for item in resp_ep.json().get("data") or []:
                            parsed = self._parse_capgemini_item(item, name, locations)
                            if parsed and parsed["external_id"] not in seen_ids:
                                seen_ids.add(parsed["external_id"])
                                results.append(parsed)

                # 3. Ricerca supplementare per Località + Keyword specifiche del candidato
                for kw in keywords[:5]:
                    kw_params = {
                        "country_code": "it-it",
                        "page": 1,
                        "size": 30,
                        "location": ",".join(locations),
                        "experience_level": ",".join(exp_levels),
                        "search_term": kw,
                    }
                    resp_kw = await client.get(api_url, params=kw_params, headers=headers)
                    if resp_kw.status_code == 200:
                        for item in resp_kw.json().get("data") or []:
                            parsed = self._parse_capgemini_item(item, name, locations)
                            if parsed and parsed["external_id"] not in seen_ids:
                                seen_ids.add(parsed["external_id"])
                                results.append(parsed)

            if results:
                logger.info(
                    f"[{name}] Estratti {len(results)} annunci filtrati da Capgemini (Sedi: {locations}, Exp: {len(exp_levels)}, Ruoli: {len(roles)})."
                )
                return results
        except Exception as e:
            logger.warning(f"[{name}] Errore API Capgemini, avvio fallback DOM interattivo: {e}")

        # 3. Fallback Browser DOM interattivo con selezione checkbox e paginazione a fondo pagina
        return await self._scrape_capgemini_browser_filters(name, url, exp_levels, roles, locations)

    async def _scrape_capgemini_browser_filters(
        self,
        name: str,
        url: str,
        exp_levels: List[str],
        roles: List[str],
        locations: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Naviga su https://www.capgemini.com/it-it/join-us/ passando i filtri di esperienza, ruolo e località,
        verifica la selezione dei checkbox e scorre tutte le pagine numerate ('1, 2, 3...' / 'Successiva').
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        mgr = StealthBrowserManager(profile_name="site_capgemini")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                q_params = urllib.parse.urlencode(
                    {
                        "page": 1,
                        "size": 11,
                        "experience_level": ",".join(exp_levels),
                        "professional_communities": ",".join(roles),
                        "location": ",".join(locations),
                        "country_code": "it-it",
                    }
                )
                filtered_url = f"https://www.capgemini.com/it-it/join-us/?{q_params}"
                await page.goto(filtered_url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.5, 5.0)

                # Verifica/spunta i checkbox DOM corrispondenti se non già attivi
                checkbox_ids = (
                    [f"{e}-experience_level" for e in exp_levels]
                    + [f"{r}-professional_communities" for r in roles]
                    + [f"{loc}-location" for loc in locations]
                )
                await page.evaluate(
                    """(ids) => {
                        for (const id of ids) {
                            const cb = document.getElementById(id);
                            if (cb && !cb.checked) cb.click();
                        }
                    }""",
                    checkbox_ids,
                )
                await human_delay(2.5, 3.5)

                for page_num in range(1, 12):
                    cards = await page.eval_on_selector_all(
                        "a.JobRow-module__job-card___riAUE, a[href*='/it-it/jobs/']",
                        """els => els.map(a => ({
                            href: a.getAttribute('href') || '',
                            text: (a.innerText || '').trim()
                        }))""",
                    )
                    added = 0
                    for c in cards:
                        href = (c.get("href") or "").strip()
                        raw_text = (c.get("text") or "").strip()
                        if not href or "/jobs/" not in href or not raw_text:
                            continue
                        full_url = urllib.parse.urljoin("https://www.capgemini.com", href)
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)
                        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
                        title = lines[0] if lines else "Posizione Capgemini"
                        loc_line = lines[1] if len(lines) >= 2 else ", ".join(locations)
                        results.append({
                            "external_id": deterministic_url_id("capgemini", full_url),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": loc_line,
                            "url": full_url,
                            "description": raw_text[:2500],
                            "is_easy_apply": False,
                        })
                        added += 1
                    if added == 0:
                        break
                    moved = await self._go_to_next_numbered_page(page, page_num + 1)
                    if not moved:
                        break
        except Exception as e:
            logger.warning(f"[{name}] Errore fallback browser Capgemini: {e}")
        return results

    async def _scrape_wsp(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per WSP in Italia (https://www.wsp.com/it-it/carriere/posizioni-aperte):
        1. Apre il portale WSP e seleziona nel menu laterale sinistro il checkbox Paese 'Italia' (`input[name='country'][value='IT']`)
           e gli eventuali filtri città (`input[name='city']`), raccogliendo tutte le offerte filtrate e paginando eventuali pagine.
        2. Integra/arricchisce dal portale italiano diretto Teamtailor di WSP (`https://wspitaly.teamtailor.com/jobs`)
           escludendo sedi estere (es. Turkey/Ankara).
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        # 1. Interazione browser con il menu laterale di filtro Paese (Italia) e Città su wsp.com
        mgr = StealthBrowserManager(profile_name="site_wsp")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.5, 5.0)

                # Seleziona il checkbox 'Italia' (value='IT') nel menu laterale sinistro
                await page.evaluate(
                    """() => {
                        const cbs = Array.from(document.querySelectorAll('input[type="checkbox"][name="country"]'));
                        for (const cb of cbs) {
                            const lbl = ((cb.closest('label') || document.querySelector(`label[for="${cb.id}"]`) || cb.parentElement)?.innerText || '').toLowerCase();
                            if (cb.value === 'IT' || lbl.includes('italia') || lbl.includes('italy')) {
                                if (!cb.checked) cb.click();
                            }
                        }
                    }"""
                )
                await human_delay(3.0, 4.0)

                cards = await page.eval_on_selector_all(
                    "h4",
                    """els => els.map(h4 => {
                        const a = h4.closest('a') || h4.parentElement?.closest('a') || h4.parentElement?.querySelector('a');
                        return {
                            title: (h4.innerText || '').trim(),
                            parentText: (h4.parentElement?.innerText || '').trim(),
                            href: a ? a.href : ''
                        };
                    })""",
                )
                for c in cards:
                    title = (c.get("title") or "").strip()
                    href = (c.get("href") or "").strip()
                    parent_txt = (c.get("parentText") or "").strip()
                    if not title or not href or not href.startswith("http"):
                        continue
                    norm_u = href.split("?")[0].rstrip("/")
                    if norm_u in seen_urls:
                        continue
                    lines = [ln.strip() for ln in parent_txt.splitlines() if ln.strip() and ln.strip() != title]
                    loc_str = lines[0].replace("|", ", ") if lines else default_location
                    if any(fk in f"{title} {loc_str}".lower() for fk in ("turkey", "ankara", "saudi", "riyadh")):
                        continue
                    seen_urls.add(norm_u)
                    results.append({
                        "external_id": deterministic_url_id("wsp", norm_u),
                        "portal": name,
                        "company": name,
                        "title": title[:140],
                        "location": f"{loc_str}, Italia" if "italia" not in loc_str.lower() else loc_str,
                        "url": href,
                        "description": f"{title} presso {name}\nSede: {loc_str}",
                        "is_easy_apply": False,
                    })
        except Exception as e:
            logger.warning(f"[{name}] Warning browser filtro laterale WSP: {e}")

        # 2. Arricchimento/Verifica dal board WSP Italy Teamtailor
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get("https://wspitaly.teamtailor.com/jobs")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        if "/jobs/" not in href or not re.search(r"/jobs/\d+", href):
                            continue
                        full_url = urllib.parse.urljoin("https://wspitaly.teamtailor.com", href)
                        norm_u = full_url.split("?")[0].rstrip("/")
                        if norm_u in seen_urls:
                            continue
                        raw_txt = a.get_text("\n", strip=True)
                        lines = [ln.strip() for ln in raw_txt.splitlines() if ln.strip()]
                        title = lines[0] if lines else _humanize_slug(full_url)
                        loc_str = " · ".join(lines[1:]) if len(lines) > 1 else default_location
                        if any(fk in f"{title} {loc_str}".lower() for fk in ("turkey", "ankara")):
                            continue
                        seen_urls.add(norm_u)
                        results.append({
                            "external_id": deterministic_url_id("wsp", norm_u),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": f"{loc_str}, Italia" if "italia" not in loc_str.lower() else loc_str,
                            "url": full_url,
                            "description": f"{title} presso {name}\nDettagli: {loc_str}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Errore WSP Teamtailor: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci italiani filtrati.")
        return results

    async def _scrape_orbyta(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per ORBYTA (https://inrecruiting.intervieweb.it/orbyta/it/career):
        Interroga entrambe le schede ('Data' -> order='date' e 'Sede' -> order='location')
        e scorre tutte le pagine AJAX (`act1=vacancyListCareer`, `page=1..N`), raccogliendo titolo,
        sede specifica e funzione di ogni annuncio.
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": url,
        }

        def _parse_orbyta_soup(soup_obj: BeautifulSoup):
            added = 0
            for vh in soup_obj.select("div.vacancy__header"):
                a_el = vh.select_one("a[href*='/jobs/']")
                h3_el = vh.select_one("h3")
                if not a_el or not h3_el:
                    continue
                title = h3_el.get_text(" ", strip=True)
                href = a_el.get("href", "").strip()
                if not title or not href:
                    continue
                t_low = title.lower()
                if "candidatura spontanea" in t_low or "categorie tutelate" in t_low or "l.68/99" in t_low or "l. 68/99" in t_low:
                    continue
                norm_u = href.split("?")[0].rstrip("/")
                if norm_u in seen_urls:
                    continue
                seen_urls.add(norm_u)

                sede_el = vh.select_one("span[title='Sede']")
                func_el = vh.select_one("span[title='Professione/Funzione']")
                sede_txt = re.sub(r"^\s*Sede\s*", "", sede_el.get_text(" ", strip=True), flags=re.I).strip() if sede_el else default_location
                func_txt = re.sub(r"^\s*Professione/Funzione\s*", "", func_el.get_text(" ", strip=True), flags=re.I).strip() if func_el else ""

                # Se nel titolo è specificata una città più precisa (es. '- Milano', '- Lodi', '- Savigliano'), usala
                m_city = re.search(r"-\s*(Torino|Milano|Lodi|Savigliano|Roma|Bologna|Padova|Napoli|Bari)\b", title, re.I)
                if m_city:
                    sede_txt = f"{m_city.group(1).title()}, Italia"

                results.append({
                    "external_id": deterministic_url_id("orbyta", norm_u),
                    "portal": name,
                    "company": name,
                    "title": title[:140],
                    "location": sede_txt or default_location,
                    "url": href,
                    "description": f"{title} presso {name} | Sede: {sede_txt} | Area: {func_txt}".strip(" |"),
                    "is_easy_apply": False,
                })
                added += 1
            return added

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    _parse_orbyta_soup(soup)

                    ajax_input = soup.select_one("#url-for-announces")
                    sec_match = re.search(r"'section':\s*'([^']+)'", r.text)
                    if ajax_input and ajax_input.get("value") and sec_match:
                        ajax_url = ajax_input["value"]
                        section_token = sec_match.group(1)
                        # Interroga sia la scheda 'Data' ('date') sia la scheda 'Sede' ('location') su tutte le pagine
                        for tab_order in ("date", "location"):
                            for page_num in range(1, 8):
                                resp_ajax = await client.post(
                                    ajax_url,
                                    data={
                                        "act1": "vacancyListCareer",
                                        "section": section_token,
                                        "order": tab_order,
                                        "page": page_num,
                                    },
                                )
                                if resp_ajax.status_code != 200:
                                    break
                                payload = json.loads(resp_ajax.text)
                                html_frag = payload.get("data") or ""
                                if not html_frag:
                                    break
                                frag_soup = BeautifulSoup(html_frag, "html.parser")
                                if not frag_soup.select("div.vacancy__header"):
                                    break
                                _parse_orbyta_soup(frag_soup)
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Orbyta: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci dalle schede Sede e Data.")
        return results

    async def _scrape_altherias(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Altherias (https://altherias.com/it/carriere/):
        Estrae tutte le card della griglia Elementor (`article.elementor-post`, `.elementor-post`),
        recuperando titolo (`h3.elementor-post__title`), sede/modalità (`Torino, Italia · Ibrido`) e link diretto.
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for card in soup.select("article.elementor-post, .elementor-post, .e-loop-item"):
                        h_el = card.select_one("h2, h3, h4")
                        a_el = card.select_one("a[href]")
                        if not h_el or not a_el:
                            continue
                        title = h_el.get_text(" ", strip=True)
                        href = urllib.parse.urljoin(url, a_el.get("href", "").strip())
                        norm_u = href.split("?")[0].rstrip("/")
                        if not title or norm_u in seen_urls:
                            continue
                        card_lines = [
                            ln.strip()
                            for ln in card.get_text("\n", strip=True).splitlines()
                            if ln.strip() and ln.strip() != title and "vedi l'offerta" not in ln.lower()
                        ]
                        meta_line = card_lines[0] if card_lines else default_location
                        # Ignora annunci esclusivamente in Polonia/estero
                        if any(fk in meta_line.lower() for fk in ("polonia", "varsavia", "poland", "warsaw")):
                            continue
                        seen_urls.add(norm_u)
                        results.append({
                            "external_id": deterministic_url_id("altherias", norm_u),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": meta_line,
                            "url": href,
                            "description": f"{title} presso {name}\nSede e modalità: {meta_line}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Altherias: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci dalla griglia.")
        return results

    async def _scrape_mareum(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Mareum (https://mareum.it/offerte-di-lavoro/):
        Estrae le card della griglia scorrendo tutte le pagine numerate (`/page/1/`, `/page/2/`, `/page/3/`, `/page/4/`...)
        e raccogliendo titolo (`a.job-title`), provincia (`Provincia: Torino`, `Bari`, `Milano`, ecc.) e link offerta.
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "close",
        }
        limits = httpx.Limits(max_keepalive_connections=0, max_connections=2)
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, verify=False, headers=headers, limits=limits
            ) as client:
                for page_num in range(1, 15):
                    page_url = url if page_num == 1 else f"{url.rstrip('/')}/page/{page_num}/"
                    try:
                        resp = await client.get(page_url)
                    except Exception as page_err:
                        logger.debug(f"[{name}] Pagina {page_num} interrotta: {page_err}")
                        break
                    if resp.status_code != 200:
                        break
                    soup = BeautifulSoup(resp.text, "html.parser")
                    job_links = soup.select("a.job-title, a[href*='dettagli-offerta']")
                    if not job_links:
                        break
                    added = 0
                    for a_el in job_links:
                        title = a_el.get_text(" ", strip=True)
                        href = urllib.parse.urljoin("https://mareum.it", a_el.get("href", "").strip())
                        if not title or not href or href in seen_urls:
                            continue
                        seen_urls.add(href)
                        parent_txt = a_el.parent.get_text(" ", strip=True) if a_el.parent else ""
                        m_prov = re.search(r"Provincia:\s*([A-Za-zÀ-ÿ\s\-]+)", parent_txt, re.I)
                        prov = m_prov.group(1).strip() if m_prov else default_location
                        loc_str = f"{prov}, Italia" if "italia" not in prov.lower() else prov
                        results.append({
                            "external_id": deterministic_url_id("mareum", href),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": loc_str,
                            "url": href,
                            "description": f"{title} presso {name} - Sede: {loc_str}",
                            "is_easy_apply": False,
                        })
                        added += 1
                    if added == 0 or not soup.select_one("a.next.page-numbers"):
                        break
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Mareum: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci su tutte le pagine numerate.")
        return results

    async def _scrape_shakers(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Shakers (https://careers.shakersworks.com/jobs):
        Estrae le offerte di lavoro disposte a griglia a fine pagina (`a[href*='/jobs/']` con ID numerico Teamtailor).
        """
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a_el in soup.find_all("a", href=True):
                        href = a_el["href"].strip()
                        if "/jobs/" not in href or not re.search(r"/jobs/\d+", href):
                            continue
                        full_url = urllib.parse.urljoin("https://careers.shakersworks.com", href)
                        norm_u = full_url.split("?")[0].rstrip("/")
                        if norm_u in seen_urls:
                            continue
                        seen_urls.add(norm_u)
                        lines = [ln.strip() for ln in a_el.get_text("\n", strip=True).splitlines() if ln.strip()]
                        title = lines[0] if lines else _humanize_slug(full_url)
                        meta_line = " · ".join(lines[1:]) if len(lines) > 1 else default_location
                        results.append({
                            "external_id": deterministic_url_id("shakers", norm_u),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": meta_line,
                            "url": full_url,
                            "description": f"{title} presso {name}\nTeam & Sede: {meta_line}",
                            "is_easy_apply": False,
                        })
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Shakers: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci dalla griglia Shakers.")
        return results

    async def _scrape_nttdata(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per NTT DATA Italia (https://it.nttdata.com/career/posizioni-aperte):
        Utilizza i campi del form di ricerca:
        - `careers-title-keyword` (`data-rk="keyword"`): diciture esatte E diciture ridotte (es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Developer')
        - `careers-department` (`data-rk="department"` / Topic): 'AI and Machine Learning', 'Data and Analytics', 'Software development and QA', 'Engineering', 'Cloud', 'Cybersecurity', 'Technology Services'
        - `careers-location` (`data-rk="location"`): città target del candidato (Torino, Milano, Roma, Bologna, Bari, Napoli, Genova, Multilocations)
        - `careers-experience` (`data-rk="experience"` / Job Family): 'Newly Graduate', 'Expert' in base alla seniority attiva
        Scorre inoltre il selettore di pagine in basso (`/career/posizioni-aperte/page/1..N` e `ul.pagination a.page-link`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=14)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        # Mappa Location di NTT DATA
        ntt_valid_cities = [
            "Torino", "Milano", "Roma", "Bologna", "Bari",
            "Genova", "Napoli", "Pisa", "Cosenza", "Salerno", "Treviso",
        ]
        target_locations = [c for c in ntt_valid_cities if any(c.lower() in rc for rc in raw_cities)]
        if not target_locations:
            target_locations = ["Torino", "Milano", "Roma"]
        if "Multilocations" not in target_locations:
            target_locations.append("Multilocations")

        # Mappa Topic (careers-department) di NTT DATA
        target_topics = [
            "AI and Machine Learning",
            "Data and Analytics",
            "Software development and QA",
            "Engineering",
            "Cloud",
            "Technology Services",
            "Cybersecurity",
            "Platform Based Solution",
        ]

        # Mappa Job Family (careers-experience) di NTT DATA
        target_experiences = []
        if "stage" in active_tiers or "junior" in active_tiers:
            target_experiences.append("Newly Graduate")
        if "mid" in active_tiers or "senior" in active_tiers:
            target_experiences.append("Expert")
        if not target_experiences:
            target_experiences = ["Newly Graduate", "Expert"]

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        def _parse_ntt_rows(soup_obj: BeautifulSoup, filter_note: str = "") -> int:
            added_cnt = 0
            for a_el in soup_obj.select("a.search-result.job-detail-link, a[href^='/jobs/']"):
                href = (a_el.get("href") or "").strip()
                if not href or "/jobs/" not in href:
                    continue
                full_url = urllib.parse.urljoin("https://it.nttdata.com", href).split("?")[0]
                if full_url in seen_urls:
                    continue
                cols = [c.get_text(" ", strip=True) for c in a_el.select("div[class*='col-']")]
                title = cols[0] if cols else a_el.get_text(" ", strip=True)
                loc_txt = cols[1] if len(cols) > 1 and cols[1] else default_location
                topic_txt = cols[2] if len(cols) > 2 else ""
                if not title:
                    continue

                # Rispetta il filtro seniority del candidato
                t_low = title.lower()
                if "senior" not in active_tiers and any(
                    sr in t_low for sr in ("senior", "manager", "director", "lead", "architect", "principal")
                ):
                    continue
                if "stage" not in active_tiers and "junior" not in active_tiers and any(
                    jr in t_low for jr in ("new graduate", "stage", "intern", "tirocin")
                ):
                    continue

                seen_urls.add(full_url)
                loc_clean = f"{loc_txt}, Italia" if "italia" not in loc_txt.lower() else loc_txt
                desc_parts = [f"{title} presso {name}", f"Sede: {loc_clean}"]
                if topic_txt:
                    desc_parts.append(f"Topic / Area: {topic_txt}")
                if filter_note:
                    desc_parts.append(filter_note)
                results.append({
                    "external_id": deterministic_url_id("nttdata", full_url),
                    "portal": name,
                    "company": name,
                    "title": title[:140],
                    "location": loc_clean,
                    "url": full_url,
                    "description": "\n".join(desc_parts),
                    "is_easy_apply": False,
                })
                added_cnt += 1
            return added_cnt

        # 1. Interazione col form su Browser (Search con diciture esatte + ridotte, Topic, Location, Job Family + click 'Search jobs' + paginazione)
        try:
            mgr = StealthBrowserManager(profile_name="site_nttdata")
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await human_delay(2.5, 3.5)

                # Prova combinazioni nel form interattivo (es. Topic + Location + Job Family + Keyword esatta/ridotta)
                primary_loc = target_locations[0] if target_locations[0] != "Multilocations" else ""
                primary_exp = target_experiences[0] if len(target_experiences) == 1 else ""
                search_combos = [
                    ("", "AI and Machine Learning", primary_loc, primary_exp),
                    ("", "Data and Analytics", primary_loc, primary_exp),
                    ("", "Software development and QA", primary_loc, primary_exp),
                    ("", "Engineering", primary_loc, primary_exp),
                ]
                for kw_term in expanded_kws[:6]:
                    search_combos.append((kw_term, "", primary_loc, primary_exp))

                for kw_val, dept_val, loc_val, exp_val in search_combos[:6]:
                    try:
                        await page.evaluate(
                            """(cfg) => {
                                const kwIn = document.getElementById('careers-title-keyword');
                                if (kwIn) {
                                    kwIn.value = cfg.kw;
                                    kwIn.dispatchEvent(new Event('input', { bubbles: true }));
                                    kwIn.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                const deptSel = document.getElementById('careers-department');
                                if (deptSel) {
                                    deptSel.value = cfg.dept;
                                    deptSel.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                const locSel = document.getElementById('careers-location');
                                if (locSel) {
                                    locSel.value = cfg.loc;
                                    locSel.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                const expSel = document.getElementById('careers-experience');
                                if (expSel) {
                                    expSel.value = cfg.exp;
                                    expSel.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                const btn = document.querySelector('button.is-search, form.js-careers-search-form button[type="submit"]');
                                if (btn) btn.click();
                            }""",
                            {"kw": kw_val, "dept": dept_val, "loc": loc_val, "exp": exp_val},
                        )
                        await human_delay(2.0, 3.0)
                        html_content = await page.content()
                        _parse_ntt_rows(
                            BeautifulSoup(html_content, "html.parser"),
                            filter_note=f"Filtro Form: kw={kw_val or 'Tutti'}, topic={dept_val or 'Tutti'}, loc={loc_val or 'Italia'}, jobFamily={exp_val or 'Tutte'}",
                        )
                        # Scorri eventuale paginazione in basso (ul.pagination a.page-link)
                        for p_num in range(2, 5):
                            moved = await self._go_to_next_numbered_page(page, p_num)
                            if not moved:
                                break
                            html_p = await page.content()
                            if _parse_ntt_rows(BeautifulSoup(html_p, "html.parser")) == 0:
                                break
                    except Exception as form_err:
                        logger.debug(f"[{name}] Warning form combo: {form_err}")
        except Exception as e:
            logger.debug(f"[{name}] Browser form NTT DATA warning: {e}")

        # 2. Completamento rapido via HTTPX su tutti i Topic tecnologici, Keyword ridotte/esatte e tutte le pagine numerate (/page/1..6)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"}
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
                # 2a. Query per Topic (careers-department / department) + Job Family + Location
                for topic in target_topics:
                    q_url = f"{url}?department={urllib.parse.quote_plus(topic)}"
                    resp = await client.get(q_url)
                    if resp.status_code == 200:
                        _parse_ntt_rows(BeautifulSoup(resp.text, "html.parser"), f"Topic: {topic}")

                # 2b. Query per Keyword esatte + ridotte (es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Python')
                for kw in expanded_kws[:8]:
                    q_url = f"{url}?keyword={urllib.parse.quote_plus(kw)}&careers-title-keyword={urllib.parse.quote_plus(kw)}"
                    resp = await client.get(q_url)
                    if resp.status_code == 200:
                        _parse_ntt_rows(BeautifulSoup(resp.text, "html.parser"), f"Keyword: {kw}")

                # 2c. Scorrimento completo pagine numerate /career/posizioni-aperte/page/1..6
                for page_num in range(1, 7):
                    p_url = url if page_num == 1 else f"{url.rstrip('/')}/page/{page_num}"
                    resp = await client.get(p_url)
                    if resp.status_code != 200:
                        break
                    soup_p = BeautifulSoup(resp.text, "html.parser")
                    if not soup_p.select("a.search-result.job-detail-link"):
                        break
                    _parse_ntt_rows(soup_p, f"Pagina {page_num}")
        except Exception as e:
            logger.warning(f"[{name}] Errore HTTPX NTT DATA: {e}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci (Keywords esatte/ridotte: {expanded_kws[:6]}, Topics: {len(target_topics)}, Sedi: {target_locations[:4]})."
        )
        return results

    async def _scrape_prima(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Prima Assicurazioni (https://www.helloprima.com/it/carriere/offerte-lavoro):
        1. Seleziona 'Italia' dal primo menu a tendina della località geografica (impostato di default su 'Tutti i paesi').
        2. Seleziona l'area di interesse dal secondo menu a tendina ('Dipartimento': Engineering, Product, Pricing & Underwriting, ecc.).
        3. Attiva/verifica lo slide button per il lavoro remoto se il candidato accetta 'remoto'.
        4. Clicca ripetutamente il pulsante 'Mostra di più' in basso per caricare dinamicamente tutte le offerte.
        5. Arricchisce ogni offerta con data di pubblicazione (`created_at`), città, paese e flag `telecommuting` dal payload Next.js.
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=16)
        work_modes = [str(m).lower() for m in (prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])]
        remote_accepted = "remoto" in work_modes

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        # Dipartimenti pertinenti su Prima Assicurazioni
        target_depts = ["Engineering", "Pricing & Underwriting", "Product"]
        kw_joined = " ".join(expanded_kws).lower()
        if any(k in kw_joined for k in ("finance", "analytic", "business", "operations")):
            target_depts.append("Finance & Operations")

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        next_jobs_by_slug: Dict[str, Dict[str, Any]] = {}

        mgr = StealthBrowserManager(profile_name="site_prima")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.0, 4.0)

                # Accetta eventuale Cookiebot banner
                await page.evaluate("""() => {
                    const btn = document.getElementById('CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll')
                             || document.getElementById('CybotCookiebotDialogBodyButtonAccept');
                    if (btn) btn.click();
                }""")
                await human_delay(1.0, 1.5)

                # Estrai il dizionario completo da __NEXT_DATA__ per avere date di pubblicazione (created_at) e location esatte
                raw_next = await page.evaluate("() => document.getElementById('__NEXT_DATA__')?.innerText || '{}'")
                try:
                    nd = json.loads(raw_next)
                    modules = nd.get("props", {}).get("pageProps", {}).get("page", {}).get("modules") or []
                    for mod in modules:
                        for j_item in (mod.get("jobs") or []):
                            slug = j_item.get("slug") or j_item.get("shortcode")
                            if slug:
                                next_jobs_by_slug[slug] = j_item
                except Exception:
                    pass

                # 1. Menu a tendina paese: clicca 'Tutti i paesi' e seleziona 'Italia'
                await page.evaluate("""() => {
                    const countryBtn = Array.from(document.querySelectorAll('button')).find(
                        b => (b.innerText || '').includes('Tutti i paesi')
                    );
                    if (countryBtn) countryBtn.click();
                }""")
                await human_delay(0.8, 1.2)
                await page.evaluate("""() => {
                    const itOpt = Array.from(document.querySelectorAll('li, button, [role="option"]')).find(
                        el => (el.innerText || '').trim() === 'Italia'
                    );
                    if (itOpt) itOpt.click();
                }""")
                await human_delay(1.5, 2.2)

                # 2. Per ogni Dipartimento target (es. Engineering, Pricing & Underwriting, Product) + vista globale Italia:
                #    seleziona il dipartimento, gestisci lo slide button Remoto e clicca 'Mostra di più' fino a fine lista
                dept_passes = target_depts + ["Dipartimento"]  # 'Dipartimento' = reset a tutti i dipartimenti in Italia
                for dept_name in dept_passes:
                    try:
                        await page.evaluate(
                            """(dName) => {
                                const inp = document.getElementById('department');
                                if (inp) inp.click();
                                const item = document.getElementById('department-dropdown-' + dName)
                                          || document.getElementById(dName === 'Dipartimento' ? 'department-dropdown-null' : '');
                                if (item) item.click();
                            }""",
                            dept_name,
                        )
                        await human_delay(1.2, 1.8)

                        # Clicca 'Mostra di più' in basso finché visibile
                        for _ in range(12):
                            clicked_more = await page.evaluate("""() => {
                                const links = Array.from(document.querySelectorAll('a, button'));
                                const more = links.find(el => {
                                    const t = (el.innerText || '').toLowerCase().trim();
                                    return (t === 'mostra di più' || t === 'show more' || t === 'carica altri') && el.offsetParent !== null;
                                });
                                if (more) {
                                    more.scrollIntoView({ behavior: 'instant', block: 'center' });
                                    more.click();
                                    return true;
                                }
                                return false;
                            }""")
                            if not clicked_more:
                                break
                            await human_delay(1.0, 1.6)

                        # Raccogli le card visibili
                        dom_cards = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a[href*="/it/carriere/offerte-lavoro/"]')).map(a => {
                                const card = a.closest('li') || a.parentElement?.parentElement;
                                return {
                                    title: (a.innerText || '').replace(/\\s+new$/i, '').trim(),
                                    href: a.href,
                                    cardTxt: (card?.innerText || '').replace(/\\s+/g, ' ').trim()
                                };
                            });
                        }""")

                        for c in dom_cards:
                            href = (c.get("href") or "").strip()
                            title = (c.get("title") or "").strip()
                            if not href or not title or href in seen_urls:
                                continue
                            slug = href.rstrip("/").split("/")[-1]
                            meta = next_jobs_by_slug.get(slug, {})
                            dept = meta.get("department") or (dept_name if dept_name != "Dipartimento" else "")
                            loc_obj = meta.get("location") or {}
                            country_code = (loc_obj.get("country_code") or "IT").upper()
                            city_name = loc_obj.get("city") or "Milano / Roma"
                            is_remote = bool(loc_obj.get("telecommuting")) or "remoto" in (c.get("cardTxt") or "").lower()

                            # Includi solo posizioni con sede/contratto in Italia (country_code == 'IT')
                            if country_code != "IT":
                                continue

                            # Rispetta la seniority del candidato
                            t_low = title.lower()
                            if "senior" not in active_tiers and any(
                                sr in t_low for sr in ("senior", "staff", "principal", "manager", "director", "head of", "lead", "vp")
                            ):
                                continue

                            seen_urls.add(href)
                            loc_str = f"{city_name}, Italia" + (" / Remoto" if is_remote else "")
                            pub_date = str(meta.get("created_at") or "")[:10]
                            results.append({
                                "external_id": deterministic_url_id("prima", href),
                                "portal": name,
                                "company": name,
                                "title": title[:140],
                                "location": loc_str,
                                "url": href,
                                "published_at": pub_date,
                                "description": f"{title} presso {name}\nDipartimento: {dept}\nSede: {loc_str}\nDettagli card: {c.get('cardTxt', '')}",
                                "is_easy_apply": False,
                            })
                    except Exception as d_err:
                        logger.debug(f"[{name}] Warning filtro dipartimento {dept_name}: {d_err}")

                # 3. Attiva anche lo slide button 'Lavoro da remoto' (input[type="checkbox"].sr-only.peer) se il candidato accetta il remoto
                if remote_accepted:
                    try:
                        await page.evaluate("""() => {
                            const remInput = document.querySelector('input[type="checkbox"].sr-only.peer');
                            if (remInput && !remInput.checked) {
                                const lbl = remInput.closest('label') || remInput;
                                lbl.click();
                            }
                        }""")
                        await human_delay(1.2, 1.8)
                        for _ in range(10):
                            clicked_more = await page.evaluate("""() => {
                                const more = Array.from(document.querySelectorAll('a, button')).find(
                                    el => (el.innerText || '').toLowerCase().trim() === 'mostra di più' && el.offsetParent !== null
                                );
                                if (more) { more.click(); return true; }
                                return false;
                            }""")
                            if not clicked_more:
                                break
                            await human_delay(0.8, 1.2)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Prima Assicurazioni: {e}")

        logger.info(f"[{name}] Estratti {len(results)} annunci filtrati (Paese=Italia, Dipartimenti={target_depts}, Remoto={remote_accepted}).")
        return results

    async def _scrape_lutech(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Lutech (https://lutech.group/it/careers/search):
        Compila e filtra tramite i 5 selettori della pagina in base al profilo del candidato:
        1. `#tcl-careers-filters_select--posizione-offerta` (Posizioni: filtrate con diciture esatte E ridotte es. 'AI', 'IA', 'Engineer', 'Data', 'Developer')
        2. `#tcl-careers-filters_select--profilo` (Seniority: 'Neolaureat*/student*' e/o 'Professionista')
        3. `#tcl-careers-filters_select--tipo-di-contratto` (Modalità: 'Ibrido' e/o 'In sede')
        4. `#tcl-careers-filters_select--sede` (Sede: Torino, Milano, Roma, Bologna, Bari, Napoli, Bergamo, Parma)
        5. `#tcl-careers-filters_select--area-di-interesse` (Settore: 'AI, Data & Hyperautomation', 'Cloud & Infrastructure Services', ecc.)
        Estrae poi tutte le offerte tabellari in basso (`tr` con `a[href*='details?slug=']`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=18)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]
        work_modes = [str(m).lower() for m in (prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        # 2. Seniority (profilo)
        allowed_profiles = []
        if "stage" in active_tiers or "junior" in active_tiers:
            allowed_profiles.append("Neolaureat*/student*")
        if "mid" in active_tiers or "senior" in active_tiers:
            allowed_profiles.append("Professionista")
        if not allowed_profiles:
            allowed_profiles = ["Neolaureat*/student*", "Professionista"]

        # 3. Modalità lavoro (tipo-di-contratto)
        allowed_modes = []
        if "ibrido" in work_modes or "remoto" in work_modes:
            allowed_modes.append("Ibrido")
        if "in_sede" in work_modes:
            allowed_modes.append("In sede")
        if not allowed_modes:
            allowed_modes = ["Ibrido", "In sede"]

        # 4. Sedi italiane Lutech
        lutech_cities = ["Torino", "Milano", "Roma", "Bologna", "Bari", "Napoli", "Bergamo", "Parma"]
        target_sedi = [c for c in lutech_cities if any(c.lower() in rc for rc in raw_cities)]
        if not target_sedi or "italia" in " ".join(raw_cities):
            target_sedi = lutech_cities

        # 5. Settori / Aree di interesse Lutech
        target_areas = [
            "AI, Data & Hyperautomation",
            "Cloud & Infrastructure Services",
            "Digital Experience",
            "Digital Manufacturing & IOT Solutions",
            "R&D & Innovative Industry Solutions",
            "Digital Telco & Media",
            "Digital Energy & Utilities",
            "Digital Public Service & Healthcare",
        ]

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        mgr = StealthBrowserManager(profile_name="site_lutech")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.0, 4.0)

                # Chiudi eventuale banner CookieScript
                await page.evaluate("""() => {
                    const btn = document.getElementById('cookiescript_accept');
                    if (btn) btn.click();
                }""")
                await human_delay(0.8, 1.2)

                # Helper per estrarre le righe attualmente visibili nella tabella Lutech
                async def _extract_lutech_table_rows() -> int:
                    rows_data = await page.evaluate("""() => {
                        const trs = Array.from(document.querySelectorAll('table tr, .tcl-careers-table tr, tr.odd, tr.even'));
                        return trs.map(tr => {
                            const tds = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                            const a = tr.querySelector('a[href*="details?slug="], a[href*="careers"]');
                            return {
                                tds,
                                href: a ? a.href : ''
                            };
                        }).filter(r => r.href && r.tds.length >= 4);
                    }""")
                    added_cnt = 0
                    for r in rows_data:
                        tds = r["tds"]
                        href = r["href"].strip()
                        if not href or href in seen_urls:
                            continue
                        title = tds[0]
                        profilo = tds[1] if len(tds) > 1 else ""
                        contratto = tds[2] if len(tds) > 2 else ""
                        sede = tds[3] if len(tds) > 3 else default_location
                        area = tds[4] if len(tds) > 4 else ""

                        # Escludi sedi estere (es. Madrid, Tirana)
                        if sede.lower() in ("madrid", "tirana", "spagna", "albania"):
                            continue

                        # Filtra per seniority del candidato
                        t_low = title.lower()
                        if "senior" not in active_tiers and any(
                            sr in t_low for sr in ("senior", "lead", "manager", "director", "principal", "executive")
                        ):
                            continue
                        if "stage" not in active_tiers and "junior" not in active_tiers and "neolaureat" in profilo.lower():
                            continue

                        seen_urls.add(href)
                        loc_str = f"{sede}, Italia ({contratto})" if contratto else f"{sede}, Italia"
                        results.append({
                            "external_id": deterministic_url_id("lutech", href),
                            "portal": name,
                            "company": name,
                            "title": title[:140],
                            "location": loc_str,
                            "url": href,
                            "description": (
                                f"{title} presso {name}\n"
                                f"Profilo / Seniority: {profilo}\n"
                                f"Modalità: {contratto}\n"
                                f"Sede: {sede}\n"
                                f"Settore / Area di interesse: {area}"
                            ),
                            "is_easy_apply": False,
                        })
                        added_cnt += 1
                    return added_cnt

                # I 5 selettori di Lutech sono single-select (<select>): applichiamo i filtri in sequenza
                # (1: Posizione con keyword esatte/ridotte, 2: Seniority/Profilo, 3: Modalità Ibrido/In sede, 4: Sede, 5: Settore)
                # raccogliendo le offerte ad ogni filtro e infine resettando per non perdere posizioni trasversali.
                filter_passes = [
                    ("tcl-careers-filters_select--sede", target_sedi[0] if target_sedi else "Milano"),
                    ("tcl-careers-filters_select--area-di-interesse", "AI, Data & Hyperautomation"),
                    ("tcl-careers-filters_select--area-di-interesse", "Cloud & Infrastructure Services"),
                    ("tcl-careers-filters_select--profilo", allowed_profiles[0]),
                    ("tcl-careers-filters_select--tipo-di-contratto", allowed_modes[0]),
                ]
                for sel_id, val_to_pick in filter_passes:
                    await page.evaluate(
                        """(arg) => {
                            // Reset tutti i 5 selettori
                            const ids = [
                                'tcl-careers-filters_select--posizione-offerta',
                                'tcl-careers-filters_select--profilo',
                                'tcl-careers-filters_select--tipo-di-contratto',
                                'tcl-careers-filters_select--sede',
                                'tcl-careers-filters_select--area-di-interesse'
                            ];
                            for (const id of ids) {
                                const s = document.getElementById(id);
                                if (s) { s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); }
                            }
                            const targetSel = document.getElementById(arg.selId);
                            if (targetSel) {
                                const opt = Array.from(targetSel.options).find(
                                    o => (o.text || o.value || '').toLowerCase().includes(arg.val.toLowerCase())
                                );
                                if (opt) {
                                    targetSel.value = opt.value;
                                    targetSel.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            }
                        }""",
                        {"selId": sel_id, "val": val_to_pick},
                    )
                    await human_delay(0.8, 1.2)
                    await _extract_lutech_table_rows()

                # Filtra anche tramite il 1° selettore (Posizione) usando le diciture esatte E ridotte (es. 'AI', 'IA', 'Engineer', 'Data', 'Developer')
                await page.evaluate(
                    """(kws) => {
                        const ids = [
                            'tcl-careers-filters_select--posizione-offerta',
                            'tcl-careers-filters_select--profilo',
                            'tcl-careers-filters_select--tipo-di-contratto',
                            'tcl-careers-filters_select--sede',
                            'tcl-careers-filters_select--area-di-interesse'
                        ];
                        for (const id of ids) {
                            const s = document.getElementById(id);
                            if (s) { s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); }
                        }
                    }""",
                    expanded_kws,
                )
                await human_delay(0.8, 1.2)
                await _extract_lutech_table_rows()
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping Lutech: {e}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci tabellari (Keywords esatte/ridotte: {expanded_kws[:6]}, Profili: {allowed_profiles}, Modalità: {allowed_modes}, Sedi: {target_sedi[:4]})."
        )
        return results

    async def _scrape_bip(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per BIP (https://www.bip-group.it/job-openings):
        1. Interagisce con la barra dei menu a tendina in alto:
           - `Seniority` ('Junior' e/o 'Experienced' in base alla seniority del candidato)
           - `Location` (città target del candidato es. 'Milano', 'Roma', 'Bologna', 'Padova', 'Bari', 'Napoli', 'Torino', 'Italy')
           - `Search...` (utilizzando sia le diciture esatte sia quelle ridotte es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Developer')
        2. Scorre le pagine numerate in basso (`1`, `2`, `3`, `4`...) ed estrae per ogni offerta:
           titolo, contratto, descrizione completa dal `<dialog class="modal">` (inclusa eventuale RAL indicata nel testo)
           e link diretto alla posizione (`oraclecloud.com/hcmUI/CandidateExperience/...`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=14)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        # Seniority BIP ('Junior', 'Experienced')
        bip_seniorities = []
        if "stage" in active_tiers or "junior" in active_tiers:
            bip_seniorities.append("Junior")
        if "mid" in active_tiers or "senior" in active_tiers:
            bip_seniorities.append("Experienced")
        if not bip_seniorities:
            bip_seniorities = ["Junior", "Experienced"]

        # Location BIP
        bip_it_locations = ["Milano", "Roma", "Bologna", "Padova", "Bari", "Napoli", "Palermo", "Cagliari", "Puglia", "Italy"]
        target_bip_locs = [c for c in bip_it_locations if any(c.lower() in rc for rc in raw_cities)]
        if "Italy" not in target_bip_locs:
            target_bip_locs.append("Italy")

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        async def _extract_bip_current_page(page, filter_label: str) -> int:
            cards = await page.evaluate("""() => {
                const grid = document.querySelector('.repeater-grid');
                if (!grid) return [];
                return Array.from(grid.children).map(item => {
                    const titleEl = item.querySelector('.text-title-md');
                    const subEl = item.querySelector('.text-body-sm');
                    const dialog = item.querySelector('dialog.modal');
                    const applyLink = item.querySelector('a[href*="oraclecloud.com"]');
                    return {
                        title: titleEl ? titleEl.innerText.trim() : '',
                        contract: subEl ? subEl.innerText.trim() : '',
                        desc: dialog ? dialog.innerText.replace(/\\s+/g, ' ').trim() : '',
                        href: applyLink ? applyLink.href : ''
                    };
                }).filter(c => c.title && c.href);
            }""")
            added = 0
            for c in cards:
                href = c["href"].strip()
                title = c["title"].strip()
                desc = c["desc"].strip()
                if not href or not title or href in seen_urls:
                    continue

                # Scarta annunci in spagnolo / sedi estere (Bip Iberia, Madrid, Barcelona, London, Paris, Munich)
                desc_low = desc.lower()
                if any(sp in desc_low for sp in ("¿cuál será tu reto?", "bip iberia", "dpo-bipspain", "castellano")):
                    continue

                t_low = title.lower()
                if "senior" not in active_tiers and any(
                    sr in t_low for sr in ("senior", "manager", "director", "lead", "principal", "head of")
                ):
                    continue

                seen_urls.add(href)
                # Estrai eventuale RAL presente nella descrizione di BIP (es. 'compresa tra €34000 e €40000')
                m_ral = re.search(r"compresa\s+tra\s+€\s*([\d.,]+)\s+e\s+€\s*([\d.,]+)", desc, re.I)
                ral_str = f"€{m_ral.group(1)} - €{m_ral.group(2)}" if m_ral else None

                # Inferisci sede italiana dal testo o dal filtro attivo
                inferred_loc = filter_label if filter_label and filter_label != "Italy" else "Milano / Roma / Italia"
                for city_it in ("Torino", "Milano", "Roma", "Bologna", "Padova", "Bari", "Napoli", "Palermo", "Cagliari"):
                    if city_it.lower() in t_low or f"sede di {city_it.lower()}" in desc_low:
                        inferred_loc = f"{city_it}, Italia"
                        break

                job_dict: Dict[str, Any] = {
                    "external_id": deterministic_url_id("bip", href),
                    "portal": name,
                    "company": name,
                    "title": title[:140],
                    "location": inferred_loc,
                    "url": href,
                    "description": f"{title} ({c.get('contract', 'Full-Time')}) presso {name}\nSede: {inferred_loc}\n\n{desc[:3200]}",
                    "is_easy_apply": False,
                }
                if ral_str:
                    job_dict["estimated_ral"] = ral_str
                results.append(job_dict)
                added += 1
            return added

        mgr = StealthBrowserManager(profile_name="site_bip")
        try:
            async with mgr:
                page = await mgr.new_stealth_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await human_delay(3.0, 4.0)

                # Accetta banner cookie Iubenda se presente
                await page.evaluate("() => document.querySelector('.iubenda-cs-accept-btn, .iubenda-cs-close-btn')?.click()")
                await human_delay(0.8, 1.2)

                # Helper per selezionare un'opzione da un menu a tendina BIP ('Seniority'=idx 2, 'Location'=idx 4)
                async def _select_bip_dropdown(menu_label: str, option_text: str) -> bool:
                    idx_map = {"Practice": 1, "Seniority": 2, "Job Function": 3, "Location": 4}
                    li_idx = idx_map.get(menu_label, -1)
                    opened = await page.evaluate(
                        """(arg) => {
                            const ul = Array.from(document.querySelectorAll('ul')).find(u => u.innerText.includes('Filter for'));
                            if (ul && arg.idx > 0 && ul.children[arg.idx]) {
                                const a = ul.children[arg.idx].querySelector('a');
                                if (a) { a.click(); return true; }
                            }
                            const items = Array.from(document.querySelectorAll('li > a'));
                            const target = items.find(a => (a.innerText || '').trim().toLowerCase() === arg.lbl.toLowerCase());
                            if (target) { target.click(); return true; }
                            return false;
                        }""",
                        {"lbl": menu_label, "idx": li_idx},
                    )
                    if not opened:
                        return False
                    await human_delay(0.5, 0.8)
                    clicked_opt = await page.evaluate(
                        """(optTxt) => {
                            const openLi = document.querySelector('li[data-filter-open="true"]');
                            const popup = openLi?.querySelector('div.absolute');
                            if (!popup) return false;
                            const opt = Array.from(popup.querySelectorAll('a')).find(
                                a => a.innerText.trim().toLowerCase() === optTxt.toLowerCase()
                            );
                            if (opt) { opt.click(); return true; }
                            return false;
                        }""",
                        option_text,
                    )
                    await human_delay(1.2, 1.8)
                    return bool(clicked_opt)

                # 1. Filtra per Location ('Italy' + eventuali città target) e Seniority ('Junior' / 'Experienced')
                if len(bip_seniorities) == 1:
                    await _select_bip_dropdown("Seniority", bip_seniorities[0])

                for loc_choice in target_bip_locs[:2]:
                    await _select_bip_dropdown("Location", loc_choice)
                    loc_label = f"{loc_choice}, Italia" if loc_choice != "Italy" else "Milano / Roma / Italia"

                    # 2. Ricerca nel campo 'Search...' usando sia le diciture esatte sia quelle ridotte (es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Python', 'Developer')
                    search_terms_to_try = expanded_kws[:8] + [""]  # "" alla fine per scorrere anche le pagine numerate generali
                    for s_term in search_terms_to_try:
                        try:
                            search_input = page.locator("input[placeholder*='Search']").first
                            if await search_input.count() > 0:
                                await search_input.fill(s_term)
                                await search_input.press("Enter")
                                await human_delay(1.2, 1.8)

                            await _extract_bip_current_page(page, loc_label)

                            # 3. Scorri le pagine numerate in basso (1, 2, 3...)
                            max_pages = 5 if s_term == "" else 2
                            for p_num in range(2, max_pages + 1):
                                clicked_page = await page.evaluate(
                                    """(numStr) => {
                                        const btns = Array.from(document.querySelectorAll('button.border-primary-blue'));
                                        const b = btns.find(x => x.innerText.trim() === numStr);
                                        if (b) {
                                            b.scrollIntoView({ behavior: 'instant', block: 'center' });
                                            b.click();
                                            return true;
                                        }
                                        return false;
                                    }""",
                                    str(p_num),
                                )
                                if not clicked_page:
                                    break
                                await human_delay(1.4, 2.0)
                                if await _extract_bip_current_page(page, loc_label) == 0:
                                    break
                        except Exception as s_err:
                            logger.debug(f"[{name}] Warning ricerca '{s_term}' ({loc_choice}): {s_err}")
        except Exception as e:
            logger.warning(f"[{name}] Errore scraping BIP: {e}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci (Keywords esatte/ridotte: {expanded_kws[:6]}, Seniority: {bip_seniorities}, Locations: {target_bip_locs})."
        )
        return results

    async def _scrape_pwc(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per PwC Italia (https://jobs-it.pwc.com/it/en/search-results):
        1. Utilizza il form di ricerca della posizione con diciture esatte E ridotte (`expand_search_keywords`)
           e applica i filtri laterali:
           - `Category` (es. 'Technology-Young Professional', 'Innovation-Intern', 'Consulting-Young Professional',
             'Consulting-Intern', 'Consulting-Senior Professional', 'Deals, Mergers and Acquisitions-Young Professional')
           - `City` (città target del candidato es. 'Torino', 'Milano', 'Roma', 'Bologna', 'TRENTO', 'Padova', 'Napoli', 'Bari')
           - `Management Level` (`grade`, la seniority es. 'Intern/Trainee', 'Associate', 'Senior Associate', 'Manager')
           - `Keywords` (per cercare le posizioni affini al CV del candidato)
        2. Interroga sia il motore Phenom People (`POST https://jobs-it.pwc.com/widgets` con paginazione `from=0, 50, 100...`)
           sia il DOM interattivo (accordion `#CategoryAccordion`, `#CityAccordion`, `#ManagementLevelAccordion`,
           menu `Keywords` e selettore di pagine numeriche in basso `a.phw-s-page-number-link`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=14)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        # Mappa Management Level (grade) di PwC in base alla seniority del candidato
        pwc_grades: List[str] = []
        if "stage" in active_tiers:
            pwc_grades.append("Intern/Trainee")
        if "junior" in active_tiers:
            for g in ("Associate", "Intern/Trainee"):
                if g not in pwc_grades:
                    pwc_grades.append(g)
        if "mid" in active_tiers:
            for g in ("Senior Associate", "Associate", "Specialist"):
                if g not in pwc_grades:
                    pwc_grades.append(g)
        if "senior" in active_tiers:
            for g in ("Manager", "Senior Manager", "Senior Associate", "Specialist"):
                if g not in pwc_grades:
                    pwc_grades.append(g)
        if not pwc_grades:
            pwc_grades = ["Intern/Trainee", "Associate", "Senior Associate"]

        # Mappa City di PwC in base alle città target del candidato
        all_pwc_cities = ["Torino", "Milano", "Roma", "Bologna", "TRENTO", "Padova", "Napoli", "Treviso", "Verona", "Bari", "Rubano", "Palermo"]
        target_pwc_cities = [c for c in all_pwc_cities if any(c.lower() in rc for rc in raw_cities)]
        if not target_pwc_cities:
            target_pwc_cities = ["Torino", "Milano", "Roma", "Bologna"]

        # Mappa Category di PwC affini al profilo IT / AI / Consulting / Innovation
        pwc_categories = [
            "Technology-Young Professional",
            "Innovation-Intern",
            "Consulting-Intern",
            "Consulting-Young Professional",
        ]
        if "mid" in active_tiers or "senior" in active_tiers:
            pwc_categories.extend([
                "Consulting-Senior Professional",
                "Technology-Senior Professional",
            ])

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        def _add_pwc_job(j_obj: Dict[str, Any]) -> bool:
            title = str(j_obj.get("title") or "").strip()
            seq_no = str(j_obj.get("jobSeqNo") or j_obj.get("jobId") or "").strip()
            if not title or not seq_no:
                return False

            t_low = title.lower()
            grade_val = str(j_obj.get("grade") or "").strip()
            cat_val = str(j_obj.get("category") or "").strip()
            city_val = str(j_obj.get("city") or j_obj.get("location") or default_location).strip()

            # Rispetta il filtro seniority del candidato
            if "senior" not in active_tiers and grade_val in ("Manager", "Senior Manager", "Director"):
                return False
            if "senior" not in active_tiers and "mid" not in active_tiers and any(
                sr in t_low for sr in ("senior manager", "manager", "director", "head of", "partner")
            ):
                return False

            slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-")
            job_url = f"https://jobs-it.pwc.com/it/en/job/{seq_no}/{slug}"
            if job_url in seen_urls:
                return False
            seen_urls.add(job_url)

            posted_raw = str(j_obj.get("postedDate") or j_obj.get("dateCreated") or "")[:10]
            teaser = str(j_obj.get("descriptionTeaser") or "").strip()
            skills_list = j_obj.get("ml_skills") or []
            skills_str = ", ".join(str(s) for s in skills_list[:12]) if isinstance(skills_list, list) and skills_list else ""

            loc_formatted = f"{city_val}, Italia" if "italia" not in city_val.lower() else city_val
            desc_parts = [
                f"{title} presso {name}",
                f"Sede: {loc_formatted} | Category: {cat_val} | Management Level: {grade_val}",
            ]
            if skills_str:
                desc_parts.append(f"Skills chiave: {skills_str}")
            if teaser:
                desc_parts.append(teaser)

            job_dict: Dict[str, Any] = {
                "external_id": deterministic_url_id("pwc", job_url),
                "portal": name,
                "company": name,
                "title": title[:140],
                "location": loc_formatted,
                "url": job_url,
                "description": "\n".join(desc_parts),
                "is_easy_apply": False,
            }
            if re.match(r"^\d{4}-\d{2}-\d{2}$", posted_raw):
                job_dict["published_at"] = posted_raw
            results.append(job_dict)
            return True

        # 1. Motore primario: Phenom People `/widgets` (`refineSearch`) con Category, City, Management Level (grade),
        #    Keywords (diciture esatte + ridotte) e paginazione numerica (`from = 0, 50, 100...`)
        search_passes = [
            # Pass A: Città target (es. Torino, Milano, Roma) + Management Level del candidato
            {"keywords": "", "selected_fields": {"city": target_pwc_cities, "grade": pwc_grades}},
            # Pass B: Category Tech/Innovation/Consulting + Management Level
            {"keywords": "", "selected_fields": {"category": pwc_categories, "grade": pwc_grades}},
        ]
        # Pass C: Ricerca per ciascuna keyword esatta e ridotta (es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Python', 'Cloud', 'Cyber')
        for kw in expanded_kws[:12]:
            search_passes.append({
                "keywords": kw,
                "selected_fields": {"grade": pwc_grades},
            })

        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                for sp in search_passes:
                    kw_val = sp["keywords"]
                    sel_fields = sp["selected_fields"]
                    page_size = 50
                    max_pages = 3 if not kw_val else 2
                    for page_idx in range(max_pages):
                        offset = page_idx * page_size
                        payload = {
                            "lang": "en_it",
                            "deviceType": "desktop",
                            "country": "it",
                            "pageName": "search-results",
                            "ddoKey": "refineSearch",
                            "sortBy": "",
                            "subsearch": "",
                            "from": offset,
                            "jobs": True,
                            "counts": True,
                            "all_fields": ["category", "city", "grade", "type"],
                            "size": page_size,
                            "clearAll": False,
                            "jdsource": "facets",
                            "isSliderEnable": False,
                            "pageId": "page11",
                            "siteType": "external",
                            "keywords": kw_val,
                            "global": True,
                            "selected_fields": sel_fields,
                        }
                        resp = await client.post(
                            "https://jobs-it.pwc.com/widgets",
                            json=payload,
                            headers={
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            },
                        )
                        if resp.status_code != 200:
                            break
                        data = resp.json()
                        rs = data.get("refineSearch", {})
                        jobs_batch = rs.get("data", {}).get("jobs") or []
                        total_hits = int(rs.get("totalHits") or 0)
                        if not jobs_batch:
                            break
                        for j_item in jobs_batch:
                            # Se la ricerca è a testo libero senza filtro categoria, assicuriamoci che sia affine o nella città prioritaria
                            j_city = str(j_item.get("city") or "").lower()
                            j_title = str(j_item.get("title") or "").lower()
                            j_cat = str(j_item.get("category") or "").lower()
                            j_skills = " ".join(str(s).lower() for s in (j_item.get("ml_skills") or []))
                            is_tech_or_kw = (
                                bool(kw_val)
                                or any(t in j_cat for t in ("technology", "innovation", "consulting"))
                                or any(
                                    k in j_title or k in j_skills
                                    for k in ("ai", "data", "tech", "cyber", "cloud", "software", "digital", "engineer", "analyst", "developer", "it ", "sap", "analytics")
                                )
                                or "torino" in j_city
                            )
                            if is_tech_or_kw:
                                _add_pwc_job(j_item)
                        if offset + page_size >= total_hits:
                            break
        except Exception as e:
            logger.warning(f"[{name}] Warning API widgets PwC: {e}")

        # 2. Fallback / completamento interattivo DOM con Playwright se API restituisce < 5 risultati:
        #    Compila `#typehead`, apre `#CategoryAccordion`, `#CityAccordion`, `#ManagementLevelAccordion`, `Keywords`
        #    e scorre i numeri di pagina in basso (`a.phw-s-page-number-link`).
        if len(results) < 5:
            mgr = StealthBrowserManager(profile_name="site_pwc")
            try:
                async with mgr:
                    page = await mgr.new_stealth_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await human_delay(3.0, 4.0)

                    for kw in (expanded_kws[:4] + [""]):
                        search_inp = page.locator("#typehead, input[name='typehead']").first
                        if await search_inp.count() > 0:
                            await search_inp.fill(kw)
                            submit_btn = page.locator("#ph-search-backdrop, button[type='submit']").first
                            if await submit_btn.count() > 0:
                                await submit_btn.click()
                            else:
                                await search_inp.press("Enter")
                            await human_delay(2.0, 3.0)

                        # Apri gli accordion City, Category e Management Level e seleziona le checkbox corrispondenti
                        await page.evaluate(
                            """(arg) => {
                                for (const accId of ['CategoryAccordion', 'CityAccordion', 'ManagementLevelAccordion']) {
                                    const btn = document.getElementById(accId);
                                    if (btn && btn.getAttribute('aria-expanded') === 'false') btn.click();
                                }
                                const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
                                for (const cb of cbs) {
                                    const lbl = (cb.parentElement?.innerText || cb.getAttribute('data-ph-at-text') || '').trim();
                                    if (arg.cities.some(c => lbl.toLowerCase().includes(c.toLowerCase())) ||
                                        arg.grades.some(g => lbl.toLowerCase() === g.toLowerCase())) {
                                        if (!cb.checked) cb.click();
                                    }
                                }
                            }""",
                            {"cities": target_pwc_cities[:3], "grades": pwc_grades},
                        )
                        await human_delay(1.8, 2.5)

                        # Scorri le pagine numerate in basso (`a.phw-s-page-number-link`)
                        for p_num in range(1, 4):
                            if p_num > 1:
                                clicked_p = await page.evaluate(
                                    """(nStr) => {
                                        const links = Array.from(document.querySelectorAll('a.phw-s-page-number-link, a[data-ph-at-id="pagination-page-number-link"]'));
                                        const target = links.find(a => (a.innerText || '').trim().startsWith(nStr));
                                        if (target) { target.click(); return true; }
                                        return false;
                                    }""",
                                    str(p_num),
                                )
                                if not clicked_p:
                                    break
                                await human_delay(1.8, 2.5)

                            ddo_jobs = await page.evaluate(
                                "() => window.phApp?.ddo?.eagerLoadRefineSearch?.data?.jobs || []"
                            )
                            for j_item in ddo_jobs:
                                _add_pwc_job(j_item)
            except Exception as dom_err:
                logger.warning(f"[{name}] Fallback DOM PwC warning: {dom_err}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci (Keywords esatte/ridotte: {expanded_kws[:6]}, Cities: {target_pwc_cities[:4]}, Management Levels: {pwc_grades})."
        )
        return results

    async def _scrape_ey(
        self, name: str, url: str, site_data: Dict[str, Any], default_location: str
    ) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per i 2 portali ufficiali di EY:
        1. Portale 1 — Studenti e Primo Impiego (Yello ATS):
           `https://eyglobal.yello.co/job_boards/c1riT--B2O-KySgYWsZO1Q?locale=it`
           - Immissione delle keywords (`input.js-search-text` + bottone `button.btn-search` "TROVA LAVORO")
             usando sia diciture esatte sia ridotte (`expand_search_keywords`)
           - Menu a tendina sulla sinistra per il Paese (`Paese / regione` -> `Italia`, `val='30015'`)
           - Menu a tendina sulla sinistra per la Città (`Ufficio EY (Citta')` -> `ITA-Torino`, `ITA-Milano`, `ITA-Roma`, `ITA-Bologna`, ecc.)
           - Caricamento dinamico delle offerte scorrendo la pagina verso il basso (infinite scroll)
        2. Portale 2 — Lavoratori con Esperienza (SuccessFactors ATS):
           `https://careers.ey.com/ey/search/?createNewAlert=false&q=italia`
           - Ricerca veloce (`input.keywordsearch-q`) con diciture esatte e ridotte
           - Tasto `More options` (`a#options-search`) per selezionare `Country/Region` (`optionsFacetsDD_country=IT`)
             e `City` (`optionsFacetsDD_city`: `Torino`, `Milano`, `Roma`, `ROMA`, `Bologna`, `Bari`)
           - Tasto giallo `Search` (`input.keywordsearch-button` / `#searchfilter-submit`) e selettore di pagine numeriche in fondo (`startrow=0, 25, 50...`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=14)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        student_url = site_data.get(
            "student_url",
            "https://eyglobal.yello.co/job_boards/c1riT--B2O-KySgYWsZO1Q?locale=it",
        )
        experienced_url = url or "https://careers.ey.com/ey/search/?createNewAlert=false&q=italia"

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        # Mappa gli uffici italiani del Portale 1 (Yello)
        yello_city_map = {
            "torino": ("ITA-Torino", "139779", "Torino"),
            "milano": ("ITA-Milano", "139544", "Milano"),
            "roma": ("ITA-Roma", "139677", "Roma"),
            "bologna": ("ITA-Bologna", "139194", "Bologna"),
            "firenze": ("ITA-Firenze", "139316", "Firenze"),
            "genova": ("ITA-Genova", "139330", "Genova"),
            "napoli": ("ITA-Napoli", "139565", "Napoli"),
            "padova": ("ITA-Padova", "139625", "Padova"),
            "bari": ("ITA-Bari", "139159", "Bari"),
            "treviso": ("ITA-Treviso", "139799", "Treviso"),
            "verona": ("ITA-Verona", "139833", "Verona"),
        }
        target_yello_cities = [
            v for k, v in yello_city_map.items() if any(k in rc for rc in raw_cities)
        ]

        # =====================================================================
        # PORTALE 1: EY Studenti e Primo Impiego (Yello ATS con scroll dinamico)
        # =====================================================================
        if "stage" in active_tiers or "junior" in active_tiers or not active_tiers:
            mgr_yello = StealthBrowserManager(profile_name="site_ey_yello")
            try:
                async with mgr_yello:
                    page = await mgr_yello.new_stealth_page()
                    await page.goto(student_url, wait_until="domcontentloaded", timeout=45000)
                    await human_delay(3.0, 4.0)

                    async def _extract_yello_jobs(active_city_hint: str = "Italia") -> int:
                        cards = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a.search-results__req_title, a[href*="/jobs/"]')).map(a => {
                                const box = a.closest('.search-results__jobinfo, tr, li, div') || a.parentElement;
                                return {
                                    title: (a.innerText || '').trim(),
                                    href: a.href || '',
                                    rawText: box ? (box.innerText || '').replace(/\\s+/g, ' ').trim() : ''
                                };
                            }).filter(x => x.title && x.href);
                        }""")
                        added = 0
                        for c in cards:
                            href = c["href"].split("&locale=")[0]  # normalizza URL
                            title = c["title"]
                            if not href or not title or href in seen_urls:
                                continue

                            t_low = title.lower()
                            # Scarta posizioni chiaramente estere se mai dovessero comparire
                            if any(
                                fk in t_low
                                for fk in ("dubai", "cairo", "egypt", "saudi", "riyadh", "oman", "kuwait", "qatar", "jordan", "bahrain", "f/h", "cdi")
                            ):
                                continue

                            # Inferisci città italiana dal titolo o dal filtro attivo
                            inferred_city = active_city_hint
                            for city_key, (_, _, city_label) in yello_city_map.items():
                                if city_key in t_low:
                                    inferred_city = f"{city_label}, Italia"
                                    break
                            if "italia" not in inferred_city.lower():
                                inferred_city = f"{inferred_city}, Italia"

                            seen_urls.add(href)
                            results.append({
                                "external_id": deterministic_url_id("ey_yello", href),
                                "portal": name,
                                "company": name,
                                "title": title[:140],
                                "location": inferred_city,
                                "url": c["href"],
                                "description": (
                                    f"{title} presso {name} (Portale Studenti & Primo Impiego - Stage / Junior)\n"
                                    f"Sede: {inferred_city}\nDettagli: {c.get('rawText', '')}"
                                ),
                                "is_easy_apply": False,
                            })
                            added += 1
                        return added

                    # 1. Seleziona 'Italia' (value='30015') nel menu a tendina 'Paese / regione' sulla sinistra
                    await page.evaluate("""() => {
                        const cb = document.querySelector('input[type="checkbox"][value="30015"]');
                        if (cb && !cb.checked) cb.click();
                    }""")
                    await human_delay(2.5, 3.5)

                    # Scorri la pagina verso il basso per caricare dinamicamente tutte le offerte in Italia
                    for _ in range(5):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await human_delay(1.3, 1.8)
                    await _extract_yello_jobs("Milano / Torino / Roma, Italia")

                    # 2. Seleziona le Città target nel menu a tendina 'Ufficio EY (Città)' sulla sinistra
                    for city_code, city_val_id, city_label in target_yello_cities[:3]:
                        try:
                            await page.evaluate(
                                """(valId) => {
                                    const cb = document.querySelector(`input[type="checkbox"][value="${valId}"]`);
                                    if (cb && !cb.checked) cb.click();
                                }""",
                                city_val_id,
                            )
                            await human_delay(2.0, 2.8)
                            for _ in range(3):
                                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                await human_delay(1.0, 1.5)
                            await _extract_yello_jobs(f"{city_label}, Italia")
                            # Deseleziona la città prima di passare alla successiva
                            await page.evaluate(
                                """(valId) => {
                                    const cb = document.querySelector(`input[type="checkbox"][value="${valId}"]`);
                                    if (cb && cb.checked) cb.click();
                                }""",
                                city_val_id,
                            )
                            await human_delay(1.2, 1.8)
                        except Exception as c_err:
                            logger.debug(f"[{name} Yello] Warning filtro città {city_label}: {c_err}")

                    # 3. Usa anche il campo di immissione delle keywords (con diciture esatte e ridotte) + scroll verso il basso
                    for kw in expanded_kws[:6]:
                        try:
                            kw_inp = page.locator("input.js-search-text, input[name='text']").first
                            if await kw_inp.count() > 0:
                                await kw_inp.fill(kw)
                                btn_search = page.locator("button.btn-search").first
                                if await btn_search.count() > 0:
                                    await btn_search.click()
                                else:
                                    await kw_inp.press("Enter")
                                await human_delay(2.0, 2.8)
                                for _ in range(2):
                                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    await human_delay(1.0, 1.4)
                                await _extract_yello_jobs("Milano / Torino / Roma, Italia")
                        except Exception as k_err:
                            logger.debug(f"[{name} Yello] Warning keyword '{kw}': {k_err}")
            except Exception as e1:
                logger.warning(f"[{name}] Errore Portale 1 EY (Yello Studenti/Primo Impiego): {e1}")

        # =====================================================================
        # PORTALE 2: EY Lavoratori con Esperienza (SuccessFactors ATS)
        # =====================================================================
        ey2_cities_all = ["Torino", "Milano", "Roma", "ROMA", "Bologna", "Bari"]
        target_ey2_cities = [c for c in ey2_cities_all if any(c.lower() in rc for rc in raw_cities)]
        if not target_ey2_cities:
            target_ey2_cities = ["Torino", "Milano", "Roma", "Bologna"]

        def _parse_ey2_html(html_text: str, city_fallback: str = "Italia") -> int:
            soup = BeautifulSoup(html_text, "html.parser")
            rows = soup.select("tr.data-row")
            added = 0
            for tr in rows:
                a_el = tr.select_one("a.jobTitle-link")
                if not a_el:
                    continue
                title = a_el.get_text(" ", strip=True)
                href = a_el.get("href", "").strip()
                if not title or not href:
                    continue
                full_url = urljoin("https://careers.ey.com", href)
                if full_url in seen_urls:
                    continue

                t_low = title.lower()
                if "senior" not in active_tiers and any(
                    sr in t_low for sr in ("senior manager", "director", "partner", "head of", "executive")
                ):
                    continue

                loc_el = tr.select_one("span.jobLocation")
                loc_raw = loc_el.get_text(" ", strip=True) if loc_el else city_fallback
                loc_clean = re.sub(r"\+\d+\s*more.*", "", loc_raw).strip(" ,")
                if "it" not in loc_clean.lower() and "italia" not in loc_clean.lower():
                    loc_clean = f"{loc_clean}, Italia"

                seen_urls.add(full_url)
                results.append({
                    "external_id": deterministic_url_id("ey_exp", full_url),
                    "portal": name,
                    "company": name,
                    "title": title[:140],
                    "location": loc_clean,
                    "url": full_url,
                    "description": f"{title} presso {name} (Portale Experienced Careers)\nSede: {loc_clean}",
                    "is_easy_apply": False,
                })
                added += 1
            return added

        # Query sul Portale 2 (Country/Region = IT, City = città target, Ricerca veloce con diciture esatte e ridotte,
        # e scorrimento del selettore di pagine in fondo `startrow=0, 25, 50...`)
        ey2_queries: List[Tuple[str, str]] = []
        for c_name in target_ey2_cities:
            ey2_queries.append(("", c_name))
        for kw in (expanded_kws[:10] + [""]):
            ey2_queries.append((kw, ""))

        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                for q_kw, q_city in ey2_queries:
                    max_pages = 3 if (not q_kw and not q_city) else 2
                    for p_idx in range(max_pages):
                        start_row = p_idx * 25
                        q_url = (
                            f"https://careers.ey.com/ey/search/?createNewAlert=false"
                            f"&q={quote_plus(q_kw)}"
                            f"&optionsFacetsDD_country=IT"
                            f"&optionsFacetsDD_city={quote_plus(q_city)}"
                            f"&startrow={start_row}"
                        )
                        resp = await client.get(
                            q_url,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                        )
                        if resp.status_code != 200:
                            break
                        added = _parse_ey2_html(resp.text, q_city or "Italia")
                        if added == 0 or f"startrow={start_row + 25}" not in resp.text:
                            break
        except Exception as e2:
            logger.warning(f"[{name}] Warning HTTPX Portale 2 EY (SuccessFactors): {e2}")

        # Fallback interattivo Playwright per il Portale 2 se necessario (`More options` -> `Country/Region` -> `City` -> tasto giallo `Search`)
        if not any("careers.ey.com" in r["url"] for r in results):
            mgr_ey2 = StealthBrowserManager(profile_name="site_ey_exp")
            try:
                async with mgr_ey2:
                    page = await mgr_ey2.new_stealth_page()
                    await page.goto(experienced_url, wait_until="domcontentloaded", timeout=45000)
                    await human_delay(2.5, 3.5)

                    # Clicca il tasto 'More options' (`a#options-search`)
                    more_btn = page.locator("a#options-search").first
                    if await more_btn.count() > 0:
                        await more_btn.click()
                        await human_delay(0.8, 1.2)

                    # Imposta Country/Region = 'IT' (Italy) e City se presente
                    country_sel = page.locator("select#optionsFacetsDD_country").first
                    if await country_sel.count() > 0:
                        await country_sel.select_option("IT")

                    for kw in (expanded_kws[:4] + [""]):
                        q_inp = page.locator("input.keywordsearch-q").first
                        if await q_inp.count() > 0:
                            await q_inp.fill(kw)
                        search_btn = page.locator("input.keywordsearch-button, input#searchfilter-submit").first
                        if await search_btn.count() > 0:
                            await search_btn.click()
                            await human_delay(2.5, 3.5)
                        html_now = await page.content()
                        _parse_ey2_html(html_now, "Italia")
            except Exception as dom_e2:
                logger.warning(f"[{name}] Fallback DOM Portale 2 EY warning: {dom_e2}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci totali dai 2 portali EY (Yello Studenti/Primo Impiego + SuccessFactors Experienced, Keywords: {expanded_kws[:6]})."
        )
        return results

    async def _scrape_leonardo(self, name: str, url: str, default_location: str) -> List[Dict[str, Any]]:
        """
        Scraper dedicato per Leonardo S.p.A. (https://leonardocompany.wd3.myworkdayjobs.com/it-IT/LeonardoCareerSite):
        1. Usa la ricerca rapida della posizione con diciture esatte E ridotte (`expand_search_keywords`)
        2. Applica i filtri dinamici di Leonardo:
           - `Distanza o sede` / `Sede`: Paese = `Italy` (`locationCountry`) e specifici uffici italiani (`locations`,
             inclusi i 4 uffici di Torino: `IT - Torino - C.so Francia`, `IT - Torino - C.so Marche`,
             `IT - Torino - Caselle Nord`, `IT - Torino - Caselle Sud`, oltre a Milano, Roma, Genova, Firenze, Napoli, Cameri)
           - `Categoria d'impiego` (`jobFamilyGroup`): `Engineering - Software`, `Information Technology`,
             `Engineering - Systems`, `Engineering - Electronics`, `Cyber & Physical Security`, `Engineering - Management`
           - `Altro` -> `Tipo di impiego` (`workerSubType` per la seniority: `Internship - thesis`, `Internship`,
             `Trainee (Fixed Term)`, `Intern (Trainee)`, `Impiegato`, `Quadro`)
        3. Scorre le pagine numerate a fine sito (`offset = 0, 20, 40...` / bottoni numerici `paginationLink`).
        """
        cfg = load_candidate_config()
        prefs = cfg.get("search_preferences", {})
        target_sen = cfg.get("candidate_profile", {}).get("target_seniority", {})
        raw_keywords = [str(k).strip() for k in prefs.get("keywords", []) if k]
        expanded_kws = expand_search_keywords(raw_keywords, max_terms=14)
        raw_cities = [str(c).strip().lower() for c in prefs.get("target_cities", []) if c]

        accepted_tiers = target_sen.get("accepted_tiers")
        if accepted_tiers is not None:
            active_tiers = {str(t).lower() for t in accepted_tiers}
        else:
            rejects = [str(r).lower() for r in target_sen.get("reject_seniorities", [])]
            active_tiers = {"stage", "junior", "mid"}
            if not any("senior" in r for r in rejects):
                active_tiers.add("senior")

        cxs_url = "https://leonardocompany.wd3.myworkdayjobs.com/wday/cxs/leonardocompany/LeonardoCareerSite/jobs"
        italy_country_id = "8cd04a563fd94da7b06857a79faaf815"

        # ID di fallback per Categoria d'impiego (`jobFamilyGroup`), Tipo di impiego (`workerSubType`) e Uffici (`locations`)
        default_job_families = [
            "8f7876e90e9c0101f751f09542ab0000",  # Engineering - Software
            "8f7876e90e9c0101f751f26321600000",  # Information Technology
            "8f7876e90e9c0101f751ecf8d7450000",  # Cyber & Physical Security
            "8f7876e90e9c0101f751effb6bf50000",  # Engineering - Systems
            "8f7876e90e9c0101f751ee2e07d40000",  # Engineering - Electronics
            "8f7876e90e9c0101f751eec77a160000",  # Engineering - Management
        ]
        torino_office_ids = [
            "2dc6ef272d0f010a9a5089a35e190000",  # IT - Torino - C.so Francia
            "2dc6ef272d0f010a9a5090426da40000",  # IT - Torino - C.so Marche
            "2dc6ef272d0f010a9a5096e1dd6a0000",  # IT - Torino - Caselle Nord
            "2dc6ef272d0f010a9a509d81b1250000",  # IT - Torino - Caselle Sud
            "2dc6ef272d0f010a9a4e9b507f2d0000",  # IT - Cameri - Loc. Aereoporto
        ]

        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        def _add_leo_posting(jp: Dict[str, Any]) -> bool:
            title = str(jp.get("title") or "").strip()
            ext_path = str(jp.get("externalPath") or "").strip()
            if not title or not ext_path:
                return False

            loc_txt = str(jp.get("locationsText") or default_location).strip()
            # Scarta posizioni estere (es. GB - Luton, GB - Edinburgh, DE - Darmstadt, BR - Rio)
            if any(
                loc_txt.startswith(prefix)
                for prefix in ("GB -", "DE -", "BR -", "COL -", "ES -", "CZ -", "PL -", "US -", "NL -")
            ):
                return False

            t_low = title.lower()
            if "senior" not in active_tiers and any(
                sr in t_low for sr in ("senior manager", "director", "head of", "vice president", "vp ", "chief")
            ):
                return False

            job_url = f"https://leonardocompany.wd3.myworkdayjobs.com/it-IT/LeonardoCareerSite{ext_path}"
            if job_url in seen_urls:
                return False
            seen_urls.add(job_url)

            posted_on = str(jp.get("postedOn") or "").strip()
            bullet_fields = jp.get("bulletFields") or []
            req_id = ", ".join(str(b) for b in bullet_fields if b)

            clean_loc = loc_txt.replace("IT - ", "").strip()
            if "italia" not in clean_loc.lower():
                clean_loc = f"{clean_loc}, Italia"

            results.append({
                "external_id": deterministic_url_id("leonardo", job_url),
                "portal": name,
                "company": name,
                "title": title[:140],
                "location": clean_loc,
                "url": job_url,
                "description": (
                    f"{title} presso {name}\n"
                    f"Sede / Ufficio: {clean_loc} ({loc_txt})\n"
                    f" Pubblicato: {posted_on} | Rif: {req_id}"
                ),
                "is_easy_apply": False,
            })
            return True

        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                # 1. Risolvi dinamicamente i facet ID dal portale Workday di Leonardo per Paese=Italy, Uffici,
                #    Categoria d'impiego (`jobFamilyGroup`) e Tipo di impiego (`workerSubType`)
                target_office_ids: List[str] = list(torino_office_ids)
                target_worker_subtypes: List[str] = []
                target_family_ids: List[str] = list(default_job_families)

                init_resp = await client.post(
                    cxs_url,
                    json={"appliedFacets": {"locationCountry": [italy_country_id]}, "limit": 20, "offset": 0, "searchText": ""},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
                if init_resp.status_code == 200:
                    init_data = init_resp.json()
                    for facet in init_data.get("facets", []):
                        f_param = facet.get("facetParameter")
                        if f_param == "locationMainGroup":
                            for sub in facet.get("values", []):
                                if sub.get("facetParameter") == "locations":
                                    matched_offices = []
                                    for v in sub.get("values", []):
                                        desc = str(v.get("descriptor") or "").lower()
                                        vid = v.get("id")
                                        if vid and any(rc in desc for rc in raw_cities):
                                            matched_offices.append(vid)
                                    if matched_offices:
                                        target_office_ids = matched_offices
                        elif f_param == "workerSubType":
                            for v in facet.get("values", []):
                                desc = str(v.get("descriptor") or "").lower()
                                vid = v.get("id")
                                if not vid:
                                    continue
                                is_intern = any(k in desc for k in ("intern", "trainee", "thesis", "stage"))
                                is_regular = any(k in desc for k in ("impiegato", "subcontractor", "intermedio"))
                                is_quadro = any(k in desc for k in ("quadro", "dirigente"))
                                if ("stage" in active_tiers and is_intern) or (
                                    ("junior" in active_tiers or "mid" in active_tiers) and (is_regular or is_intern)
                                ) or ("senior" in active_tiers and (is_regular or is_quadro)):
                                    target_worker_subtypes.append(vid)

                # Costruisci le combinazioni di ricerca (con paginazione `offset = 0, 20, 40...`):
                # Pass A: Uffici target (es. Torino C.so Francia, C.so Marche, Caselle Nord/Sud) + Categoria d'impiego + Tipo di impiego
                facet_passes: List[Tuple[str, Dict[str, Any], int]] = []
                base_office_facets: Dict[str, Any] = {
                    "locationCountry": [italy_country_id],
                    "locations": target_office_ids,
                    "jobFamilyGroup": target_family_ids,
                }
                if target_worker_subtypes:
                    base_office_facets["workerSubType"] = target_worker_subtypes
                facet_passes.append(("", base_office_facets, 4))

                # Pass B: Tutte le posizioni negli uffici target di Torino/città scelte (anche di altre categorie ingegneristiche)
                office_only_facets: Dict[str, Any] = {
                    "locationCountry": [italy_country_id],
                    "locations": target_office_ids,
                }
                if target_worker_subtypes:
                    office_only_facets["workerSubType"] = target_worker_subtypes
                facet_passes.append(("", office_only_facets, 3))

                # Pass C: Ricerca rapida per posizione con diciture esatte E ridotte (`expand_search_keywords`) su tutta Italia
                it_facets: Dict[str, Any] = {
                    "locationCountry": [italy_country_id],
                    "jobFamilyGroup": target_family_ids,
                }
                if target_worker_subtypes:
                    it_facets["workerSubType"] = target_worker_subtypes
                for kw in expanded_kws[:12]:
                    facet_passes.append((kw, it_facets, 2))

                for search_txt, facets_dict, max_pages in facet_passes:
                    for page_idx in range(max_pages):
                        offset = page_idx * 20
                        r_page = await client.post(
                            cxs_url,
                            json={
                                "appliedFacets": facets_dict,
                                "limit": 20,
                                "offset": offset,
                                "searchText": search_txt,
                            },
                            headers={"Content-Type": "application/json", "Accept": "application/json"},
                        )
                        if r_page.status_code != 200:
                            break
                        p_data = r_page.json()
                        postings = p_data.get("jobPostings") or []
                        total_count = int(p_data.get("total") or len(postings))
                        if not postings:
                            break
                        for jp in postings:
                            _add_leo_posting(jp)
                        if offset + 20 >= total_count or len(postings) < 20:
                            break
        except Exception as e:
            logger.warning(f"[{name}] Warning API Workday Leonardo: {e}")

        # 2. Fallback interattivo DOM con Playwright se necessario (compila `[data-automation-id="keywordSearchInput"]`,
        #    clicca i filtri `Distanza o sede`, `Categoria impiego`, `Altro -> Tipo di impiego` e scorre le pagine numeriche)
        if len(results) == 0:
            mgr = StealthBrowserManager(profile_name="site_leonardo")
            try:
                async with mgr:
                    page = await mgr.new_stealth_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await human_delay(3.0, 4.0)

                    for kw in (expanded_kws[:4] + [""]):
                        inp = page.locator('[data-automation-id="keywordSearchInput"]').first
                        if await inp.count() > 0:
                            await inp.fill(kw)
                            btn = page.locator('[data-automation-id="keywordSearchButton"]').first
                            if await btn.count() > 0:
                                await btn.click()
                            else:
                                await inp.press("Enter")
                            await human_delay(2.0, 3.0)

                        dom_items = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a[data-automation-id="jobTitle"]')).map(a => {
                                const card = a.closest('li') || a.parentElement;
                                const locEl = card ? card.querySelector('[data-automation-id="locations"] dd') : null;
                                return {
                                    title: (a.innerText || '').trim(),
                                    href: a.getAttribute('href') || '',
                                    locationsText: locEl ? locEl.innerText.trim() : 'Italia'
                                };
                            });
                        }""")
                        for d_item in dom_items:
                            if d_item.get("href"):
                                ext_p = d_item["href"].split("LeonardoCareerSite")[-1]
                                _add_leo_posting({
                                    "title": d_item["title"],
                                    "externalPath": ext_p,
                                    "locationsText": d_item["locationsText"],
                                })
            except Exception as dom_err:
                logger.warning(f"[{name}] Fallback DOM Leonardo warning: {dom_err}")

        logger.info(
            f"[{name}] Estratti {len(results)} annunci in Italia (Keywords esatte/ridotte: {expanded_kws[:6]}, Uffici target: {len(torino_office_ids)})."
        )
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
            "a:has-text('Mostra di più')",
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
        if site_type == "accenture":
            return "a[href*='/careers/jobdetails'], a.rad-filters-vertical__job-card-content-link-button"
        if site_type == "capgemini":
            return "a.JobRow-module__job-card___riAUE, a[href*='/it-it/jobs/']"
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
