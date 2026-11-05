import sys
import asyncio
import logging
from contextlib import asynccontextmanager

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

from app.config import settings, load_candidate_config, save_candidate_config, DATA_DIR
from app.database import init_db, get_recent_jobs, update_job_status
from app.orchestrator import JobAgentOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("JobAgent.Server")

orchestrator = JobAgentOrchestrator()
scheduler = AsyncIOScheduler()
telegram_polling_task = None

PRESET_ROLES = [
    "AI Engineer",
    "Machine Learning Engineer",
    "Data Scientist",
    "Python Developer",
    "Software Engineer",
    "Data Engineer",
    "Computer Vision",
    "NLP Engineer",
    "Backend Developer",
    "Full Stack Developer",
    "DevOps Engineer",
    "Cybersecurity",
]

PRESET_CITIES = [
    "Milano",
    "Roma",
    "Torino",
    "Bologna",
    "Firenze",
    "Napoli",
    "Padova",
    "Genova",
    "Bari",
    "Italia",
]

WORK_MODE_LABELS = {
    "remoto": "🏠 Remoto",
    "ibrido": "🔄 Ibrido",
    "in_sede": "🏢 In Sede",
}


async def send_telegram_reply(
    client: httpx.AsyncClient,
    chat_id: str,
    text: str,
    reply_markup: dict = None,
    edit_message_id: int = None,
):
    token = settings.TELEGRAM_BOT_TOKEN
    if edit_message_id:
        url = f"https://api.telegram.org/bot{token}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": edit_message_id,
            "text": text,
            "parse_mode": "Markdown",
        }
    else:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200 and edit_message_id:
            # Fallback to sendMessage if edit fails
            url_send = f"https://api.telegram.org/bot{token}/sendMessage"
            payload.pop("message_id", None)
            await client.post(url_send, json=payload)
    except Exception as e:
        logger.warning(f"Errore invio risposta Telegram: {e}")


def build_status_summary_text() -> str:
    cfg = load_candidate_config()
    p = cfg.get("candidate_profile", {})
    prefs = cfg.get("search_preferences", {})
    li = cfg.get("target_portals", {}).get("linkedin", {})

    keywords = prefs.get("keywords", []) or ["AI Engineer"]
    cities = prefs.get("target_cities", []) or [p.get("city", "Italia")]
    modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])
    modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
    tf_labels = {"today": "Oggi (24h)", "week": "Settimana", "month": "Mese", "all": "Tutto"}
    tf = li.get("time_filter", "month")

    return (
        "⚙️ *Pannello di Controllo & Configurazione Agente*\n\n"
        f"💼 *Posizioni Cercate ({len(keywords)}):*\n`{', '.join(keywords)}`\n\n"
        f"📍 *Area di Ricerca ({len(cities)}):*\n`{', '.join(cities)}`\n\n"
        f"🏠 *Modalità Lavoro:* {modes_str}\n"
        f"💰 *RAL Minima:* *{p.get('expected_salary_ral', '30000')}€*\n"
        f"⏳ *Esperienza Minima:* *{p.get('min_years_experience', 0)} anni*\n"
        f"📅 *Filtro Data LinkedIn:* *{tf_labels.get(tf, tf)}*\n"
        f"🎯 *Soglia Match Notifica:* *{settings.REVIEW_THRESHOLD}%*\n\n"
        "Usa i pulsanti qui sotto per modificare ruoli, città/modalità o avviare scansioni:"
    )


def build_help_message() -> str:
    cfg = load_candidate_config()
    prefs = cfg.get("search_preferences", {})
    keywords = prefs.get("keywords", []) or ["AI Engineer"]
    cities = prefs.get("target_cities", []) or ["Italia"]
    modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])

    return (
        "📖 *GUIDA COMPLETA COMANDI - AI JOB HUNTER BOT*\n\n"
        "Da questo bot puoi controllare l'intero agente, modificare in tempo reale le posizioni cercate, "
        "le città (singola o multi-zona) e la modalità di lavoro (Remoto, Ibrido, In Sede).\n\n"
        "🔹 *1. GESTIONE POSIZIONI / RUOLI CERCATI*\n"
        "• `/ruoli` — Apre il menu interattivo con pulsanti per attivare/disattivare i ruoli\n"
        "• `/add_ruolo <nome>` — Aggiunge una posizione (es. `/add_ruolo Data Analyst`)\n"
        "• `/del_ruolo <nome>` — Rimuove una posizione (es. `/del_ruolo Data Analyst`)\n"
        "• `/set_ruoli <r1, r2, ...>` — Imposta da zero la lista dei ruoli separati da virgola\n"
        f"  _Attuali:_ `{', '.join(keywords)}`\n\n"
        "🔹 *2. GESTIONE AREA GEOGRAFICA E MODALITÀ (MULTI-RANGE)*\n"
        "• `/zone` — Apre il menu interattivo multi-selezione per Città (Milano, Roma, Bologna, ecc.) e Modalità (Remoto, Ibrido, In Sede)\n"
        "• `/add_zona <città>` — Aggiunge una città al range (es. `/add_zona Milano`)\n"
        "• `/del_zona <città>` — Rimuove una città dal range (es. `/del_zona Roma`)\n"
        "• `/set_zone <c1, c2, ...>` — Sostituisce le città (es. `/set_zone Milano, Roma, Bologna`)\n"
        "• `/modalita` — Apre il selettore rapido Remoto / Ibrido / In Sede\n"
        "• `/set_modalita <m1, m2>` — Imposta le modalità (es. `/set_modalita remoto, ibrido`)\n"
        f"  _Zone attuali:_ `{', '.join(cities)}` | _Modalità:_ `{', '.join(modes)}`\n\n"
        "🔹 *3. AVVIO E ARRESTO SCANSIONI*\n"
        "• `/scan` — Avvia la scansione completa (LinkedIn + X-Ray + 50 portali aziendali)\n"
        "• `/scan_li` — Avvia la scansione Booleana solo su LinkedIn\n"
        "• `/xray` — Cerca negli ATS nascosti (Workday, Greenhouse, Lever) e nei Post degli Hiring Manager\n"
        "• `/scout` — Signal Scouting (notizie su round di finanziamento, nuove aperture, appalti)\n"
        "• `/stop` — Interrompe immediatamente qualsiasi scansione in corso\n\n"
        "🔹 *4. FILTRI E PARAMETRI CANDIDATO*\n"
        "• `/config` — Apre il pannello di controllo principale con tutti i bottoni\n"
        "• `/set_ral <cifra>` — Imposta la RAL minima desiderata (es. `/set_ral 32000`)\n"
        "• `/set_exp <anni>` — Imposta gli anni minimi di esperienza (es. `/set_exp 0`)\n"
        "• `/set_min <10-95>` — Imposta la % minima di compatibilità CV per ricevere notifica\n"
        "• `/status` — Mostra le statistiche del database e lo stato dell'agente\n"
        "• `/help` — Mostra questa guida completa in qualsiasi momento"
    )


