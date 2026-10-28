# -*- coding: utf-8 -*-
"""复核调查通道(⑦)测试。

锁六件语义(2026-10-24 双层架构裁定):
  1. 触发器确定性:crisis_soft 窗 / watch 候选且弱结构腿 / lapse_signal 例外,
     其余(强结构腿、stable、无数据)一律不触发;
  2. 冷却与单槽:冷却期内或已有待办不重复挂;
  3. 案卷全部来自确定性计算(LLM 只判定不取数);
  4. 结论规范化:非法 conclusion 拒绝、短引文丢弃、证据截断;
  5. 落盘可审计:完整报告存 ns_review;只有 support 进 risk_events;
  6. 失败退避:任务保留计数,超过上限丢弃,不影响 worker。
"""
import time

import pytest

from gzhu_cup2.agent.algo.components import review
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import review_channel as rc

NOW = 1_800_000_000.0


def _sc(s_total=0.60, top="fast", quality_note="ok"):
    paths = {"emotion": 0.2, "chronic": 0.1, "behavior": 0.15,
             "fast": 0.2, "dimension": 0.1, "precursor": 0.15}
    paths[top] = 0.9 if top in ("fast", "precursor") else 0.9
    return {"s_total": s_total, "quality_note": quality_note,
            "detail": {"paths": paths}}


_F = {"engagement_drop": 0.55, "phase_shift_h": 1.2, "activity_cp":
      {"p_break": 0.9, "cp_idx": 13, "shift": -0.7}, "coverage": 0.8,
      "idle_days": 1, "withdraw_rate": 0.3, "conflict_rate": 0.0,
      "anhedonia_rate": 0.2, "somatic_rate": 0.1}


# ==================== 1. 触发器(纯函数) ====================
class TestShouldReview:
    def test_crisis_soft_window_triggers_even_low_score(self):
        assert review.should_review(_F, _sc(0.2), confirm_open=True) \
            == {"reason": "crisis_soft"}

    def test_watch_candidate_with_weak_leg(self):
        for leg in ("fast", "precursor"):
            assert review.should_review(_F, _sc(0.60, top=leg),
                                        confirm_open=False) is not None

    def test_strong_leg_does_not_trigger(self):
        assert review.should_review(_F, _sc(0.60, top="emotion"),
                                    confirm_open=False) is None

    def test_lapse_signal_abstains(self):
        rep = review.should_review(_F, _sc(0.30, quality_note="lapse_signal"),
                                   confirm_open=False)
        assert rep == {"reason": "abstain"}

    def test_stable_or_missing_data_no_trigger(self):
        assert review.should_review(_F, _sc(0.2), confirm_open=False) is None
        assert review.should_review(None, None, confirm_open=False) is None


# ==================== 2. 结论规范化(纯函数) ====================
class TestNormalizeVerdict:
    def test_valid_verdict_passes(self):
        v = review.normalize_verdict({
            "conclusion": "support", "confidence": "high",
            "evidence": [{"fact": "参与度两周降半", "quote": "engagement_drop=0.55",
                          "source": "signals"}],
            "conflicts": ["负面水平平稳"], "note": "多路弱信号同向"})
        assert v["conclusion"] == "support"
        assert v["evidence"][0]["source"] == "signals"

    def test_bad_conclusion_rejected(self):
        assert review.normalize_verdict({"conclusion": "maybe"}) is None
        assert review.normalize_verdict(None) is None
        assert review.normalize_verdict("support") is None

    def test_short_quote_and_fact_dropped(self):
        v = review.normalize_verdict({
            "conclusion": "watch",
            "evidence": [{"fact": "a", "quote": "x", "source": ""},
                         {"fact": "ok fact", "quote": "", "source": "score"}]})
        assert len(v["evidence"]) == 1 and v["evidence"][0]["quote"] == ""

    def test_evidence_capped_and_bad_confidence_fallback(self):
        ev = [{"fact": "事实%d" % i, "quote": "引文%d" % i} for i in range(9)]
        v = review.normalize_verdict({"conclusion": "refute",
                                      "confidence": "extreme", "evidence": ev})
        assert len(v["evidence"]) == review.MAX_EVIDENCE
        assert v["confidence"] == "low"


