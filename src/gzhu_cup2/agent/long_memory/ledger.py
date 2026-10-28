# -*- coding: utf-8 -*-
"""维度账本:从「已固化的 episode 标签卡」只读推导每个量表单维度的证据覆盖。

两个口径,用途不同:
  - ``scan_evidence``  归并成「最近一次 / 条数 / 最高档」,回答"这个维度有没有被覆盖"
    —— 缺口驱动的间接探询用它(uncovered_dims);
  - ``scan_daily``     保留时间轴,给出「哪一天是什么档」的序列
    —— 判定层做衰减累积用它。

两者都不新增持久化状态:维度证据随 episode 一并落库(见 store.commit_segment),
这里按需扫描。本模块只经由 long_memory.store 触碰 Store;纯归并逻辑可离线单测。
"""
import time
from datetime import datetime, timedelta
from typing import Optional

from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory.tags import DIM_KEYS, DIM_NAMES


def iter_episodes(user: str):
    """遍历该用户**未归档**的 episode,产出 ``(ts_end, value)``。

    ts 取 ``ts_end``(缺则 ``last_seen``),取不到有效时间戳的条目跳过。
    窗口过滤(按天/按 ts)由调用方各自施加。
    """
    if not user:
        return
    for it in mstore.list_items(mstore.ns_episodes(user)):
        v = it.value or {}
        if v.get("archived"):
            continue
        ts = float(v.get("ts_end") or v.get("last_seen") or 0.0)
        if ts <= 0:
            continue
        yield ts, v


def _level_quote(entry) -> tuple:
    """从一条维度证据取 (level, quote);level 不可转换时归 0(视为无证据)。"""
    if isinstance(entry, dict):
        try:
            level = int(entry.get("level"))
        except (TypeError, ValueError):
            level = 0
        quote = str(entry.get("quote") or "")
        return level, quote
    try:
        level = int(getattr(entry, "level", 0) or 0)
    except (TypeError, ValueError):
        level = 0
    return level, str(getattr(entry, "quote", "") or "")


def scan_daily(user: str, days: Optional[int] = None,
               now: Optional[float] = None) -> dict:
    """按本地自然日聚合维度证据 → {维度key: [(age_days, level), ...]}。

    ``age_days`` = 该证据所属自然日距 now 的整天数(0 = 今天),按由近到远排序。
    同一维度同一天出现多段时只取**最高档** —— 段数反映的是"说起过几次",
    不是"难受了几天",按段累加会让话说得多的人分数虚高。
    ``days`` 按自然日过滤(含今天),None 表示全部历史。
    """
    if not user:
        return {}
    now = float(now) if now is not None else time.time()
    today = datetime.fromtimestamp(now).date()
    start = today - timedelta(days=max(1, int(days)) - 1) if days else None

    per_dim: dict[str, dict] = {}
    for ts, v in iter_episodes(user):
        day = datetime.fromtimestamp(ts).date()
        if start is not None and day < start:
            continue
        for key, entry in (v.get("dimension_evidence") or {}).items():
            if key not in DIM_NAMES:
                continue
            level, _q = _level_quote(entry)
            if level <= 0:
                continue
            by_day = per_dim.setdefault(key, {})
            if level > by_day.get(day, 0):
                by_day[day] = level

    out: dict[str, list] = {}
    for key, by_day in per_dim.items():
        out[key] = sorted(((today - d).days, lv) for d, lv in by_day.items())
    return out


def scan_evidence(user: str, days: Optional[int] = None,
                  now: Optional[float] = None) -> dict:
    """扫描该用户已固化 episodes,归并出维度证据覆盖。

    返回 {维度key: {"count": 证据段数, "level": 最近一次档(1-3),
                   "max_level": 窗口内最高档, "last_ts": 最近证据时间, "quote": 最近引用}}
    days=None 表示全部历史;只统计未归档的 episode。
    """
    if not user:
        return {}
    now = now if now is not None else time.time()
    start_ts = (now - float(days) * 86400.0) if days else None
    merged: dict[str, dict] = {}

    for ts, v in iter_episodes(user):
        if start_ts is not None and ts < start_ts:
            continue
        ev = v.get("dimension_evidence") or {}
        for key, entry in ev.items():
            if key not in DIM_NAMES:
                continue
            level, quote = _level_quote(entry)
            if level <= 0:
                continue
            prev = merged.get(key)
            if prev is None or ts >= prev["last_ts"]:
                merged[key] = {
                    "count": int((prev or {}).get("count", 0)) + 1,
                    "level": level,
                    "max_level": max(int((prev or {}).get("max_level", 0)), level),
                    "last_ts": ts,
                    "quote": quote,
                }
            else:
                prev["count"] = int(prev.get("count", 0)) + 1
                prev["max_level"] = max(int(prev.get("max_level", 0)), level)
    return merged


def uncovered_dims(user: str, days: Optional[int] = None,
                   now: Optional[float] = None) -> list:
    """窗口内缺失证据的维度(按注册表顺序)—— 缺口账本,主动关怀探询候选。"""
    ev = scan_evidence(user, days=days, now=now)
    return [k for k in DIM_KEYS if k not in ev]


def has_episodes(user: str) -> bool:
    """该用户是否已有(未归档的)固化记忆段—— 探询候选的门槛。"""
    for _ts, _v in iter_episodes(user):
        return True
    return False
