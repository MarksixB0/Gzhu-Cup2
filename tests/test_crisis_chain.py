# -*- coding: utf-8 -*-
"""危机确认链 ⑥a 测试。

锁六件语义(2026-10-24 用户裁定):
  1. hard 词命中 = 计划性证据,直接 hard 协议,清窗;
  2. soft 词命中 = soft 警铃:开窗,本轮**不**进协议;
  3. 窗内再表达 → 升级 hard 协议;level≥2 固化证据不走状态机,
     经升级标记下一轮以 hard_hit 进入(memory_graph._crisis_hook);
  4. denied 冻结自动升级:冻结期内再命中按**新警铃**重开窗;
  5. 窗内连续 N 轮无新证据 / 窗口超时 → 撤销;
  6. soft 警铃留痕(risk_events:soft_alert/retracted),全程零提问。
"""
import time
from types import SimpleNamespace

from langgraph.types import Command

from gzhu_cup2.agent.algo.components import crisis_chain as cc
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.nodes.triage import fast_triage

NOW = 1_800_000_000.0


def _adv(state_rep, event, dt=0.0, **kw):
    return cc.update(state_rep["state"], event, NOW + dt, **kw)


# ==================== 纯状态机 ====================
class TestStateMachine:
    def test_hard_hit_direct_and_clears(self):
        rep = _adv({"state": None}, "hard_hit")
        assert rep["action"] == "hard_protocol"
        assert rep["state"]["alert_ts"] is None          # 清窗

    def test_soft_hit_opens_window_not_protocol(self):
        rep = _adv({"state": None}, "soft_hit")
        assert rep["action"] == "soft_alert"
        assert rep["state"]["alert_ts"] == NOW
        assert rep["state"]["hits"] == 1

    def test_reexpression_escalates(self):
        rep = _adv({"state": None}, "soft_hit")
        rep = _adv(rep, "soft_hit", dt=600)
        assert rep["action"] == "hard_protocol"
        assert rep["state"]["alert_ts"] is None

    def test_denied_freezes_then_rehit_restarts_window(self):
        rep = _adv({"state": None}, "soft_hit")
        rep = _adv(rep, "denied", dt=60)
        assert rep["state"]["frozen"] is True
        # 冻结期内再命中:新警铃,不自动升级
        rep = _adv(rep, "soft_hit", dt=120)
        assert rep["action"] == "soft_alert"
        assert rep["state"]["frozen"] is False
        # 新窗内再表达 → 正常升级
        rep = _adv(rep, "soft_hit", dt=180)
        assert rep["action"] == "hard_protocol"

    def test_denied_does_not_block_hard_hit(self):
        """冻结只挡 soft 自动升级,挡不住 hard_hit(固化升级标记的等价事件)。"""
        rep = _adv({"state": None}, "soft_hit")
        rep = _adv(rep, "denied", dt=60)
        rep = _adv(rep, "hard_hit", dt=120)
        assert rep["action"] == "hard_protocol"
        assert rep["state"]["alert_ts"] is None

    def test_idle_turns_retract_after_n(self):
        rep = _adv({"state": None}, "soft_hit")
        for _ in range(cfg.CRISIS_CONFIRM_TURNS - 1):
            rep = _adv(rep, "idle_turn", dt=60)
            assert rep["action"] == "none"
        rep = _adv(rep, "idle_turn", dt=60)
        assert rep["action"] == "retract"
        assert rep["state"]["alert_ts"] is None

    def test_window_expiry_retracts(self):
        rep = _adv({"state": None}, "soft_hit")
        rep = _adv(rep, "idle_turn", dt=cfg.CRISIS_CONFIRM_WINDOW_H * 3600 + 60)
        assert rep["action"] == "retract"

    def test_idle_without_window_is_noop(self):
        rep = _adv({"state": None}, "idle_turn")
        assert rep["action"] == "none"


# ==================== 路由与留痕(store 集成) ====================
class TestRouting:
    def test_hard_routes_to_protocol(self):
        out = fast_triage({"process_input": "我已经写好了遗书", "user_id": "u-cc-hard"})
        assert isinstance(out, Command)
        assert out.goto == "handle_crisis"
        assert (out.update or {})["risk_level"] == "crisis"
        assert (out.update or {})["crisis_soft"] is False

    def test_soft_first_hit_stays_in_normal_flow(self):
        out = fast_triage({"process_input": "真想消失算了", "user_id": "u-cc-soft"})
        assert out.goto == "retrieve_memory"
        assert (out.update or {})["risk_level"] == "high"
        assert (out.update or {})["crisis_soft"] is True
        assert not (out.update or {}).get("crisis_handled")
        # 留痕:soft_alert + 原话
        facets = [it.value.get("facet") for it in mstore.list_risk_events("u-cc-soft")]
        assert "soft_alert" in facets
        quotes = [it.value.get("quote") for it in mstore.list_risk_events("u-cc-soft")]
        assert "真想消失算了" in quotes

    def test_soft_reexpression_escalates(self):
        fast_triage({"process_input": "不想再醒了", "user_id": "u-cc-esc"})
        out = fast_triage({"process_input": "好想消失", "user_id": "u-cc-esc"})
        assert out.goto == "handle_crisis"
        assert (out.update or {})["risk_level"] == "crisis"

    def test_soft_then_idle_retracts_with_trace(self):
        fast_triage({"process_input": "想彻底消失", "user_id": "u-cc-ret"})
        for _ in range(cfg.CRISIS_CONFIRM_TURNS):
            fast_triage({"process_input": "今天吃了顿好的",
                         "user_id": "u-cc-ret"})
        assert mstore.get_crisis_confirm("u-cc-ret") is None
        facets = [it.value.get("facet") for it in mstore.list_risk_events("u-cc-ret")]
        assert "retracted" in facets

    def test_idle_marks_reset_crisis_flags(self):
        """非危机轮要显式清掉上一轮残留的 crisis_soft/crisis_level。"""
        out = fast_triage({"process_input": "今天天气不错", "user_id": "u-cc-idle"})
        assert (out.update or {})["crisis_soft"] is False
        assert (out.update or {})["crisis_level"] is None