def get_config_keyboard():
    cfg = load_candidate_config()
    profile = cfg.get("candidate_profile", {})
    prefs = cfg.get("search_preferences", {})
    li_conf = cfg.get("target_portals", {}).get("linkedin", {})

    ral = profile.get("expected_salary_ral", "30000")
    min_exp = profile.get("min_years_experience", 0)
    time_filter = li_conf.get("time_filter", "month")
    tf_labels = {"today": "Oggi (24h)", "week": "Settimana", "month": "Mese", "all": "Tutto"}

    n_roles = len(prefs.get("keywords", []) or [])
    cities = prefs.get("target_cities", []) or [profile.get("city", "Italia")]
    cities_short = ", ".join(cities[:2]) + (f" +{len(cities)-2}" if len(cities) > 2 else "")

    is_running = orchestrator.is_running
    scan_rows = (
        [[{"text": "🛑 STOP Scansione in Corso", "callback_data": "cfg_stop_scan"}]]
        if is_running
        else [
            [
                {"text": "🚀 Solo LinkedIn", "callback_data": "cfg_scan_li"},
                {"text": "🔎 X-Ray ATS & Post", "callback_data": "cfg_scan_xray"},
            ],
            [
                {"text": "🌐 Scansione Completa (Tutti)", "callback_data": "cfg_scan_all"},
                {"text": "🕵️‍♂️ Segnali (/scout)", "callback_data": "cfg_scout"},
            ],
        ]
    )

    return {
        "inline_keyboard": [
            [
                {"text": f"💼 Posizioni Cercate ({n_roles})", "callback_data": "cfg_menu_roles"},
                {"text": f"📍 Zone: {cities_short}", "callback_data": "cfg_menu_zones"},
            ],
            [
                {"text": f"💰 RAL Min: {ral}€", "callback_data": "cfg_prompt_ral"},
                {"text": f"⏳ Exp Min: {min_exp} anni", "callback_data": "cfg_prompt_exp"},
            ],
            [
                {"text": f"📅 LinkedIn: {tf_labels.get(time_filter, time_filter)}", "callback_data": "cfg_menu_time"},
            ],
            *scan_rows,
            [
                {"text": "📖 Guida Comandi (/help)", "callback_data": "cfg_show_help"},
                {"text": "🔄 Aggiorna Stato", "callback_data": "cfg_refresh"},
            ],
        ]
    }


def get_roles_keyboard():
    cfg = load_candidate_config()
    prefs = cfg.get("search_preferences", {})
    current_roles = prefs.get("keywords", []) or []
    current_lower = {r.lower(): r for r in current_roles}

    rows = []
    # 2 buttons per row for preset roles
    for i in range(0, len(PRESET_ROLES), 2):
        row = []
        for idx in range(i, min(i + 2, len(PRESET_ROLES))):
            role_name = PRESET_ROLES[idx]
            active = role_name.lower() in current_lower
            prefix = "✅" if active else "⬜"
            row.append({"text": f"{prefix} {role_name}", "callback_data": f"role_tog_{idx}"})
        rows.append(row)

    # Show custom roles (not in PRESET_ROLES) so user can remove them with a tap
    preset_lower = {r.lower() for r in PRESET_ROLES}
    custom_roles = [r for r in current_roles if r.lower() not in preset_lower]
    for c_role in custom_roles[:6]:
        safe_cb = f"role_del_{c_role[:20]}"
        rows.append([{"text": f"✅ {c_role} (tocca per rimuovere)", "callback_data": safe_cb}])

    rows.append([
        {"text": "➕ Aggiungi Ruolo Custom", "callback_data": "role_prompt_add"},
        {"text": "✏️ Sostituisci Tutti", "callback_data": "role_prompt_set"},
    ])
    rows.append([
        {"text": "⬅️ Torna al Pannello (/config)", "callback_data": "cfg_refresh"},
    ])
    return {"inline_keyboard": rows}


def get_zones_keyboard():
    cfg = load_candidate_config()
    prefs = cfg.get("search_preferences", {})
    current_cities = prefs.get("target_cities", []) or []
    current_cities_lower = {c.lower() for c in current_cities}
    current_modes = [m.lower() for m in (prefs.get("work_modes", []) or ["remoto", "ibrido", "in_sede"])]

    rows = []
    # Work modes row (Remoto, Ibrido, In Sede)
    mode_row = []
    for mode_key, mode_label in [
        ("remoto", "Remoto"),
        ("ibrido", "Ibrido"),
        ("in_sede", "In Sede"),
    ]:
        active = mode_key in current_modes
        prefix = "✅" if active else "⬜"
        mode_row.append({"text": f"{prefix} {mode_label}", "callback_data": f"mode_tog_{mode_key}"})
    rows.append(mode_row)

    # Cities rows (2 per row)
    for i in range(0, len(PRESET_CITIES), 2):
        row = []
        for idx in range(i, min(i + 2, len(PRESET_CITIES))):
            city_name = PRESET_CITIES[idx]
            active = city_name.lower() in current_cities_lower
            prefix = "✅" if active else "⬜"
            row.append({"text": f"{prefix} {city_name}", "callback_data": f"city_tog_{idx}"})
        rows.append(row)

    # Custom cities not in PRESET_CITIES
    preset_cities_lower = {c.lower() for c in PRESET_CITIES}
    custom_cities = [c for c in current_cities if c.lower() not in preset_cities_lower]
    for c_city in custom_cities[:6]:
        rows.append([{"text": f"✅ 📍 {c_city} (tocca per rimuovere)", "callback_data": f"city_del_{c_city[:20]}"}])

    rows.append([
        {"text": "➕ Aggiungi Altra Città", "callback_data": "zone_prompt_add"},
        {"text": "✏️ Imposta Lista Città", "callback_data": "zone_prompt_set"},
    ])
    rows.append([
        {"text": "⬅️ Torna al Pannello (/config)", "callback_data": "cfg_refresh"},
    ])
    return {"inline_keyboard": rows}


