import hashlib
import re
import urllib.parse
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from app.config import DATA_DIR

DB_PATH = DATA_DIR / "jobs.db"

# Parametri di tracking comuni che cambiano ad ogni visita ma puntano alla stessa identica offerta
TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "_gl",
    "_ga",
    "refid",
    "trackingid",
    "trk",
    "position",
    "pagenum",
    "ebp",
    "lipi",
    "lici",
    "original_referer",
    " originalsubdomain",
}

MONTHS_MAP = {
    "gen": 1, "gennaio": 1, "jan": 1, "january": 1,
    "feb": 2, "febbraio": 2, "february": 2,
    "mar": 3, "marzo": 3, "march": 3,
    "apr": 4, "aprile": 4, "april": 4,
    "mag": 5, "maggio": 5, "may": 5,
    "giu": 6, "giugno": 6, "jun": 6, "june": 6,
    "lug": 7, "luglio": 7, "jul": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8,
    "set": 9, "settembre": 9, "sep": 9, "sept": 9, "september": 9,
    "ott": 10, "ottobre": 10, "oct": 10, "october": 10,
    "nov": 11, "novembre": 11, "november": 11,
    "dic": 12, "dicembre": 12, "dec": 12, "december": 12,
}


def parse_published_date(raw_text: str, reference_iso: str = None) -> str:
    """
    Estrae o normalizza la data di pubblicazione di un annuncio in formato YYYY-MM-DD.
    Supporta date ISO, formati testuali italiani/inglesi ('4 nov 2026', 'Oct 28, 2026'),
    formati DD/MM/YYYY e date relative ('Posted 3 Days Ago', '2 settimane fa', 'Oggi', 'Ieri').
    """
    ref_dt = datetime.utcnow()
    if reference_iso:
        try:
            ref_dt = datetime.fromisoformat(reference_iso.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    if not raw_text:
        return ref_dt.strftime("%Y-%m-%d")

    txt = str(raw_text).strip()

    # 1. Data ISO diretta (es. 2026-10-29 o 2026-10-29T12:30:00Z)
    m_iso = re.search(r"\b(202[4-7])-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", txt)
    if m_iso:
        try:
            dt = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            if dt <= ref_dt + timedelta(days=2):
                return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    low = txt.lower()

    # 2. Espressioni relative ("posted today", "oggi", "ieri", "yesterday", "X days ago", "X giorni fa", "X settimane fa")
    if any(k in low for k in ["posted today", "pubblicato oggi", "just posted", "poche ore fa", "hours ago", "hour ago"]):
        return ref_dt.strftime("%Y-%m-%d")
    if any(k in low for k in ["posted yesterday", "pubblicato ieri", "1 day ago", "1 giorno fa"]):
        return (ref_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    m_rel_days = re.search(r"\b(\d{1,2})\+?\s*(?:days?\s+ago|giorn[oi]\s+fa)\b", low)
    if m_rel_days:
        days = int(m_rel_days.group(1))
        return (ref_dt - timedelta(days=min(days, 90))).strftime("%Y-%m-%d")

    m_rel_weeks = re.search(r"\b(\d{1,2})\s*(?:weeks?\s+ago|settiman[ae]\s+fa)\b", low)
    if m_rel_weeks:
        weeks = int(m_rel_weeks.group(1))
        return (ref_dt - timedelta(days=min(weeks * 7, 120))).strftime("%Y-%m-%d")

    m_rel_months = re.search(r"\b(\d{1,2})\s*(?:months?\s+ago|mes[ei]\s+fa)\b", low)
    if m_rel_months:
        months = int(m_rel_months.group(1))
        return (ref_dt - timedelta(days=min(months * 30, 180))).strftime("%Y-%m-%d")

    # 3. Date testuali IT/EN: "4 nov 2026", "15 ottobre 2026", "24 October 2026"
    m_dmy_txt = re.search(
        r"\b([0-3]?\d)\s+(gen(?:naio)?|feb(?:braio)?|mar(?:zo)?|apr(?:ile)?|mag(?:gio)?|giu(?:gno)?|lug(?:lio)?|ago(?:sto)?|set(?:tembre)?|ott(?:obre)?|nov(?:embre)?|dic(?:embre)?|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[a-z]*[\s,]+(202[4-7])\b",
        low,
    )
    if m_dmy_txt:
        day = int(m_dmy_txt.group(1))
        mon_str = m_dmy_txt.group(2)[:3]
        year = int(m_dmy_txt.group(3))
        month = MONTHS_MAP.get(m_dmy_txt.group(2)) or MONTHS_MAP.get(mon_str, 0)
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                dt = datetime(year, month, day)
                if dt <= ref_dt + timedelta(days=2):
                    return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 4. Date testuali EN: "Oct 24, 2026"
    m_mdy_txt = re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+([0-3]?\d)[\s,]+(202[4-7])\b",
        low,
    )
    if m_mdy_txt:
        month = MONTHS_MAP.get(m_mdy_txt.group(1)[:3], 0)
        day = int(m_mdy_txt.group(2))
        year = int(m_mdy_txt.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                dt = datetime(year, month, day)
                if dt <= ref_dt + timedelta(days=2):
                    return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 5. Date numeriche DD/MM/YYYY o DD-MM-YYYY (escludendo scadenze future)
    for m_num in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-](202[4-7])\b", txt):
        try:
            dt = datetime(int(m_num.group(3)), int(m_num.group(2)), int(m_num.group(1)))
            if dt <= ref_dt + timedelta(days=1):
                return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return ref_dt.strftime("%Y-%m-%d")


def normalize_job_url(url: str) -> str:
    """
    Normalizza l'URL di un'offerta di lavoro rimuovendo parametri di tracciamento volatili
    (es. refId, trackingId, utm_source, _gl) e slash finali, così da confrontare
    in modo affidabile i link nel database.
    """
    if not url:
        return ""
    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        if netloc.endswith(".linkedin.com") and netloc != "www.linkedin.com":
            netloc = "www.linkedin.com"

        path = parsed.path.rstrip("/") or "/"

        if "linkedin.com" in netloc and "/jobs/view/" in path:
            m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)(?:/|$)", path)
            if m:
                return f"https://www.linkedin.com/jobs/view/{m.group(1)}"

        if parsed.query:
            q_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            filtered_pairs = [
                (k, v)
                for k, v in q_pairs
                if k.lower() not in TRACKING_QUERY_PARAMS and not k.lower().startswith("utm_")
            ]
            query = urllib.parse.urlencode(filtered_pairs, doseq=True)
        else:
            query = ""

        fragment = parsed.fragment if parsed.fragment.startswith("/") else ""
        return urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        return url.rstrip("/")


