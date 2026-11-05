import hashlib
import re
import urllib.parse
import aiosqlite
from datetime import datetime
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
        # Uniforma sottodomini regionali di LinkedIn (es. it.linkedin.com -> www.linkedin.com)
        if netloc.endswith(".linkedin.com") and netloc != "www.linkedin.com":
            netloc = "www.linkedin.com"

        path = parsed.path.rstrip("/") or "/"

        # Per LinkedIn /jobs/view/..., estrai l'ID numerico canonico se presente
        if "linkedin.com" in netloc and "/jobs/view/" in path:
            m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)(?:/|$)", path)
            if m:
                return f"https://www.linkedin.com/jobs/view/{m.group(1)}"

        # Filtra i query param eliminando solo quelli di tracking
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

        # Conserva il fragment solo se è un route SPA (es. #/jobs/...)
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
                applied_at TEXT
            )
            """
        )
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
        # 1. Controllo rapido indicizzato su external_id e varianti dirette dell'URL
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

            # 2. Se l'URL ha query di tracking o è un link LinkedIn con job ID numerico, confronta con gli URL normalizzati
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
    Restituisce l'ID della riga inserita, oppure None se l'offerta era già presente.
    """
    raw_url = (job_data.get("url") or "").strip()
    ext_id = job_data["external_id"]

    if await is_job_seen(ext_id, raw_url):
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO jobs (
                external_id, portal, company, title, location, url,
                description, is_easy_apply, match_score, estimated_ral, missing_skills,
                matching_skills, ai_reasoning, tailored_cover_note, status, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.utcnow().isoformat(),
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
                "UPDATE jobs SET status = ? WHERE id = ?", (status, job_id)
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
