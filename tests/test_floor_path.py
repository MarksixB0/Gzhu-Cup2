# -*- coding: utf-8 -*-
"""低水位路径(S_floor)与慢性输入口径测试。

锁三件语义:
  1. 混合日(单坏时段+好时段)的低水位低于日均值,固定作息形状不触发档位;
  2. 无基线开局的持续深陷由低水位路径推进 watch,elevated 必须另有支撑;
  3. 稀疏孤立极值被数据可信度打折,不得推档。
"""
from datetime import datetime, timedelta

import pytest

from gzhu_cup2.agent.algo import skeleton as guard
from gzhu_cup2.agent.algo.components import features as feat
from gzhu_cup2.agent.long_memory import ticks as mticks

USER = "u-floor"


def _ts(offset: int, hour: int) -> float:
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=offset, hours=hour)).timestamp()


def _turn(offset: int, hour: int, neg: float) -> None:
    mticks.bump_day_bucket(USER, {
        "risk_level": "low", "emotion": "neutral", "intensity": 0.5,
        "negative_score": neg, "lex_neg": neg, "behavior": [],
    }, ts=_ts(offset, hour))


def _features(now=None):
    from gzhu_cup2.agent.algo.components import fetch as fetch_mod
    return feat.compute(fetch_mod.fetch(USER, now=now))


class TestFloorFeature:
    def test_mixed_day_floor_below_mean(self):
        """混合日(早坏晚好)的低水位是"好时段"的水平,低于日均值。"""
        for d in range(-9, 1):
            _turn(d, 8, 0.62)
            _turn(d, 22, 0.30)
        r = _features()["recent"]
        assert r["floor_median"] == pytest.approx(0.30, abs=0.01)
        assert r["neg_median"] > r["floor_median"] + 0.10

    def test_uniform_day_floor_equals_mean(self):
        """单时段日(旧桶同形)低水位与日均值同值,不产生新信息。"""
        for d in range(-9, 1):
            _turn(d, 12, 0.55)
        r = _features()["recent"]
        assert r["floor_median"] == pytest.approx(r["neg_median"], abs=0.01)


class TestFloorPath:
    def test_no_baseline_misery_reaches_watch_not_elevated(self):
        """无基线开局、全时段深陷:低水位推进 watch,elevated 须另有支撑。"""
        for d in range(-6, 1):
            for h in (9, 15, 22):
                _turn(d, h, 0.70)
        a = guard.analyze(USER)
        assert a["tier"] == "watch"
        assert a["score"]["s_floor"] > 0.9
        assert a["score"]["s_total"] < 0.78

    def test_mixed_routine_stays_stable(self):
        """固定坏时段+好时段的作息形状:低水位与慢性通道都不触发。"""
        for d in range(-20, 1):
            _turn(d, 8, 0.62)
            _turn(d, 22, 0.30)
        a = guard.analyze(USER)
        assert a["tier"] == "stable"
        assert a["score"]["s_floor"] == 0.0
        assert a["score"]["s_chronic"] == 0.0

    def test_sparse_extremes_stay_stable(self):
        """稀疏孤立极值:窗内样本不足,数据可信度打折后不得推档。"""
        for d in (-25, -12, 0):
            _turn(d, 14, 0.85)
        a = guard.analyze(USER)
        assert a["tier"] == "stable"
