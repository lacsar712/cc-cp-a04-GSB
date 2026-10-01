"""后台工人：用 SKIP LOCKED 认领候审读数并写入合格/超温结论。

认领顺序与接口、下一辆预告完全一致：先冷媒加急后普通，同级按编号升序（见 db.QUEUE_ORDER）。
人工通过接口认领进 processing 的单，也由本工人兜底完成判定。
"""

import os
import time

from db import QUEUE_ORDER, connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    """认领队头候审单：加急优先、同级编号小的优先。

    用 FOR UPDATE 而非 SKIP LOCKED：队头正被人工认领事务锁住时等待，
    绝不跳到第二笔形成后台超车；对方提交后该行已非 pending，本语句自然顺延到新队头。
    """
    with conn.transaction():
        row = conn.execute(
            f"""
            SELECT id, probe_id, temp_c
            FROM probe_readings
            WHERE status = 'pending'
            ORDER BY {QUEUE_ORDER}
            FOR UPDATE
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE probe_readings SET status = 'processing', claimed_by = 'worker' "
            "WHERE id = %s",
            (row["id"],),
        )
        return row


def sweep_processing(conn):
    """接走一笔已在处理中（可能由人工认领）的单并返回，无则 None。"""
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id, probe_id, temp_c
            FROM probe_readings
            WHERE status = 'processing'
            ORDER BY id
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
    row = claim_one(conn)
    if not row:
        # 没有新候审单时，兜底完成人工认领进 processing 的单
        row = sweep_processing(conn)
    if not row:
        return False
    try:
        finish(conn, row["id"], float(row["temp_c"]))
    except Exception:
        conn.execute(
            "UPDATE probe_readings SET status = 'pending' WHERE id = %s",
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
