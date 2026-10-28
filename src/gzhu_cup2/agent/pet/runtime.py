# -*- coding: utf-8 -*-
"""桌宠运行时状态(纯文件 IO;与长期记忆/关怀数据分离)。

只存"桌宠自己的轻量状态":
  - 每日生活叙事生成开关(用户可关;默认见 cfg.PET_DAILY_DEFAULT_ENABLED);
  - 每个用户最近一次生成的自然日,保证"每天最多一次"。

设计约定:
  - 锁 / 原子替换 / 默认路径钩子等样板统一由 ``common.json_state.JsonStateFile``
    提供;
  - 测试用 ``set_state_path()`` 指到临时文件隔离(空串回到 config 默认)。

⚠ 与 care.runtime 是**各自独立的域**,各写各的文件,不共享状态。
"""
import time
from datetime import date

from gzhu_cup2.agent.common.json_state import JsonStateFile
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet import mode as pmode


def _empty() -> dict:
    return {"users": {}}


def _ensure_memory_dir() -> None:
    """使用默认路径时,确保统一记忆目录存在并完成旧数据一次性迁移。

    重定向到自定义路径(测试/部署)时**不会**被调用,避免无端创建记忆目录。
    """
    from gzhu_cup2.agent.long_memory import paths
    paths.ensure_memory_dir()


_STATE = JsonStateFile(cfg.PET_STATE_PATH, _empty, on_default_path=_ensure_memory_dir)


# ============================ 路径与重置 ============================

def set_state_path(path: str) -> None:
    """重定向状态文件(测试/部署用);空串回到 config 默认。"""
    _STATE.set_path(path)


def reset() -> None:
    """清空全部桌宠运行时状态(测试隔离用)。"""
    _STATE.reset()


# ============================ 领域辅助 ============================

def _entry(data: dict, user: str) -> dict:
    e = data["users"].get(user)
    if e is None:
        e = {}
        data["users"][user] = e
    e.setdefault("daily_enabled", bool(cfg.PET_DAILY_DEFAULT_ENABLED))
    e.setdefault("last_generated", None)
    # ---- 打扰等级(见 pet/mode.py 与 PLAN_NEXT.md §2) ----
    # interruption_level = 用户**默认**档(用户可改);manual_* = **临时**覆盖(带到期)。
    # 两者的优先级与自动规则都在 pet/mode.py::resolve 里算,这里只负责存。
    e.setdefault("interruption_level", str(cfg.PET_INTERRUPTION_DEFAULT))
    e.setdefault("night_sleep", bool(cfg.PET_NIGHT_SLEEP_ENABLED))
    e.setdefault("manual_level", None)
    e.setdefault("manual_until", None)
    # ---- 空闲时「主动走到鼠标旁」的行为开关与间隔(见 config/pet.py 默认值) ----
    e.setdefault("approach_enabled", bool(cfg.PET_APPROACH_ENABLED))
    e.setdefault("approach_interval_min", int(cfg.PET_APPROACH_INTERVAL_MIN))
    return e


# ============================ 开关与记录 ============================

def daily_enabled(user: str) -> bool:
    with _STATE.view() as data:
        return bool(_entry(data, user).get("daily_enabled", True))


def set_daily_enabled(user: str, on: bool) -> bool:
    with _STATE.locked() as data:
        _entry(data, user)["daily_enabled"] = bool(on)
        return bool(on)


def mark_generated(user: str, day: str | None = None) -> str:
    """记录某自然日已生成(幂等);返回记录日。"""
    day = day or date.today().isoformat()
    with _STATE.locked() as data:
        _entry(data, user)["last_generated"] = str(day)
        return str(day)


# ============================ 打扰等级(见 pet/mode.py) ============================

def _mode_from_entry(e: dict, now: float) -> dict:
    """把状态条目交给纯函数算出生效等级 —— 唯一的组装点。"""
    return pmode.resolve(
        default_level=e.get("interruption_level"),
        manual_level=e.get("manual_level"),
        manual_until=e.get("manual_until"),
        night_sleep=e.get("night_sleep"),
        now=now,
    )


