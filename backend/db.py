import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 唯一排序口径：先清冷媒加急，再碰普通；同级按编号从小到大。
# 认领接口、后台工人、下一辆预告都必须用这一条，禁止另写排序。
QUEUE_ORDER = "urgent DESC, id ASC"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    urgent boolean NOT NULL DEFAULT false,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    claimed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS urgent boolean NOT NULL DEFAULT false;
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS claimed_by text;
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);
"""

READING_COLS = (
    "id, probe_id, temp_c, urgent, verdict, reason, status, "
    "created_by, claimed_by, created_at, processed_at"
)


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


def reading_dict(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "urgent": r["urgent"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "claimed_by": r["claimed_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2, False),
            ("探头B02", 12.5, False),
        ]
        for probe_id, temp_c, urgent in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, urgent, verdict, reason, status,
                     created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                urgent,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2, False),
        ("探头B02", 12.5, False),
    ]
    for probe_id, temp_c, urgent in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, urgent, verdict, reason, status,
                 created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, urgent, verdict, reason),
        )
    conn.commit()
