# -*- coding: utf-8 -*-
"""个人基线估计量(Q1 + 1.4826×MAD)与锚定窗口回归锁。

背景
----
旧口径用 均值 + 总体 σ 做个人基线,有两个已知污染源:
  1. **离群点**:基线里混进一两个极端日(如某天 0.60),均值被拉高、σ 被放大,
     后续真实恶化算出来的 z 反而变小 —— 异常被"稀释";
  2. **长期低水位**:用户连续数周低落时,滚动基线跟着下沉,把异常当正常。

修法:
  1. Q1(下四分位)+ 1.4826×MAD:Q1 天然抗离群,且**只有 >75% 的日子低到同一水平
     才会被拖进低谷**(median 只要 >50% 就被拖走);1.4826 使 MAD 在正态分布下
     与 σ 可比,因此下游 z 阈值口径不变;
  2. 基线取数锚定 90 天(MEMORY_BASELINE_ANCHOR_WINDOW_DAYS):旧的 14 天滑动窗口
     会把健康史滑出窗外,让基线被多周级恶化整体吞掉 —— 锚定后健康史仍占基线,
     Q1 钉在健康水平。
开关 MEMORY_BASELINE_ROBUST 可回到旧口径做 A/B 对照;MEMORY_BASELINE_QUANTILE=0.5
可单独回到 median 中心。

本文件钉死:
  1. _center_scale 的数学口径(Q1、1.4826×MAD 逐一精确;quantile 插值精确);
  2. 离群点不再污染基线(robust 中心 ≈0.11,classic 均值被拉到 ≈0.212);
  3. 开关置 False 完全回到旧口径(均值+总体 σ,estimator 标记为 mean_sigma);
  4. MAD=0 坍缩不抛异常,σ=0 沿用既有回退规则;
  5. 长期低水位基线不再掩盖后续抬升(classic 会被"混入的正常日"拉偏而漏报);
  6. 锚定窗口:多周级恶化不再污染基线(关回 14 天时 Q1 也救不了);
  7. 绝对位移兜底闸:MAD 膨胀使 z<1.0 时,位移 ≥0.20 仍判漂移。
"""
import math
import time
from datetime import date, timedelta

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks as mticks
from gzhu_cup2.agent.long_memory import trend

USER = "u-robust"


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _put(day_key: str, neg: float) -> None:
    d = date.fromisoformat(day_key)
    b = {"day": day_key, "count": 1,
         "neg_sum": float(neg), "neg_n": 1,
         "last_ts": time.time() - (date.today() - d).days * 86400.0}
    mem_store.put_item(mticks.ns_ticks(USER), day_key, b)


def _put_days(pairs) -> None:
    """pairs = [(offset, neg), ...],升序书写。"""
    for off, neg in pairs:
        _put(_day(off), neg)


# ==================== 1. 估计量数学口径 ====================
class TestCenterScaleMath:
    def test_median_odd_length(self):
        assert trend._median([0.10, 0.11, 0.12, 0.13, 0.60]) == 0.12

    def test_median_even_length_averages_two_middles(self):
        assert trend._median([0.10, 0.12, 0.13, 0.60]) == 0.125

    def test_quantile_interpolation_exact(self):
        vals = [0.10, 0.11, 0.12, 0.13]
        assert trend._quantile(vals, 0.5) == pytest.approx(0.115)   # 中位数口径
        assert trend._quantile(vals, 0.25) == pytest.approx(0.1075)  # Q1 = 0.25×3 → 0.10×0.25+0.11×0.75
        assert trend._quantile([0.10], 0.25) == pytest.approx(0.10)
        assert trend._quantile([], 0.25) == 0.0

    def test_center_scale_robust_exact(self):
        """Q1 = 0.11;MAD = median(|v−Q1|) = 0.01 → scale = 1.4826×0.01。"""
        center, scale, name = trend._center_scale([0.10, 0.12, 0.11, 0.13, 0.60])
        assert center == pytest.approx(0.11)
        assert scale == pytest.approx(1.4826 * 0.01)
        assert name == "q25_mad"

    def test_q1_does_not_rise_into_decline(self):
        """67% 的日子都是低落日时 median 已被拖进低谷,但 Q1 仍钉在健康水平。

        这是 Q1 取代 median 的全部理由:只有 >75% 的日子低到同水平,Q1 才会被拖走。
        """
        vals = [0.10] * 7 + [0.55] * 14
        center, scale, name = trend._center_scale(vals)
        assert center == pytest.approx(0.10)          # Q1 没被拖走
        assert trend._median(sorted(vals)) == 0.55    # 对照:median 已被污染
        assert scale == pytest.approx(1.4826 * 0.45)  # MAD=0.45(围绕 Q1)
        assert name == "q25_mad"

    def test_center_scale_median_mode_restored_at_q50(self, monkeypatch):
        """cfg.MEMORY_BASELINE_QUANTILE=0.5 → 完全回到旧 median 口径(可切换)。"""
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_QUANTILE", 0.5)
        center, scale, name = trend._center_scale([0.10, 0.12, 0.11, 0.13, 0.60])
        assert center == pytest.approx(0.12)
        assert scale == pytest.approx(1.4826 * 0.01)
        assert name == "median_mad"

    def test_center_scale_classic_exact(self, monkeypatch):
        """开关置 False → 均值 + 总体 σ(逐项手算)。"""
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        vals = [0.10, 0.20, 0.30, 0.40]
        mean = sum(vals) / len(vals)                      # 0.25
        sigma = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        center, scale, name = trend._center_scale(vals)
        assert center == pytest.approx(mean)
        assert scale == pytest.approx(sigma)
        assert name == "mean_sigma"


