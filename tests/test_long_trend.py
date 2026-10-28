# -*- coding: utf-8 -*-
"""长期趋势摘要(profile.long_horizon_summary)测试 —— 离线、零 LLM。

修的是一个实测到的误导:42 天注入里用户"22 天持续低落 + 末 5 天明显好转",
而【近期状态】只给 3 天窗,于是提示词写的是「整体还算平稳」。
本文件钉住:长期窗必须能说出"比平时偏低",且冷暖要分得开。
"""
import time

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks as mticks
from gzhu_cup2.agent.long_memory import profile as mprofile

USER = "u-longtrend"
_DAY = 86400.0


def _put_day(days_ago: int, neg: float, now: float, count: int = 4):
    """写入"days_ago 天前"的日桶(直接落桶,避免依赖 wall-clock)。"""
    ts = now - days_ago * _DAY
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    mem_store.put_item(mticks.ns_ticks(USER), day, {
        "day": day, "count": count,
        "neg_sum": round(neg * count, 4), "neg_n": count,
        "lex_neg_sum": round(neg * count, 4), "lex_neg_n": count,
        "last_ts": ts,
    })


class TestLongHorizonSummary:
    def test_none_when_too_few_days(self):
        now = time.time()
        for i in range(2):
            _put_day(i, 0.5, now)
        assert mprofile.long_horizon_summary(USER, now=now) is None

    def test_worsening_phase_is_not_reported_as_stable(self):
        """核心回归:平稳基线 + 近三周持续低落 → 必须说"比平时偏低",不能说"平稳"。"""
        now = time.time()
        for i in range(10, 40):          # 基线:更早的日子偏低
            _put_day(i, 0.30, now)
        for i in range(0, 20):           # 近三周:持续低落
            _put_day(i, 0.60, now)
        text = mprofile.long_horizon_summary(USER, now=now)
        assert text is not None
        assert ("比平时" in text) and ("低沉" in text or "偏低" in text)
        assert "和平时差不多" not in text

    def test_improving_phase_reads_lighter(self):
        now = time.time()
        for i in range(10, 40):
            _put_day(i, 0.60, now)
        for i in range(0, 20):
            _put_day(i, 0.30, now)
        text = mprofile.long_horizon_summary(USER, now=now)
        assert text is not None and ("轻松" in text or "好转" in text)

    def test_risk_day_is_flagged(self):
        now = time.time()
        for i in range(0, 10):
            _put_day(i, 0.6, now)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        mem_store.put_item(mticks.ns_ticks(USER), day, {
            "day": day, "count": 1, "neg_sum": 0.9, "neg_n": 1,
            "risk_max_rank": 3, "protected": True, "last_ts": now,
        })
        text = mprofile.long_horizon_summary(USER, now=now)
        assert text is not None and "危险" in text

    def test_window_comes_from_config(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_TREND_LONG_DAYS", 14)
        monkeypatch.setattr(cfg, "MEMORY_TREND_LONG_MIN_DAYS", 3)
        now = time.time()
        for i in range(1, 6):
            _put_day(i, 0.6, now)
        text = mprofile.long_horizon_summary(USER, now=now)
        assert text is not None and "近两周" in text

    def test_no_score_numbers_leak(self):
        """与 recent_mood_summary 同口径:只用档位词,不把内部**分数**写进文本。

        (轮次数是可见事实、可以出现;真正不能泄漏的是 neg_avg / 基线这类内部度量。)
        """
        now = time.time()
        for i in range(10, 40):
            _put_day(i, 0.30, now)
        for i in range(0, 20):
            _put_day(i, 0.60, now)
        text = mprofile.long_horizon_summary(USER, now=now)
        assert "0." not in text
        assert "neg" not in text.lower()
