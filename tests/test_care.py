# -*- coding: utf-8 -*-
"""care(主动关怀)纯逻辑离线测试:policy 决策短路规则 + content 消息安全。

不触网、不依赖记忆 store(policy 全部输入走 CareSnapshot)。
记忆相关(ticks.last_activity_ts)用 conftest 的内存后端跑一条冒烟。
"""
import time
from datetime import datetime

from gzhu_cup2.agent.care.policy import CareSnapshot, decide, is_quiet_hour
from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks

NOW = time.time()
HOUR = 3600.0
DAY = 86400.0


def snap(**over):
    base = dict(
        enabled=True,
        now=NOW,
        quiet_start_hour=0,      # 默认无免扰,避免测试在深夜执行被误短路
        quiet_end_hour=0,
        max_per_day=2,
        cooldown_hours=4,
        recall_min_gap_hours=24,
        checkin_gap_hours=48,
        last_proactive_ts=None,
        date_count=0,
        last_user_ts=NOW - 2 * DAY,   # 默认:2 天没来
        initiable_topics=[],
    )
    base.update(over)
    return CareSnapshot(**base)


# ============================== policy:短路规则 ==============================
class TestDecideGates:
    def test_disabled_never_speaks(self):
        d = decide(snap(enabled=False))
        assert d.due is False and d.reason == "disabled"

    def test_quiet_hour_blocks(self):
        d = decide(snap(quiet_start_hour=23, quiet_end_hour=8,
                        now=datetime(2026, 1, 1, 2, 0).timestamp()))
        assert d.due is False and d.reason == "quiet_hour"

    def test_daily_cap_blocks(self):
        d = decide(snap(date_count=2))
        assert d.due is False and d.reason == "daily_cap"

    def test_cooldown_blocks(self):
        d = decide(snap(last_proactive_ts=NOW - 1 * HOUR))
        assert d.due is False and d.reason == "cooldown"

    def test_never_active_treated_as_long_absent(self):
        d = decide(snap(last_user_ts=None))
        assert d.due is True and d.kind == "checkin"


# ============================== policy:两类触发 ==============================
class TestDecideKinds:
    def test_recall_when_topic_and_gap_ok(self):
        d = decide(snap(last_user_ts=NOW - 25 * HOUR,
                        initiable_topics=["用户喜欢打篮球"]))
        assert d.due is True
        assert d.kind == "recall"
        assert d.topic == "用户喜欢打篮球"

    def test_recall_needs_min_gap(self):
        d = decide(snap(last_user_ts=NOW - 2 * HOUR,
                        initiable_topics=["用户喜欢打篮球"]))
        assert d.due is False and d.reason == "not_due"

    def test_checkin_when_gap_long(self):
        d = decide(snap(last_user_ts=NOW - 50 * HOUR))
        assert d.due is True and d.kind == "checkin"

    def test_silent_when_recent_user_activity(self):
        d = decide(snap(last_user_ts=NOW - 1 * HOUR))
        assert d.due is False and d.reason == "not_due"


# ============================== policy:免扰时段 ==============================
class TestQuietHour:
    def test_non_wrap(self):
        assert is_quiet_hour(datetime(2026, 1, 1, 1, 0).timestamp(), 0, 6) is True
        assert is_quiet_hour(datetime(2026, 1, 1, 12, 0).timestamp(), 0, 6) is False

    def test_wrap_midnight(self):
        # 23:00-08:00 跨午夜:23 点与凌晨 2 点都在免扰,中午 12 点不在,8 点整不在
        ts = lambda h: datetime(2026, 1, 1, h, 0).timestamp()
        assert is_quiet_hour(ts(23), 23, 8) is True
        assert is_quiet_hour(ts(2), 23, 8) is True
        assert is_quiet_hour(ts(8), 23, 8) is False
        assert is_quiet_hour(ts(12), 23, 8) is False

    def test_equal_hours_never_quiet(self):
        assert is_quiet_hour(NOW, 8, 8) is False


# ============================== content:LLM 生成(无模板兜底) ==============================
class TestContent:
    def test_llm_generate_checkin_returns_injected(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "嗨，最近过得怎么样呀？")
        assert ccontent.generate_message("checkin") == "嗨，最近过得怎么样呀？"

    def test_forbidden_token_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "你的风险等级是 high")
        assert ccontent.generate_message("checkin") is None

    def test_generator_exception_returns_none(self, monkeypatch):
        def _boom(messages):
            raise RuntimeError("llm down")

        monkeypatch.setattr(ccontent, "_llm_generate", _boom)
        assert ccontent.generate_message("checkin") is None

    def test_empty_output_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "")
        assert ccontent.generate_message("checkin") is None

    def test_no_internal_markers_in_llm_context(self):
        # 提示词构造(直接走 prompts.care_prompts)本身不含内部标记
        from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt
        forbidden = ("PHQ", "GAD", "C-SSRS", "风险", "等级", "量表", "分数", "危急", "热线")
        msgs = build_care_llm_prompt("recall", context={"topic": "打篮球"})
        human = msgs[1].content
        for token in forbidden:
            assert token not in human, f"提示词泄漏内部标记: {human}"


# ============================== 记忆读函数冒烟 ==============================
class TestLastActivityHelper:
    def test_empty_user_none(self):
        assert ticks.last_activity_ts("u-care-empty") is None

    def test_after_capture_returns_ts(self):
        before = time.time()
        mem_store.capture_turn("u-care", "t", "内容", "回复",
                               meta={"risk_level": "low", "emotion": "sad"})
        after = time.time()
        ts = ticks.last_activity_ts("u-care")
        assert ts is not None and before <= ts <= after + 1
