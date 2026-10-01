# 冷链探头超温台

记录员上报探头编号与摄氏温度，可勾「冷媒加急」走专用车道。后台工人与人工认领共用同一排序——**先清冷媒加急、再碰普通，同级按编号从小到大**——按 **8℃** 上限判定 **合格** 或 **超温**。

## 冷媒车道

顶栏「冷媒车道」为落地页：

- 左列 = 加急候审，右列 = 普通候审；醒目标出「下一辆」（即当前可认领队头）。
- 加急记号只在记录员报温当下可勾，**随单冻住**，系统不提供任何事后改勾入口；读数总览（首页）提交的一律是普通单，首页勾选不算。
- 认领接口与「下一辆」预告共用同一条排序 SQL（`db.QUEUE_ORDER = urgent DESC, id ASC`），且只允许认领队头；两名记录员并发抢同一候审行时，行锁保证恰一笔进入 `processing`，另一笔收到 `409`。
- 值班员可查看两列、加急记号与下一辆，但不能勾加急、报温或认领。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
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
| logger | log123456 | 记录员，可报温/勾加急/认领 |
| logger2 | log2123456 | 记录员（第二名写账号，用于并发抢认） |
| watcher | watch123456 | 值班员，只读车道与列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