def get_time_filter_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "⚡ Solo Oggi (Ultime 24h)", "callback_data": "set_tf_today"},
                {"text": "📅 Ultima Settimana", "callback_data": "set_tf_week"},
            ],
            [
                {"text": "🗓 Ultimo Mese", "callback_data": "set_tf_month"},
                {"text": "♾ Qualsiasi Data", "callback_data": "set_tf_all"},
            ],
            [
                {"text": "⬅️ Torna a Configurazione", "callback_data": "cfg_refresh"},
            ],
        ]
    }


async def register_telegram_commands_and_welcome():
    """
    Registra il menu comandi nativo su Telegram (setMyCommands) e invia
    il messaggio di benvenuto con la spiegazione dei comandi all'avvio.
    """
    if not orchestrator.notifier.telegram_enabled:
        return
    token = settings.TELEGRAM_BOT_TOKEN
    commands = [
        {"command": "help", "description": "Guida completa a tutti i comandi del bot"},
        {"command": "config", "description": "Pannello interattivo di configurazione e avvio"},
        {"command": "ruoli", "description": "Modifica le posizioni lavorative cercate"},
        {"command": "zone", "description": "Modifica città (Torino, Milano, ecc.) e Ibrido/Remoto"},
        {"command": "modalita", "description": "Seleziona modalità Remoto, Ibrido o In Sede"},
        {"command": "scan", "description": "Avvia scansione completa (LinkedIn + X-Ray + 50 portali)"},
        {"command": "scan_li", "description": "Avvia scansione solo su LinkedIn"},
        {"command": "xray", "description": "Scova offerte in ATS nascosti e Post Hiring Manager"},
        {"command": "scout", "description": "Signal Scouting su espansioni e finanziamenti aziende"},
        {"command": "stop", "description": "Ferma immediatamente la scansione in corso"},
        {"command": "set_ral", "description": "Imposta la RAL minima (es. /set_ral 32000)"},
        {"command": "set_exp", "description": "Imposta gli anni minimi di esperienza (es. /set_exp 0)"},
        {"command": "set_min", "description": "Imposta la % minima di match CV (es. /set_min 75)"},
        {"command": "status", "description": "Mostra statistiche e configurazione attuale"},
    ]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/setMyCommands",
                json={"commands": commands},
            )
            welcome_msg = (
                "🟢 *AI Job Hunter Bot Avviato e Pronto!*\n\n"
                + build_help_message()
            )
            await send_telegram_reply(
                client,
                str(settings.TELEGRAM_CHAT_ID),
                welcome_msg,
                reply_markup=get_config_keyboard(),
            )
    except Exception as e:
        logger.warning(f"Impossibile registrare i comandi Telegram all'avvio: {e}")


