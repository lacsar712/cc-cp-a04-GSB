import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import NEXT_HEAD_SQL, QUEUE_ORDER_SQL, judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 两名写权限账号（记录员）用于并发抢单；watcher 为只读值班员。
USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "logger2": {"role": "writer", "password_hash": pwd.hash("log223456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "值班员只读，不能报温或认领"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def _err(status: int, detail: str) -> web.HTTPException:
    cls = {400: web.HTTPBadRequest, 404: web.HTTPNotFound, 409: web.HTTPConflict}.get(
        status, web.HTTPInternalServerError
    )
    return cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def serialize_reading(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "is_rush": r["is_rush"],
        "claimed_by": r["claimed_by"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


READING_COLS = (
    "id, probe_id, temp_c, verdict, reason, status, is_rush, claimed_by, "
    "claimed_at, created_by, created_at, processed_at"
)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(f"SELECT {READING_COLS} FROM probe_readings ORDER BY id DESC")
    return web.json_response([serialize_reading(r) for r in rows])


async def get_queue(request: web.Request) -> web.Response:
    """冷媒车道：左列加急候审、右列普通候审，并标出下一辆。

    值班员可读但不可写。排序与认领、后台工人共用 rules.QUEUE_ORDER_SQL。
    """
    user = require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    pending = await pool.fetch(
        f"SELECT {READING_COLS} FROM probe_readings "
        f"WHERE status = 'pending' ORDER BY {QUEUE_ORDER_SQL}"
    )
    processing = await pool.fetch(
        f"SELECT {READING_COLS} FROM probe_readings "
        f"WHERE status = 'processing' ORDER BY {QUEUE_ORDER_SQL}"
    )
    proc_items = [serialize_reading(r) for r in processing]
    items = [serialize_reading(r) for r in pending]
    rush = [x for x in items if x["is_rush"]]
    normal = [x for x in items if not x["is_rush"]]
    # 下一辆与认领共用同一选取表达式（NEXT_HEAD_SQL），不在这里另算排序。
    next_id = await pool.fetchval(f"({NEXT_HEAD_SQL})")
    nxt = next((x for x in items if x["id"] == next_id), None)
    # 加急道还有单（候审或处理中）时，普通道闸口关闭——先清加急再碰普通。
    rush_active = bool(rush) or any(x["is_rush"] for x in proc_items)
    return web.json_response(
        {
            "rush": rush,
            "normal": normal,
            "next_id": nxt["id"] if nxt else None,
            "next_lane": (
                "rush" if nxt and nxt["is_rush"] else "normal" if nxt else None
            ),
            "rush_active": rush_active,
            "processing": proc_items,
            "viewer_role": user["role"],
        }
    )


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    # 冷媒加急记号只在报温这一刻可勾，写入后随单冻结，系统不提供任何修改入口。
    is_rush = bool(body.get("is_rush", False))

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"""
        INSERT INTO probe_readings (probe_id, temp_c, status, is_rush, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, $4, now())
        RETURNING {READING_COLS}
        """,
        probe_id,
        temp_c,
        is_rush,
        user["username"],
    )
    out = serialize_reading(row)
    out["message"] = "已进入冷媒车道候审（加急记号已随单冻结）" if is_rush else "已进入普通车道候审"
    return web.json_response(out, status=201)


async def claim_reading(request: web.Request) -> web.Response:
    """把“下一辆”候审单原子地置为处理中。

    用一条带 CTE 的 UPDATE：在同一条语句里先按共享排序选出当前下一辆，
    再把目标行从 pending 改成 processing。两名记录员并发抢同一行时，
    先到者拿行锁提交，后到者在 READ COMMITTED 下重读后匹配失败 → 409，
    全库至多一笔进入处理中。非下一辆（乱序认领）同样被拒。
    """
    user = require_writer(request)
    try:
        reading_id = int(request.match_info["id"])
    except (TypeError, ValueError) as exc:
        raise _err(400, "非法编号") from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"""
        WITH next_one AS ({NEXT_HEAD_SQL})
        UPDATE probe_readings r
        SET status = 'processing', claimed_by = $2, claimed_at = now()
        FROM next_one
        WHERE r.id = $1
          AND r.status = 'pending'
          AND r.id = next_one.id
        RETURNING {', '.join('r.' + c for c in READING_COLS.split(', '))}
        """,
        reading_id,
        user["username"],
    )
    if row:
        out = serialize_reading(row)
        out["message"] = f"已认领 #{reading_id}，进入处理中"
        return web.json_response(out)

    # 未命中：区分 不存在 / 已被他人认领或结案 / 普通道闸口关闭 / 非队首乱序。
    cur = await pool.fetchrow(
        "SELECT status, is_rush FROM probe_readings WHERE id = $1", reading_id
    )
    if not cur:
        raise _err(404, "该读数不存在")
    if cur["status"] != "pending":
        raise _err(409, "该候审单已被认领或已结案，处理中名额只有一个")
    if not cur["is_rush"]:
        raise _err(409, "冷媒加急车道尚未清空，请先处理完全部加急单")
    raise _err(409, "该单不是当前下一辆，请认领车道排序首位的候审单")


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/queue", get_queue)
    app.router.add_post(r"/api/readings/{id:\d+}/claim", claim_reading)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
