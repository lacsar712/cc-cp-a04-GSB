"""后台工人：只判定已被记录员“认领”（status=processing）的读数。

候审（pending）单不再被工人自动吞掉——它们停在冷媒车道上等待记录员手动
认领，认领顺序由车道排序（加急优先、编号升序）决定。工人这里沿用同一段
QUEUE_ORDER_SQL 取处理中单并用 SKIP LOCKED 行锁，多工人互不阻塞。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import QUEUE_ORDER_SQL, judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def next_processing(conn):
    with conn.transaction():
        row = conn.execute(
            f"""
            SELECT id, probe_id, temp_c, claimed_by
            FROM probe_readings
            WHERE status = 'processing'
            ORDER BY {QUEUE_ORDER_SQL}
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        return row


def finish(conn, reading_id: int, temp_c: float) -> None:
    verdict, reason = judge_temp(temp_c)
    conn.execute(
        """
        UPDATE probe_readings
        SET status = 'done', verdict = %s, reason = %s, processed_at = now()
        WHERE id = %s
        """,
        (verdict, reason, reading_id),
    )
    conn.commit()


def run_once(conn) -> bool:
    row = next_processing(conn)
    if not row:
        return False
    try:
        finish(conn, row["id"], float(row["temp_c"]))
    except Exception:
        # 判定失败：退回候审、清掉认领痕迹，允许重新认领，避免卡死在处理中。
        conn.execute(
            """
            UPDATE probe_readings
            SET status = 'pending', claimed_by = NULL, claimed_at = NULL
            WHERE id = %s
            """,
            (row["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
