# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁判定按 **8℃** 上限为 **合格** 或 **超温**。冷媒（加急）探头走专用车道**插队**：先清加急、再碰普通，各自按编号从小到大放行。

## 冷媒车道规则

- 顶栏默认落地页即 **❄ 冷媒车道**：**左列＝加急候审**，**右列＝普通候审**，顶部横幅标出 **下一辆**是谁。
- 记录员**报温时**可勾「冷媒加急」。记号**随单冻结**——提交后在台账/车道均只读，系统不提供任何事后改勾入口。
- **认领顺序与“下一辆”预告共用同一段数据库选取表达式**（`rules.NEXT_HEAD_SQL`）：先清加急再碰普通，组内按编号升序。只要还有加急单在候审或处理中，普通车道闸口关闭。
- 认领把候审单原子地置为「处理中」：一条带 CTE 的 `UPDATE … FROM (下一辆)` 完成选队首＋翻状态。两名写账号几乎同时抢同一候审行时，**只许一笔进处理中，另一笔收到 409**。
- 值班员只读：能看加急记号与下一辆，**不能勾加急、不能报温、也不能认领**（写接口返回 403）。

### 插队验收场景

先报普通「探头丙」、再报加急「探头丁」：下一辆预告与实际认领判定顺序都必须 **先丁后丙**；丁在处理中期间普通车道仍闸闭，丁结案后丙才放行。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`，只判已认领的 processing 单） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员（写），可报温、勾加急、认领 |
| logger2 | log223456 | 记录员（写），第二名写账号，用于并发抢单 |
| watcher | watch123456 | 值班员（只读），可看车道/记号/下一辆 |

## 接口

| 方法 路径 | 权限 | 说明 |
|-----------|------|------|
| `POST /api/auth/login` | 公开 | 登录换取 JWT |
| `GET  /api/queue` | 登录 | 车道：`rush`/`normal` 候审列、`next_id`、`rush_active`、`processing` |
| `POST /api/readings` | 记录员 | 报温入队，body 可带 `is_rush`（仅此一次可设，随后冻结） |
| `POST /api/readings/{id}/claim` | 记录员 | 原子认领「下一辆」→处理中；非队首/加急未清/已被认领返回 409 |
| `GET  /api/readings` | 登录 | 全量台账（含只读 `is_rush`、`claimed_by`） |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。候审单停在车道上等待人工认领；后台工人只判定已进入「处理中」的单。