async def poll_telegram_callbacks():
    """
    Gestisce in background:
    1. Click sui pulsanti interattivi (Approva/Scarta CV, Toggle Ruoli, Toggle Città/Modalità, Avvio/Stop Scansioni)
    2. Comandi di testo da chat (/help, /config, /ruoli, /zone, /modalita, /scan, /stop, ecc.)
    """
    if not orchestrator.notifier.telegram_enabled:
        return

    token = settings.TELEGRAM_BOT_TOKEN
    offset = 0
    logger.info("Polling comandi e bottoni Telegram attivo.")

    async with httpx.AsyncClient(timeout=35.0) as client:
        while True:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                resp = await client.get(url, params={"offset": offset, "timeout": 25})
                if resp.status_code == 200:
                    updates = resp.json().get("result", [])
                    for upd in updates:
                        offset = upd["update_id"] + 1

                        # 1. Gestione click pulsanti inline
                        cb = upd.get("callback_query")
                        if cb:
                            data = cb.get("data", "")
                            cb_id = cb.get("id")
                            cb_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                            cb_msg_id = cb.get("message", {}).get("message_id")

                            if data.startswith("apply_"):
                                job_id = int(data.split("_")[1])
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Invio candidatura in corso..."},
                                )
                                asyncio.create_task(orchestrator.approve_and_apply_job(job_id))

                            elif data.startswith("reject_"):
                                job_id = int(data.split("_")[1])
                                await update_job_status(job_id, "REJECTED")
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Offerta scartata."},
                                )

                            elif data == "cfg_show_help":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    build_help_message(),
                                    reply_markup=get_config_keyboard(),
                                )

                            elif data == "cfg_menu_roles":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                cfg = load_candidate_config()
                                kws = cfg.get("search_preferences", {}).get("keywords", []) or []
                                text_roles = (
                                    "💼 *Gestione Posizioni / Ruoli Cercati*\n\n"
                                    f"*Attivi ({len(kws)}):* `{', '.join(kws) or 'Nessuno'}`\n\n"
                                    "Tocca i pulsanti per *attivare (✅) o disattivare (⬜)* i ruoli in tempo reale, "
                                    "oppure usa `/add_ruolo Nome Ruolo` per aggiungerne uno personalizzato:"
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    text_roles,
                                    reply_markup=get_roles_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                            elif data.startswith("role_tog_"):
                                idx = int(data.replace("role_tog_", ""))
                                if 0 <= idx < len(PRESET_ROLES):
                                    role_name = PRESET_ROLES[idx]
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    kws = prefs.get("keywords", []) or []
                                    existing_lower = [k.lower() for k in kws]
                                    if role_name.lower() in existing_lower:
                                        if len(kws) <= 1:
                                            await client.post(
                                                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                                json={"callback_query_id": cb_id, "text": "Deve rimanere almeno 1 ruolo attivo!"},
                                            )
                                            continue
                                        kws = [k for k in kws if k.lower() != role_name.lower()]
                                        action_msg = f"Rimosso: {role_name}"
                                    else:
                                        kws.append(role_name)
                                        action_msg = f"Aggiunto: {role_name}"
                                    prefs["keywords"] = kws
                                    save_candidate_config(cfg)
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": action_msg},
                                    )
                                    text_roles = (
                                        "💼 *Gestione Posizioni / Ruoli Cercati*\n\n"
                                        f"*Attivi ({len(kws)}):* `{', '.join(kws)}`\n\n"
                                        "Tocca i pulsanti per *attivare (✅) o disattivare (⬜)* i ruoli in tempo reale:"
                                    )
                                    await send_telegram_reply(
                                        client,
                                        cb_chat_id,
                                        text_roles,
                                        reply_markup=get_roles_keyboard(),
                                        edit_message_id=cb_msg_id,
                                    )

                            elif data.startswith("role_del_"):
                                prefix_to_del = data.replace("role_del_", "").strip().lower()
                                cfg = load_candidate_config()
                                prefs = cfg.setdefault("search_preferences", {})
                                kws = prefs.get("keywords", []) or []
                                new_kws = [k for k in kws if not k.lower().startswith(prefix_to_del)]
                                if not new_kws:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": "Deve rimanere almeno 1 ruolo attivo!"},
                                    )
                                    continue
                                prefs["keywords"] = new_kws
                                save_candidate_config(cfg)
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Ruolo rimosso"},
                                )
                                text_roles = (
                                    "💼 *Gestione Posizioni / Ruoli Cercati*\n\n"
                                    f"*Attivi ({len(new_kws)}):* `{', '.join(new_kws)}`\n\n"
                                    "Tocca i pulsanti per *attivare (✅) o disattivare (⬜)* i ruoli in tempo reale:"
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    text_roles,
                                    reply_markup=get_roles_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                            elif data == "role_prompt_add":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    "➕ Per aggiungere una posizione personalizzata, scrivi in chat:\n"
                                    "`/add_ruolo Nome Posizione`\n\n"
                                    "Esempio:\n`/add_ruolo Prompt Engineer`\n`/add_ruolo Embedded Developer`",
                                )

                            elif data == "role_prompt_set":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    "✏️ Per sostituire l'intera lista dei ruoli cercati, scrivi in chat i ruoli separati da virgola:\n"
                                    "`/set_ruoli AI Engineer, Data Scientist, Python Developer`",
                                )

                            elif data == "cfg_menu_zones":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                cfg = load_candidate_config()
                                prefs = cfg.get("search_preferences", {})
                                cities = prefs.get("target_cities", []) or ["Italia"]
                                modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])
                                modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
                                text_zones = (
                                    "📍 *Area di Ricerca & Modalità Lavoro (Multi-Range)*\n\n"
                                    f"🏙 *Città Attive ({len(cities)}):* `{', '.join(cities)}`\n"
                                    f"🏠 *Modalità Accettate:* {modes_str}\n\n"
                                    "Tocca più città o modalità per comporre il tuo range di ricerca (es. *Torino + Milano + Remoto/Ibrido*):"
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    text_zones,
                                    reply_markup=get_zones_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                            elif data.startswith("city_tog_"):
                                idx = int(data.replace("city_tog_", ""))
                                if 0 <= idx < len(PRESET_CITIES):
                                    city_name = PRESET_CITIES[idx]
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    cities = prefs.get("target_cities", []) or []
                                    existing_lower = [c.lower() for c in cities]
                                    if city_name.lower() in existing_lower:
                                        if len(cities) <= 1:
                                            await client.post(
                                                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                                json={"callback_query_id": cb_id, "text": "Seleziona almeno una zona o 'Italia'!"},
                                            )
                                            continue
                                        cities = [c for c in cities if c.lower() != city_name.lower()]
                                        action_msg = f"Zona rimossa: {city_name}"
                                    else:
                                        cities.append(city_name)
                                        action_msg = f"Zona aggiunta: {city_name}"
                                    prefs["target_cities"] = cities
                                    save_candidate_config(cfg)
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": action_msg},
                                    )
                                    modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])
                                    modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
                                    text_zones = (
                                        "📍 *Area di Ricerca & Modalità Lavoro (Multi-Range)*\n\n"
                                        f"🏙 *Città Attive ({len(cities)}):* `{', '.join(cities)}`\n"
                                        f"🏠 *Modalità Accettate:* {modes_str}\n\n"
                                        "Tocca più città o modalità per comporre il tuo range di ricerca:"
                                    )
                                    await send_telegram_reply(
                                        client,
                                        cb_chat_id,
                                        text_zones,
                                        reply_markup=get_zones_keyboard(),
                                        edit_message_id=cb_msg_id,
                                    )

                            elif data.startswith("city_del_"):
                                prefix_to_del = data.replace("city_del_", "").strip().lower()
                                cfg = load_candidate_config()
                                prefs = cfg.setdefault("search_preferences", {})
                                cities = prefs.get("target_cities", []) or []
                                new_cities = [c for c in cities if not c.lower().startswith(prefix_to_del)]
                                if not new_cities:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": "Deve rimanere almeno una città!"},
                                    )
                                    continue
                                prefs["target_cities"] = new_cities
                                save_candidate_config(cfg)
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Città rimossa"},
                                )
                                modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])
                                modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
                                text_zones = (
                                    "📍 *Area di Ricerca & Modalità Lavoro (Multi-Range)*\n\n"
                                    f"🏙 *Città Attive ({len(new_cities)}):* `{', '.join(new_cities)}`\n"
                                    f"🏠 *Modalità Accettate:* {modes_str}\n\n"
                                    "Tocca più città o modalità per comporre il tuo range di ricerca:"
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    text_zones,
                                    reply_markup=get_zones_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                            elif data.startswith("mode_tog_"):
                                mode_key = data.replace("mode_tog_", "")
                                if mode_key in WORK_MODE_LABELS:
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    modes = [m.lower() for m in (prefs.get("work_modes", []) or ["remoto", "ibrido", "in_sede"])]
                                    if mode_key in modes:
                                        if len(modes) <= 1:
                                            await client.post(
                                                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                                json={"callback_query_id": cb_id, "text": "Mantieni almeno una modalità attiva!"},
                                            )
                                            continue
                                        modes = [m for m in modes if m != mode_key]
                                        action_msg = f"Modalità disattivata: {mode_key}"
                                    else:
                                        modes.append(mode_key)
                                        action_msg = f"Modalità attivata: {mode_key}"
                                    prefs["work_modes"] = modes
                                    prefs["remote_accepted"] = "remoto" in modes
                                    prefs["hybrid_accepted"] = "ibrido" in modes
                                    save_candidate_config(cfg)
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": action_msg},
                                    )
                                    cities = prefs.get("target_cities", []) or ["Italia"]
                                    modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
                                    text_zones = (
                                        "📍 *Area di Ricerca & Modalità Lavoro (Multi-Range)*\n\n"
                                        f"🏙 *Città Attive ({len(cities)}):* `{', '.join(cities)}`\n"
                                        f"🏠 *Modalità Accettate:* {modes_str}\n\n"
                                        "Tocca più città o modalità per comporre il tuo range di ricerca:"
                                    )
                                    await send_telegram_reply(
                                        client,
                                        cb_chat_id,
                                        text_zones,
                                        reply_markup=get_zones_keyboard(),
                                        edit_message_id=cb_msg_id,
                                    )

                            elif data == "zone_prompt_add":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    "➕ Per aggiungere qualsiasi altra città al tuo range di ricerca, scrivi:\n"
                                    "`/add_zona Nome Città`\n\n"
                                    "Esempio:\n`/add_zona Verona`\n`/add_zona Bergamo`",
                                )

                            elif data == "zone_prompt_set":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    "✏️ Per impostare una nuova combinazione di città e modalità:\n"
                                    "• Città: `/set_zone Torino, Milano, Bologna`\n"
                                    "• Modalità: `/set_modalita remoto, ibrido` (oppure `remoto, ibrido, in_sede`)",
                                )

                            elif data == "cfg_prompt_ral":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(client, cb_chat_id, "💰 Scrivi il comando con la nuova RAL minima, es:\n`/set_ral 32000`")

                            elif data == "cfg_prompt_exp":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                await send_telegram_reply(client, cb_chat_id, "⏳ Scrivi il comando con gli anni minimi di esperienza desiderati (0 per stage/entry level), es:\n`/set_exp 1`")

                            elif data == "cfg_menu_time":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                                time_text = (
                                    "📅 *Filtro data offerte LinkedIn*\n"
                                    "Scegli quale orizzonte temporale analizzare:"
                                )
                                await send_telegram_reply(client, cb_chat_id, time_text, reply_markup=get_time_filter_keyboard(), edit_message_id=cb_msg_id)

                            elif data.startswith("set_tf_"):
                                tf_val = data.replace("set_tf_", "")
                                cfg = load_candidate_config()
                                cfg.setdefault("target_portals", {}).setdefault("linkedin", {})["time_filter"] = tf_val
                                save_candidate_config(cfg)

                                tf_names = {"today": "Solo Oggi (Ultime 24h)", "week": "Ultima Settimana", "month": "Ultimo Mese", "all": "Qualsiasi Data"}
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": f"LinkedIn impostato su: {tf_names.get(tf_val, tf_val)}"},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    f"✅ *Filtro LinkedIn aggiornato a:* {tf_names.get(tf_val, tf_val)}\n\n" + build_status_summary_text(),
                                    reply_markup=get_config_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                            elif data == "cfg_scan_li":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Avvio scansione LinkedIn..."},
                                )
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, cb_chat_id, "⚠️ Una scansione è già in corso!")
                                else:
                                    cfg = load_candidate_config()
                                    tf = cfg.get("target_portals", {}).get("linkedin", {}).get("time_filter", "month")
                                    await send_telegram_reply(client, cb_chat_id, f"🔍 *Scansione LinkedIn Booleana avviata!*\nPeriodo annunci: *{tf}*\nTi notificherò le posizioni in linea.")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="linkedin"))

                            elif data == "cfg_scan_xray":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Avvio X-Ray ATS & Post..."},
                                )
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, cb_chat_id, "⚠️ Una scansione è già in corso!")
                                else:
                                    await send_telegram_reply(client, cb_chat_id, "🔎 *Scansione X-Ray (ATS nascosti + Post Hiring Manager LinkedIn) avviata!*")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="xray"))

                            elif data == "cfg_scout":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Ricerca segnali mercato nascosto..."},
                                )
                                asyncio.create_task(orchestrator.run_signal_scouting(ignore_seen=True))

                            elif data == "cfg_stop_scan":
                                if orchestrator.is_running:
                                    orchestrator.stop_requested = True
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": "Arresto scansione richiesto..."},
                                    )
                                    await send_telegram_reply(client, cb_chat_id, "🛑 *Richiesta di stop inviata.* La scansione in corso verrà interrotta.", reply_markup=get_config_keyboard())
                                else:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                        json={"callback_query_id": cb_id, "text": "Nessuna scansione in corso."},
                                    )
                                    await send_telegram_reply(client, cb_chat_id, "ℹ️ Nessuna scansione attualmente in corso.", reply_markup=get_config_keyboard())

                            elif data == "cfg_scan_all":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Avvio scansione completa..."},
                                )
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, cb_chat_id, "⚠️ Una scansione è già in corso!")
                                else:
                                    await send_telegram_reply(client, cb_chat_id, "🌐 *Scansione Completa (LinkedIn + X-Ray + 50 Portali Aziendali) avviata!*")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="all"))

                            elif data == "cfg_refresh":
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "Pannello aggiornato"},
                                )
                                await send_telegram_reply(
                                    client,
                                    cb_chat_id,
                                    build_status_summary_text(),
                                    reply_markup=get_config_keyboard(),
                                    edit_message_id=cb_msg_id,
                                )

                        # 2. Gestione messaggi di testo e comandi da chat
                        msg = upd.get("message")
                        if msg and msg.get("text"):
                            raw_text = msg["text"].strip()
                            # Rimuovi eventuale @botname dal comando
                            cmd_parts = raw_text.split(maxsplit=1)
                            cmd = cmd_parts[0].split("@")[0].lower()
                            arg_str = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                            chat_id = str(msg["chat"]["id"])

                            if cmd in ["/start", "/help", "help", "/guida"]:
                                await send_telegram_reply(
                                    client,
                                    chat_id,
                                    build_help_message(),
                                    reply_markup=get_config_keyboard(),
                                )

                            elif cmd in ["/config", "config", "/impostazioni", "impostazioni"]:
                                await send_telegram_reply(
                                    client,
                                    chat_id,
                                    build_status_summary_text(),
                                    reply_markup=get_config_keyboard(),
                                )

                            elif cmd in ["/ruoli", "/posizioni", "ruoli"]:
                                cfg = load_candidate_config()
                                kws = cfg.get("search_preferences", {}).get("keywords", []) or []
                                text_roles = (
                                    "💼 *Gestione Posizioni / Ruoli Cercati*\n\n"
                                    f"*Attivi ({len(kws)}):* `{', '.join(kws)}`\n\n"
                                    "Tocca i pulsanti per *attivare (✅) o disattivare (⬜)* i ruoli, "
                                    "oppure usa:\n"
                                    "• `/add_ruolo <nome>`\n"
                                    "• `/del_ruolo <nome>`\n"
                                    "• `/set_ruoli <ruolo1, ruolo2, ...>`"
                                )
                                await send_telegram_reply(client, chat_id, text_roles, reply_markup=get_roles_keyboard())

                            elif cmd == "/add_ruolo":
                                if not arg_str:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/add_ruolo Nome Posizione` (es. `/add_ruolo Data Analyst`)")
                                else:
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    kws = prefs.get("keywords", []) or []
                                    new_items = [x.strip() for x in arg_str.split(",") if x.strip()]
                                    for item in new_items:
                                        if item.lower() not in [k.lower() for k in kws]:
                                            kws.append(item)
                                    prefs["keywords"] = kws
                                    save_candidate_config(cfg)
                                    await send_telegram_reply(
                                        client,
                                        chat_id,
                                        f"✅ *Posizioni aggiornate ({len(kws)}):*\n`{', '.join(kws)}`",
                                        reply_markup=get_roles_keyboard(),
                                    )

                            elif cmd == "/del_ruolo":
                                if not arg_str:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/del_ruolo Nome Posizione`")
                                else:
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    kws = prefs.get("keywords", []) or []
                                    target_low = arg_str.lower()
                                    new_kws = [k for k in kws if k.lower() != target_low and target_low not in k.lower()]
                                    if not new_kws:
                                        await send_telegram_reply(client, chat_id, "⚠️ Deve rimanere almeno una posizione attiva!")
                                    else:
                                        prefs["keywords"] = new_kws
                                        save_candidate_config(cfg)
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            f"✅ *Posizione rimossa. Ruoli attivi ({len(new_kws)}):*\n`{', '.join(new_kws)}`",
                                            reply_markup=get_roles_keyboard(),
                                        )

                            elif cmd == "/set_ruoli":
                                if not arg_str:
                                    await send_telegram_reply(
                                        client,
                                        chat_id,
                                        "ℹ️ Indica i nuovi ruoli separati da virgola, es.:\n`/set_ruoli AI Engineer, Data Scientist, Python Developer`",
                                    )
                                else:
                                    new_kws = [x.strip() for x in arg_str.split(",") if x.strip()]
                                    if not new_kws:
                                        await send_telegram_reply(client, chat_id, "❌ Inserisci almeno un ruolo valido.")
                                    else:
                                        cfg = load_candidate_config()
                                        cfg.setdefault("search_preferences", {})["keywords"] = new_kws
                                        save_candidate_config(cfg)
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            f"✅ *Lista posizioni sostituita ({len(new_kws)}):*\n`{', '.join(new_kws)}`",
                                            reply_markup=get_roles_keyboard(),
                                        )

                            elif cmd in ["/zone", "/zona", "/area", "/modalita", "zone"]:
                                cfg = load_candidate_config()
                                prefs = cfg.get("search_preferences", {})
                                cities = prefs.get("target_cities", []) or ["Italia"]
                                modes = prefs.get("work_modes", ["remoto", "ibrido", "in_sede"])
                                modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in modes)
                                text_zones = (
                                    "📍 *Area di Ricerca & Modalità Lavoro (Multi-Range)*\n\n"
                                    f"🏙 *Città Attive ({len(cities)}):* `{', '.join(cities)}`\n"
                                    f"🏠 *Modalità Accettate:* {modes_str}\n\n"
                                    "Tocca i bottoni per attivare/disattivare più città o modalità, oppure usa:\n"
                                    "• `/add_zona <città>`\n"
                                    "• `/del_zona <città>`\n"
                                    "• `/set_zone <città1, città2, ...>`\n"
                                    "• `/set_modalita <remoto, ibrido, in_sede>`"
                                )
                                await send_telegram_reply(client, chat_id, text_zones, reply_markup=get_zones_keyboard())

                            elif cmd == "/add_zona":
                                if not arg_str:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/add_zona Milano` (o più città separate da virgola)")
                                else:
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    cities = prefs.get("target_cities", []) or []
                                    for item in [x.strip() for x in arg_str.split(",") if x.strip()]:
                                        if item.lower() not in [c.lower() for c in cities]:
                                            cities.append(item)
                                    prefs["target_cities"] = cities
                                    save_candidate_config(cfg)
                                    await send_telegram_reply(
                                        client,
                                        chat_id,
                                        f"✅ *Area di ricerca aggiornata ({len(cities)}):*\n`{', '.join(cities)}`",
                                        reply_markup=get_zones_keyboard(),
                                    )

                            elif cmd == "/del_zona":
                                if not arg_str:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/del_zona NomeCittà`")
                                else:
                                    cfg = load_candidate_config()
                                    prefs = cfg.setdefault("search_preferences", {})
                                    cities = prefs.get("target_cities", []) or []
                                    target_low = arg_str.lower()
                                    new_cities = [c for c in cities if c.lower() != target_low]
                                    if not new_cities:
                                        await send_telegram_reply(client, chat_id, "⚠️ Deve rimanere almeno una zona attiva!")
                                    else:
                                        prefs["target_cities"] = new_cities
                                        save_candidate_config(cfg)
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            f"✅ *Zona rimossa. Città attive ({len(new_cities)}):*\n`{', '.join(new_cities)}`",
                                            reply_markup=get_zones_keyboard(),
                                        )

                            elif cmd == "/set_zone":
                                if not arg_str:
                                    await send_telegram_reply(
                                        client,
                                        chat_id,
                                        "ℹ️ Indica le città separate da virgola, es.:\n`/set_zone Torino, Milano, Roma`",
                                    )
                                else:
                                    new_cities = [x.strip() for x in arg_str.split(",") if x.strip()]
                                    if not new_cities:
                                        await send_telegram_reply(client, chat_id, "❌ Inserisci almeno una città valida.")
                                    else:
                                        cfg = load_candidate_config()
                                        cfg.setdefault("search_preferences", {})["target_cities"] = new_cities
                                        save_candidate_config(cfg)
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            f"✅ *Area di ricerca impostata su ({len(new_cities)}):*\n`{', '.join(new_cities)}`",
                                            reply_markup=get_zones_keyboard(),
                                        )

                            elif cmd == "/set_modalita":
                                if not arg_str:
                                    await send_telegram_reply(
                                        client,
                                        chat_id,
                                        "ℹ️ Indica una o più modalità tra `remoto`, `ibrido`, `in_sede`, es.:\n"
                                        "`/set_modalita remoto, ibrido`",
                                    )
                                else:
                                    raw_modes = [x.strip().lower().replace(" ", "_") for x in arg_str.split(",") if x.strip()]
                                    norm_modes = []
                                    for m in raw_modes:
                                        if m in ("remoto", "remote", "full_remote", "smart_working"):
                                            norm_modes.append("remoto")
                                        elif m in ("ibrido", "hybrid"):
                                            norm_modes.append("ibrido")
                                        elif m in ("in_sede", "onsite", "on_site", "sede", "presenza"):
                                            norm_modes.append("in_sede")
                                    norm_modes = list(dict.fromkeys(norm_modes))
                                    if not norm_modes:
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            "❌ Valori ammessi: `remoto`, `ibrido`, `in_sede` (es. `/set_modalita remoto, ibrido`)",
                                        )
                                    else:
                                        cfg = load_candidate_config()
                                        prefs = cfg.setdefault("search_preferences", {})
                                        prefs["work_modes"] = norm_modes
                                        prefs["remote_accepted"] = "remoto" in norm_modes
                                        prefs["hybrid_accepted"] = "ibrido" in norm_modes
                                        save_candidate_config(cfg)
                                        modes_str = ", ".join(WORK_MODE_LABELS.get(m, m) for m in norm_modes)
                                        await send_telegram_reply(
                                            client,
                                            chat_id,
                                            f"✅ *Modalità di lavoro aggiornate:* {modes_str}",
                                            reply_markup=get_zones_keyboard(),
                                        )

                            elif cmd in ["/scout", "scout"]:
                                asyncio.create_task(orchestrator.run_signal_scouting(ignore_seen=True))

                            elif cmd in ["/xray", "xray"]:
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, chat_id, "⚠️ Una scansione è già in corso! Attendi il termine.")
                                else:
                                    await send_telegram_reply(client, chat_id, "🔎 *Scansione X-Ray ATS & Post Hiring Manager avviata!*")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="xray"))

                            elif cmd in ["/stop", "stop"]:
                                if orchestrator.is_running:
                                    orchestrator.stop_requested = True
                                    await send_telegram_reply(client, chat_id, "🛑 *Richiesta di stop inviata.* La scansione in corso verrà interrotta a breve.", reply_markup=get_config_keyboard())
                                else:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Nessuna scansione attualmente in corso.", reply_markup=get_config_keyboard())

                            elif cmd == "/scan":
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, chat_id, "⚠️ Una scansione è già in corso! Attendi il termine.")
                                else:
                                    await send_telegram_reply(client, chat_id, "🌐 *Scansione completa avviata!*\nTi notificherò appena trovo posizioni in linea.")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="all"))

                            elif cmd in ["/scan_li", "/scan_linkedin"]:
                                if orchestrator.is_running:
                                    await send_telegram_reply(client, chat_id, "⚠️ Una scansione è già in corso! Attendi il termine.")
                                else:
                                    cfg = load_candidate_config()
                                    tf = cfg.get("target_portals", {}).get("linkedin", {}).get("time_filter", "month")
                                    await send_telegram_reply(client, chat_id, f"🔍 *Scansione solo LinkedIn avviata!*\nFiltro data: *{tf}*")
                                    asyncio.create_task(orchestrator.run_hourly_cycle(portal_filter="linkedin"))

                            elif cmd == "/set_min":
                                if arg_str.isdigit():
                                    val = int(arg_str)
                                    if 10 <= val <= 95:
                                        settings.REVIEW_THRESHOLD = val
                                        await send_telegram_reply(client, chat_id, f"✅ Soglia minima di notifica aggiornata a: *{val}%*", reply_markup=get_config_keyboard())
                                    else:
                                        await send_telegram_reply(client, chat_id, "❌ Inserisci un valore compreso tra 10 e 95.")
                                else:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/set_min 75` (es. 75 per 75% di fedeltà)")

                            elif cmd == "/set_ral":
                                if arg_str:
                                    val = arg_str.replace("€", "").replace(".", "").strip()
                                    cfg = load_candidate_config()
                                    cfg.setdefault("candidate_profile", {})["expected_salary_ral"] = val
                                    save_candidate_config(cfg)
                                    await send_telegram_reply(client, chat_id, f"✅ RAL minima aggiornata a: *{val}€*", reply_markup=get_config_keyboard())
                                else:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/set_ral 32000`")

                            elif cmd == "/set_exp":
                                if arg_str.isdigit():
                                    val = int(arg_str)
                                    cfg = load_candidate_config()
                                    cfg.setdefault("candidate_profile", {})["min_years_experience"] = val
                                    save_candidate_config(cfg)
                                    await send_telegram_reply(client, chat_id, f"✅ Anni minimi di esperienza impostati a: *{val} anni*", reply_markup=get_config_keyboard())
                                else:
                                    await send_telegram_reply(client, chat_id, "ℹ️ Usa: `/set_exp 1` (oppure 0 per accettare qualsiasi livello)")

                            elif cmd == "/status":
                                jobs = await get_recent_jobs(limit=100)
                                in_linea = sum(1 for x in jobs if (x.get("match_score") or 0) >= settings.REVIEW_THRESHOLD)
                                auto_inviate = sum(1 for x in jobs if x.get("status") in ["AUTO_APPLIED", "APPROVED_APPLIED"])
                                reply = (
                                    build_status_summary_text()
                                    + "\n\n📈 *Statistiche Database:*\n"
                                    + f"• Agente in esecuzione: `{'Sì (Scansione attiva)' if orchestrator.is_running else 'In attesa (Idle)'}`\n"
                                    + f"• Totale Offerte Analizzate: *{len(jobs)}*\n"
                                    + f"• Offerte Valutate in Linea: *{in_linea}*\n"
                                    + f"• Candidature Inviate: *{auto_inviate}*"
                                )
                                await send_telegram_reply(client, chat_id, reply, reply_markup=get_config_keyboard())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Telegram polling retry: {e}")
                await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_polling_task
    await init_db()

    scheduler.add_job(
        orchestrator.run_hourly_cycle,
        "interval",
        minutes=settings.CHECK_INTERVAL_MINUTES,
        id="hourly_job_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler attivo: controllo portali ogni {settings.CHECK_INTERVAL_MINUTES} minuti."
    )

    asyncio.create_task(register_telegram_commands_and_welcome())
    telegram_polling_task = asyncio.create_task(poll_telegram_callbacks())
    yield

    scheduler.shutdown(wait=False)
    if telegram_polling_task:
        telegram_polling_task.cancel()


