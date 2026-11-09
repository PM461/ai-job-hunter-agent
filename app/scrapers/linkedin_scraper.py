import asyncio
import logging
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup

from app.config import BASE_DIR, load_candidate_config
from app.stealth_browser import StealthBrowserManager, human_delay
from app.ai_matcher import GeminiMatcher
from app.scrapers.multinationals_scraper import expand_search_keywords

logger = logging.getLogger("JobAgent.LinkedIn")

# Mappa ufficiale dei GeoId LinkedIn per le principali città italiane e l'Italia intera
LINKEDIN_GEO_IDS: Dict[str, str] = {
    "torino": "106187582",
    "turin": "106187582",
    "bari": "100856834",
    "milano": "100293800",
    "milan": "100293800",
    "roma": "106171260",
    "rome": "106171260",
    "bologna": "101336226",
    "napoli": "105149290",
    "naples": "105149290",
    "padova": "103966236",
    "padua": "103966236",
    "firenze": "101705918",
    "florence": "101705918",
    "genova": "102700626",
    "genoa": "102700626",
    "modena": "106526554",
    "italia": "103350119",
    "italy": "103350119",
}

# Mappa degli ID Aziendali (f_C) ufficiali su LinkedIn per ricerca filtrata nativa
LINKEDIN_COMPANY_IDS: Dict[str, str] = {
    "reply": "16291048,2092842,76134892,26956115,164507,98152663",
    "machine learning reply": "76134892",
    "data reply": "2092842",
    "target reply": "26956115",
    "zucchetti": "11997",
    "intesa sanpaolo": "1809",
    "banca sella": "10884",
    "leonardo": "164702",
    "thales alenia space": "3558490",
    "cnh industrial": "3173510",
    "iveco group": "76963992",
    "amazon": "1586",
    "accenture": "1033",
    "capgemini": "157240",
    "capgemini engineering": "71535061",
    "deloitte": "1038",
    "pwc": "1044",
    "ey": "1073",
    "kpmg": "1079",
    "ntt data": "218305",
    "satispay": "10051660",
    "docebo": "233899",
    "almaviva": "13941",
    "teoresi": "67652",
    "lutech": "25573",
    "alten": "3890",
    "akkodis": "82516667",
}


