# -*- coding: utf-8 -*-
"""care soothe(低落回访)分支的离线测试:policy 触发/闸门 + content 模板安全。"""
import time

from gzhu_cup2.agent.care.policy import CareSnapshot, decide
from gzhu_cup2.agent.care import content as ccontent

NOW = time.time()
HOUR = 3600.0


def snap(**over):
    base = dict(
        enabled=True,
        now=NOW,
        quiet_start_hour=0,
        quiet_end_hour=0,
        max_per_day=2,
        cooldown_hours=4,
        recall_min_gap_hours=24,
        checkin_gap_hours=48,
        soothe_min_gap_hours=1,     # 短冷却:离开低落约 1 小时后可回访
        last_proactive_ts=None,
        date_count=0,
        last_user_ts=None,
        initiable_topics=[],
        recent_distress=False,
    )
    base.update(over)
    return CareSnapshot(**base)


class TestDecideSoothe:
    def test_soothe_after_distress_with_short_gap(self):
        # 用户低落收尾后 2 小时没回来 → soothe
        d = decide(snap(recent_distress=True, last_user_ts=NOW - 2 * HOUR))
        assert d.due is True and d.kind == "soothe" and d.reason == "after_distress"

    def test_no_soothe_while_still_around(self):
        # 刚聊完几分钟(< 冷却),不打扰
        d = decide(snap(recent_distress=True, last_user_ts=NOW - 10 * 60))
        assert d.due is False

    def test_no_soothe_without_distress_signal(self):
        # 没有低落信号,不该走 soothe
        d = decide(snap(recent_distress=False, last_user_ts=NOW - 2 * HOUR))
        assert d.due is False or d.kind != "soothe"

    def test_gates_still_apply(self):
        # 即使低落收尾,每日上限与冷却依旧拦截
        assert decide(snap(recent_distress=True, last_user_ts=NOW - 2 * HOUR,
                           date_count=2)).due is False
        assert decide(snap(recent_distress=True, last_user_ts=NOW - 2 * HOUR,
                           last_proactive_ts=NOW - 1 * HOUR)).due is False


class TestSootheContent:
    def test_soothe_via_llm(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "团团一直在这里陪着你，随时都可以来找我。")
        assert ccontent.generate_message("soothe") == "团团一直在这里陪着你，随时都可以来找我。"

    def test_soothe_forbidden_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "你的焦虑指数有点高")
        assert ccontent.generate_message("soothe") is None
