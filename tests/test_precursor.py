# -*- coding: utf-8 -*-
"""前驱路径(precursor_score)与数据质量例外离线测试(不触网):

安静恶化型的三道结构性闸各自要有单元级锁定 ——
- score.precursor_score:参与度塌陷 / 作息后移自立路径,封顶不进 elevated;
- score.data_quality:lapse_signal 例外(塌陷被确认时稀疏是信号不是脏数据);
- machine.apply 的 lapse 放行在 test_guard.test_confirmed_collapse_breaks_lapse_hold。
"""
from gzhu_cup2.agent.algo.components import score


def _quiet_features(drop=0.75, p_break=0.98, phase=None):
    """安静恶化型的特征面:文字侧全静,只有参与度与作息。"""
    return {
        "recent": {"n_days": 7, "neg_median": 0.1},
        "delta": None,
        "coverage": 0.3,
        "engagement_drop": drop,
        "activity_cp": {"p_break": p_break, "cp_idx": 12, "shift": -0.85, "n": 28},
        "phase_shift_h": phase,
    }


# ============================ precursor_score ============================
class TestPrecursorScore:
    def test_quiet_collapse_fires_on_engagement_leg(self):
        pc = score.precursor_score(_quiet_features())
        assert pc["leg"] == "engagement"
        assert pc["score"] > 0
        # 幅度满档 + 置信接近满档时应接近封顶
        assert pc["score"] >= 0.5

    def test_below_engagement_floor_is_silent(self):
        pc = score.precursor_score(_quiet_features(drop=0.2))
        assert pc["score"] == 0.0 and pc["leg"] is None

    def test_below_cp_confidence_is_silent(self):
        pc = score.precursor_score(_quiet_features(p_break=0.3))
        assert pc["score"] == 0.0 and pc["leg"] is None

    def test_capped_below_elevated(self):
        pc = score.precursor_score(_quiet_features())
        assert pc["score"] <= score.PRECURSOR_CAP

    def test_phase_leg_alone_discounted(self):
        pc = score.precursor_score(_quiet_features(drop=0.0, p_break=0.0,
                                                   phase=5.0))
        assert pc["leg"] == "phase"
        # 幅度满档也只有 0.80×0.55 = 0.44
        assert pc["score"] <= score.PRECURSOR_CAP * 0.55 + 1e-9

    def test_no_cp_dict_is_silent(self):
        f = _quiet_features()
        f["activity_cp"] = None
        pc = score.precursor_score(f)
        assert pc["score"] == 0.0 and pc["leg"] is None


# ============================ data_quality 例外 ============================
class TestDataQualityLapseSignal:
    def test_confirmed_lapse_is_signal_not_noise(self):
        # 近 7 天只有 2 天有记录 + 覆盖率低 → 本应 sparse/insufficient,
        # 但塌陷被 BOCPD 确认时按 0.9 打折继续判。
        q = score.data_quality(_quiet_features())
        assert q["note"] == "lapse_signal"
        assert q["factor"] == 0.9 and q["ok"] is True

    def test_sparse_without_collapse_still_discounted(self):
        f = _quiet_features(drop=0.1, p_break=0.1)
        q = score.data_quality(f)
        assert q["note"] == "sparse" and q["factor"] == 0.75

    def test_healthy_quality_untouched(self):
        f = _quiet_features()
        f["recent"] = {"n_days": 7}
        f["coverage"] = 0.9
        q = score.data_quality(f)
        assert q["note"] == "ok" and q["factor"] == 1.0


# ============================ compute 集成 ============================
class TestPrecursorInCompute:
    def test_quiet_collapse_reaches_total_via_precursor(self):
        # 其它路径全静时,前驱路径应独立把总分顶到 watch 带(≥0.55),
        # 且 evidence 里可审计:paths.precursor 与 leg 都要写出来。
        out = score.compute(_quiet_features())
        assert out["s_precursor"] > 0
        assert out["detail"]["paths"]["precursor"] == out["s_precursor"]
        assert out["detail"]["precursor_leg"] == "engagement"
        assert out["s_total"] >= 0.55

    def test_precursor_cannot_reach_elevated_alone(self):
        out = score.compute(_quiet_features())
        assert out["s_total"] < 0.78
