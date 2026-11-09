import asyncio
import logging
from typing import Dict, Any, List

from app.config import settings, load_candidate_config
from app.database import (
    is_job_seen,
    normalize_job_url,
    save_job,
    update_job_status,
    get_job_by_id,
)
from app.ai_matcher import GeminiMatcher
from app.scrapers.linkedin_scraper import LinkedInPortal
from app.scrapers.multinationals_scraper import MultinationalsScraper
from app.scrapers.xray_signal_scraper import XRayScraper, SignalScout, get_boolean_xray_radar_links
from app.notifier import NotificationManager
from app.site_catalog import get_mapped_sites

logger = logging.getLogger("JobAgent.Orchestrator")


class JobAgentOrchestrator:
    def __init__(self):
        self.ai_matcher = GeminiMatcher()
        self.linkedin = LinkedInPortal(self.ai_matcher)
        self.multinationals = MultinationalsScraper(self.ai_matcher)
        self.xray = XRayScraper()
        self.signal_scout = SignalScout()
        self.notifier = NotificationManager()
        self.is_running = False

    async def _evaluate_and_process_job(
        self, raw_job: Dict[str, Any], semaphore: asyncio.Semaphore, stats: Dict[str, int]
    ):
        """Elabora una singola offerta in concorrenza limitata con semaforo."""
        async with semaphore:
            if getattr(self, "stop_requested", False):
                return
            ext_id = raw_job["external_id"]
            job_url = raw_job.get("url", "")
            if await is_job_seen(ext_id, job_url):
                return

            if len(raw_job.get("description", "")) < 200 and raw_job.get("url", "").startswith("http"):
                try:
                    import httpx
                    from bs4 import BeautifulSoup
                    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                        r = await client.get(raw_job["url"], headers={"User-Agent": "Mozilla/5.0"})
                        if r.status_code == 200:
                            soup = BeautifulSoup(r.text, "html.parser")
                            for s in soup(["script", "style", "nav", "footer"]):
                                s.decompose()
                            clean_txt = " ".join(soup.stripped_strings)
                            if len(clean_txt) > 200:
                                raw_job["description"] = clean_txt[:5000]
                except Exception:
                    pass

            stats["new_evaluated"] += 1
            logger.info(
                f"[AI Evaluate] {raw_job['title']} @ {raw_job['company']} ({raw_job['portal']})"
            )

            eval_res = await self.ai_matcher.evaluate_job(raw_job)
            score = int(eval_res.get("match_score", 0))
            dealbreaker = bool(eval_res.get("is_dealbreaker_missing", False))

            raw_job.update(eval_res)

            if dealbreaker or score < settings.REVIEW_THRESHOLD:
                raw_job["status"] = "SKIPPED_LOW_SCORE"
                await save_job(raw_job)
                stats["skipped_low_score"] += 1
                return

            if score >= settings.AUTO_APPLY_THRESHOLD:
                applied_ok = await self.execute_application(raw_job)
                raw_job["status"] = "AUTO_APPLIED" if applied_ok else "PENDING_APPROVAL"
                job_db_id = await save_job(raw_job)
                if job_db_id is not None:
                    await self.notifier.notify_job(
                        raw_job, job_db_id=job_db_id, auto_applied=applied_ok
                    )
                    if applied_ok:
                        stats["auto_applied"] += 1
                    else:
                        stats["pending_approval"] += 1
            else:
                raw_job["status"] = "PENDING_APPROVAL"
                job_db_id = await save_job(raw_job)
                if job_db_id is not None:
                    await self.notifier.notify_job(
                        raw_job, job_db_id=job_db_id, auto_applied=False
                    )
                    stats["pending_approval"] += 1

    async def run_hourly_cycle(self, portal_filter: str = "all") -> Dict[str, int]:
        if self.is_running:
            logger.info("Un ciclo di ricerca è già in esecuzione, salto questo turno.")
            return {"status": "already_running"}

        self.is_running = True
        self.stop_requested = False
        stats = {
            "discovered_total": 0,
            "new_evaluated": 0,
            "auto_applied": 0,
            "pending_approval": 0,
            "skipped_low_score": 0,
        }

        try:
            logger.info(f"=== AVVIO CICLO RICERCA (Target: {portal_filter.upper()}) ===")
            cfg = load_candidate_config()
            prefs = cfg.get("search_preferences", {})
            cities = ", ".join(prefs.get("target_cities") or ["Italia"])
            modes = ", ".join(prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])
            num_portals = len(get_mapped_sites())

            mode_labels = {
                "linkedin": "Solo LinkedIn (Query Booleane)",
                "xray": "Solo X-Ray ATS & Post Hiring Manager",
                "all": f"Completa (LinkedIn Booleano + X-Ray ATS + {num_portals} Portali)",
            }
            start_msg = (
                f"🚀 *Avvio scansione!*\n"
                f"• Modalità: *{mode_labels.get(portal_filter, portal_filter)}*\n"
                f"• Area: *{cities}* ({modes})\n"
                f"• Ricerca nuove posizioni in corso..."
            )
            await self.notifier.send_simple_telegram_message(start_msg)

            tasks = []
            if portal_filter in ["all", "linkedin"]:
                tasks.append(asyncio.create_task(self.linkedin.scrape_jobs()))
            if portal_filter in ["all", "xray"]:
                tasks.append(asyncio.create_task(self.xray.scrape_xray_jobs()))
            if portal_filter == "all":
                tasks.append(asyncio.create_task(self.multinationals.scrape_all()))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            if getattr(self, "stop_requested", False):
                logger.info("Scansione interrotta dall'utente prima della valutazione AI.")
                await self.notifier.send_simple_telegram_message("🛑 *Scansione interrotta dall'utente.*")
                return stats

            all_found: List[Dict[str, Any]] = []
            seen_batch_urls = set()
            for res in results:
                if isinstance(res, list):
                    for job_item in res:
                        norm_u = normalize_job_url(job_item.get("url", "")) or job_item.get("external_id", "")
                        if norm_u and norm_u not in seen_batch_urls:
                            seen_batch_urls.add(norm_u)
                            all_found.append(job_item)
                elif isinstance(res, Exception):
                    logger.error(f"Errore in un canale di scraping: {res}")

            stats["discovered_total"] = len(all_found)
            logger.info(f"Totale annunci raccolti: {len(all_found)}. Avvio valutazione AI concorrente...")

            semaphore = asyncio.Semaphore(3)
            eval_tasks = [
                self._evaluate_and_process_job(job, semaphore, stats)
                for job in all_found
            ]

            await asyncio.gather(*eval_tasks, return_exceptions=True)

            logger.info(f"=== CICLO COMPLETATO: {stats} ===")

            summary_msg = (
                "🏁 *Scansione Completata con Successo!*\n\n"
                f"• Canale: *{portal_filter.upper()}*\n"
                f"• Annunci totali raccolti: *{stats['discovered_total']}*\n"
                f"• Nuovi annunci valutati con AI: *{stats['new_evaluated']}*\n"
                f"• Posizioni in linea inviate per approvazione: *{stats['pending_approval']}*\n"
                f"• Candidature automatiche inviate: *{stats['auto_applied']}*\n"
                f"• Annunci scartati (basso match/dealbreaker): *{stats['skipped_low_score']}*"
            )
            await self.notifier.send_simple_telegram_message(summary_msg)
            return stats
        finally:
            self.is_running = False

    async def execute_application(self, job: Dict[str, Any]) -> bool:
        portal = job.get("portal", "").lower()
        if "linkedin" in portal and job.get("is_easy_apply"):
            return await self.linkedin.apply_easy_apply(job)
        if "greenhouse" in portal or job.get("is_easy_apply"):
            return await self.multinationals.apply_standard_ats_form(job)
        return False

    async def approve_and_apply_job(self, job_id: int) -> bool:
        job = await get_job_by_id(job_id)
        if not job:
            return False

        success = await self.execute_application(job)
        if success:
            await update_job_status(job_id, "APPROVED_APPLIED")
            await self.notifier.send_simple_telegram_message(
                f"✅ Candidatura inviata con successo per: {job['title']} @ {job['company']}!"
            )
            return True
        else:
            await update_job_status(job_id, "MANUAL_LINK_REQUIRED")
            await self.notifier.send_simple_telegram_message(
                f"ℹ️ Il portale di {job['company']} richiede la conferma finale sul sito.\n"
                f"Ecco il link diretto già pronto: {job['url']}"
            )
            return False

    async def run_signal_scouting(self, ignore_seen: bool = True) -> int:
        cfg = load_candidate_config()
        cities = ", ".join(cfg.get("search_preferences", {}).get("target_cities") or ["Italia"])
        await self.notifier.send_simple_telegram_message(
            f"🕵️‍♂️ *Avvio Signal-Based Scouting (Mercato Nascosto: {cities})*\n"
            "Ricerca di round di finanziamento, nuovi progetti tech/AI, espansioni e startup in corso..."
        )
        signals = await self.signal_scout.find_market_signals(ignore_seen=ignore_seen)
        if not signals:
            await self.notifier.send_simple_telegram_message(
                f"ℹ️ Nessun nuovo segnale di espansione rilevato nelle ultime ore per: {cities}."
            )
            return 0

        for sig in signals:
            has_net = bool(sig.get("hm_url"))
            net_text = ""
            if has_net:
                net_text = (
                    f"• 🎯 [Trova CTO / Hiring Manager]({sig['hm_url']})\n"
                    f"• 🤝 [Trova Colleghi nel Team]({sig['ref_url']})\n"
                    f"• 🌐 [X-Ray Google senza limiti]({sig['xray_url']})\n"
                )
            msg = (
                f"📡 *SEGNALE MERCATO NASCOSTO ({cities})*\n\n"
                f"🏢 *Realtà / Hub:* {sig['entity']}\n"
                f"📰 *Notizia:* {sig['title']}\n"
                f"🗓 *Data:* {sig['pub_date']}\n\n"
                f"💡 *Azione consigliata (Reverse Headhunting):*\n"
                f"Contatta direttamente i decisori tecnici o chiedi un referral interno citando questa iniziativa prima che aprano le posizioni ufficiali!\n\n"
                f"{net_text}"
                f"• 🔗 [Leggi Notizia Completa]({sig['url']})"
            )
            rows = []
            if has_net:
                rows.append([
                    {"text": "🎯 CTO / Hiring Manager", "url": sig["hm_url"]},
                    {"text": "🤝 Team / Referral", "url": sig["ref_url"]},
                ])
                rows.append([
                    {"text": "🌐 X-Ray Profili", "url": sig["xray_url"]},
                    {"text": "📰 Apri Notizia", "url": sig["url"]},
                ])
            else:
                rows.append([{"text": "📰 Apri Notizia", "url": sig["url"]}])
            kb = {"inline_keyboard": rows}
            await self.notifier.send_simple_telegram_message(msg, reply_markup=kb)
            await asyncio.sleep(0.5)

        radar = get_boolean_xray_radar_links()
        radar_msg = (
            f"🧭 *Radar Booleano & Google X-Ray ({cities})*\n\n"
            "Usa questi pulsanti con query Booleane già configurate per intercettare i post informali "
            "degli Hiring Manager su LinkedIn o esplorare direttamente gli ATS:"
        )
        radar_kb = {
            "inline_keyboard": [
                [
                    {"text": "📝 Post: 'Stiamo cercando'", "url": radar["post_hm_url"]},
                    {"text": "📣 Post: 'Cerco per il team'", "url": radar["post_ref_url"]},
                ],
                [
                    {"text": "🎯 CTO & Tech Lead", "url": radar["people_hm_url"]},
                    {"text": "👩‍💼 Recruiter #Hiring", "url": radar["people_hr_url"]},
                ],
                [
                    {"text": "🌐 X-Ray Greenhouse/Lever", "url": radar["xray_ats_url"]},
                    {"text": "🏢 X-Ray Workday", "url": radar["xray_wd_url"]},
                ],
            ]
        }
        await self.notifier.send_simple_telegram_message(radar_msg, reply_markup=radar_kb)

        return len(signals)
