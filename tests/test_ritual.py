# -*- coding: utf-8 -*-
"""定时仪式(care/ritual.py):时间窗、当天幂等、打扰等级,以及**不占额度/不退避**的账。

不触网:LLM 用注入替身(test_petlife_e2e.py 同做法)。
"""
from datetime import datetime, timedelta

import pytest


def _at(hour: int, minute: int = 0, day: int = 0) -> float:
    """今天(±`day` 天)的 HH:MM 时间戳。

    刻意**贴着真实当前时间**构造,而不是写死某个历史日期:
    ``care.runtime.pending`` 读取时会用 ``time.time()`` 做过期过滤,
    用历史时刻造的数据会被判为过期而查不到(写进去时看不出任何问题)。
    """
    base = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (base + timedelta(days=day)).timestamp()


@pytest.fixture
def ritual(care_state_file, pet_state_file):
    """仪式模块;两块状态(关怀状态 + 打扰等级)都隔离到临时文件。"""
    from gzhu_cup2.agent.care import ritual as mod
    return mod


# ============================ 时间窗 ============================

class TestWindow:
    def test_before_target_is_closed(self, ritual):
        assert ritual.in_window(_at(22, 29)) is False

    def test_at_target_opens(self, ritual):
        assert ritual.in_window(_at(22, 30)) is True

    def test_late_evening_still_open(self, ritual):
        assert ritual.in_window(_at(23, 59)) is True

    def test_after_midnight_closed(self, ritual):
        """跨过午夜**不再补发** —— 「凌晨三点收到晚安」不是体贴(PLAN_NEXT.md §5.1)。"""
        assert ritual.in_window(_at(0, 30)) is False
        assert ritual.in_window(_at(3, 0)) is False
        assert ritual.in_window(_at(8, 0)) is False

    def test_disabled_closes_window(self, ritual, monkeypatch):
        monkeypatch.setattr(ritual.cfg, "CARE_RITUAL_ENABLED", False)
        assert ritual.in_window(_at(23, 0)) is False


# ============================ due 的各个门槛 ============================

class TestDue:
    def test_not_due_before_window(self, ritual):
        assert ritual.due("u1", now=_at(20)) is False

    def test_due_in_window(self, ritual):
        assert ritual.due("u1", now=_at(22, 30)) is True

    def test_not_due_when_care_disabled(self, ritual):
        """用户的「永久关闭主动关怀」优先于仪式。"""
        from gzhu_cup2.agent.care import runtime as crt
        crt.set_enabled("u1", False)
        assert ritual.due("u1", now=_at(22, 30)) is False

    def test_not_due_when_quiet_level(self, ritual):
        """打扰等级与主动消息**同一把尺子**:安静及以上不发。"""
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_level("u1", "quiet")
        assert ritual.due("u1", now=_at(22, 30)) is False

    def test_due_again_after_level_restored(self, ritual):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_level("u1", "quiet")
        prt.set_level("u1", "active")
        assert ritual.due("u1", now=_at(22, 30)) is True

    def test_manual_dnd_also_blocks(self, ritual):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_manual("u1", "dnd")
        assert ritual.due("u1", now=_at(22, 30)) is False

    def test_not_due_twice_same_day(self, ritual):
        """判定与落库必须**同基准**:都用判定时刻那一天(见 care/runtime._day_of)。"""
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_ritual_done("u1", now=_at(22, 30))
        assert ritual.due("u1", now=_at(22, 30)) is False

    def test_day_rolls_over_reopens(self, ritual):
        """幂等以**自然日**为界,不是"永久只发一条"。"""
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_ritual_done("u1", now=_at(22, 30, day=1))
        assert ritual.due("u1", now=_at(22, 30, day=1)) is False
        assert ritual.due("u1", now=_at(22, 30, day=2)) is True


# ============================ 记账:与普通主动消息的唯一差别 ============================

class TestAccounting:
    def test_ritual_does_not_consume_daily_quota(self, ritual):
        from gzhu_cup2.agent.care import runtime as crt
        before = crt.counters("u1")["date_count"]
        crt.mark_sent("u1", "ritual", "晚安", ritual=True)
        assert crt.counters("u1")["date_count"] == before

    def test_normal_message_does_consume_quota(self, ritual):
        """对照:普通主动消息照旧占额度(证明上面的差异确实来自 ritual 参数)。"""
        from gzhu_cup2.agent.care import runtime as crt
        before = crt.counters("u1")["date_count"]
        crt.mark_sent("u1", "checkin", "在吗")
        assert crt.counters("u1")["date_count"] == before + 1

    def test_ritual_does_not_touch_backoff(self, ritual):
        """晚安不在等回应,不该被记成「未获回应」。"""
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_sent("u1", "ritual", "晚安", ritual=True)
        c = crt.counters("u1")
        assert c["unanswered_streak"] == 0
        assert c["last_delivered_ts"] is None

    def test_ritual_marks_day_and_enters_outbox(self, ritual):
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_sent("u1", "ritual", "晚安", ritual=True)
        assert crt.last_ritual_day("u1") is not None
        assert "ritual" in [m["kind"] for m in crt.pending("u1")]


# ============================ ensure:端到端的落库行为 ============================

class TestEnsure:
    def test_silent_outside_window(self, ritual):
        assert ritual.ensure("u1", now=_at(10)) is None

    def test_sends_once_and_is_idempotent(self, ritual, monkeypatch):
        from gzhu_cup2.agent.care import content as ccontent
        from gzhu_cup2.agent.care import runtime as crt
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "晚安,早点休息。")
        first = ritual.ensure("u1", now=_at(22, 30))
        assert first and first["kind"] == "ritual" and first["text"]
        # 同一天再调:不再发
        assert ritual.ensure("u1", now=_at(22, 45)) is None
        got = [m for m in crt.pending("u1") if m["kind"] == "ritual"]
        assert len(got) == 1

    def test_generation_failure_marks_day_done(self, ritual, monkeypatch):
        """生成失败 → 标记今天已处理,避免调度线程每轮重打 LLM。"""
        from gzhu_cup2.agent.care import content as ccontent
        from gzhu_cup2.agent.care import runtime as crt
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: None)
        assert ritual.ensure("u1", now=_at(22, 30)) is None
        assert crt.last_ritual_day("u1") is not None
        assert ritual.ensure("u1", now=_at(22, 40)) is None

    def test_forbidden_text_is_dropped_and_marks_day(self, ritual, monkeypatch):
        """命中禁词 → 丢弃 + 标记今天已处理(复用 care.content 的过滤链路)。"""
        from gzhu_cup2.agent.care import content as ccontent
        from gzhu_cup2.agent.care import runtime as crt
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "我注意到你的风险等级偏高。")
        assert ritual.ensure("u1", now=_at(22, 30)) is None
        assert crt.last_ritual_day("u1") is not None
        assert [m for m in crt.pending("u1") if m["kind"] == "ritual"] == []
