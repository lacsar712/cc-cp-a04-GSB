import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import (
    QUEUE_ORDER,
    READING_COLS,
    create_pool,
    ensure_schema_async,
    reading_dict,
    seed_if_empty,
)

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 两名写权限账号可并发抢认；watcher 只读。
USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "logger2": {"role": "writer", "password_hash": pwd.hash("log2123456")},
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
            text=json.dumps({"detail": "仅记录员可执行该操作"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def http_error(status: int, detail: str) -> web.HTTPException:
    cls = {
        400: web.HTTPBadRequest,
        404: web.HTTPNotFound,
        409: web.HTTPConflict,
    }[status]
    return cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
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
    return web.json_response([reading_dict(r) for r in rows])


async def get_lane(request: web.Request) -> web.Response:
    """冷媒车道：左列加急候审、右列普通候审，并给出下一辆。

    取数与认领共用 QUEUE_ORDER，保证预告顺序即实领顺序。值班员同样可看。
    """
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        f"SELECT {READING_COLS} FROM probe_readings "
        f"WHERE status = 'pending' ORDER BY {QUEUE_ORDER}"
    )
    pending = [reading_dict(r) for r in rows]
    urgent = [r for r in pending if r["urgent"]]
    normal = [r for r in pending if not r["urgent"]]
    return web.json_response(
        {
            "urgent": urgent,
            "normal": normal,
            "next": pending[0] if pending else None,
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
        raise http_error(400, "探头编号不能为空")
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise http_error(400, "温度必须是数字") from exc

    # 冷媒加急记号只在报温当下可勾；随单冻住，系统不提供任何事后修改路径。
    urgent = body.get("urgent", False)
    if not isinstance(urgent, bool):
        raise http_error(400, "冷媒加急记号必须是布尔值")

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"""
        INSERT INTO probe_readings (probe_id, temp_c, urgent, status, created_by, created_at)
        VALUES ($1, $2, $3, 'pending', $4, now())
        RETURNING {READING_COLS}
        """,
        probe_id,
        temp_c,
        urgent,
        user["username"],
    )
    payload = reading_dict(row)
    payload["message"] = (
        "已入冷媒加急车道，将优先认领" if urgent else "已入队，按顺序候审"
    )
    return web.json_response(payload, status=201)


async def claim_reading(request: web.Request) -> web.Response:
    """认领队头候审单。

    排序与下一辆预告共用 QUEUE_ORDER（先加急后普通、同级编号升序），
    并把“只能认领队头”钉进同一条条件 UPDATE：只要还存在排得更前的候审单，
    目标行就不满足条件。两个写账号并发抢同一行时由行锁串行化，
    只可能有一笔改成 processing，另一笔重检后拿到 0 行 → 409。
    """
    user = require_writer(request)
    try:
        reading_id = int(request.match_info["id"])
    except ValueError as exc:
        raise http_error(400, "单号必须是整数") from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        f"""
        UPDATE probe_readings AS r
        SET status = 'processing', claimed_by = $2
        WHERE r.id = $1
          AND r.status = 'pending'
          AND NOT EXISTS (
              SELECT 1 FROM probe_readings AS h
              WHERE h.status = 'pending'
                AND (h.urgent > r.urgent
                     OR (h.urgent = r.urgent AND h.id < r.id))
          )
        RETURNING {READING_COLS}
        """,
        reading_id,
        user["username"],
    )
    if row is None:
        cur = await pool.fetchrow(
            "SELECT status FROM probe_readings WHERE id = $1", reading_id
        )
        if cur is None:
            raise http_error(404, "候审单不存在")
        if cur["status"] != "pending":
            raise http_error(409, "该候审单已被他人认领，请改领下一辆")
        head_id = await pool.fetchval(
            f"SELECT id FROM probe_readings WHERE status = 'pending' "
            f"ORDER BY {QUEUE_ORDER} LIMIT 1"
        )
        raise http_error(409, f"请按车道顺序认领，当前下一辆是 #{head_id}")
    return web.json_response(reading_dict(row))


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
    app.router.add_get("/api/lane", get_lane)
    app.router.add_post("/api/readings/{id}/claim", claim_reading)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