def current_mode(user: str, now: float | None = None) -> dict:
    """当前生效的打扰等级(含来源与到期时间)。

    这是**门控唯一该读的入口** —— care 调度、接口层、桌面端都走它,
    避免各处各自拼装(拼装逻辑只在 _mode_from_entry 一份)。
    """
    ts = float(now) if now is not None else time.time()
    with _STATE.view() as data:
        return _mode_from_entry(_entry(data, user), ts)


def set_level(user: str, level: str) -> dict:
    """设置**用户默认**档(持久)。未知档位回落到默认档,不抛错。

    走 ``normalize_manual``:休眠不在手动可选档内,手动路径不得放行。
    """
    lvl = pmode.normalize_manual(level)
    with _STATE.locked() as data:
        _entry(data, user)["interruption_level"] = lvl
    return current_mode(user)


def set_manual(user: str, level: str, until: float | None = None) -> dict:
    """设置**临时**覆盖(可带到期时间戳;None = 长期有效直到被改)。

    同样走 ``normalize_manual`` —— 手动设成休眠会被规范化掉。
    """
    lvl = pmode.normalize_manual(level)
    with _STATE.locked() as data:
        e = _entry(data, user)
        e["manual_level"] = lvl
        e["manual_until"] = float(until) if until else None
    return current_mode(user)


def clear_manual(user: str) -> dict:
    """清除临时覆盖,回到「自动规则 + 默认档」。"""
    with _STATE.locked() as data:
        e = _entry(data, user)
        e["manual_level"] = None
        e["manual_until"] = None
    return current_mode(user)


def set_night_sleep(user: str, on: bool) -> dict:
    """夜间自动休眠开关(持久)。"""
    with _STATE.locked() as data:
        _entry(data, user)["night_sleep"] = bool(on)
    return current_mode(user)


# ============================ 主动靠近(鼠标追踪) ============================

def approach_prefs(user: str) -> dict:
    """空闲靠近行为的用户偏好:开关 + 间隔分钟数。"""
    with _STATE.view() as data:
        e = _entry(data, user)
        return {
            "approach_enabled": bool(e.get("approach_enabled", True)),
            "approach_interval_min": max(1, int(e.get("approach_interval_min", 10) or 10)),
        }


def set_approach(user: str, enabled: bool | None = None,
                 interval_min: int | None = None) -> dict:
    """更新靠近偏好;None = 不动。间隔夹取 1~120 分钟。"""
    with _STATE.locked() as data:
        e = _entry(data, user)
        if enabled is not None:
            e["approach_enabled"] = bool(enabled)
        if interval_min is not None:
            e["approach_interval_min"] = max(1, min(120, int(interval_min)))
    return approach_prefs(user)


def mode_status(user: str, now: float | None = None) -> dict:
    """给前端的完整模式视图:生效等级 + 各来源原值 + 可选档位/时长。

    前端需要它来「**把降级显示出来**」(PLAN_NEXT.md §2.1:静默降级会被误解成桌宠坏了),
    因此这里连档位清单与时间窗一起给,避免前端各写一份枚举。
    """
    ts = float(now) if now is not None else time.time()
    with _STATE.view() as data:
        e = _entry(data, user)
        active = _mode_from_entry(e, ts)
        return {
            "user": user,
            "level": active["level"],
            "source": active["source"],
            "reason": active["reason"],
            "until": active["until"],
            "default_level": pmode.normalize(e.get("interruption_level")),
            "manual_level": e.get("manual_level"),
            "manual_until": e.get("manual_until"),
            "night_sleep": bool(e.get("night_sleep", True)),
            "approach_enabled": bool(e.get("approach_enabled", True)),
            "approach_interval_min": max(1, int(e.get("approach_interval_min", 10) or 10)),
            "choices": list(pmode.MANUAL_CHOICES),
            "levels": list(pmode.LEVELS),
            "preset_minutes": list(cfg.PET_MANUAL_PRESET_MINUTES),
            "night_window": [
                int(cfg.PET_NIGHT_SLEEP_START_HOUR),
                int(cfg.PET_NIGHT_SLEEP_END_HOUR),
            ],
        }


def status(user: str) -> dict:
    with _STATE.view() as data:
        e = _entry(data, user)
        return {
            "user": user,
            "daily_enabled": bool(e.get("daily_enabled", True)),
            "last_generated": e.get("last_generated"),
        }