app = FastAPI(
    title="AI Job Hunter & Auto-Apply Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent_running": orchestrator.is_running,
        "check_interval_minutes": settings.CHECK_INTERVAL_MINUTES,
        "auto_apply_threshold": settings.AUTO_APPLY_THRESHOLD,
        "review_threshold": settings.REVIEW_THRESHOLD,
    }


@app.post("/api/run-now")
async def trigger_scan_now(background_tasks: BackgroundTasks):
    if orchestrator.is_running:
        return JSONResponse({"message": "Una scansione è già in corso."}, status_code=409)
    background_tasks.add_task(orchestrator.run_hourly_cycle)
    return {"message": "Scansione avviata in background!"}


@app.post("/api/jobs/{job_id}/apply")
async def approve_job_from_dashboard(job_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(orchestrator.approve_and_apply_job, job_id)
    return {"message": f"Invio candidatura per offerta #{job_id} avviato."}


@app.get("/api/jobs")
async def list_jobs():
    return await get_recent_jobs(limit=100)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    jobs = await get_recent_jobs(limit=100)
    full_cfg = load_candidate_config()
    cfg = full_cfg.get("candidate_profile", {})
    prefs = full_cfg.get("search_preferences", {})
    current_ral = cfg.get("expected_salary_ral", "30000")
    candidate_name = f"{cfg.get('first_name', 'Candidato')} {cfg.get('last_name', '')}".strip()
    active_cities = ", ".join(prefs.get("target_cities", []) or [cfg.get("city", "Italia")])

    rows_html = ""
    for j in jobs:
        score = j.get("match_score") or 0
        badge_color = (
            "#10b981"
            if score >= settings.AUTO_APPLY_THRESHOLD
            else ("#f59e0b" if score >= settings.REVIEW_THRESHOLD else "#64748b")
        )
        status_label = j["status"]
        ral_display = j.get("estimated_ral") or "Non specificata"

        action_btn = (
            f"""<button onclick="applyJob({j['id']})" style="padding:6px 12px;cursor:pointer;background:#2563eb;color:white;border:none;border-radius:6px;font-size:12px;">Invia CV</button>"""
            if score >= settings.REVIEW_THRESHOLD
            else """<span style="color:#64748b;font-size:12px;">Archiviata</span>"""
        )

        rows_html += f"""
        <tr>
            <td><strong>{j['company']}</strong><br><small style="color:#94a3b8;">{j['portal']}</small></td>
            <td><a href="{j['url']}" target="_blank" style="color:#38bdf8;text-decoration:none;font-weight:500;">{j['title']}</a><br><small style="color:#94a3b8;">{j['location']}</small></td>
            <td><span style="color:#10b981;font-weight:600;font-size:13px;">💰 {ral_display}</span></td>
            <td><span style="background:{badge_color};color:#fff;padding:3px 8px;border-radius:12px;font-weight:bold;font-size:12px;">{score}%</span></td>
            <td><span style="font-family:monospace;font-size:12px;color:#cbd5e1;">{status_label}</span></td>
            <td><small style="color:#cbd5e1;line-height:1.3;">{j.get('ai_reasoning', '')[:180]}</small></td>
            <td>{action_btn}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="20">
        <title>AI Job Hunter - {candidate_name}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .stats-bar {{ display: flex; gap: 16px; margin-bottom: 20px; }}
            .stat-box {{ background: #1e293b; padding: 12px 20px; border-radius: 8px; border: 1px solid #334155; }}
            .stat-box strong {{ font-size: 18px; color: #38bdf8; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th {{ text-align: left; padding: 12px 10px; border-bottom: 2px solid #475569; color: #94a3b8; font-size: 13px; text-transform: uppercase; }}
            td {{ padding: 12px 10px; border-bottom: 1px solid #334155; font-size: 14px; vertical-align: top; }}
            .btn-primary {{ background: #10b981; color: #fff; padding: 10px 18px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }}
            .btn-primary:hover {{ background: #059669; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h1 style="margin:0 0 6px 0;">🤖 AI Job Hunter & Auto-Apply Agent</h1>
                <p style="margin:0;color:#94a3b8;">Candidato: <strong>{candidate_name}</strong> • Area: <strong>{active_cities}</strong> • Obiettivo RAL: <strong style="color:#10b981;">{current_ral}€</strong> • Soglia Notifica: <strong>&ge;{settings.REVIEW_THRESHOLD}%</strong></p>
            </div>
            <button class="btn-primary" onclick="runNow()">🔍 Avvia Scansione Ora</button>
        </div>

        <div class="stats-bar">
            <div class="stat-box">Posizioni Monitorate: <strong>{len(jobs)}</strong></div>
            <div class="stat-box">In Linea / Da Valutare: <strong>{sum(1 for x in jobs if (x.get('match_score') or 0) >= settings.REVIEW_THRESHOLD)}</strong></div>
            <div class="stat-box">Comandi Telegram: <code>/help</code> <code>/ruoli</code> <code>/zone</code> <code>/scan</code></div>
        </div>

        <div class="card">
            <table>
                <thead>
                    <tr>
                        <th style="width:18%;">Azienda / Portale</th>
                        <th style="width:24%;">Ruolo</th>
                        <th style="width:14%;">RAL (da annuncio)</th>
                        <th style="width:8%;">Fedeltà</th>
                        <th style="width:12%;">Stato</th>
                        <th style="width:18%;">Analisi Gemini AI</th>
                        <th style="width:6%;">Azione</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html or '<tr><td colspan="7" style="text-align:center;padding:30px;">Nessuna posizione archiviata. Avvia la prima scansione.</td></tr>'}
                </tbody>
            </table>
        </div>
        <script>
            async function runNow() {{
                const res = await fetch('/api/run-now', {{ method: 'POST' }});
                const data = await res.json();
                alert(data.message);
                setTimeout(() => location.reload(), 2000);
            }}
            async function applyJob(id) {{
                const res = await fetch('/api/jobs/' + id + '/apply', {{ method: 'POST' }});
                const data = await res.json();
                alert(data.message);
                location.reload();
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
