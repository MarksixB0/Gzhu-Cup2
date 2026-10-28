# -*- coding: utf-8 -*-
"""记忆时序常量的成对不变式。

这两个常量不是独立的:``capture_turn`` 用 GAP 决定"是否封存旧段",
``pending_is_due`` 用 IDLE 决定"空闲多久算到期"。单独调一个而不管另一个,
会出现"该切段却没切"的窗口 —— 新轮次继续挂在旧段上,相对日期锚在旧段首。
"""
from gzhu_cup2.agent.config import config as cfg


def test_session_gap_not_shorter_than_idle():
    assert cfg.MEMORY_CAPTURE_SESSION_GAP_SECONDS >= cfg.MEMORY_IDLE_SECONDS, (
        "切段阈值必须不早于空闲固化阈值;否则用户在两者之间回来会继续复用旧段,"
        "新轮次的相对日期会锚在旧段首"
    )
