# -*- coding: utf-8 -*-
"""FastAPI Web 服务入口(供 Vue 前端调用)。

启动:
    export LLM_API_KEY=sk-xxx
    uv run uvicorn gzhu_cup2.entrypoints.api:app --host 127.0.0.1 --port 8000

**本文件只做组装**:创建 app、挂 CORS、随服务启停后台线程、include 各路由。
具体实现按关注点拆在 ``src/gzhu_cup2/agent/web/`` 与业务域:
    sessions.py       会话记录存储(零 FastAPI 依赖)
    engine.py         状态图单例 + 一轮执行 + 分析摘要
    care/scheduler.py 主动关怀调度线程
    routes_chat.py    /api/chat*、/api/threads*
    routes_care.py    /api/care/*、/api/pet/*
    routes_agenda.py  /api/agenda/*
    routes_admin.py   /api/health、/api/connection*、/api/memory/*
    routes_settings.py /api/settings
    routes_user.py    /api/user/profile(用户自述资料 + 首启引导状态)

接口清单以 OpenAPI(/docs)为准,不在本文件重复维护。

可选请求头 ``X-User-Id`` 决定长期记忆分区(缺省回落默认用户,见
``web/sessions.resolve_user``)。⚠ 它**不是鉴权**:真正的多用户仍需身份层。
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from gzhu_cup2.agent.care import scheduler as care_scheduler  # noqa: E402
from gzhu_cup2.agent.config import config as cfg  # noqa: E402
from gzhu_cup2.agent.long_memory.worker import ensure_worker_running, stop_worker  # noqa: E402
from gzhu_cup2.agent.web import routes_admin, routes_agenda, routes_care, routes_chat, routes_settings, routes_user  # noqa: E402


def _cors_settings() -> tuple:
    """解析 CORS 白名单与凭据开关,返回 ``(origins, allow_credentials)``。

    ⚠ "通配源 + 凭据"是非法的(Fetch 规范禁止;Starlette 会把通配源回显成
      请求方 Origin,等于放行任意网站)。故只要白名单含 ``"*"``,凭据一律强制 False。
    本应用不用 Cookie 鉴权,默认凭据即为 False。
    """
    origins = list(getattr(cfg, "CORS_ALLOW_ORIGINS", ()) or ())
    credentials = bool(getattr(cfg, "CORS_ALLOW_CREDENTIALS", False))
    if "*" in origins and credentials:
        credentials = False
    return origins, credentials


@asynccontextmanager
async def lifespan(_: FastAPI):
    """随服务启停后台线程:长期记忆固化 worker + 主动关怀调度线程。"""
    ensure_worker_running()
    care_scheduler.ensure_running()
    yield
    care_scheduler.stop()
    stop_worker()


app = FastAPI(title="心伴 心理健康陪伴助手 API", version="1.3.2", lifespan=lifespan)

_CORS_ORIGINS, _CORS_CREDENTIALS = _cors_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    # 与通配源互斥(见 _cors_settings);本应用不用 Cookie 鉴权,默认 False。
    allow_credentials=_CORS_CREDENTIALS,
    allow_methods=["*"],
    # X-User-Id 需显式放行:它是长期记忆按用户分区的标识(见 web/sessions.resolve_user)
    allow_headers=["*", "X-User-Id"],
)

app.include_router(routes_chat.router)
app.include_router(routes_care.router)
app.include_router(routes_agenda.router)
app.include_router(routes_admin.router)
app.include_router(routes_settings.router)
app.include_router(routes_user.router)
