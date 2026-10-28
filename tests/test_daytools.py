# -*- coding: utf-8 -*-
"""daytools 组件测试:两个确定性工具的输出钉死 + 旧日桶兼容。

锁四件事:
  1. 时段映射边界(0/6/12/18/23)与 ticks 分段累积的字段名;
  2. 分时点基线:中位数口径、样本不足时段缺席、旧桶(无分段字段)降级;
  3. compare_to_baseline:正常值小偏差、异常值大偏差、样本不足不下结论;
  4. find_similar_days:确定性(同输入同输出)、目标日排除、
     无负面观测日跳过、相位参与排序。
"""
import math
from datetime import datetime, timedelta

from gzhu_cup2.agent.algo.components import daytools
from gzhu_cup2.agent.long_memory import ticks as mticks

USER = "u-daytools"


def _bucket(count=1, neg_avg=0.3, hour=None, **slot_fields):
    """造一个日桶;hour 给定时附带作息相位充分统计量。"""
    b = {"count": count, "neg_sum": neg_avg * max(1, count), "neg_n": max(1, count)}
    if hour is not None:
        ang = 2.0 * math.pi * (hour + 0.5) / 24.0
        b["hour_sin_sum"] = round(math.sin(ang), 6)
        b["hour_cos_sum"] = round(math.cos(ang), 6)
        b["hour_n"] = 1
    b.update(slot_fields)
    return b


class TestSlotMapping:
    def test_slot_boundaries(self):
        assert daytools._slot_of_hour(0) == "dawn"
        assert daytools._slot_of_hour(5) == "dawn"
        assert daytools._slot_of_hour(6) == "morning"
        assert daytools._slot_of_hour(11) == "morning"
        assert daytools._slot_of_hour(12) == "afternoon"
        assert daytools._slot_of_hour(17) == "afternoon"
        assert daytools._slot_of_hour(18) == "evening"
        assert daytools._slot_of_hour(23) == "evening"

    def test_ticks_accumulates_slot_fields(self):
        """走真实 bump 路径:22:30 的轮次应落在 evening 段,危机轮不聚合段负面。"""
        base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ts = (base + timedelta(hours=22, minutes=30)).timestamp()
        mticks.bump_day_bucket(USER, {"negative_score": 0.6}, ts=ts)
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        items = dict(mticks.list_day_buckets(USER))
        b = items[day]
        assert b["slot_evening_count"] == 1
        assert b["slot_evening_neg_n"] == 1
        assert b["slot_evening_neg_sum"] == 0.6
        # 危机轮:段 count 照常 +1,段负面不聚合
        mticks.bump_day_bucket(USER, {"negative_score": 0.9, "risk_level": "crisis"},
                               ts=ts + 60)
        b = dict(mticks.list_day_buckets(USER))[day]
        assert b["slot_evening_count"] == 2
        assert b["slot_evening_neg_n"] == 1

    def test_slot_dawn(self):
        base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ts = (base + timedelta(hours=2)).timestamp()
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        mticks.bump_day_bucket(USER + "-2", {"negative_score": 0.4}, ts=ts)
        b = dict(mticks.list_day_buckets(USER + "-2"))[day]
        assert b["slot_dawn_count"] == 1
        assert "slot_morning_count" not in b


class TestSlotBaseline:
    def test_median_and_sample_count(self):
        buckets = {}
        for i, neg in enumerate((0.2, 0.3, 0.4, 0.9)):
            buckets["d%02d" % i] = _bucket(
                neg_avg=neg, slot_evening_neg_sum=neg, slot_evening_neg_n=1)
        base = daytools.slot_baseline(buckets)
        assert base["evening"]["n"] == 4
        assert base["evening"]["mu"] == 0.35      # 中位数 (0.3+0.4)/2

    def test_insufficient_slot_absent(self):
        buckets = {"d0": _bucket(slot_evening_neg_sum=0.3, slot_evening_neg_n=1),
                   "d1": _bucket(slot_evening_neg_sum=0.4, slot_evening_neg_n=1)}
        base = daytools.slot_baseline(buckets)
        assert "evening" not in base              # 2 天 < SLOT_MIN_N,不下结论

    def test_legacy_bucket_degrades(self):
        """旧桶没有分段字段 → 不产生基线样本,不把缺省当真值。"""
        buckets = {"d%d" % i: _bucket(neg_avg=0.5) for i in range(10)}
        assert daytools.slot_baseline(buckets) == {}


