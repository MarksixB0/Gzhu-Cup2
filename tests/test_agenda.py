# -*- coding: utf-8 -*-
"""日程:CRUD / 校验 / 到点提醒 / 与关怀账本互不干扰。

口径:
  - 只有填了时刻的才提醒;纪念日按「月-日」跨年命中(2/29 平年按 2/28);
  - 当天窗口补发,跨过午夜不补;同一事件同一自然日只提醒一次;
  - 改日期或时刻 → 重置「已提醒」,可以再提醒;
  - 提醒**不受**主动关怀开关与打扰等级约束,也**不动**关怀的任何计数。
"""
from datetime import datetime

import pytest

from gzhu_cup2.agent.agenda import reminder as arem
from gzhu_cup2.agent.agenda import store as astore
from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import runtime as crt

_USER = "agenda_u"
_OTHER = "agenda_other"


def _ts(y, m, d, hh=0, mm=0) -> float:
    return datetime(y, m, d, hh, mm).timestamp()


def _mk(**kw) -> dict:
    payload = {"kind": "todo", "title": "去拿快递", "date": "2026-10-27", "time": None}
    payload.update(kw)
    return astore.create_event(_USER, payload)


# ============================ CRUD 与校验 ============================

def test_create_and_list():
    ev = _mk()
    assert ev["id"] and ev["last_notified"] is None
    assert ev["yearly"] is False          # 待办默认一次性
    got = astore.list_events(_USER)
    assert [e["id"] for e in got] == [ev["id"]]


def test_create_anni_defaults_yearly():
    ev = _mk(kind="anni", title="和小咪相遇", date="2025-04-01")
    assert ev["yearly"] is True


def test_update_title_keeps_notified():
    ev = _mk(time="15:00")
    astore.mark_notified(_USER, ev["id"], "2026-10-27")
    got = astore.update_event(_USER, ev["id"], {"title": "去拿快递(改)"})
    assert got["title"] == "去拿快递(改)"
    assert got["last_notified"]["day"] == "2026-10-27"


def test_update_time_resets_notified():
    ev = _mk(time="15:00")
    astore.mark_notified(_USER, ev["id"], "2026-10-27")
    got = astore.update_event(_USER, ev["id"], {"time": "16:00"})
    assert got["time"] == "16:00"
    assert got["last_notified"] is None


def test_update_and_delete_missing():
    assert astore.update_event(_USER, "nope", {"title": "x"}) is None
    assert astore.delete_event(_USER, "nope") is False


def test_delete():
    ev = _mk()
    assert astore.delete_event(_USER, ev["id"]) is True
    assert astore.list_events(_USER) == []


@pytest.mark.parametrize("payload,msg", [
    ({"title": "   "}, "标题"),
    ({"title": "x" * 41}, "标题"),
    ({"date": "2026/10/27"}, "日期"),
    ({"time": "25:00"}, "时刻"),
    ({"time": "9点"}, "时刻"),
    ({"kind": "alarm"}, "类型"),
])
def test_validate_rejects(payload, msg):
    import copy
    base = {"kind": "todo", "title": "去拿快递", "date": "2026-10-27", "time": None}
    merged = copy.deepcopy(base)
    merged.update(payload)
    with pytest.raises(ValueError) as e:
        astore.create_event(_USER, merged)
    assert msg in str(e.value)


def test_time_normalized():
    assert _mk(time="9:5")["time"] == "09:05"


def test_user_isolation():
    _mk()
    assert astore.list_events(_OTHER) == []


# ============================ 到点判定 ============================

def test_only_events_with_time_are_due():
    _mk(time=None)
    assert arem.due_events(_USER, _ts(2026, 10, 27, 23, 0)) == []


def test_not_due_before_time():
    _mk(time="15:00")
    assert arem.due_events(_USER, _ts(2026, 10, 27, 14, 59)) == []


def test_due_within_same_day():
    _mk(time="15:00")
    assert len(arem.due_events(_USER, _ts(2026, 10, 27, 15, 0))) == 1
    # 当天晚些时候仍算到时(补发),不要求分秒精确
    assert len(arem.due_events(_USER, _ts(2026, 10, 27, 23, 30))) == 1


def test_not_due_across_midnight():
    """昨天 15:00 的事,今天不补发。"""
    _mk(time="15:00", date="2026-10-26")
    assert arem.due_events(_USER, _ts(2026, 10, 27, 9, 0)) == []


def test_yearly_hits_same_month_day():
    _mk(kind="anni", title="相遇纪念", date="2020-03-15", time="12:00")
    evs = arem.due_events(_USER, _ts(2026, 3, 15, 12, 0))
    assert [e["title"] for e in evs] == ["相遇纪念"]
    assert arem.due_events(_USER, _ts(2026, 3, 16, 12, 0)) == []


