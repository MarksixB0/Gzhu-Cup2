# -*- coding: utf-8 -*-
"""machine 组件:状态机,把分数落成档位。

进入/退出用双阈值,并按沉默时长与数据充足度分派 mode:
  mode = normal       按分数正常升降
         lapse        3~21 天没来:保持档位、不降
         lapse_decay  >21 天没来:每 14 天降一档
         insufficient 数据不足:保持档位,不做升降
"""
from __future__ import annotations

import logging
from typing import Optional

from gzhu_cup2.agent.algo.contracts import Decision
from gzhu_cup2.agent.config import config as cfg

logger = logging.getLogger(__name__)

TIER_RANK = {"stable": 0, "watch": 1, "elevated": 2}

LAPSE_START_DAYS = 3        # 距最近活跃 ≥3 天视为"沉默"(与 care 的 checkin 口径一致)
LAPSE_HOLD_DAYS = 21        # 沉默 ≤21 天:保持档位不动
LAPSE_DECAY_DAYS = 14       # 之后每 14 天降一档
MAX_STAY_DAYS = 28          # 同档最长停留(超时且无升档证据 → 降一档)


def _p(name: str, default: float) -> float:
    v = getattr(cfg, name, None)
    try:
        x = float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)
    # NaN 会让所有阈值比较恒为 False → 永不升档,属于"配错一个字符就静默失效"。
    return float(default) if x != x else x


def params() -> dict:
    """当前生效的进入/退出阈值(评估脚本可直接改 cfg 覆盖)。

    做一次顺序自检:迟滞的前提是 enter > exit,抄反了状态机会在两条线之间
    反复横跳且不报错,宁可就地纠正并留一行日志。
    """
    enter_watch = _p("GUARD_ENTER_WATCH", 0.55)
    enter_elevated = _p("GUARD_ENTER_ELEVATED", 0.78)
    exit_watch = _p("GUARD_EXIT_WATCH", 0.38)
    exit_elevated = _p("GUARD_EXIT_ELEVATED", 0.58)
    if not (enter_elevated > enter_watch > exit_watch) \
            or exit_elevated > enter_elevated or exit_elevated < exit_watch:
        logger.warning(
            "GUARD_* 阈值顺序异常(enter_watch=%.3f enter_elevated=%.3f "
            "exit_watch=%.3f exit_elevated=%.3f),已回退到默认值",
            enter_watch, enter_elevated, exit_watch, exit_elevated)
        enter_watch, enter_elevated = 0.55, 0.78
        exit_watch, exit_elevated = 0.38, 0.58
    return {
        "enter_watch": enter_watch,
        "enter_elevated": enter_elevated,
        "exit_watch": exit_watch,
        "exit_elevated": exit_elevated,
        "min_dwell_h": max(0.0, _p("GUARD_MIN_DWELL_HOURS", 48.0)),
    }


def _tier(v) -> str:
    s = str(v or "stable").strip().lower()
    return s if s in TIER_RANK else "stable"


def apply(f: Optional[dict], sc: Optional[dict], prev_tier: Optional[str],
          prev_since: Optional[float] = None, now: Optional[float] = None,
          protected_until: Optional[float] = None) -> Decision:
    """返回 {"tier", "mode", "reason", "score", "hold"}。

    ``hold=True`` 表示档位是被非分数原因保持的(沉默/数据不足/保护期),
    调用方据此不再叠加 v1 的降档保护期,避免双重迟滞。
    """
    import time as _t
    now = float(now) if now is not None else _t.time()
    p = params()
    prev = _tier(prev_tier)
    prev_rank = TIER_RANK[prev]
    # 分数必须夹到 [0,1]:NaN 与任何阈值比较都是 False,会一路掉到 stable,
    # 变成"越算越糊就判没事"。坏数据不能伪装成好消息。
    try:
        s = float((sc or {}).get("s_total") or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    if s != s:                      # NaN
        s = 0.0
    s = max(0.0, min(1.0, s))
    prot = protected_until is not None and now < float(protected_until)

    def _out(tier: str, mode: str, reason: str, hold: bool) -> dict:
        return {"tier": tier, "mode": mode, "reason": reason,
                "score": round(s, 4), "hold": bool(hold), "params": p}

    # ---------- 1. 沉默:优先级**高于**"数据不足" ----------
    # 顺序很重要:沉默时近 7 天窗口是空的,若先判"数据不足"就会保持档位、
    # 永远走不到 lapse_decay,档位被永久闩锁。
    # 例外:分数本身已达 watch 进入线(前驱路径支撑)。此时沉默不保持,
    # 落到正常判定。
    idle = (f or {}).get("idle_days")
    if idle is not None and int(idle) >= LAPSE_START_DAYS:
        idle = int(idle)
        if s < p["enter_watch"]:
            if idle <= LAPSE_HOLD_DAYS:
                return _out(prev, "lapse", "lapse_hold:%dd" % idle, True)
            steps = 1 + (idle - LAPSE_HOLD_DAYS) // LAPSE_DECAY_DAYS
            want = max(0, prev_rank - steps)
            tier = [k for k, v in TIER_RANK.items() if v == want][0]
            return _out(tier, "lapse_decay", "lapse_decay:%dd" % idle, True)
        # 分数够线 → 落到正常判定

    # ---------- 2. 数据不足:保持,不清零 ----------
    if not f or not sc or (sc.get("quality_note") == "insufficient"):
        return _out(prev, "insufficient", "no_data", True)

    # ---------- 3. 正常:进入 / 退出 / 停留上限 ----------
    # 升档立即生效
    if s >= p["enter_elevated"]:
        return _out("elevated", "normal", "enter_elevated", False)
    if s >= p["enter_watch"]:
        # 从 elevated 掉到"够 watch"这一档:停留期内/保护期内保持,
        # 期满后若仍在迟滞带 [exit_elevated, enter_elevated) 内则继续保持 elevated。
        if prev == "elevated":
            since = float(prev_since or now)
            if prot or (now - since) < p["min_dwell_h"] * 3600.0:
                return _out("elevated", "normal", "elevated_dwell", True)
            if s >= p["exit_elevated"]:
                return _out("elevated", "normal", "hold_elevated", True)
        return _out("watch", "normal", "enter_watch", False)

    # 分数低于进入线:已在高档时才谈退出
    if prev_rank > 0:
        if prot:
            return _out(prev, "normal", "crisis_protected", True)
        since = float(prev_since or now)
        dwelled = (now - since) >= p["min_dwell_h"] * 3600.0
        stayed = (now - since) >= MAX_STAY_DAYS * 86400.0
        if prev == "elevated":
            if s >= p["exit_watch"] and dwelled:
                return _out("watch", "normal", "deescalate", True)
            if stayed:
                # 停留超期且分数已明显回落 → 降一档,防止永久闩锁
                return _out("watch", "normal", "max_stay_decay", True)
            return _out(prev, "normal", "dwell_guard", True)
        # prev == watch
        if s >= p["exit_watch"] and dwelled:
            return _out("watch", "normal", "hold_watch", True)
        if stayed:
            return _out("stable", "normal", "max_stay_decay", True)
        return _out(prev, "normal", "dwell_guard", True)

    return _out("stable", "normal", "stable", False)
