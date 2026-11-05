import logging
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any
import httpx
from bs4 import BeautifulSoup

from app.config import BASE_DIR, load_candidate_config
from app.stealth_browser import StealthBrowserManager, human_delay
from app.ai_matcher import GeminiMatcher

logger = logging.getLogger("JobAgent.LinkedIn")


class LinkedInPortal:
    def __init__(self, ai_matcher: GeminiMatcher):
        self.ai_matcher = ai_matcher

    async def scrape_jobs(self) -> List[Dict[str, Any]]:
        """
        Motore ad alte prestazioni per LinkedIn:
        Interroga dinamicamente il feed annunci per i ruoli, le città e le modalità
        configurate dall'utente tramite Telegram o JSON.
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
        work_modes: List[str] = [
            m.lower() for m in (search_prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])
        ]

        # Costruisci le località da interrogare su LinkedIn in base a città e modalità scelte
        search_locations: List[str] = []
        if "ibrido" in work_modes or "in_sede" in work_modes or "insede" in work_modes:
            for c in target_cities:
                if c.strip():
                    search_locations.append(c.strip())
        if "remoto" in work_modes or "remote" in work_modes:
            search_locations.append("Remote Italia")
        if not search_locations:
            search_locations = target_cities or ["Italia"]

        # Costruisci query Booleane anti-spam + query dirette a partire dai ruoli scelti dall'utente
        queries: List[tuple] = []
        chunk_size = 3
        for loc in search_locations[:5]:
            for i in range(0, min(len(keywords), 9), chunk_size):
                group = keywords[i : i + chunk_size]
                or_clause = " OR ".join(f'"{k}"' if " " in k else k for k in group)
                bool_q = f"({or_clause}) NOT (Senior OR Lead OR Manager OR Director OR Stage)"
                queries.append((bool_q, loc))
            # Aggiungi anche le prime keyword dirette per massima copertura
            for kw in keywords[:4]:
                queries.append((kw, loc))

        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        time_range = li_conf.get("time_filter", "month")
        tpr_map = {
            "today": "&f_TPR=r86400",
            "week": "&f_TPR=r604800",
            "month": "&f_TPR=r2592000",
            "all": "",
        }
        tpr_param = tpr_map.get(time_range, "&f_TPR=r2592000")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        }

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for kw, loc in queries:
                url = (
                    f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
                    f"keywords={urllib.parse.quote(kw)}&location={urllib.parse.quote(loc)}{tpr_param}&start=0"
                )
                logger.info(f"[LinkedIn Feed] Ricerca '{kw}' in '{loc}' (periodo: {time_range})...")

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
                            company = comp_el.get_text(strip=True) if comp_el else "LinkedIn Company"

                            loc_el = c.select_one("span.job-search-card__location")
                            j_loc = loc_el.get_text(strip=True) if loc_el else loc

                            job_id = href.rstrip("/").split("-")[-1]
                            ext_id = f"linkedin_{job_id}"

                            discovered.append({
                                "external_id": ext_id,
                                "portal": "LinkedIn",
                                "company": company,
                                "title": title,
                                "location": j_loc,
                                "url": href,
                                "description": f"{title} presso {company} ({j_loc})",
                                "is_easy_apply": False,
                            })

                except Exception as e:
                    logger.warning(f"Errore caricamento feed LinkedIn ({kw}): {e}")

        logger.info(f"[LinkedIn] Scansione completata: estratti {len(discovered)} annunci reali!")
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
