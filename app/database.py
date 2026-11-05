import aiosqlite
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.config import DATA_DIR

DB_PATH = DATA_DIR / "jobs.db"


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
        await db.commit()


async def is_job_seen(external_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM jobs WHERE external_id = ?", (external_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def save_job(job_data: Dict[str, Any]) -> int:
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
                job_data["external_id"],
                job_data["portal"],
                job_data["company"],
                job_data["title"],
                job_data.get("location", ""),
                job_data["url"],
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