class LinkedInPortal:
    def __init__(self, ai_matcher: GeminiMatcher):
        self.ai_matcher = ai_matcher

    def _build_native_filter_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traduce la configurazione del profilo e di Telegram nei filtri nativi di ricerca LinkedIn:
        - f_WT: 1 (In sede), 2 (Remoto), 3 (Ibrido)
        - f_E: 1 (Stage/Internship), 2 (Entry level), 3 (Associate), 4 (Mid-Senior), 5 (Director)
        - f_TPR: r86400 (24h), r604800 (Settimana), r2592000 (Mese)
        - f_C: ID Aziendali nativi per le target_companies del profilo
        - Clausola booleana NOT dinamica coerente con la seniority scelta.
        """
        li_conf = config.get("target_portals", {}).get("linkedin", {})
        search_prefs = config.get("search_preferences", {})
        profile = config.get("candidate_profile", {})
        sen_cfg = profile.get("target_seniority", {})

        # 1. Filtro nativo Modalità di lavoro (f_WT)
        work_modes = [str(m).lower() for m in (search_prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])]
        wt_codes: List[str] = []
        if any(m in work_modes for m in ("in_sede", "insede", "onsite")):
            wt_codes.append("1")
        if any(m in work_modes for m in ("remoto", "remote")):
            wt_codes.append("2")
        if any(m in work_modes for m in ("ibrido", "hybrid")):
            wt_codes.append("3")
        f_wt_param = f"&f_WT={urllib.parse.quote(','.join(wt_codes))}" if wt_codes else ""

        # 2. Filtro nativo Livello di Esperienza (f_E) + clausola NOT dinamica
        accepted_tiers = [
            str(t).strip().lower()
            for t in (sen_cfg.get("accepted_tiers") or ["stage", "junior", "mid", "senior"])
        ]
        fe_codes: List[str] = []
        if "stage" in accepted_tiers:
            fe_codes.append("1")
        if "junior" in accepted_tiers:
            fe_codes.extend(["2", "3"])
        if "mid" in accepted_tiers:
            fe_codes.extend(["3", "4"])
        if "senior" in accepted_tiers:
            fe_codes.extend(["4", "5"])
        fe_unique = sorted(set(fe_codes))
        f_e_param = f"&f_E={urllib.parse.quote(','.join(fe_unique))}" if fe_unique else ""

        not_terms: List[str] = []
        if "senior" not in accepted_tiers:
            not_terms.extend(["Senior", "Lead", "Principal", "Director", "Manager"])
        if "stage" not in accepted_tiers:
            not_terms.extend(["Stage", "Intern", "Internship", "Tirocinio"])
        if "junior" not in accepted_tiers:
            not_terms.append("Junior")
        not_clause = f" NOT ({' OR '.join(not_terms)})" if not_terms else ""

        # 3. Filtro nativo Data di pubblicazione (f_TPR)
        time_range = li_conf.get("time_filter", "month")
        tpr_map = {
            "today": "&f_TPR=r86400",
            "week": "&f_TPR=r604800",
            "month": "&f_TPR=r2592000",
            "all": "",
        }
        f_tpr_param = tpr_map.get(time_range, "&f_TPR=r2592000")

        # 4. Risoluzione Aziende Target del profilo (f_C nativi + nomi aziende per ricerca mirata)
        raw_companies: List[str] = li_conf.get("target_companies") or []
        fc_ids: List[str] = []
        unmapped_companies: List[str] = []
        for comp in raw_companies:
            c_low = comp.strip().lower()
            matched_id = None
            for k, cid in LINKEDIN_COMPANY_IDS.items():
                if k == c_low or k in c_low:
                    matched_id = cid
                    break
            if matched_id:
                for sub_id in matched_id.split(","):
                    if sub_id not in fc_ids:
                        fc_ids.append(sub_id)
            elif comp.strip():
                unmapped_companies.append(comp.strip())

        return {
            "f_wt_param": f_wt_param,
            "f_e_param": f_e_param,
            "f_tpr_param": f_tpr_param,
            "not_clause": not_clause,
            "fc_ids": fc_ids,
            "unmapped_companies": unmapped_companies,
            "work_modes": work_modes,
            "time_range": time_range,
        }

    async def _enrich_linkedin_job_detail(
        self, client: httpx.AsyncClient, job: Dict[str, Any], headers: Dict[str, str], sem: asyncio.Semaphore
    ) -> None:
        """
        Scarica i dettagli completi dell'offerta da `/jobs-guest/jobs/api/jobPosting/{job_id}`:
        - Descrizione completa dell'annuncio
        - Criteri strutturati (Livello di anzianità, Tipo di impiego, Settore)
        - Eventuale profilo diretto del Recruiter / Hiring Manager se pubblicato nell'offerta.
        """
        job_id = str(job.get("external_id", "")).replace("linkedin_", "").strip()
        if not job_id.isdigit():
            return
        detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        async with sem:
            try:
                resp = await client.get(detail_url, headers=headers)
                if resp.status_code != 200 or not resp.text:
                    return
                soup = BeautifulSoup(resp.text, "html.parser")
                desc_el = soup.select_one("div.show-more-less-html__markup, div.description__text")
                criteria_items = [
                    li.get_text(" ", strip=True)
                    for li in soup.select("li.description__job-criteria-item")
                    if li.get_text(strip=True)
                ]
                criteria_str = " | ".join(criteria_items)

                if desc_el:
                    clean_desc = desc_el.get_text("\n", strip=True)
                    header = f"{job['title']} presso {job['company']} ({job['location']})"
                    if criteria_str:
                        header += f"\n{criteria_str}"
                    job["description"] = f"{header}\n\n{clean_desc}"[:4000]

                # Estrai eventuale link diretto del Recruiter / Poster se presente nella scheda
                rec_link_el = soup.select_one(
                    "div.message-the-recruiter a[href*='/in/'], a.message-the-recruiter__cta[href*='/in/'], div.base-main-card a[href*='/in/']"
                )
                if rec_link_el:
                    rec_href = (rec_link_el.get("href") or "").split("?")[0].strip()
                    rec_name_el = soup.select_one("div.message-the-recruiter h3, div.message-the-recruiter h4, h3.base-main-card__title")
                    rec_name = rec_name_el.get_text(strip=True) if rec_name_el else "Recruiter / Hiring Poster"
                    if rec_href.startswith("http"):
                        job["recruiter_url"] = rec_href
                        job["recruiter_name"] = rec_name
            except Exception as e:
                logger.debug(f"Dettaglio LinkedIn {job_id} non disponibile: {e}")

    async def scrape_jobs(self) -> List[Dict[str, Any]]:
        """
        Motore avanzato per LinkedIn che combina:
        1. Filtri nativi di LinkedIn (`f_WT` per modalità lavoro, `f_E` per livello seniority, `f_TPR` per data, `geoId` per città, `f_C` per aziende target)
        2. Operatori Booleani dinamici sui ruoli e sulle aziende configurate nel profilo (`target_companies`)
        3. Arricchimento delle offerte con descrizione completa e profilo Recruiter (se esposto).
        """
        config = load_candidate_config()
        li_conf = config.get("target_portals", {}).get("linkedin", {})
        if not li_conf.get("enabled", True):
            return []

        search_prefs = config.get("search_preferences", {})
        keywords: List[str] = search_prefs.get("keywords") or [
            "AI Engineer",
            "Machine Learning Engineer",
            "Data Scientist",
            "Python Developer",
            "Software Engineer",
        ]
        target_cities: List[str] = search_prefs.get("target_cities") or [
            config.get("candidate_profile", {}).get("city", "Italia")
        ]

        native = self._build_native_filter_params(config)
        f_wt_param = native["f_wt_param"]
        f_e_param = native["f_e_param"]
        f_tpr_param = native["f_tpr_param"]
        not_clause = native["not_clause"]
        fc_ids = native["fc_ids"]
        unmapped_companies = native["unmapped_companies"]
        work_modes = native["work_modes"]
        time_range = native["time_range"]

        # Costruisci le località con il relativo geoId ufficiale LinkedIn
        search_Specs: List[Dict[str, str]] = []
        if any(m in work_modes for m in ("ibrido", "in_sede", "insede", "hybrid", "onsite")):
            for c in target_cities[:5]:
                c_clean = c.strip()
                if not c_clean:
                    continue
                geo_id = LINKEDIN_GEO_IDS.get(c_clean.lower(), "")
                search_Specs.append({"location": c_clean, "geo_id": geo_id, "f_wt": f_wt_param})

        # Se è attivo il lavoro da remoto, aggiungi la ricerca con filtro nativo Remote (f_WT=2) su tutta Italia (geoId=103350119)
        if any(m in work_modes for m in ("remoto", "remote")):
            search_Specs.append({"location": "Italia", "geo_id": "103350119", "f_wt": "&f_WT=2"})

        if not search_Specs:
            search_Specs.append({"location": "Italia", "geo_id": "103350119", "f_wt": f_wt_param})

        # Costruisci le ricerche combinando:
        # A) Query Booleane + Filtri Nativi (f_WT, f_E, geoId)
        # B) Query dirette per ruolo + Filtri Nativi
        # C) Ricerche mirate sulle aziende del profilo (tramite filtro nativo f_C e tramite operatori Booleani aziendali)
        search_requests: List[Dict[str, str]] = []
        chunk_size = 3
        for spec in search_Specs:
            # A. Gruppi Booleani di ruoli
            for i in range(0, min(len(keywords), 9), chunk_size):
                group = keywords[i : i + chunk_size]
                or_clause = " OR ".join(f'"{k}"' if " " in k else k for k in group)
                bool_q = f"({or_clause}){not_clause}"
                search_requests.append({
                    "kw": bool_q,
                    "location": spec["location"],
                    "geo_id": spec["geo_id"],
                    "f_wt": spec["f_wt"],
                    "f_c": "",
                })
            # B. Keyword esatte e diciture ridotte (es. 'AI Engineer', 'AI', 'IA', 'Engineer', 'Data', 'Developer') con filtri nativi
            expanded_li_kws = expand_search_keywords(keywords, max_terms=12)
            for kw in expanded_li_kws[:8]:
                search_requests.append({
                    "kw": kw,
                    "location": spec["location"],
                    "geo_id": spec["geo_id"],
                    "f_wt": spec["f_wt"],
                    "f_c": "",
                })
            # C1. Filtro nativo Aziende (f_C) combinato con i ruoli principali del profilo
            if fc_ids:
                primary_or = " OR ".join(f'"{k}"' if " " in k else k for k in keywords[:4])
                for j in range(0, min(len(fc_ids), 24), 8):
                    fc_batch = ",".join(fc_ids[j : j + 8])
                    search_requests.append({
                        "kw": f"({primary_or})",
                        "location": spec["location"],
                        "geo_id": spec["geo_id"],
                        "f_wt": spec["f_wt"],
                        "f_c": f"&f_C={urllib.parse.quote(fc_batch)}",
                    })
            # C2. Ricerca combinata per le aziende target del profilo senza f_C statico
            if unmapped_companies and spec["location"] != "Italia":
                role_short = " OR ".join(f'"{k}"' for k in keywords[:3])
                for j in range(0, min(len(unmapped_companies), 16), 4):
                    comp_group = unmapped_companies[j : j + 4]
                    comp_or = " OR ".join(f'"{c}"' for c in comp_group)
                    search_requests.append({
                        "kw": f"({role_short}) AND ({comp_or})",
                        "location": spec["location"],
                        "geo_id": spec["geo_id"],
                        "f_wt": spec["f_wt"],
                        "f_c": "",
                    })

        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        }

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for req in search_requests:
                kw = req["kw"]
                loc = req["location"]
                geo_param = f"&geoId={req['geo_id']}" if req["geo_id"] else ""
                f_wt = req["f_wt"]
                f_c = req["f_c"]

                url = (
                    f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
                    f"keywords={urllib.parse.quote(kw)}&location={urllib.parse.quote(loc)}"
                    f"{geo_param}{f_wt}{f_e_param}{f_tpr_param}{f_c}&start=0"
                )
                logger.info(
                    f"[LinkedIn Feed] Ricerca '{kw}' in '{loc}' (periodo: {time_range}, filtri: {f_wt}{f_e_param}{f_c})..."
                )

                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200 and resp.text:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        cards = soup.select("li, div.base-card")

                        for c in cards:
                            card_text = c.get_text(" ", strip=True).lower()
                            if "candidatura inoltrata" in card_text or "application submitted" in card_text:
                                continue
                            if any(
                                p in card_text
                                for p in ["categoria protetta", "categorie protette", "l. 68/99", "l.68/99", "l 68/99", "rif. 68/99"]
                            ):
                                continue

                            link_el = c.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
                            if not link_el:
                                continue

                            href = link_el.get("href", "").split("?")[0]
                            if not href or href in seen_urls:
                                continue
                            seen_urls.add(href)

                            title_el = c.select_one("h3.base-search-card__title, a.base-card__full-link")
                            title = title_el.get_text(strip=True) if title_el else kw

                            comp_el = c.select_one("h4.base-search-card__subtitle, a[data-tracking-control-name*='company-name']")
                            company = comp_el.get_text(strip=True) if comp_el else ""

                            loc_el = c.select_one("span.job-search-card__location")
                            j_loc = loc_el.get_text(strip=True) if loc_el else loc
                            if f_wt == "&f_WT=2" and "remote" not in j_loc.lower() and "remoto" not in j_loc.lower():
                                j_loc = f"{j_loc} (Remote)"

                            time_el = c.select_one("time")
                            pub_date = ""
                            if time_el:
                                pub_date = (time_el.get("datetime") or time_el.get_text(strip=True) or "").strip()

                            job_id = href.rstrip("/").split("-")[-1]
                            ext_id = f"linkedin_{job_id}"

                            discovered.append({
                                "external_id": ext_id,
                                "portal": "LinkedIn",
                                "company": company or "Azienda Non Specificata",
                                "title": title,
                                "location": j_loc,
                                "url": href,
                                "published_at": pub_date,
                                "description": f"{title} presso {company or 'Azienda'} ({j_loc})",
                                "is_easy_apply": False,
                            })

                except Exception as e:
                    logger.warning(f"Errore caricamento feed LinkedIn ({kw}): {e}")

            # Arricchisci i primi 35 annunci con descrizione completa, seniority e link Recruiter (se presente)
            sem = asyncio.Semaphore(5)
            enrich_tasks = [
                self._enrich_linkedin_job_detail(client, j, headers, sem)
                for j in discovered[:35]
            ]
            if enrich_tasks:
                await asyncio.gather(*enrich_tasks, return_exceptions=True)

        logger.info(f"[LinkedIn] Scansione completata: estratti {len(discovered)} annunci reali con filtri nativi!")
        return discovered

    async def apply_easy_apply(self, job: Dict[str, Any]) -> bool:
        config = load_candidate_config()
        profile = config.get("candidate_profile", {})
        cv_path: Path = BASE_DIR / profile.get("cv_pdf_path", "data/cv.pdf")

        mgr = StealthBrowserManager(profile_name="linkedin")
        async with mgr:
            page = await mgr.new_stealth_page()
            await page.goto(job["url"], wait_until="domcontentloaded")
            await human_delay(3.0, 5.0)

            apply_btn = page.locator("button.jobs-apply-button").first
            if await apply_btn.count() == 0:
                return False

            await apply_btn.click()
            await human_delay(2.5, 4.0)

            for _ in range(8):
                modal = page.locator("div.jobs-easy-apply-modal").first
                if await modal.count() == 0:
                    break

                file_input = modal.locator('input[type="file"]')
                if await file_input.count() > 0 and cv_path.exists():
                    try:
                        await file_input.first.set_input_files(str(cv_path))
                        await human_delay(1.5, 2.5)
                    except Exception:
                        pass

                text_inputs = modal.locator('input[type="text"], input[type="number"], textarea')
                for i in range(await text_inputs.count()):
                    inp = text_inputs.nth(i)
                    val = await inp.input_value()
                    if not val.strip():
                        inp_id = await inp.get_attribute("id")
                        label_text = "Campo richiesto"
                        if inp_id:
                            lbl = modal.locator(f'label[for="{inp_id}"]')
                            if await lbl.count() > 0:
                                label_text = (await lbl.first.inner_text()).strip()
                        if any(w in label_text.lower() for w in ["phone", "telefono", "cellulare"]):
                            answer = profile.get("phone", "")
                        else:
                            answer = await self.ai_matcher.answer_application_question(label_text)
                        await inp.fill(str(answer))
                        await human_delay(0.5, 1.2)

                submit_btn = modal.locator('button[aria-label*="Invia"], button[aria-label*="Submit"]').first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                    await human_delay(3.0, 4.5)
                    return True

                next_btn = modal.locator('button[aria-label*="Avanti"], button[aria-label*="Continue"], button[aria-label*="Rivedi"], button[aria-label*="Review"]').first
                if await next_btn.count() > 0:
                    await next_btn.click()
                    await human_delay(2.0, 3.5)
                else:
                    break

        return False