class TestCompareToBaseline:
    def _evening_base(self):
        buckets = {}
        for i in range(5):
            buckets["d%02d" % i] = _bucket(
                neg_avg=0.3, slot_evening_neg_sum=0.3, slot_evening_neg_n=1)
        return buckets

    def test_normal_value_small_z(self):
        now = datetime.now().replace(hour=20).timestamp()
        rep = daytools.compare_to_baseline(self._evening_base(), 0.35, now=now)
        assert rep["ok"] is True
        assert rep["slot"] == "evening"
        assert abs(rep["delta"]) <= 0.1
        assert abs(rep["z"]) <= 2.0

    def test_anomalous_value_large_delta(self):
        now = datetime.now().replace(hour=20).timestamp()
        rep = daytools.compare_to_baseline(self._evening_base(), 0.8, now=now)
        assert rep["ok"] is True
        assert rep["delta"] == 0.5
        assert rep["z"] > 2.0

    def test_insufficient_no_conclusion(self):
        now = datetime.now().replace(hour=10).timestamp()   # 上午段无样本
        rep = daytools.compare_to_baseline(self._evening_base(), 0.8, now=now)
        assert rep["ok"] is False
        assert rep["slot"] == "morning"


class TestFindSimilarDays:
    def _history(self):
        buckets = {
            "d01": _bucket(count=6, neg_avg=0.7, hour=22),   # 与目标最像
            "d02": _bucket(count=6, neg_avg=0.7, hour=9),    # 数值像但相位远
            "d03": _bucket(count=1, neg_avg=0.1, hour=22),   # 相位像但状态远
            "d04": _bucket(count=3, neg_avg=0.3),            # 无相位,数值中等
            "d05": _bucket(count=4, neg_avg=0.0, neg_avg_used=0),  # 见下:被 neg_n 覆盖
        }
        buckets["d05"] = {"count": 4, "neg_sum": 0.0, "neg_n": 0}   # 无负面观测
        return buckets

    def test_deterministic_and_topk(self):
        tgt = {"count": 6, "neg_avg": 0.7, "phase_hours": 22.0}
        r1 = daytools.find_similar_days(self._history(), target=tgt)
        r2 = daytools.find_similar_days(self._history(), target=tgt)
        assert r1 == r2                       # 确定性
        assert len(r1) == 3
        assert [r["day"] for r in r1] == ["d01", "d04", "d02"]
        assert r1[0]["distance"] < 0.1        # d01 与目标几乎重合

    def test_phase_breaks_numeric_tie(self):
        """数值相同、相位不同 → 相位近的排前面。"""
        buckets = {"d01": _bucket(count=6, neg_avg=0.7, hour=22),
                   "d02": _bucket(count=6, neg_avg=0.7, hour=9)}
        tgt = {"count": 6, "neg_avg": 0.7, "phase_hours": 22.0}
        days = daytools.find_similar_days(buckets, target=tgt)
        assert days[0]["day"] == "d01"

    def test_default_target_is_latest_with_neg(self):
        buckets = self._history()
        rep = daytools.find_similar_days(buckets)
        assert rep and all(r["day"] != "d04" for r in rep)   # d04 是目标(最近有负面),被排除

    def test_days_without_neg_skipped(self):
        buckets = {"d01": _bucket(count=6, neg_avg=0.7, hour=22),
                   "d02": {"count": 6, "neg_sum": 0.0, "neg_n": 0}}
        tgt = {"count": 6, "neg_avg": 0.7, "phase_hours": 22.0}
        days = daytools.find_similar_days(buckets, target=tgt)
        assert [r["day"] for r in days] == ["d01"]

    def test_empty_history_returns_empty(self):
        assert daytools.find_similar_days({}) == []