# ==================== 2. 离群点不再污染基线 ====================
class TestOutlierDoesNotContaminateBaseline:
    BASELINE = [(-8, 0.10), (-7, 0.12), (-6, 0.11), (-5, 0.13), (-4, 0.60)]
    DRIFT = [(-2, 0.20), (-1, 0.20), (0, 0.20)]

    def _rep(self):
        _put_days(self.BASELINE + self.DRIFT)
        return trend.baseline_and_drift(USER, now=time.time())

    def test_robust_center_ignores_outlier(self):
        rep = self._rep()
        assert rep is not None
        assert rep["mu"] == pytest.approx(0.11, abs=1e-4)        # Q1:0.60 被忽略
        assert rep["sigma"] == pytest.approx(0.014826, abs=1e-4)  # 1.4826 × MAD(0.01)
        assert rep["estimator"] == "q25_mad"

    def test_classic_mean_is_pulled_up_by_outlier(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        rep = self._rep()
        assert rep is not None
        # 手算:mean = 1.06/5 = 0.212;var = 0.18868/5 → σ ≈ 0.194258
        assert rep["mu"] == pytest.approx(0.212, abs=1e-4)
        assert rep["sigma"] == pytest.approx(0.194258, abs=1e-4)
        assert rep["estimator"] == "mean_sigma"

    def test_drift_day_has_larger_robust_z_than_classic(self, monkeypatch):
        robust_rep = self._rep()
        z_robust = robust_rep["drift_days"][0]["z"]
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        classic_rep = self._rep()
        z_classic = classic_rep["drift_days"][0]["z"]
        # robust: (0.20−0.11)/0.014826 ≈ 6.07;classic: (0.20−0.212)/0.1943 ≈ −0.06
        assert z_robust == pytest.approx(6.070, abs=1e-3)
        assert z_robust > z_classic


# ==================== 3. 开关置 False → 完全回到旧口径 ====================
class TestToggleReproducesClassicBehaviour:
    def test_baseline_and_drift_classic_matches_hand_computed(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        _put_days([(-7, 0.10), (-6, 0.20), (-5, 0.30), (-4, 0.40),
                   (-2, 0.15), (-1, 0.15), (0, 0.15)])
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        # 手算:mean = 0.25;var = 0.0125 → σ = √0.0125 ≈ 0.1118034
        assert rep["mu"] == pytest.approx(0.25, abs=5e-5)
        assert rep["sigma"] == pytest.approx(math.sqrt(0.0125), abs=5e-5)
        assert rep["estimator"] == "mean_sigma"

    def test_center_scale_echoes_estimator_name(self, monkeypatch):
        """估计量名随配置切换:排障面板靠它解释当前基线口径。"""
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        assert trend._center_scale([0.10, 0.20, 0.30, 0.40])[2] == "mean_sigma"
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", True)
        assert trend._center_scale([0.10, 0.20, 0.30, 0.40])[2] == "q25_mad"


# ==================== 4. MAD=0 坍缩:σ=0 且不抛异常 ====================
class TestMadZeroCollapse:
    def test_center_scale_flat_returns_zero_scale(self):
        center, scale, name = trend._center_scale([0.10] * 5)
        assert center == pytest.approx(0.10)
        assert scale == 0.0
        assert name == "q25_mad"

    def test_flat_baseline_reports_zero_sigma_and_falls_back_gracefully(self):
        """全同基线 → MAD=0 → σ=0,走既有 z 回退规则,不得抛异常。"""
        _put_days([(off, 0.2875) for off in range(-10, -2)] + [(0, 0.30)])
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sigma"] == 0.0
        assert rep["estimator"] == "q25_mad"
        # z 回退:高于基线即 999(既有规则),地板仍拦下微小位移
        raised = next(r for r in rep["drift_days"] if r["neg"] == 0.30)
        assert raised["z"] == 999.0


# ==================== 5. 长期低水位不掩盖后续抬升 ====================
class TestSustainedLowNotMasked:
    """基线 = 7 天持续低水位(0.10)+ 1 天混入的"正常日"(0.28)。

    robust:Q1=0.10、MAD=0 → σ=0 → 抬升日 z=999、位移 0.16 ≥ 0.15 → 检出。
    classic:均值被 0.28 拉偏到 0.1225,σ≈0.0595 → z≈2.31 但位移 0.1375 < 0.15 → 漏报。
    这正是旧口径"把异常当正常"的失效模式:一个混在长期低水位里的往日正常日,
    就把后续恶化稀释到了地板之下。
    """

    def _setup(self):
        _put_days([(off, 0.10) for off in range(-11, -4)]  # 7 天低水位
                  + [(-4, 0.28)]                            # 混入的往日正常日
                  + [(-2, 0.26), (-1, 0.26), (0, 0.26)])    # 轻微抬升

    def test_robust_detects_step(self):
        self._setup()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["estimator"] == "q25_mad"
        assert rep["sigma"] == 0.0
        assert rep["sustained_worsening"] is True
        assert rep["consecutive_above"] == 3

    def test_classic_masks_step(self, monkeypatch):
        """对照:同一条轨迹在旧口径下被混入的正常日稀释 → 漏报。"""
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ROBUST", False)
        self._setup()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["estimator"] == "mean_sigma"
        assert rep["sustained_worsening"] is False


# ==================== 6. 锚定窗口 + 绝对位移兜底闸 ====================
class TestAnchorAndAbsBackstop:
    """"第 8 天起持续低落"的典型轨迹:7 天健康 + 14 天持续低落。

    旧的 14 天滑动取数会把健康史滑出窗外 → 基线全是低落日 → 异常被"正常化"
    (实测"第8天起持续低落"/"深夜活跃"两例漏报即源于此)。90 天锚定取数让
    健康史仍占基线 ≥25% → Q1 钉在健康水平。同时长期恶化膨胀 MAD → z 钝化到
    1.0 以下,此时由 MEMORY_DRIFT_ABS_ALERT 兜底闸兜住。
    """

    def _seed(self):
        _put_days([(off, 0.10) for off in range(-21, -14)]   # 7 天健康史(旧窗口够不着)
                  + [(off, 0.55) for off in range(-13, -2)]  # 11 天低落(基线内)
                  + [(off, 0.55) for off in (-2, -1, 0)])    # 漂移窗口 3 天

    def test_anchor_keeps_baseline_at_healthy_level(self):
        self._seed()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["baseline_days"] == 18                 # 锚定窗口吃进全部 18 个基线日
        assert rep["mu"] == pytest.approx(0.10, abs=1e-4)  # Q1 钉在健康水平
        assert rep["estimator"] == "q25_mad"

    def test_old_14day_window_would_be_polluted(self, monkeypatch):
        """A/B:锚定窗口关回 14 天 → 健康史滑出窗外,基线被低落日吞掉(Q1 也救不了)。"""
        monkeypatch.setattr(cfg, "MEMORY_BASELINE_ANCHOR_WINDOW_DAYS", 14)
        self._seed()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["mu"] == pytest.approx(0.55, abs=1e-4)  # 基线全低落 → 污染

    def test_abs_backstop_fires_when_z_below_threshold(self):
        """MAD 膨胀使 z<1.0,但位移 ≥0.20 → 兜底闸判漂移(连续 3 日 → 持续恶化)。

        修复前:z 钝化(0.45/0.667≈0.67)+ 地板通过 → 但 z 门槛卡住 → 漏报。
        """
        self._seed()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sigma"] > 0.0
        assert all(r["z"] < 1.0 for r in rep["drift_days"])    # z 钝化 —— 正是要修的
        assert all(r["is_drift"] for r in rep["drift_days"])   # 兜底闸兜住
        assert all(r["delta"] >= cfg.MEMORY_DRIFT_ABS_ALERT for r in rep["drift_days"])
        assert rep["sustained_worsening"] is True
        assert rep["consecutive_above"] == 3
        assert rep["abs_alert"] == cfg.MEMORY_DRIFT_ABS_ALERT  # 阈值回显,可审计

    def test_moderate_drop_below_backstop_still_blocked(self):
        """位移落在 [min_abs, abs_alert) 区间且 z<1 → 仍不算漂移(σ 坍缩误报保护仍在)。"""
        self._seed()
        for off in (-2, -1, 0):
            _put(_day(off), 0.27)   # delta=0.17:≥0.15 地板,<0.20 兜底;z≈0.25 钝化
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        for r in rep["drift_days"]:
            assert r["z"] < 1.0
            assert r["is_drift"] is False
        assert rep["sustained_worsening"] is False

    def test_backstop_disengageable(self, monkeypatch):
        """兜底闸置 0 → 回到纯 z+地板口径(z 钝化即漏报,可对照)。"""
        monkeypatch.setattr(cfg, "MEMORY_DRIFT_ABS_ALERT", 0.0)
        self._seed()
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert all(not r["is_drift"] for r in rep["drift_days"])
        assert rep["sustained_worsening"] is False
