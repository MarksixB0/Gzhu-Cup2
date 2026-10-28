# -*- coding: utf-8 -*-
"""care 的轻量好奇钩子:用户自述 likes 只在无近期事实时兜底,且 7 天频控。"""
from datetime import datetime

from gzhu_cup2.agent.care import policy as cpolicy
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.care import coordinator
from gzhu_cup2.agent.core import user_profile as uprofile

_NOW = datetime(2026, 10, 26, 12, 0).timestamp()
_USER = "profile_like_u"


def _snap(**kw):
    base = dict(
        enabled=True, now=_NOW,
        quiet_start_hour=23, quiet_end_hour=7,
        max_per_day=10, cooldown_hours=0,
        recall_min_gap_hours=24,
        checkin_gap_hours=100,
        soothe_min_gap_hours=1,
        probe_min_gap_hours=6, probe_max_gap_hours=72,
        min_user_silence_minutes=0,
        farewell_quiet_hours=0,
        last_proactive_ts=None, date_count=0,
        last_user_ts=_NOW - 30 * 3600.0,
        last_farewell_ts=None,
        initiable_topics=[], profile_likes=["篮球"],
        last_profile_like_ts=None, profile_like_min_gap_days=7,
        recent_distress=False, probe_candidates=[],
        petlife_text=None, petlife_min_gap_hours=36, last_petlife_ts=None,
        last_delivered_ts=None, unanswered_streak=0,
        guard_tier="stable", force=False,
    )
    base.update(kw)
    return cpolicy.CareSnapshot(**base)


def test_profile_like_used_when_no_recent_topic():
    d = cpolicy.decide(_snap())
    assert d.due is True
    assert d.kind == "recall"
    assert d.topic == "篮球"
    assert d.reason == "profile_like"


def test_recent_fact_wins_over_profile_like():
    d = cpolicy.decide(_snap(initiable_topics=["最近在准备答辩"]))
    assert d.due is True
    assert d.topic == "最近在准备答辩"
    assert d.reason == "recall_topic"


def test_profile_like_respects_7_day_gap():
    d = cpolicy.decide(_snap(last_profile_like_ts=_NOW - 1 * 86400.0))
    assert d.due is False        # 未过 7 天,且没有其它到期项


def test_profile_like_allowed_after_gap():
    d = cpolicy.decide(_snap(last_profile_like_ts=_NOW - 8 * 86400.0))
    assert d.due is True
    assert d.reason == "profile_like"


def test_profile_likes_only_uses_likes():
    uprofile.set_field("likes", "篮球\n跑步、游泳")
    uprofile.set_field("dislikes", "下雨天")
    assert coordinator._profile_likes() == ["篮球", "跑步", "游泳"]


def test_mark_sent_records_profile_like_ts(care_state_file):
    cruntime.mark_sent(_USER, "recall", "篮球最近有打吗？",
                       topic="篮球", profile_like=True, now=_NOW)
    assert cruntime.counters(_USER)["last_profile_like_ts"] == _NOW

    cruntime.mark_sent(_USER, "recall", "答辩准备得怎么样？",
                       topic="答辩", now=_NOW + 3600.0)
    assert cruntime.counters(_USER)["last_profile_like_ts"] == _NOW


def test_coordinator_end_to_end_uses_profile_like(care_state_file, pet_state_file, monkeypatch):
    """整条链:没有近期事实时,evaluate 应生成一条以 likes 为话题的 recall。"""
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.care import content as ccontent

    uprofile.set_field("likes", "篮球")
    monkeypatch.setattr(cfg, "CARE_REQUIRE_ACTIVITY", False)
    monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "最近有打球吗？")

    msg = coordinator.evaluate("profile_like_e2e", now=_NOW)
    assert msg is not None
    assert msg["kind"] == "recall"
    assert msg["topic"] == "篮球"
    assert msg["reason"] == "profile_like"
    assert cruntime.counters("profile_like_e2e")["last_profile_like_ts"] == _NOW
