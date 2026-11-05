import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any
import httpx

from app.config import settings
from app.scrapers.xray_signal_scraper import build_networking_links

logger = logging.getLogger("JobAgent.Notifier")


class NotificationManager:
    """
    Gestisce l'invio di notifiche tramite:
    1. Bot Telegram con pulsanti interattivi (Approva Invio CV / Ignora / Apri Annuncio)
    2. Email SMTP (opzionale)
    """

    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.telegram_enabled = bool(
            self.bot_token
            and self.chat_id
            and self.bot_token != "inserisci_token_bot_telegram"
        )

    async def notify_job(self, job: Dict[str, Any], job_db_id: int, auto_applied: bool = False):
        score = job.get("match_score", 0)
        status_badge = (
            "✅ CANDIDATURA INVIATA AUTOMATICAMENTE"
            if auto_applied
            else "🟡 IN ATTESA DI TUA CONFERMA (Grado di fedeltà medio-alto)"
        )

        company = job.get("company") or "Azienda"
        net = build_networking_links(company)

        text = (
            f"🎯 *Nuova Offerta in Linea ({score}% Fedeltà CV)*\n"
            f"*{status_badge}*\n\n"
            f"🏢 *Azienda:* {company}\n"
            f"💼 *Ruolo:* {job.get('title')}\n"
            f"📍 *Località:* {job.get('location')} ({job.get('portal')})\n"
            f"💰 *RAL:* {job.get('estimated_ral', 'Non specificata')}\n"
            f"⚡ *Candidatura Rapida/ATS:* {'Sì' if job.get('is_easy_apply') else 'Link Diretto'}\n\n"
            f"🟢 *Skill in linea:* {', '.join(job.get('matching_skills', [])[:6]) or 'N/D'}\n"
            f"🟠 *Extra richiesti nell'offerta:* {', '.join(job.get('missing_skills', [])[:5]) or 'Nessuno rilevante'}\n\n"
            f"🧠 *Analisi Gemini AI:*\n_{job.get('ai_reasoning', '')}_\n\n"
            f"✉️ *Messaggio di accompagnamento pronto:*\n`{job.get('tailored_cover_note', '')}`\n\n"
            f"🕵️‍♂️ *Reverse Headhunting & Referral ({net['clean_company']}):*\n"
            f"• 🎯 [Cerca CTO / Hiring Manager]({net['hm_url']})\n"
            f"• 🤝 [Cerca Colleghi nel Team (Referral)]({net['ref_url']})\n"
            f"• 👩‍💼 [Cerca Recruiter #Hiring]({net['hr_url']}) • 🌐 [X-Ray Profili]({net['xray_url']})\n\n"
            f"🔗 [Apri Offerta Originale]({job.get('url')})"
        )

        if self.telegram_enabled:
            await self._send_telegram(text, job_db_id, show_buttons=not auto_applied, net_links=net)

        if settings.SMTP_ENABLED:
            self._send_email(
                subject=f"[{score}% Match] {job.get('title')} @ {company}",
                body=text.replace("*", "").replace("_", "").replace("`", ""),
            )

    async def _send_telegram(self, text: str, job_db_id: int, show_buttons: bool = True, net_links: Dict[str, str] = None):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        keyboard = []
        if show_buttons:
            keyboard.append([
                {
                    "text": "🚀 Approva e Invia CV",
                    "callback_data": f"apply_{job_db_id}",
                },
                {
                    "text": "❌ Scarta",
                    "callback_data": f"reject_{job_db_id}",
                },
            ])
        if net_links:
            keyboard.append([
                {"text": "🎯 CTO / Hiring Manager", "url": net_links["hm_url"]},
                {"text": "🤝 Referral Team", "url": net_links["ref_url"]},
            ])
            keyboard.append([
                {"text": "👩‍💼 Recruiter #Hiring", "url": net_links["hr_url"]},
                {"text": "🌐 X-Ray Google", "url": net_links["xray_url"]},
            ])

        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning(f"Telegram API warning: {resp.text}")
        except Exception as e:
            logger.error(f"Errore invio notifica Telegram: {e}")

    async def send_simple_telegram_message(self, message: str, reply_markup: dict = None):
        if not self.telegram_enabled:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(url, json=payload)
        except Exception as e:
            logger.error(f"Errore messaggio Telegram: {e}")

    def _send_email(self, subject: str, body: str):
        try:
            msg = MIMEMultipart()
            msg["From"] = settings.SMTP_USER
            msg["To"] = settings.NOTIFICATION_EMAIL
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
            logger.info(f"Email inviata a {settings.NOTIFICATION_EMAIL}")
        except Exception as e:
            logger.error(f"Errore invio Email SMTP: {e}")
