# -*- coding: utf-8 -*-
"""Web 服务层(由 ``entrypoints/api.py`` 组装)。

分层与依赖方向::

    entrypoints/api.py       仅组装:app + CORS + lifespan + include_router
    web/routes_*.py          仅 HTTP:参数校验 + 调下层 + 序列化
    web/sessions.py          会话记录存储(零 FastAPI 依赖)
    web/engine.py            状态图单例 + 一轮执行 + 分析摘要(零 FastAPI 依赖)

主动关怀调度线程在 ``care/scheduler.py``(业务域,不在本层)。

依赖方向固定为 **web → 业务域(care / pet / long_memory / graphs)**;
业务域**绝不**反向依赖 web(唯一例外:``care/scheduler`` 经 ``web.sessions``
投递会话历史,见该模块 docstring)。``sessions`` / ``engine`` 可脱离
FastAPI 单独单测。
"""