# ==================== 3. 案卷(纯函数,确定性) ====================
class TestBuildCase:
    def test_deterministic_and_bounded(self):
        slot = {"slot": "evening", "ok": True, "value": 0.7, "mu": 0.3,
                "delta": 0.4, "z": 2.1, "n": 5}
        sim = [{"day": "d1", "distance": 0.1}, {"day": "d2", "distance": 0.2}]
        c1 = review.build_case(_F, _sc(), {"reason": "watch_candidate"},
                               slot_cmp=slot, similar_days=sim,
                               risk_counts={"soft_alert": 1})
        c2 = review.build_case(_F, _sc(), {"reason": "watch_candidate"},
                               slot_cmp=slot, similar_days=sim,
                               risk_counts={"soft_alert": 1})
        assert c1 == c2                                  # 同输入同案卷
        assert c1["slot_baseline"] == slot
        assert c1["similar_days"] == sim


# ==================== 4. 触发与落盘(store 集成) ====================
class TestChannel:
    def _res(self, **kw):
        return {"features": dict(_F), "score": _sc(**kw)}

    def test_trigger_creates_task_with_snapshot(self):
        u = "u-rev1"
        assert rc.maybe_trigger(u, self._res(), now=NOW) is True
        task = mstore.get_review_task(u)
        assert task["reason"] == "watch_candidate"
        assert task["features"]["engagement_drop"] == 0.55
        assert task["score"]["s_total"] == 0.60

    def test_cooldown_and_single_slot(self, monkeypatch):
        u = "u-rev2"
        monkeypatch.setattr(cfg, "REVIEW_COOLDOWN_H", 24.0)
        mstore.save_review_report(u, {"ts": NOW - 3600, "verdict": {}})
        assert rc.maybe_trigger(u, self._res(), now=NOW) is False   # 冷却内
        mstore.save_review_report(u, {"ts": NOW - 25 * 3600, "verdict": {}})
        assert rc.maybe_trigger(u, self._res(), now=NOW) is True    # 冷却已过
        assert rc.maybe_trigger(u, self._res(), now=NOW) is False   # 已有待办

    def test_disabled_channel_never_triggers(self, monkeypatch):
        u = "u-rev3"
        monkeypatch.setattr(cfg, "REVIEW_ENABLED", False)
        assert rc.maybe_trigger(u, self._res(), now=NOW) is False

    def test_support_verdict_goes_to_risk_events(self, monkeypatch):
        u = "u-rev4"
        rc.maybe_trigger(u, self._res(), now=NOW)
        monkeypatch.setattr(rc, "_investigate", lambda case: {
            "conclusion": "support", "confidence": "mid",
            "evidence": [{"fact": "参与度塌陷", "quote": "engagement_drop=0.55",
                          "source": "signals"}],
            "conflicts": [], "note": "多路弱信号同向"})
        rep = rc.run_due_review(u, now=NOW + 60)
        assert rep["verdict"]["conclusion"] == "support"
        assert mstore.get_review_task(u) is None
        facets = [it.value.get("facet") for it in mstore.list_risk_events(u)]
        assert facets.count("review_support") == 1
        assert rc.last_summary(u)["conclusion"] == "support"

    def test_refute_verdict_stays_audits_only(self, monkeypatch):
        u = "u-rev5"
        rc.maybe_trigger(u, self._res(), now=NOW)
        monkeypatch.setattr(rc, "_investigate", lambda case: {
            "conclusion": "refute", "confidence": "high",
            "evidence": [], "conflicts": [], "note": "时段基线显示本就如此"})
        rep = rc.run_due_review(u, now=NOW + 60)
        assert rep["verdict"]["conclusion"] == "refute"
        facets = [it.value.get("facet") for it in mstore.list_risk_events(u)]
        assert "review_support" not in facets
        assert mstore.last_review_report(u)["verdict"]["note"] == "时段基线显示本就如此"

    def test_llm_failure_backoff_then_drop(self, monkeypatch):
        u = "u-rev6"
        monkeypatch.setattr(cfg, "REVIEW_MAX_RETRY", 1)
        rc.maybe_trigger(u, self._res(), now=NOW)

        def _boom(case):
            raise RuntimeError("llm down")
        monkeypatch.setattr(rc, "_investigate", _boom)
        assert rc.run_due_review(u, now=NOW + 60) is None      # 第 1 次:保留
        assert mstore.get_review_task(u)["failures"] == 1
        assert rc.run_due_review(u, now=NOW + 120) is None     # 第 2 次:超限丢弃
        assert mstore.get_review_task(u) is None

    def test_no_task_noop(self):
        assert rc.run_due_review("u-rev-none", now=NOW) is None

    def test_case_carries_deterministic_tools(self, monkeypatch):
        """案卷里的时段基线/相似日来自 daytools(确定性),字段齐可审计。"""
        from gzhu_cup2.agent.long_memory import ticks
        u = "u-rev7"
        # NOW 本地约 16 点(afternoon 段);+4h 落进 evening 段,便于断言时段
        ts0 = NOW + 4 * 3600.0
        for d in range(-5, 0):
            ticks.bump_day_bucket(u, {"risk_level": "low",
                                      "negative_score": 0.75}, ts=ts0 + d * 86400.0)
        rc.maybe_trigger(u, self._res(), now=ts0)
        captured = {}

        def _fake(case):
            captured.update(case)
            return {"conclusion": "watch", "confidence": "low",
                    "evidence": [], "conflicts": [], "note": ""}
        monkeypatch.setattr(rc, "_investigate", _fake)
        rc.run_due_review(u, now=ts0 + 60)
        assert captured["slot_baseline"]["slot"] == "evening"
        assert captured["slot_baseline"]["ok"] is True
        assert isinstance(captured["similar_days"], list)


