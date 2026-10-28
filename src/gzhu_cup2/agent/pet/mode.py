# -*- coding: utf-8 -*-
"""桌宠打扰等级:决定「此刻能多主动地打扰用户」。

约束:
1. 有序等级 —— 用序数比较,保证「更安静」包含「较安静」的全部行为;
2. 不阻断用户先开口 —— 静默只针对系统发起的行为;
3. 降级必须可被看见 —— 本模块只算等级,展示由前端负责。

来源优先级:手动临时 > 自动(夜间) > 用户默认。

与 care 安静时段独立且叠加:那个管"不发主动消息",本模块管"桌宠的行为等级"。
"""
from typing import Literal, Optional

from gzhu_cup2.agent.care.policy import is_quiet_hour
from gzhu_cup2.agent.config import config as cfg

Level = Literal["active", "quiet", "dnd", "asleep"]

# 有序:索引越大越安静。所有「至少到某档」的判断都走 rank 比较,不罗列组合。
LEVELS: tuple[str, ...] = ("active", "quiet", "dnd", "asleep")

# 手动菜单只暴露前三档 —— 休眠仅由夜间规则自动进入,
# 让用户认知负担不增,同时保留深夜「它睡了」的直觉。
MANUAL_CHOICES: tuple[str, ...] = ("active", "quiet", "dnd")

# 各档语义(前后端共用同一份口径,改动此处请同步 PLAN_NEXT.md §2.1 的表):
#   active 全开 : 主动消息 / 主动靠近 / 系统通知 / 待机小动作 全部可用
#   quiet  安静 : 不发主动消息、不主动靠近;待机小动作降频
#   dnd    勿扰 : 以上 + 不出系统通知;只播静止呼吸
#   asleep 休眠 : 以上 + 播放 Sleep;仅保留「被唤醒」


def normalize(level: Optional[str]) -> str:
    """规范化等级;未知/空值回落到**用户默认档**。

    不回落成最安静档:一次脏数据(手改状态文件)会变成永久静默。
    """
    v = str(level or "").strip().lower()
    fallback = str(cfg.PET_INTERRUPTION_DEFAULT).strip().lower()
    if v in LEVELS:
        return v
    return fallback if fallback in LEVELS else "active"


def normalize_manual(level: Optional[str]) -> str:
    """手动档位规范化:只允许 MANUAL_CHOICES。

    `asleep` 是合法等级但不是合法**手动**档 —— 休眠只由夜间规则进入。
    收口在接口层,否则"手动菜单不暴露休眠"只是 UI 上的君子协定,
    直接调 API 或手改状态文件都能把桌宠按进休眠。
    """
    v = normalize(level)
    if v in MANUAL_CHOICES:
        return v
    return normalize(str(cfg.PET_INTERRUPTION_DEFAULT))


def rank(level: Optional[str]) -> int:
    """等级的序数(0 = 最响)。"""
    return LEVELS.index(normalize(level))


def at_least(level: Optional[str], floor: str) -> bool:
    """``level`` 是否「至少和 ``floor`` 一样安静」。"""
    return rank(level) >= rank(floor)


def manual_expired(manual_until: Optional[float], now: float) -> bool:
    """手动覆盖是否已过期(``None`` = 不设到期,视为长期有效)。"""
    return manual_until is not None and float(manual_until) <= float(now)


def resolve(
    *,
    default_level: Optional[str],
    now: float,
    manual_level: Optional[str] = None,
    manual_until: Optional[float] = None,
    night_sleep: Optional[bool] = None,
    night_start_hour: Optional[int] = None,
    night_end_hour: Optional[int] = None,
) -> dict:
    """算出生效等级,返回 ``{level, source, until, reason}``。

    纯函数、零 IO:所有输入由调用方(``pet.runtime.current_mode``)从状态文件读出。
    优先级:**手动临时(未过期) > 自动(夜间) > 用户默认**。
    """
    now = float(now)

    # 1) 手动临时 —— 用户此刻的明确意愿,压过一切自动规则。
    #    过期不清理存储,只在读取时判定(下次设置会自然覆盖)。
    if manual_level and not manual_expired(manual_until, now):
        until = float(manual_until) if manual_until else None
        return {
            "level": normalize(manual_level),
            "source": "manual",
            "until": until,
            "reason": "manual",
        }

    # 2) 自动:夜间休眠。用与 care 同一套时段判断(默认 23–8,支持跨午夜)。
    enabled = cfg.PET_NIGHT_SLEEP_ENABLED if night_sleep is None else bool(night_sleep)
    start = cfg.PET_NIGHT_SLEEP_START_HOUR if night_start_hour is None else int(night_start_hour)
    end = cfg.PET_NIGHT_SLEEP_END_HOUR if night_end_hour is None else int(night_end_hour)
    if enabled and is_quiet_hour(now, start, end):
        return {"level": "asleep", "source": "auto", "until": None, "reason": "night"}

    # 3) 用户默认
    return {
        "level": normalize(default_level),
        "source": "default",
        "until": None,
        "reason": "default",
    }