def test_yearly_event_without_time_is_quiet():
    _mk(kind="anni", title="相遇纪念", date="2020-03-15")
    assert arem.due_events(_USER, _ts(2026, 3, 15, 12, 0)) == []


def test_yearly_feb29_hits_feb28_on_common_year():
    _mk(kind="anni", title="闰日纪念", date="2024-02-29", time="09:00")
    # 平年 2025:2/29 不存在,按 2/28 命中
    evs = arem.due_events(_USER, _ts(2025, 2, 28, 9, 0))
    assert [e["title"] for e in evs] == ["闰日纪念"]
    # 闰年 2028:仍在 2/29 当天命中,2/28 不提前
    assert arem.due_events(_USER, _ts(2028, 2, 28, 9, 0)) == []
    evs = arem.due_events(_USER, _ts(2028, 2, 29, 9, 0))
    assert [e["title"] for e in evs] == ["闰日纪念"]


# ============================ 投递 ============================

def test_ensure_delivers_once(care_state_file, monkeypatch):
    monkeypatch.setattr(ccontent, "_llm_generate", lambda _m: "到点啦,该去拿快递了。")
    _mk(time="15:00")
    now = _ts(2026, 10, 27, 15, 0)

    first = arem.ensure(_USER, now)
    assert len(first) == 1 and first[0]["kind"] == "agenda"
    assert first[0]["text"] == "到点啦,该去拿快递了。"

    # 同一自然日不再重复
    assert arem.ensure(_USER, now) == []


def test_ensure_after_time_change(care_state_file, monkeypatch):
    monkeypatch.setattr(ccontent, "_llm_generate", lambda _m: "到点啦。")
    ev = _mk(time="15:00")
    now = _ts(2026, 10, 27, 15, 0)
    assert len(arem.ensure(_USER, now)) == 1
    astore.update_event(_USER, ev["id"], {"time": "15:00"})   # 同值不改提醒状态
    assert arem.ensure(_USER, now) == []
    astore.update_event(_USER, ev["id"], {"time": "14:00"})   # 真改了 → 重新提醒
    assert len(arem.ensure(_USER, now)) == 1


def test_ensure_falls_back_when_llm_fails(care_state_file, monkeypatch):
    """LLM 不可用时退模板 —— 闹钟不能静默。"""
    def boom(_m):
        raise RuntimeError("model down")
    monkeypatch.setattr(ccontent, "_llm_generate", boom)
    _mk(time="15:00", title="交房租")

    msgs = arem.ensure(_USER, _ts(2026, 10, 27, 15, 0))
    assert len(msgs) == 1 and "交房租" in msgs[0]["text"]


def test_ensure_caps_per_tick(care_state_file, monkeypatch):
    monkeypatch.setattr(ccontent, "_llm_generate", lambda _m: "到点啦。")
    for i in range(3):
        _mk(time="15:00", title="第%d件" % i)
    now = _ts(2026, 10, 27, 15, 0)

    assert len(arem.ensure(_USER, now)) == arem._MAX_PER_TICK
    # 剩下的下一轮继续,不会漏
    assert len(arem.ensure(_USER, now)) == 3 - arem._MAX_PER_TICK


def test_reminder_does_not_touch_care_counters(care_state_file, monkeypatch):
    monkeypatch.setattr(ccontent, "_llm_generate", lambda _m: "到点啦。")
    before = crt.status(_USER)
    _mk(time="15:00")
    arem.ensure(_USER, _ts(2026, 10, 27, 15, 0))
    after = crt.status(_USER)

    assert after["date_count"] == before["date_count"]
    assert after["last_proactive_ts"] == before["last_proactive_ts"]
    assert after["pending_count"] == before["pending_count"] + 1   # 只多了一条待读


def test_reminder_ignores_care_switch(care_state_file, monkeypatch):
    """用户关掉主动关怀,自己设的闹钟照样响。"""
    monkeypatch.setattr(ccontent, "_llm_generate", lambda _m: "到点啦。")
    crt.set_enabled(_USER, False)
    _mk(time="15:00")

    assert len(arem.ensure(_USER, _ts(2026, 10, 27, 15, 0))) == 1


# ============================ 清空记忆 ============================

def test_wipe_removes_agenda():
    from gzhu_cup2.agent.web import routes_admin, sessions
    user = sessions.default_user()
    astore.create_event(user, {"kind": "todo", "title": "去拿快递",
                               "date": "2026-10-27", "time": "15:00"})
    assert astore.list_events(user)

    out = routes_admin.memory_wipe(confirm=1)

    assert out["ok"] is True
    assert out["removed"].get("agenda") == 1
    assert astore.list_events(user) == []
