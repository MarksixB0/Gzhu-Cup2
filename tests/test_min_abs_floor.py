# -*- coding: utf-8 -*-
"""绝对位移地板回归锁(与 z 检验并列的第二道闸)。

背景(实测,8 人格 × 21 天带真值语料,见算法层 eval_guard 对拍)
----------------------------------------------------------------------
`trend.baseline_and_drift` 原本只判 `z = (v−μ)/σ ≥ 1.0`。σ 是**个人基线的标准差**,
当基线点少且彼此接近时 σ 会坍缩到 0.01 量级,于是**测量分辨率以内的噪声**也能
达标。实测后果:

  · 一个 neg_avg 恒为 0.300、毫无变化的健康用户(基线 μ=0.2875、σ=0.0125),
    其"漂移日"位移只有 0.0125 却达到 z=1.0,凑满连续 3 日 → 被抬成 elevated。

修法是给通道加一道**绝对位移地板**(z 回答"是否异常",地板回答"是否够大"):
    baseline_and_drift  : 漂移日还需 (v − μ) ≥ MEMORY_DRIFT_MIN_ABS_DELTA

本文件钉死三件事:
  1. 零方差基线 + 微小位移 → 不得成为漂移日(修的就是这个);
  2. 真实位移仍必须被识别(修法不能把召回也一起削掉);
  3. 地板关到 0 时行为回到旧口径(可回退、可对照)。

兜底闸(MEMORY_DRIFT_ABS_ALERT:MAD 膨胀使 z<1 时位移 ≥0.20 仍点亮)的
用例见 test_robust_baseline.py(TestAnchorAndAbsBackstop)。
"""
import time
from datetime import date, timedelta

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks as mticks
from gzhu_cup2.agent.long_memory import trend

USER = "u-absfloor"


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _put(day_key: str, neg: float) -> None:
    d = date.fromisoformat(day_key)
    b = {"day": day_key, "count": 1,
         "neg_sum": float(neg), "neg_n": 1,
         "last_ts": time.time() - (date.today() - d).days * 86400.0}
    mem_store.put_item(mticks.ns_ticks(USER), day_key, b)


def _flat_baseline(days: int = 8, neg: float = 0.2875) -> None:
    """铺一段**恒定**基线 → σ=0,正是 σ 坍缩的极端情形。"""
    for off in range(-2 - days, -2):
        _put(_day(off), neg)


# ==================== 1. 零方差基线 + 微小位移:不得判漂移 ====================
class TestTinyDeltaOnFlatBaselineIsNotDrift:
    def test_hairline_rise_is_not_sustained_worsening(self):
        """基线恒定 0.2875,最近三天 0.300 —— 位移 0.0125,远在分辨率噪声内。

        修复前:σ=0 → z=999(高于基线即视为显著)→ 连续 3 日 → sustained_worsening
        = True,健康用户被抬档。这就是本次修复要拦下的误报。
        """
        _flat_baseline(8, neg=0.2875)
        for off in (-2, -1, 0):
            _put(_day(off), 0.300)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sigma"] == 0.0                 # 确认构造出了 σ 坍缩
        assert rep["sustained_worsening"] is False
        assert rep["consecutive_above"] == 0
        for row in rep["drift_days"]:
            assert row["delta"] == pytest.approx(0.0125, abs=1e-6)
            assert row["is_drift"] is False
            assert row["z"] >= 1.0                 # z 仍然"显著"——正是问题所在


# ==================== 2. 真实位移仍必须被识别(不许削召回) ====================
class TestRealDeltaStillDetected:
    def test_large_sustained_rise_is_worsening(self):
        """位移 0.55 且连续 3 日 —— 真的恶化,必须照旧识别。"""
        _flat_baseline(8, neg=0.20)
        for off, v in ((-2, 0.70), (-1, 0.80), (0, 0.90)):
            _put(_day(off), v)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sustained_worsening"] is True
        assert rep["consecutive_above"] == 3
        assert all(r["is_drift"] for r in rep["drift_days"])


# ==================== 3. 关掉地板 → 完全回到旧口径 ====================
class TestFloorIsDisengageable:
    """把阈值置 0 = 完全等价于修复前的行为。

    这条同时是"可回退"的保证,也是对照实验的入口:改坏了能把地板关掉。
    """

    def test_zero_floor_reproduces_old_behaviour(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_DRIFT_MIN_ABS_DELTA", 0.0)
        _flat_baseline(8, neg=0.2875)
        for off in (-2, -1, 0):
            _put(_day(off), 0.300)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep["sustained_worsening"] is True      # 旧口径:又变成误报

    def test_threshold_is_echoed_for_auditability(self):
        """返回体必须回显实际生效的阈值 —— 排障要能逐条解释"为什么没报"。"""
        _flat_baseline(8, neg=0.30)
        _put(_day(0), 0.90)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep["min_abs_delta"] == cfg.MEMORY_DRIFT_MIN_ABS_DELTA

