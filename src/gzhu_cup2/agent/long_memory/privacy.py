# -*- coding: utf-8 -*-
"""记忆隐私控制:暂停记录 / 清空 / 导出(见 ``PLAN_NEXT.md`` §6)。

用户对自己心理数据的控制权,不该只体现在「能不能看见」,还要体现在
「能不能让它不存在」。本模块提供其中**可独立成立**的那一半(后端能力),
前端入口随后与设置面板重构一起做。

设计约束
--------
1. **暂停必须「如实可见」**。本模块只负责**如实返回状态**,展示由前端承担。
2. **暂停只影响写入,不影响对话**。用户照常聊天,只是不留痕。
3. **清空要能说清后果**。``wipe()`` 返回实际删了什么(命名空间 + 条数)。
4. **默认记录开启**。知情权体现在**暂停开关与清空/导出入口始终可得**。
"""
from gzhu_cup2.agent.common.json_state import JsonStateFile
from gzhu_cup2.agent.config import config as cfg


def _empty() -> dict:
    return {"users": {}}


def _ensure_memory_dir() -> None:
    from gzhu_cup2.agent.long_memory import paths
    paths.ensure_memory_dir()


_STATE = JsonStateFile(cfg.MEMORY_PRIVACY_PATH, _empty, on_default_path=_ensure_memory_dir)


# ============================ 路径与重置 ============================

def set_state_path(path: str) -> None:
    """重定向状态文件(测试/部署用);空串回到 config 默认。"""
    _STATE.set_path(path)


def reset() -> None:
    """清空隐私状态(测试隔离用)。"""
    _STATE.reset()


# ============================ 暂停记录 ============================

def _entry(data: dict, user: str) -> dict:
    e = data["users"].get(user)
    if e is None:
        e = {}
        data["users"][user] = e
    e.setdefault("recording", True)
    return e


def recording_enabled(user: str) -> bool:
    """是否允许为该用户写入记忆(默认 True)。"""
    with _STATE.view() as data:
        return bool(_entry(data, user).get("recording", True))


def set_recording(user: str, on: bool) -> bool:
    with _STATE.locked() as data:
        _entry(data, user)["recording"] = bool(on)
        return bool(on)


def status(user: str) -> dict:
    """给前端的隐私状态(前端负责常驻显示,见文件头 §1)。"""
    with _STATE.view() as data:
        return {"user": user, "recording": bool(_entry(data, user).get("recording", True))}
