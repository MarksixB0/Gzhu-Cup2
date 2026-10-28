# -*- coding: utf-8 -*-
"""「下周X」的周起始约定测试(锁"同一句话隔一天不能换一周"这条性质)。

实机数据(2026-10-18 / 10-19):
    10-18(周日)「我下周三有个模拟考，挺紧张的」→ 落库 2026-10-21
    10-19(周一)「我下周三什么来着?」        → 落库 2026-10-28
于是库内并存两条互相矛盾的考试日期。

这不是"解析错了",而是**周起始约定没被写下来**:
  · 周一起算:10-18 的「下周」= 10-19~10-25 → 下周三 = 10-21;
              10-19 的「下周」= 10-26~11-01 → 下周三 = 10-28(隔一天就换周);
  · 周日起算(国内日历普遍如此):两句都落在 10-28 —— 与用户自己的记忆一致。

因此这里不当"某句话 → 某一天"的夹具,而锁三条性质:
  1. 结果必须落在目标星期几,且在锚点之后;
  2. **同一周内的相邻两天**说同一个「下周X」,必须解析到**同一天**
     (周一起算在周末锚点上会破坏这条 —— 正是上述事故的成因);
  3. 两种约定在周末锚点上恰好差 7 天(所以"猜一个"是不可接受的)。
"""
from datetime import datetime, timedelta

import pytest

from gzhu_cup2.agent.long_memory import dates

_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
ANCHORS = [datetime(2026, 10, 12) + timedelta(days=i) for i in range(21)]


def _next_week(text: str, anchor: datetime, week_start: str) -> datetime:
    iso = dates.resolve_event_date(text, anchor, week_start=week_start)
    assert iso, (text, anchor, week_start)
    return datetime.strptime(iso, "%Y-%m-%d")


@pytest.mark.parametrize("week_start", ["sunday", "monday"])
@pytest.mark.parametrize("token", list(_WEEKDAY_CN))
def test_next_week_lands_on_requested_weekday(week_start, token):
    """性质 1:周一~周日七个 token 各自落到正确星期几,且严格在未来。"""
    text = f"我下周{token}有安排"
    for anchor in ANCHORS:
        got = _next_week(text, anchor, week_start)
        assert got.weekday() == _WEEKDAY_CN[token], (text, anchor, got)
        assert got > anchor, (text, anchor, got)
        assert (got - anchor).days <= 13, (text, anchor, got)   # 不跨两周以上


@pytest.mark.parametrize("week_start", ["sunday", "monday"])
def test_anchor_within_same_week_resolves_identically(week_start):
    """性质 2:同一周内换一天说话,「下周三」必须解析到同一天。

    ⚠ 这条在「周一起算 + 周日/周六锚点」下不成立 —— 那正是两条考试日期的来源。
    允许的例外只有"跨周"的那一跳,故按周分桶断言:同一个桶内结果必须唯一。
    """
    start = dates._week_start_index(week_start)
    buckets = {}
    for anchor in ANCHORS:
        key = (anchor - timedelta(days=(anchor.weekday() - start) % 7)).date()
        buckets.setdefault(key, set()).add(_next_week("我下周三要考试", anchor, week_start).date())
    for key, results in buckets.items():
        assert len(results) == 1, f"{week_start}: 同一周({key})内出现多个结果 {results}"


def test_monday_start_breaks_consistency_on_weekend_anchor():
    """反证:周一起算时,周末与周一说的「下周三」会差整整一周(事故复现)。"""
    sun, mon = datetime(2026, 10, 18), datetime(2026, 10, 19)
    a = _next_week("我下周三有个模拟考", sun, "monday")
    b = _next_week("我下周三什么来着", mon, "monday")
    assert (b - a).days == 7, (a, b)


def test_sunday_start_matches_user_ground_truth():
    """正解:按周日起算,10-18 与 10-19 的「下周三」都是 2026-10-28。"""
    assert dates.resolve_event_date("我下周三有个模拟考",
                                    datetime(2026, 10, 18), week_start="sunday") == "2026-10-28"
    assert dates.resolve_event_date("我下周三什么来着？",
                                    datetime(2026, 10, 19), week_start="sunday") == "2026-10-28"


def test_default_week_start_is_sunday(monkeypatch):
    """默认约定来自 config(可整体切回周一起算,不必改业务代码)。"""
    from gzhu_cup2.agent.config import config as cfg
    monkeypatch.setattr(cfg, "MEMORY_WEEK_START", "sunday")
    assert dates.resolve_event_date("我下周三有模拟考",
                                    datetime(2026, 10, 18)) == "2026-10-28"
    monkeypatch.setattr(cfg, "MEMORY_WEEK_START", "monday")
    assert dates.resolve_event_date("我下周三有模拟考",
                                    datetime(2026, 10, 18)) == "2026-10-21"


def test_absolutize_uses_same_convention():
    """落库文本的绝对化与 event_date 必须同源(否则文本说 21、字段说 28)。"""
    text = "用户下周三有个模拟考"
    anchor = datetime(2026, 10, 18)
    assert dates.absolutize(text, anchor, week_start="sunday") == "用户在2026-10-28有个模拟考"
    assert dates.resolve_event_date(text, anchor, week_start="sunday") == "2026-10-28"


def test_date_variant_merge_keeps_single_row():
    """落库侧:同一件事被锚成两个日期时,只留一条并保留首次断言。

    这是"两行并存"的写入路径回归 —— 若合并通道失效,日期约定再怎么对,
    库内也会重新长出第二条。
    """
    import os
    os.environ.setdefault("LONG_MEMORY_BACKEND", "memory")
    from gzhu_cup2.agent.long_memory import store

    user = "u-datevariant-test"
    store.merge_fact(user, "用户在2026-10-28有模拟考", "school", 0.7,
                     event_date="2026-10-28")
    store.merge_fact(user, "用户在2026-10-21有个模拟考", "school", 0.7,
                     event_date="2026-10-21")
    rows = list(store.list_items(store.ns_facts(user)))
    assert len(rows) == 1, [r.value.get("text") for r in rows]
    assert rows[0].value.get("event_date") == "2026-10-28"