# ==================== 5. prompt 组装(不调 LLM) ====================
class TestPrompt:
    def test_review_prompt_contains_case_sections(self):
        from gzhu_cup2.agent.prompts.long_memory_prompts import (
            build_review_investigation_prompt)
        case = review.build_case(_F, _sc(), {"reason": "crisis_soft"},
                                 slot_cmp={"ok": False})
        msgs = build_review_investigation_prompt(case)
        assert len(msgs) == 2
        assert "复核调查员" in msgs[0].content
        assert "触发原因" in msgs[1].content
        assert "无数据" in msgs[1].content       # None 值显式降级不装看不见


# ==================== 1b. 形状歧义腿(mean_floor_gap) ====================
def _fgap(med=0.46, fl=0.30, n=7):
    f = dict(_F)
    f["recent"] = {"neg_median": med, "floor_median": fl, "n_days": n}
    return f


class TestMeanFloorGapLeg:
    def test_gap_shape_triggers_below_watch(self):
        """均值-低水位差大且分数未及 watch 线 → 挂形状调查。"""
        rep = review.should_review(_fgap(), _sc(0.38), confirm_open=False)
        assert rep == {"reason": "mean_floor_gap", "leg": "slot_shape"}

    def test_no_trigger_when_score_already_at_watch(self):
        """分数已进 watch 线时由档位/watch_candidate 语义接管,形状腿不重复。"""
        assert review.should_review(_fgap(), _sc(0.60, top="emotion"),
                                    confirm_open=False) is None

    def test_gap_needs_enough_recorded_days(self):
        assert review.should_review(_fgap(n=3), _sc(0.38),
                                    confirm_open=False) is None

    def test_uniform_shape_does_not_trigger(self):
        """全面持续低位(S3 形状)均值=低水位,无形状歧义。"""
        assert review.should_review(_fgap(med=0.55, fl=0.55), _sc(0.38),
                                    confirm_open=False) is None

    def test_small_gap_does_not_trigger(self):
        assert review.should_review(_fgap(med=0.40, fl=0.30), _sc(0.30),
                                    confirm_open=False) is None


# ==================== 1c. confirm_open 谓词(空状态不算开窗) ====================
class TestConfirmOpenPredicate:
    def test_empty_confirm_state_is_not_open(self):
        """普通轮落盘的空确认状态(alert_ts=None)不得当作"确认窗开着"。"""
        from gzhu_cup2.agent.algo.components import crisis_chain
        u = "u-empty-confirm"
        mstore.set_crisis_confirm(u, crisis_chain.new_state())
        res = {"features": dict(_F), "score": _sc(0.20)}
        assert rc.maybe_trigger(u, res, now=NOW) is False
        assert mstore.get_review_task(u) is None

    def test_real_alert_window_is_open(self):
        """soft 警铃开窗(alert_ts 非空)→ crisis_soft 触发。"""
        from gzhu_cup2.agent.algo.components import crisis_chain
        u = "u-open-confirm"
        mstore.set_crisis_confirm(u, {"alert_ts": NOW - 60, "turns": 0,
                                      "hits": 1, "frozen": False})
        res = {"features": dict(_F), "score": _sc(0.20)}
        assert rc.maybe_trigger(u, res, now=NOW) is True
        assert mstore.get_review_task(u)["reason"] == "crisis_soft"
