# -*- coding: utf-8 -*-
"""跨域公共基础设施(与任何具体业务域无关,只依赖标准库)。

依赖方向固定为 **业务域 → common**,common 绝不反向 import 业务域。
"""
from gzhu_cup2.agent.common.console import force_utf8_stdout  # noqa: F401
from gzhu_cup2.agent.common.json_state import JsonStateFile  # noqa: F401

__all__ = ["JsonStateFile", "force_utf8_stdout"]
