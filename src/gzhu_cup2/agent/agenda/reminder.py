# -*- coding: utf-8 -*-
"""日程到点提醒:判定、文案、投递。

只处理带具体时刻的日程;投递复用主动消息的 outbox 与会话历史。
"""
from __future__ import annotations

import logging
import time
from calendar import isleap
from datetime import datetime
from typing import Optional

from gzhu_cup2.agent.agenda import store as astore
from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import runtime as cruntime

logger = logging.getLogger(__name__)

# 落库与前端标签用的消息类型(见 CARE_KIND_LABEL)
KIND = "agenda"

# LLM 不可用时的兜底文案:只报事件标题,不加人格修饰
_FALLBACK = "到点啦:{title}"

# 单轮最多处理几条;超出的留给下一轮
_MAX_PER_TICK = 2


def _today_iso(now: float) -> str:
    return datetime.fromtimestamp(float(now)).date().isoformat()


def hit_day(ev: dict, day: str) -> bool:
    """这条日程在 day(YYYY-MM-DD)是否命中;纪念日按「月-日」跨年命中。"""
    d = str(ev.get("date") or "")
    if len(d) != 10:
        return False
    if not ev.get("yearly"):
        return d == day
    if d[5:] == day[5:]:
        return True
    # 2/29 的纪念日:平年落到 2/28 命中,否则全年静默
    return d[5:] == "02-29" and day[5:] == "02-28" and not isleap(int(day[:4]))


def due_events(user: str, now: Optional[float] = None) -> list:
    """已到点、今天还没提醒过的日程(按时刻先后)。"""
    now = float(now) if now is not None else time.time()
    day = _today_iso(now)
    hhmm = datetime.fromtimestamp(now).strftime("%H:%M")
    out = []
    for ev in astore.list_events(user):
        when = ev.get("time")
        if not when:                 # 没有具体时刻 → 不提醒
            continue
        if str(when) > hhmm:         # 还没到
            continue
        if not hit_day(ev, day):
            continue
        last = ev.get("last_notified")
        if isinstance(last, dict) and str(last.get("day") or "") == day:
            continue
        out.append(ev)
    return out


def _text_for(ev: dict) -> str:
    """先试角色卡口吻,失败退模板。"""
    title = str(ev.get("title") or "").strip()
    got = ccontent.generate_reminder(title, str(ev.get("time") or ""))
    if got:
        return got
    return _FALLBACK.format(title=title)


def ensure(user: str, now: Optional[float] = None) -> list:
    """调度线程每轮调用:把到点日程各投递一条,返回投递出去的消息列表。"""
    now = float(now) if now is not None else time.time()
    sent = []
    for ev in due_events(user, now)[:_MAX_PER_TICK]:
        text = _text_for(ev)
        msg_id = cruntime.mark_reminder(user, text, now=now, kind=KIND)
        # 先投递再标记已提醒,投递抛错时保持未标记
        astore.mark_notified(user, str(ev.get("id")), _today_iso(now))
        sent.append({
            "id": msg_id,
            "user": user,
            "kind": KIND,
            "topic": None,
            "text": text,
            "reason": "agenda_due",
            "created_ts": now,
        })
    return sent