# ==================== ⑥b 固化钩子(抽取联动) ====================
class TestExtractionHook:
    """固化产出 level≥2 证据 / denied / uncertain 的确认链接线。"""

    def _extraction(self, **facet_kw):
        from gzhu_cup2.agent.core.schemas import CrisisFacets
        ex = SimpleNamespace(digest="d", crisis=CrisisFacets(**facet_kw))
        return ex

    def test_intent_evidence_marks_escalation_and_next_turn_goescrisis(self):
        from gzhu_cup2.agent.graphs.memory_graph import _crisis_hook
        u = "u-cc-hook1"
        _crisis_hook(u, self._extraction(intent="我打算今晚就动手"), NOW)
        # 前台下一轮(普通话)直接硬协议,标记一次性消费
        out = fast_triage({"process_input": "在吗", "user_id": u})
        assert out.goto == "handle_crisis"
        assert (out.update or {})["risk_level"] == "crisis"
        # 再下一轮不再触发(标记已消费)
        out2 = fast_triage({"process_input": "吃了吗", "user_id": u})
        assert out2.goto == "retrieve_memory"

    def test_ideation_only_does_not_escalate(self):
        from gzhu_cup2.agent.graphs.memory_graph import _crisis_hook
        u = "u-cc-hook2"
        _crisis_hook(u, self._extraction(ideation="活着没意思"), NOW)
        out = fast_triage({"process_input": "嗯", "user_id": u})
        assert out.goto == "retrieve_memory"

    def test_denied_freezes_window(self):
        from gzhu_cup2.agent.graphs.memory_graph import _crisis_hook
        u = "u-cc-hook3"
        # 先开 soft 窗(triage 用真实时钟),固化抽到否认 → 冻结
        fast_triage({"process_input": "想消失", "user_id": u})
        _crisis_hook(u, self._extraction(denied_quote="我没有想不开,别担心"),
                     time.time() + 60)
        st = mstore.get_crisis_confirm(u)
        assert st is not None and st.get("frozen") is True

    def test_uncertain_only_leaves_trace(self):
        from gzhu_cup2.agent.graphs.memory_graph import _crisis_hook
        u = "u-cc-hook4"
        _crisis_hook(u, self._extraction(uncertain_quote="感觉撑不下去了的样子"), NOW)
        assert mstore.get_crisis_confirm(u) is None          # 不开窗不冻结
        facets = [it.value.get("facet") for it in mstore.list_risk_events(u)]
        assert facets.count("uncertain") == 1

    def test_no_crisis_object_is_noop(self):
        from gzhu_cup2.agent.graphs.memory_graph import _crisis_hook
        u = "u-cc-hook5"
        _crisis_hook(u, SimpleNamespace(digest="d", crisis=None), NOW)
        assert mstore.get_crisis_confirm(u) is None
        assert mstore.pop_crisis_escalated(u) is False

    def test_merge_votes_new_fields(self):
        from gzhu_cup2.agent.core.schemas import CrisisFacets, MemoryExtraction
        from gzhu_cup2.agent.long_memory.consolidate import merge_extractions
        a = MemoryExtraction(digest="x", crisis=CrisisFacets(
            denied_quote="我没想不开", uncertain_quote="好像不太好"))
        b = MemoryExtraction(digest="x", crisis=CrisisFacets(
            denied_quote="我没有想不开,别担心"))
        m = merge_extractions([a, b])
        assert m.crisis.denied_quote == "我没有想不开,别担心"   # 取最长
        assert m.crisis.uncertain_quote == "好像不太好"
        m2 = merge_extractions([MemoryExtraction(digest="x")])
        assert m2.crisis is None


# ==================== ⑥c care 层与 soft 窗口的交互 ====================
class TestCareSuppression:
    def _bump(self, user, ts, risk, neg=0.85):
        from gzhu_cup2.agent.long_memory import ticks
        ticks.bump_day_bucket(user, {
            "risk_level": risk, "negative_score": neg, "emotion": "sad",
        }, ts=ts)

    def test_soft_high_turn_suppresses_proactive_care(self):
        """soft 轮 risk=high → 日桶 rank=2 → 近期低落慰问不触发(高风险日不走主动关怀)。"""
        from gzhu_cup2.agent.care.coordinator import _recent_distress
        from gzhu_cup2.agent.long_memory import ticks
        u = "u-cc-care1"
        self._bump(u, NOW - 3600, "high")
        assert ticks.last_activity_ts(u, now=NOW) is not None
        assert _recent_distress(u, NOW) is False

    def test_dialogue_mode_never_playful_on_high(self):
        from gzhu_cup2.agent.care.mode import dialogue_mode
        assert dialogue_mode("happy", "high") == "gentle"
        assert dialogue_mode("sad", "high") == "gentle"
