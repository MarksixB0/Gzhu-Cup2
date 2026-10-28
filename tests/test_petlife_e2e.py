# -*- coding: utf-8 -*-
"""coordinator → petlife 端到端下发(离线,不触网)。

验证:调度方把桌宠今日叙事传给 evaluate 后,在满足间隔/上限条件下产出 petlife
主动消息(含叙事文本)并写入 outbox,记录 last_petlife_ts;间隔内再次评估保持静默。
"""
import time

from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.care import coordinator as ccoord

HOUR = 3600.0
NOW = time.time()


# 关怀运行时状态隔离夹具(care_state_file)已统一收到 tests/conftest.py。


def _fake_memory(monkeypatch, last_active_hours_ago):
    """把长期记忆只读 API 换成可控假实现(避免真实存储/免扰时段影响),
    并注入假 LLM 生成器(coordinator 现走 generate_message,无模板兜底)。"""
    from gzhu_cup2.agent.config import config as _cfg
    from gzhu_cup2.agent.care import content as ccontent
    monkeypatch.setattr(_cfg, "CARE_QUIET_START_HOUR", 0)
    monkeypatch.setattr(_cfg, "CARE_QUIET_END_HOUR", 0)
    # 关怀调度还叠加了桌宠打扰等级门控(coordinator 在 policy 之前短路 quiet 及以上);
    # 夜间窗口 23–8 会把档位解析成 asleep,于是夜里即使免扰时段已关仍不下发 —— 这里
    # 一并关掉夜间自动休眠,断言才只随逻辑、不随墙上时间红绿。
    monkeypatch.setattr(_cfg, "PET_NIGHT_SLEEP_ENABLED", False)
    monkeypatch.setattr("gzhu_cup2.agent.long_memory.ticks.last_activity_ts",
                        lambda u, **kw: NOW - last_active_hours_ago * HOUR)
    monkeypatch.setattr("gzhu_cup2.agent.long_memory.trend.window_stats",
                        lambda u, days=None, now=None: {"turn_count": 3,
                                                        "neg_avg": None,
                                                        "risk_max_rank": 0})
    monkeypatch.setattr("gzhu_cup2.agent.long_memory.ledger.has_episodes", lambda u: False)
    monkeypatch.setattr("gzhu_cup2.agent.long_memory.ledger.uncovered_dims",
                        lambda u, days=None, now=None: [])
    monkeypatch.setattr("gzhu_cup2.agent.long_memory.retrieve.list_initiable", lambda u: [])
    monkeypatch.setattr(ccontent, "_llm_generate",
                        lambda messages: "团团今天晒了太阳，尾巴尖都暖了。你呢今天开心吗？")


class TestPetlifeDownlink:
    def test_first_petlife_downlinks_and_records_ts(self, care_state_file, monkeypatch):
        _fake_memory(monkeypatch, last_active_hours_ago=1)
        msg = ccoord.evaluate("u-pl", now=NOW, petlife_text="今天我晒了太阳，尾巴尖都暖了。")
        assert msg is not None and msg["kind"] == "petlife"
        assert "今天" in msg["text"] and "晒了太阳" in msg["text"]
        assert len(cruntime.pending("u-pl")) == 1
        c = cruntime.counters("u-pl")
        assert c["last_petlife_ts"] is not None

    def test_second_eval_within_gap_is_silent(self, care_state_file, monkeypatch):
        _fake_memory(monkeypatch, last_active_hours_ago=1)
        first = ccoord.evaluate("u-pl2", now=NOW, petlife_text="今天我晒了太阳。")
        assert first is not None and first["kind"] == "petlife"
        # 1 小时后再评估(仍在上一条 petlife 的 36h 间隔内)且其它关怀不触发 → 静默
        second = ccoord.evaluate("u-pl2", now=NOW + 1 * HOUR,
                                 petlife_text="今天我晒了太阳。")
        assert second is None

    def test_no_text_never_petlife(self, care_state_file, monkeypatch):
        _fake_memory(monkeypatch, last_active_hours_ago=24)
        assert ccoord.evaluate("u-pl3", now=NOW, petlife_text=None) is None
