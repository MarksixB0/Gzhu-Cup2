# -*- coding: utf-8 -*-
"""前台「快速分流」图节点:把 algo.triage 的粗分类接进主图。

判定逻辑全部在 algo/components/triage.py;本文件只负责 LangGraph 接线
与危机确认链(crisis_chain)的每轮推进。

危机路由:
  - hard 词命中(方法/手段/告别)= 计划性证据 → 直接 handle_crisis;
  - soft 词命中 → soft 警铃:开确认窗、本轮走正常回复流(crisis_soft=True,
    共情+留门,**零提问**);窗内再表达 → 升级 handle_crisis;
  - 普通轮照常推进确认窗(连续 N 轮无新证据 → 撤销并留痕)。
"""
import time

from langgraph.types import Command
from gzhu_cup2.agent.algo.components import triage
from gzhu_cup2.agent.algo.components import crisis_chain
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.schemas import AnalyzeEmotionResult
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.long_memory import store as mstore

classify = triage.classify
risk_from = triage.risk_from


def _confirm(user: str, event: str, now: float, quote: str | None = None) -> str:
    """推进该用户的危机确认链并落库,返回动作。

    留痕(受保护 risk_events):soft_alert 记触发原话;retracted 记撤销标记;
    升级/hard 轮的原话由固化路径的 signal 留痕覆盖,此处不重复记。
    """
    st = mstore.get_crisis_confirm(user)
    rep = crisis_chain.update(
        st, event, now,
        turns=int(getattr(cfg, "CRISIS_CONFIRM_TURNS", crisis_chain.CONFIRM_TURNS)),
        window_h=float(getattr(cfg, "CRISIS_CONFIRM_WINDOW_H",
                               crisis_chain.CONFIRM_WINDOW_H)))
    act = rep["action"]
    if act in (crisis_chain.A_HARD, crisis_chain.A_RETRACT):
        mstore.set_crisis_confirm(user, None)
    else:
        mstore.set_crisis_confirm(user, rep["state"])
    if act == crisis_chain.A_SOFT and quote:
        mstore.add_risk_event(user, "soft_alert", quote, ts=now)
    elif act == crisis_chain.A_RETRACT:
        mstore.add_risk_event(user, "retracted", "（确认窗自然消退，无新证据）", ts=now)
    return act


def fast_triage(state: MentalHealthState) -> dict:
    """主图分流节点:写回粗情绪/风险,按危机确认链语义路由。"""
    text = state.get("process_input") or ""
    c = triage.classify(text)
    risk, _ = triage.risk_from(c)
    emo = AnalyzeEmotionResult(
        emotion=c["emotion"],
        intensity=c["intensity"],
        negative_score=c["negative_score"],
        description=c["description"],
    )
    update = {
        "emotional_analysis": emo,
        "risk_level": risk,
        # 默认清掉上一轮可能残留的危机标记(每轮都会经过本节点覆盖)
        "crisis_level": None,
        "crisis_soft": False,
    }
    user = str(state.get("user_id") or "")
    level = c.get("crisis_level")
    now = time.time()

    # 固化侧升级标记(intent/plan/behavior 证据)优先级最高:下一轮开口即硬协议
    if user and mstore.pop_crisis_escalated(user):
        _confirm(user, "hard_hit", now)
        update["risk_level"] = "crisis"
        update["crisis_level"] = "hard"
        return Command(goto="handle_crisis", update=update)

    if level == "hard":
        # 计划性证据:直接完整协议(残留确认窗一并清除)
        if user:
            _confirm(user, "hard_hit", now)
        update["crisis_level"] = "hard"
        return Command(goto="handle_crisis", update=update)

    if level == "soft":
        act = _confirm(user, "soft_hit", now, quote=text) if user else "none"
        if act == crisis_chain.A_HARD:
            # 确认窗内再表达 → 升级完整协议
            update["risk_level"] = "crisis"
            update["crisis_level"] = "hard"
            return Command(goto="handle_crisis", update=update)
        # soft 警铃:走正常回复流;回复层按 crisis_soft 给共情+留门指令(零提问)。
        # risk_level 抬到 high 以承接"共情倾听"的语气分寸;固化侧 high 轮
        # 照常写 signal 留痕(原话),不丢审计。
        update["risk_level"] = "high"
        update["crisis_soft"] = True
        return Command(goto="retrieve_memory", update=update)

    if user:
        _confirm(user, "idle_turn", now)
    return Command(goto="retrieve_memory", update=update)
