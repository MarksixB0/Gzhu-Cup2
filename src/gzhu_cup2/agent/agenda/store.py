# -*- coding: utf-8 -*-
"""日程存储:用户自己录入的待办与纪念日(CRUD 与校验,零 FastAPI 依赖)。

字段与前端日历同构:``{id, kind, title, date, time, yearly}``,另带内部字段
``created_ts`` / ``updated_ts`` / ``last_notified``。

约定:
  - ``kind`` 只表达分类(``todo`` 待办 / ``anni`` 纪念日),``yearly`` 才是重复口径;
  - ``date`` 始终是完整日期;``yearly`` 时只有「月-日」参与命中,年份用来算第 N 年;
  - ``last_notified`` 记 ``{"day": "YYYY-MM-DD"}``,同一事件同一天只提醒一次;
    改日期或时刻时清空。
"""
from __future__ import annotations

import time
import uuid
from datetime import date as _date
from typing import Optional

from gzhu_cup2.agent.long_memory import store as mstore

KINDS = ("todo", "anni")
TITLE_MAX = 40


def _now() -> float:
    return time.time()


def _validate_title(raw) -> str:
    s = " ".join(str(raw or "").split())
    if not s:
        raise ValueError("标题不能为空")
    if len(s) > TITLE_MAX:
        raise ValueError("标题最长 %d 个字" % TITLE_MAX)
    return s


def _validate_date(raw) -> str:
    s = str(raw or "").strip()
    try:
        return _date.fromisoformat(s).isoformat()
    except (TypeError, ValueError):
        raise ValueError("日期格式应为 YYYY-MM-DD") from None


def _validate_time(raw) -> Optional[str]:
    """``None``/空串 = 没有具体时刻(当天待办,不提醒)。"""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError("时刻格式应为 HH:MM")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("时刻格式应为 HH:MM") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时刻超出范围")
    return "%02d:%02d" % (h, m)


def _validate_kind(raw) -> str:
    s = str(raw or "todo").strip()
    if s not in KINDS:
        raise ValueError("未知的日程类型: %s" % s)
    return s


def _sort_key(ev: dict) -> tuple:
    """按日期、时刻排;没有时刻的排当天最后。"""
    return (str(ev.get("date") or ""), str(ev.get("time") or "99:99"))


def list_events(user: str) -> list:
    items = mstore.list_items(mstore.ns_agenda(user), limit=2000)
    events = [dict(it.value) for it in items if isinstance(it.value, dict)]
    events.sort(key=_sort_key)
    return events


def create_event(user: str, payload: dict) -> dict:
    """建一条日程;字段非法抛 ``ValueError``(路由层转 400)。"""
    kind = _validate_kind(payload.get("kind"))
    now = _now()
    ev = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "title": _validate_title(payload.get("title")),
        "date": _validate_date(payload.get("date")),
        "time": _validate_time(payload.get("time")),
        # 纪念日默认每年重复;待办默认一次性;显式 None 视为未提供
        "yearly": bool(raw_yearly) if (raw_yearly := payload.get("yearly")) is not None else (kind == "anni"),
        "created_ts": now,
        "updated_ts": now,
        "last_notified": None,
    }
    mstore.put_item(mstore.ns_agenda(user), ev["id"], ev)
    return ev


def update_event(user: str, event_id: str, patch: dict) -> Optional[dict]:
    """改一条日程;事件不存在返回 ``None``。"""
    ns = mstore.ns_agenda(user)
    cur = mstore.get_item(ns, str(event_id))
    if not isinstance(cur, dict):
        return None
    ev = dict(cur)
    old_date, old_time = ev.get("date"), ev.get("time")

    if "title" in patch:
        ev["title"] = _validate_title(patch.get("title"))
    if "kind" in patch:
        ev["kind"] = _validate_kind(patch.get("kind"))
    if "date" in patch:
        ev["date"] = _validate_date(patch.get("date"))
    if "time" in patch:
        ev["time"] = _validate_time(patch.get("time"))
    if "yearly" in patch and patch.get("yearly") is not None:
        ev["yearly"] = bool(patch.get("yearly"))

    # 改日期或时刻才重置提醒状态;只改标题不动
    if ev.get("date") != old_date or ev.get("time") != old_time:
        ev["last_notified"] = None

    ev["updated_ts"] = _now()
    mstore.put_item(ns, str(event_id), ev)
    return ev


def delete_event(user: str, event_id: str) -> bool:
    """删一条日程;返回是否真的删掉了。"""
    ns = mstore.ns_agenda(user)
    if not isinstance(mstore.get_item(ns, str(event_id)), dict):
        return False
    mstore.delete_item(ns, str(event_id))
    return True


def mark_notified(user: str, event_id: str, day: str) -> None:
    """记下「这条日程在 day 这天已经提醒过」。"""
    ns = mstore.ns_agenda(user)
    ev = mstore.get_item(ns, str(event_id))
    if not isinstance(ev, dict):
        return
    ev = dict(ev)
    ev["last_notified"] = {"day": str(day)}
    ev["updated_ts"] = _now()
    mstore.put_item(ns, str(event_id), ev)
