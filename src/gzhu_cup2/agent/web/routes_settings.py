# -*- coding: utf-8 -*-
"""统一设置面:把散在各域的用户设置聚成一个入口(见 ``PLAN_NEXT.md`` §7)。

本模块**不重新实现任何业务**:各域仍是唯一真相,这里只把既有读写函数聚合起来,
统一成两条接口::

    GET   /api/settings     → 全量快照(按域分组)
    PATCH /api/settings     → 增量更新(只改传了的键)

键名用扁平点号命名空间(``care.enabled``):一眼看出归属,未知键**明确拒绝**
(PATCH 走显式白名单,不认识就 400 并列出可用键)。

**连接**(base_url / api_key)不并入这里:它有自己的「测试连接」流程与
脱敏规则,写它要走 ``PUT /api/connection``。这里只提供**只读摘要**(Key 打码)。
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gzhu_cup2.agent.care import runtime as crt
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.config import runtime_connection
from gzhu_cup2.agent.core import user_profile as uprofile
from gzhu_cup2.agent.long_memory import privacy as mprivacy
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.pet import runtime as prt
from gzhu_cup2.agent.pet.character import loader as cloader
from gzhu_cup2.agent.web import sessions

router = APIRouter()


# ============================ 只读快照 ============================

def _connection_summary() -> dict:
    """连接摘要(Key 只回打码;写连接走 PUT /api/connection)。"""
    state = runtime_connection.load_state()
    key = str(state.get("api_key") or "")
    return {
        "base_url": str(state.get("base_url") or ""),
        "api_key_set": bool(key),
        "api_key_masked": runtime_connection.mask_key(key) if key else "",
        "active": runtime_connection.get_active() is not None,
    }


def snapshot(user: str) -> dict:
    """按域聚合的完整设置快照(供 GET 与 PATCH 的返回复用)。"""
    return {
        # 用户自述资料:第一次见面问的那三项,随时可改(见 core/user_profile.py)。
        # 放在最前:它是"关于我"的设置,与 persona/称呼直接相关。
        "profile": {
            "display_name": uprofile.get_field("display_name"),
            "likes": uprofile.get_field("likes"),
            "dislikes": uprofile.get_field("dislikes"),
        },
        "care": {
            "enabled": crt.enabled(user),
            # 只读的运行参数:展示给用户看"系统会多久找我一次",不在此处改
            "max_per_day": cfg.CARE_MAX_PER_DAY,
            "cooldown_hours": cfg.CARE_COOLDOWN_HOURS,
            "quiet_window": [cfg.CARE_QUIET_START_HOUR, cfg.CARE_QUIET_END_HOUR],
        },
        "pet": {
            "daily_enabled": prt.daily_enabled(user),
            "active_card": cloader.active_card_name() or None,
            **prt.approach_prefs(user),
        },
        "mode": prt.mode_status(user),
        "privacy": {
            **mprivacy.status(user),
            "memory_dir": cfg.MEMORY_DIR,
            "files": sessions.memory_files(),
        },
        "connection": _connection_summary(),
    }


@router.get("/api/settings")
def get_settings():
    """全量设置(按域分组)。

    分组顺序与前端设置面板的分区一致(profile / care / pet / mode / privacy / connection),
    这样前端加一个分区不需要改后端。
    """
    return snapshot(sessions.default_user())


# ============================ 增量更新 ============================
# 白名单:键 → 应用函数。新增设置项只需在这里加一行,不必再开新接口。
def _set_care_enabled(user: str, value: Any) -> None:
    crt.set_enabled(user, bool(value))


def _set_pet_daily(user: str, value: Any) -> None:
    prt.set_daily_enabled(user, bool(value))


def _set_pet_approach_enabled(user: str, value: Any) -> None:
    prt.set_approach(user, enabled=bool(value))


def _set_pet_approach_interval(user: str, value: Any) -> None:
    """靠近间隔(分钟)。select 传来的是字符串,这里统一转 int 并夹取范围。"""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="靠近间隔必须是分钟数")
    prt.set_approach(user, interval_min=minutes)


def _set_mode_default(user: str, value: Any) -> None:
    prt.set_level(user, str(value))


def _set_mode_night(user: str, value: Any) -> None:
    prt.set_night_sleep(user, bool(value))


def _set_mode_manual(user: str, value: Any) -> None:
    """临时覆盖(勿扰 / 专注)。

    值有**三种形态**:
      - 空串 / None → **清除**覆盖(回到「自动 + 默认」);
      - 档位字符串 → 长期有效,直到被改;
      - ``{"level": "dnd", "minutes": 60}`` → 带到期时间,到点自动恢复。

    一个键承载三种形态,不拆成 ``manual_level`` + ``manual_minutes``:两者是同一个
    动作的两半,拆开会出现「传了 minutes 没传 level」这种半截状态。
    """
    import time

    if value is None:
        prt.clear_manual(user)
        return
    if isinstance(value, dict):
        level = value.get("level")
        minutes = value.get("minutes")
    else:
        level, minutes = value, None

    if level is None or not str(level).strip():
        prt.clear_manual(user)
        return
    until = None
    if minutes:
        until = time.time() + float(minutes) * 60.0
    prt.set_manual(user, str(level), until)


def _set_privacy_recording(user: str, value: Any) -> None:
    mprivacy.set_recording(user, bool(value))


def _set_profile_field(field: str):
    """用户自述资料的三个可写键(称呼 / 喜欢 / 讨厌)。

    走统一设置面而不是单开 PATCH 接口:它们就是普通设置项,设置页是唯一入口;
    非法值(超长、未知字段)转 400 并在 detail 里说清限额 —— 与"未知键 400"同一口径。
    """

    def _apply(user: str, value: Any) -> None:
        try:
            uprofile.set_field(field, value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return _apply


_APPLIERS: Dict[str, Any] = {
    "profile.display_name": _set_profile_field("display_name"),
    "profile.likes": _set_profile_field("likes"),
    "profile.dislikes": _set_profile_field("dislikes"),
    "care.enabled": _set_care_enabled,
    "pet.daily_enabled": _set_pet_daily,
    "pet.approach_enabled": _set_pet_approach_enabled,
    "pet.approach_interval_min": _set_pet_approach_interval,
    "mode.default_level": _set_mode_default,
    "mode.night_sleep": _set_mode_night,
    "mode.manual_level": _set_mode_manual,
    "privacy.recording": _set_privacy_recording,
}


class SettingsPatch(BaseModel):
    """部分更新:``{"care.enabled": false, "mode.manual_level": "dnd"}``。

    扁平字典 + 显式白名单:点号键一眼看出归属,未知键被**明确拒绝**。
    """

    model_config = {"extra": "allow"}

    values: Dict[str, Any] = Field(
        default_factory=dict, description="兼容用:也可写成 {\"values\": {...}}")


def _iter_changes(req: SettingsPatch) -> Dict[str, Any]:
    """把请求体摊平成 {key: value};同时接受扁平写法与 {"values": {...}} 写法。"""
    changes = dict(req.values or {})
    for key, value in (req.model_extra or {}).items():
        changes[key] = value
    return changes


@router.patch("/api/settings")
def patch_settings(req: SettingsPatch):
    """增量更新:只改传了的键,其余不动。

    未知键 → 400 并列出可用键(不静默忽略)。
    """
    user = sessions.default_user()
    changes = _iter_changes(req)
    if not changes:
        return snapshot(user)

    unknown = sorted(k for k in changes if k not in _APPLIERS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知的设置项: {', '.join(unknown)};可用: {', '.join(sorted(_APPLIERS))}",
        )

    # 允许一次 PATCH 改多项:逐项应用,任一项失败就整体报错(不吞异常)
    for key, value in changes.items():
        _APPLIERS[key](user, value)
    return snapshot(user)


# ============================ 关于「清空记忆」的说明 ============================
# 删除类操作**不并入** PATCH:它们不可逆、需要显式确认、且要回执"删了什么",
# 语义与"改一个设置"完全不同 —— 留在 DELETE /api/memory/* (routes_admin)。
