# -*- coding: utf-8 -*-
"""主动关怀的「在场门槛」测试:用户正在说话 / 刚道过晚安时一律不开口。

对应 2026-10-20 21:11 的实机事故:
  21:11:14 用户「睡了。」 → 21:11:15 桌宠弹出
  「今天中午阳光超好…你今天过得怎么样呀」(kind=petlife)。

根因不是文案,而是**节流比较对象错了**:此前所有门槛比的都是「距上一条主动
消息」,petlife 连"用户最近活跃"都不看 —— 于是"刚结束对话就追加一句"在逻辑上
完全合规。这里按性质断言:**不论哪一类消息、不论别的门槛多么宽松,
只要用户还在场(或刚宣告离场),就不得开口。**
"""
import time

import pytest

from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.care.policy import CareSnapshot, decide

NOW = time.time() - 60.0     # 锚定当前时刻,用例内全部用相对偏移,不依赖墙钟推移
HOUR = 3600.0
DAY = 86400.0


def snap(**over):
    """默认:所有"该发"的条件都满足 —— 只留在场门槛这一个变量。"""
    base = dict(
        enabled=True, now=NOW,
        quiet_start_hour=0, quiet_end_hour=0,
        max_per_day=99, cooldown_hours=0,
        recall_min_gap_hours=0, checkin_gap_hours=0,
        soothe_min_gap_hours=0, probe_min_gap_hours=0, probe_max_gap_hours=999,
        min_user_silence_minutes=30, farewell_quiet_hours=8,
        last_proactive_ts=None, date_count=0,
        last_user_ts=NOW - 2 * DAY,
        last_farewell_ts=None,
        initiable_topics=["模拟考"],
        recent_distress=True,
        probe_candidates=["phq_sleep"],
        petlife_text="今天中午阳光超好",
        last_petlife_ts=None,
        last_delivered_ts=None, unanswered_streak=0,
        guard_tier="elevated",
    )
    base.update(over)
    return CareSnapshot(**base)


# ============================== 门槛:用户正在说话 ==============================
@pytest.mark.parametrize("gap_seconds", [1, 5, 60, 60 * 29])
def test_no_kind_fires_while_user_is_active(gap_seconds):
    """用户最后一句话距现在不足静默门槛 → 无论哪类消息都不发。"""
    d = decide(snap(last_user_ts=NOW - gap_seconds))
    assert d.due is False, d
    assert d.reason == "user_active"


def test_all_kinds_blocked_not_just_petlife():
    """逐类确认:soothe/checkin/probe/recall/petlife 一个都不能漏。

    用"去掉在场门槛后它确实会发"来反证 —— 否则断言可能因为别的门槛而空过。
    ``guard_tier`` 固定为 stable:本文测的是在场门槛,不是守护升温的排序。
    """
    want = {
        "soothe": dict(recent_distress=True, initiable_topics=[], probe_candidates=[],
                       petlife_text=None),
        "checkin": dict(recent_distress=False, initiable_topics=[], probe_candidates=[],
                        petlife_text=None),
        "probe": dict(recent_distress=False, initiable_topics=[],
                      petlife_text=None),
        "recall": dict(recent_distress=False, probe_candidates=[], petlife_text=None),
        "petlife": dict(recent_distress=False, initiable_topics=[], probe_candidates=[]),
    }
    for kind, over in want.items():
        free = decide(snap(last_user_ts=NOW - 2 * DAY, guard_tier="stable", **over))
        assert free.due and free.kind == kind, (kind, free)
        busy = decide(snap(last_user_ts=NOW - 1, guard_tier="stable", **over))
        assert busy.due is False, (kind, busy)


def test_silence_threshold_boundary():
    """门槛边界:29 分钟不发,31 分钟可发(严格按配置)。"""
    assert decide(snap(last_user_ts=NOW - 29 * 60)).due is False
    assert decide(snap(last_user_ts=NOW - 31 * 60)).due is True


# ============================== 门槛:用户已宣告离场 ==============================
@pytest.mark.parametrize("elapsed_hours", [1 / 60, 1, 7.9])
def test_farewell_suppresses_any_kind(elapsed_hours):
    """说完「睡了」之后数小时内一律静默,即使距上次主动已很久、用户也早就静默。"""
    d = decide(snap(last_farewell_ts=NOW - elapsed_hours * HOUR))
    assert d.due is False and d.reason == "user_farewell", d


def test_farewell_expires():
    """离场静默会过期(否则一次晚安就把桌宠永久关掉)。"""
    assert decide(snap(last_farewell_ts=NOW - 8.1 * HOUR)).due is True


def test_farewell_does_not_unlock_for_guard_escalation():
    """守护升温也不得绕过离场门槛:用户正在睡,不是"该更温柔地陪着"。"""
    d = decide(snap(last_farewell_ts=NOW - 600, guard_tier="elevated",
                    recent_distress=True))
    assert d.due is False


# ============================== 离场识别 ==============================
@pytest.mark.parametrize("text", ["睡了。", "晚安", "我先睡啦", "去睡了", "我困了",
                                  "该睡了，明天见"])
def test_farewell_detected(text):
    assert cruntime.is_farewell(text) is True, text


@pytest.mark.parametrize("text", ["你睡了吗", "你还不睡?", "睡不着", "早", "在吗。",
                                  "我今天睡了一下午"])
def test_not_farewell(text):
    """疑问句与"睡不着"不算离场 —— 判错会让桌宠整晚静默(真实的错过关心)。"""
    assert cruntime.is_farewell(text) is False, text


def test_note_user_turn_sets_then_clears(care_state_file):
    """落库语义:命中离场 → 记时刻;用户再说话 → 清除标记。"""
    assert cruntime.note_user_turn("u-x", "睡了。", now=NOW) is True
    assert cruntime.counters("u-x")["last_farewell_ts"] == NOW
    assert cruntime.note_user_turn("u-x", "早啊", now=NOW + 9 * HOUR) is True
    assert cruntime.counters("u-x")["last_farewell_ts"] is None
    # 没有变化时不重复写盘(正常对话轮次不为"没离场"这件事落文件)
    assert cruntime.note_user_turn("u-x", "今天吃什么", now=NOW + 9 * HOUR) is False


# ============================== 投递时二次判活 ==============================
def test_pending_drops_messages_superseded_by_user_turn(care_state_file, monkeypatch):
    """outbox 里"生成后用户又先说话了"的消息,投递时静默丢弃(不补发)。

    事故同源:一条 21:11:05 生成的 petlife 压到 21:11:15 客户端轮询才弹,
    而用户 21:11:14 已经说了「睡了。」—— 弹出来就是"刚说完晚安又被搭话"。
    """
    cruntime.mark_sent("u-y", "petlife", "今天中午阳光超好", now=NOW - 10)
    monkeypatch.setattr(cruntime, "_last_user_ts", lambda user: NOW - 5)
    assert cruntime.pending("u-y") == []

    monkeypatch.setattr(cruntime, "_last_user_ts", lambda user: NOW - 20)
    assert len(cruntime.pending("u-y")) == 1
