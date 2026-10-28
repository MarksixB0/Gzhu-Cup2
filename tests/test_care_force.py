# -*- coding: utf-8 -*-
"""手动强制触发(force)的口径回归。

钉死三条:
  1. force 跳过全部**时机闸**(免扰/每日上限/沉默门槛/晚安禁言/未回应退避/冷却),
     且 checkin 兜底无条件可用 —— 手动点击保证"必有一条消息的资格";
  2. force 不绕过开关门:总开关关、打扰等级 quiet+ 时 evaluate 仍返回 None;
  3. force=False(默认)行为与旧版完全一致 —— 全部闸照常生效。
"""
from datetime import datetime

from gzhu_cup2.agent.care import policy as cpolicy
from gzhu_cup2.agent.config import config as cfg

_USER = "care_force_u"

# 本地 14:00(与机器时区无关:timestamp() 与 fromtimestamp() 同一钟)
_NOW = datetime(2026, 10, 26, 14, 0, 0).timestamp()


def _hostile_snap(**kw):
    """一个把所有时机闸都调成"拒绝"的快照:免扰时段内/额度用尽/冷却中/
    刚聊过天/刚说晚安/连续未回应。"""
    base = dict(
        now=_NOW,
        quiet_start_hour=12, quiet_end_hour=23,       # 14 点正处免扰时段
        max_per_day=0,                                # 每日额度已用尽
        cooldown_hours=48, last_proactive_ts=_NOW - 3600,
        min_user_silence_minutes=120, last_user_ts=_NOW - 60,   # 1 分钟前刚聊过
        farewell_quiet_hours=12, last_farewell_ts=_NOW - 600,   # 刚说完晚安
        last_delivered_ts=_NOW - 600, unanswered_streak=3,      # 未回应退避中
        checkin_gap_hours=48,
    )
    base.update(kw)
    return cpolicy.CareSnapshot(**base)


def test_force_bypasses_all_timing_gates():
    d = cpolicy.decide(_hostile_snap(force=True))
    assert d.due is True
    # 各 kind 链的候选都为空(无 distress/话题/探询/叙事)→ checkin 兜底
    assert d.kind == "checkin"
    assert d.reason == "manual_force"


def test_no_force_respects_all_timing_gates():
    d = cpolicy.decide(_hostile_snap())
    assert d.due is False


def test_force_still_respects_enabled_switch():
    d = cpolicy.decide(_hostile_snap(force=True, enabled=False))
    assert d.due is False
    assert d.reason == "disabled"


def test_force_keeps_kind_priority_chain():
    """force 只跳时机闸,不改变选材优先级:有安全话题时 recall 优先于 checkin。"""
    d = cpolicy.decide(_hostile_snap(
        force=True,
        initiable_topics=["用户的猫最近换毛"],
        recall_min_gap_hours=0.0,
    ))
    assert d.due is True
    assert d.kind == "recall"


# ---------- coordinator 层:开关门不绕,记账一致 ----------
def test_coordinator_force_respects_quiet_mode(care_state_file, pet_state_file):
    from gzhu_cup2.agent.care import coordinator as ccoord
    from gzhu_cup2.agent.care import runtime as cruntime
    from gzhu_cup2.agent.pet import runtime as prt
    prt.set_level(_USER, "quiet")
    assert ccoord.evaluate(_USER, now=_NOW, force=True) is None
    # 总开关同样不绕
    monkey_off = cfg.CARE_ENABLED
    try:
        cfg.CARE_ENABLED = False
        assert ccoord.evaluate(_USER, now=_NOW, force=True) is None
    finally:
        cfg.CARE_ENABLED = monkey_off
    assert cruntime.counters(_USER)["date_count"] == 0


def test_coordinator_force_generates_and_counts(care_state_file, pet_state_file,
                                                monkeypatch):
    from gzhu_cup2.agent.care import content as ccontent
    from gzhu_cup2.agent.care import coordinator as ccoord
    from gzhu_cup2.agent.care import runtime as cruntime

    monkeypatch.setattr(cfg, "CARE_REQUIRE_ACTIVITY", False)   # 从未活跃也可被自己叫出来
    monkeypatch.setattr(ccontent, "_llm_generate",
                        lambda messages: "嗨，我在呢。今天想找你说说话。")
    msg = ccoord.evaluate(_USER, now=_NOW, force=True)
    assert msg is not None
    assert msg["kind"] == "checkin"
    assert msg["reason"] == "manual_force"
    # 手动消息与其他主动消息共用同一套账本(冷却/每日上限/退避都看它)
    assert cruntime.counters(_USER)["date_count"] == 1


def test_coordinator_force_generation_failure_stays_silent(care_state_file,
                                                           pet_state_file,
                                                           monkeypatch):
    """LLM 失败/被过滤 → mark_blocked 静默,不占当日额度(与调度线程同口径)。"""
    from gzhu_cup2.agent.care import content as ccontent
    from gzhu_cup2.agent.care import coordinator as ccoord
    from gzhu_cup2.agent.care import runtime as cruntime

    monkeypatch.setattr(cfg, "CARE_REQUIRE_ACTIVITY", False)
    monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "")
    assert ccoord.evaluate(_USER, now=_NOW, force=True) is None
    counters = cruntime.counters(_USER)
    assert counters["date_count"] == 0


# ---------- 路由层:勿扰下给前端明确的置灰信号 ----------
def test_route_force_blocked_in_quiet_mode(api_client, care_state_file,
                                           pet_state_file):
    from gzhu_cup2.agent.pet import runtime as prt
    from gzhu_cup2.agent.web import sessions
    prt.set_level(sessions.default_user(), "quiet")
    r = api_client.post("/api/care/evaluate", json={"force": True})
    assert r.status_code == 200
    assert r.json() == {"messages": [], "blocked": "quiet_mode"}


def test_route_accepts_legacy_bodyless_post(api_client):
    """旧调用(无 body)必须继续工作:force 默认 False。"""
    r = api_client.post("/api/care/evaluate")
    assert r.status_code == 200
    assert "messages" in r.json()
