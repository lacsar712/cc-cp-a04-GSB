import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 建表 + 对既有库做幂等加列（is_rush 即“冷媒加急”记号，写入后随单冻结）。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    is_rush boolean NOT NULL DEFAULT false,
    claimed_by text,
    claimed_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS is_rush boolean NOT NULL DEFAULT false;
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS claimed_by text;
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS claimed_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);
CREATE INDEX IF NOT EXISTS idx_probe_readings_pending_queue
    ON probe_readings (is_rush DESC, id ASC) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_probe_readings_processing_queue
    ON probe_readings (is_rush DESC, id ASC) WHERE status = 'processing';
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        # 仅落两笔已判定的历史单（其中 B02 带加急记号，展示记号随单冻到结案）。
        # 不预置候审单，保证车道从空开始，插队场景由记录员实际报温产生。
        samples = [
            ("探头A01", 4.2, False),
            ("探头B02", 12.5, True),
        ]
        for probe_id, temp_c, is_rush in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, is_rush,
                     created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', $5, 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
                is_rush,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2, False),
        ("探头B02", 12.5, True),
    ]
    for probe_id, temp_c, is_rush in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, is_rush,
                 created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', %s, 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason, is_rush),
        )
    conn.commit()