def deterministic_url_id(prefix: str, url: str) -> str:
    """Genera un external_id deterministico (indipendente dal riavvio di Python) basato sull'URL normalizzato."""
    norm = normalize_job_url(url) or url
    digest = hashlib.md5(norm.encode("utf-8", errors="ignore")).hexdigest()[:14]
    return f"{prefix}_{digest}"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT UNIQUE NOT NULL,
                portal TEXT NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                url TEXT NOT NULL,
                description TEXT,
                is_easy_apply BOOLEAN DEFAULT 0,
                match_score INTEGER,
                estimated_ral TEXT DEFAULT 'Non specificata',
                missing_skills TEXT,
                matching_skills TEXT,
                ai_reasoning TEXT,
                tailored_cover_note TEXT,
                status TEXT DEFAULT 'DISCOVERED', -- DISCOVERED, AUTO_APPLIED, PENDING_APPROVAL, APPROVED_APPLIED, REJECTED, SKIPPED_LOW_SCORE, FAILED
                discovered_at TEXT NOT NULL,
                published_at TEXT,
                applied_at TEXT
            )
            """
        )
        # Migrazione automatica se la colonna published_at non esisteva
        async with db.execute("PRAGMA table_info(jobs)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "published_at" not in cols:
            await db.execute("ALTER TABLE jobs ADD COLUMN published_at TEXT")

        # Rimuovi eventuali duplicati storici aventi lo stesso identico URL (mantenendo la prima occorrenza)
        await db.execute(
            """
            DELETE FROM jobs
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM jobs
                GROUP BY rtrim(url, '/')
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_published_at ON jobs(published_at)")

        # Tabella per monitorare lo stato del DOM, la visibilità in chiaro delle offerte e la necessità di aggiornare lo scraper
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scraper_health (
                company TEXT PRIMARY KEY,
                site_type TEXT NOT NULL,
                portal_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'HEALTHY', -- HEALTHY, WARNING_EMPTY, DOM_CHANGED, HTTP_ERROR
                last_jobs_count INTEGER DEFAULT 0,
                last_success_count INTEGER DEFAULT 0,
                diagnostic_message TEXT DEFAULT '',
                dom_fingerprint TEXT DEFAULT '',
                needs_scraper_update BOOLEAN DEFAULT 0,
                last_checked_at TEXT NOT NULL,
                last_healthy_at TEXT
            )
            """
        )

        # Popola published_at per i record che non l'hanno ancora valorizzato
        async with db.execute(
            "SELECT id, title, url, description, discovered_at FROM jobs WHERE published_at IS NULL OR published_at = ''"
        ) as cursor:
            missing_rows = await cursor.fetchall()

        if missing_rows:
            updates = []
            for r_id, title, url, desc, disc_at in missing_rows:
                pub_date = parse_published_date(f"{title or ''}\n{desc or ''}", disc_at)
                updates.append((pub_date, r_id))
            await db.executemany("UPDATE jobs SET published_at = ? WHERE id = ?", updates)

        await db.commit()


async def is_job_seen(external_id: str, url: str = "") -> bool:
    """
    Controlla se l'offerta è già presente nel database confrontando:
    1. L'external_id
    2. Il link esatto (con o senza slash finale)
    3. Il link normalizzato (senza parametri di tracking come utm_source, refId, _gl)
    """
    norm_url = normalize_job_url(url) if url else ""
    raw_url = (url or "").strip()
    raw_trimmed = raw_url.rstrip("/")
    norm_trimmed = norm_url.rstrip("/")

    async with aiosqlite.connect(DB_PATH) as db:
        if raw_url:
            async with db.execute(
                """
                SELECT 1 FROM jobs
                WHERE external_id = ?
                   OR url = ?
                   OR url = ?
                   OR rtrim(url, '/') = ?
                   OR rtrim(url, '/') = ?
                LIMIT 1
                """,
                (external_id, raw_url, norm_url, raw_trimmed, norm_trimmed),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    return True

            base_prefix = norm_trimmed.split("?")[0]
            if base_prefix and len(base_prefix) > 15:
                async with db.execute(
                    "SELECT url FROM jobs WHERE url LIKE ? LIMIT 50",
                    (f"{base_prefix}%",),
                ) as cursor:
                    rows = await cursor.fetchall()
                    for (existing_url,) in rows:
                        if normalize_job_url(existing_url) == norm_url:
                            return True
        else:
            async with db.execute(
                "SELECT 1 FROM jobs WHERE external_id = ? LIMIT 1",
                (external_id,),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    return True

    return False


async def save_job(job_data: Dict[str, Any]) -> Optional[int]:
    """
    Salva l'offerta nel database se il suo link non è già presente.
    Memorizza anche la data di pubblicazione (published_at) per lo storico di mercato.
    """
    raw_url = (job_data.get("url") or "").strip()
    ext_id = job_data["external_id"]

    if await is_job_seen(ext_id, raw_url):
        return None

    now_iso = datetime.utcnow().isoformat()
    raw_pub = job_data.get("published_at") or ""
    published_at = (
        parse_published_date(raw_pub, now_iso)
        if raw_pub
        else parse_published_date(
            f"{job_data.get('title', '')}\n{job_data.get('description', '')}",
            now_iso,
        )
    )

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO jobs (
                external_id, portal, company, title, location, url,
                description, is_easy_apply, match_score, estimated_ral, missing_skills,
                matching_skills, ai_reasoning, tailored_cover_note, status, discovered_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ext_id,
                job_data["portal"],
                job_data["company"],
                job_data["title"],
                job_data.get("location", ""),
                raw_url,
                job_data.get("description", ""),
                1 if job_data.get("is_easy_apply", False) else 0,
                job_data.get("match_score", 0),
                job_data.get("estimated_ral", "Non specificata"),
                ", ".join(job_data.get("missing_skills", [])),
                ", ".join(job_data.get("matching_skills", [])),
                job_data.get("ai_reasoning", ""),
                job_data.get("tailored_cover_note", ""),
                job_data.get("status", "DISCOVERED"),
                now_iso,
                published_at,
            ),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        return cursor.lastrowid


async def update_job_status(job_id: int, status: str):
    applied_at = datetime.utcnow().isoformat() if "APPLIED" in status else None
    async with aiosqlite.connect(DB_PATH) as db:
        if applied_at:
            await db.execute(
                "UPDATE jobs SET status = ?, applied_at = ? WHERE id = ?",
                (status, applied_at, job_id),
            )
        else:
            await db.execute(
                "UPDATE jobs SET status = ?, applied_at = NULL WHERE id = ?", (status, job_id)
            )
        await db.commit()


async def get_job_by_id(job_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_recent_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_all_jobs(limit: int = 10000) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs ORDER BY COALESCE(published_at, substr(discovered_at, 1, 10)) DESC, id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def record_scraper_health(
    company: str,
    site_type: str,
    portal_url: str,
    status: str,
    jobs_count: int,
    diagnostic_message: str,
    dom_fingerprint: str = "",
    needs_scraper_update: bool = False,
) -> None:
    """
    Registra o aggiorna lo stato di salute dello scraper per una specifica azienda:
    - status: 'HEALTHY', 'WARNING_EMPTY', 'DOM_CHANGED', 'HTTP_ERROR'
    - needs_scraper_update: True se il DOM è cambiato / le offerte non sono più in chiaro e va aggiornato lo scraper.
    """
    now_iso = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT last_success_count, last_healthy_at, dom_fingerprint FROM scraper_health WHERE company = ?",
            (company,),
        ) as cursor:
            existing = await cursor.fetchone()

        prev_success = int(existing["last_success_count"]) if existing and existing["last_success_count"] else 0
        prev_healthy_at = existing["last_healthy_at"] if existing else None
        prev_fp = existing["dom_fingerprint"] if existing else ""

        new_success_count = jobs_count if jobs_count > 0 else prev_success
        new_healthy_at = now_iso if (status == "HEALTHY" or jobs_count > 0) else prev_healthy_at
        final_fp = dom_fingerprint or prev_fp

        await db.execute(
            """
            INSERT INTO scraper_health (
                company, site_type, portal_url, status,
                last_jobs_count, last_success_count, diagnostic_message,
                dom_fingerprint, needs_scraper_update, last_checked_at, last_healthy_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company) DO UPDATE SET
                site_type = excluded.site_type,
                portal_url = excluded.portal_url,
                status = excluded.status,
                last_jobs_count = excluded.last_jobs_count,
                last_success_count = excluded.last_success_count,
                diagnostic_message = excluded.diagnostic_message,
                dom_fingerprint = excluded.dom_fingerprint,
                needs_scraper_update = excluded.needs_scraper_update,
                last_checked_at = excluded.last_checked_at,
                last_healthy_at = excluded.last_healthy_at
            """,
            (
                company,
                site_type,
                portal_url,
                status,
                jobs_count,
                new_success_count,
                diagnostic_message,
                final_fp,
                1 if needs_scraper_update else 0,
                now_iso,
                new_healthy_at,
            ),
        )
        await db.commit()


async def get_all_scraper_health() -> List[Dict[str, Any]]:
    """
    Restituisce lo stato di integrità DOM e visibilità offerte di tutti i portali monitorati,
    sincronizzando automaticamente eventuali aziende presenti in career_sites_mapping.json.
    """
    mapping_file = DB_PATH.parent / "career_sites_mapping.json"
    mapped_sites: Dict[str, Any] = {}
    if mapping_file.exists():
        try:
            import json
            mapped_sites = json.loads(mapping_file.read_text(encoding="utf-8"))
        except Exception:
            mapped_sites = {}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Conta le offerte attualmente nel DB per azienda
        async with db.execute("SELECT company, COUNT(*) as cnt FROM jobs GROUP BY company") as cur_cnt:
            db_counts = {r["company"]: int(r["cnt"]) for r in await cur_cnt.fetchall()}

        async with db.execute("SELECT * FROM scraper_health") as cursor:
            existing_rows = {r["company"]: dict(r) for r in await cursor.fetchall()}

        now_iso = datetime.utcnow().isoformat()
        to_insert = []
        for comp_name, s_data in mapped_sites.items():
            s_type = s_data.get("type", "custom_career")
            if s_type == "linkedin_search":
                continue
            p_url = s_data.get("mapped_url") or s_data.get("iframe_url") or ""
            cnt = db_counts.get(comp_name, 0)
            if comp_name not in existing_rows:
                st = "HEALTHY" if cnt > 0 else "WARNING_EMPTY"
                msg = (
                    f"✅ DOM integro e offerte in chiaro ({cnt} offerte presenti nel DB)."
                    if cnt > 0
                    else "ℹ️ In attesa di verifica DOM live o 0 offerte in archivio."
                )
                to_insert.append(
                    (comp_name, s_type, p_url, st, cnt, cnt, msg, "", 0, now_iso, now_iso if cnt > 0 else None)
                )

        if to_insert:
            await db.executemany(
                """
                INSERT OR IGNORE INTO scraper_health (
                    company, site_type, portal_url, status,
                    last_jobs_count, last_success_count, diagnostic_message,
                    dom_fingerprint, needs_scraper_update, last_checked_at, last_healthy_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                to_insert,
            )
            await db.commit()

        async with db.execute(
            """
            SELECT * FROM scraper_health
            ORDER BY needs_scraper_update DESC,
                     CASE status
                         WHEN 'DOM_CHANGED' THEN 0
                         WHEN 'HTTP_ERROR' THEN 1
                         WHEN 'WARNING_EMPTY' THEN 2
                         ELSE 3
                     END ASC,
                     company ASC
            """
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
            for r in rows:
                r["needs_scraper_update"] = bool(r.get("needs_scraper_update"))
            return rows


async def get_scraper_health_summary() -> Dict[str, Any]:
    rows = await get_all_scraper_health()
    alerts = [r for r in rows if r.get("needs_scraper_update") or r.get("status") in ("DOM_CHANGED", "HTTP_ERROR")]
    healthy = [r for r in rows if r.get("status") == "HEALTHY" and not r.get("needs_scraper_update")]
    warnings = [r for r in rows if r.get("status") == "WARNING_EMPTY" and not r.get("needs_scraper_update")]
    return {
        "total_portals": len(rows),
        "healthy_count": len(healthy),
        "warning_count": len(warnings),
        "needs_update_count": len(alerts),
        "has_critical_alerts": len(alerts) > 0,
        "alerts": alerts,
        "portals": rows,
    }
