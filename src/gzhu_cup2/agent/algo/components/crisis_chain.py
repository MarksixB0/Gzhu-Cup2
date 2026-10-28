# -*- coding: utf-8 -*-
"""crisis_chain 组件:危机确认链的确定性状态机(纯函数、零 IO、零 LLM)。

语义(2026-10-24 用户裁定):
  - hard 词表命中(方法/手段/告别类)= 计划性证据,直接 hard 协议,不延迟;
  - soft 词表命中(死亡意象无计划)= soft 警铃:开确认窗,本轮走正常回复流
    带 soft 标记(共情+留门);**全程不主动提问**——证据只来自用户自发
    表达与固化抽取;
  - 确认升级:确认窗内用户再表达(第 2 次词表命中)→ hard 协议;
  - denied(用户明确否认)→ 冻结自动升级至窗口结束;冻结期内再命中按
    新警铃重开窗(召回优先,不自动升级);
  - 自然消退:窗内连续 CONFIRM_TURNS 轮无危机证据或窗口超时 → 撤销警铃。

事件输入:hard_hit / soft_hit / denied / idle_turn。level≥2 固化证据不走
本状态机 —— 由固化侧写升级标记(memory_graph._crisis_hook →
mark_crisis_escalated),前台下一轮 pop 后以 hard_hit 事件进入。
动作输出:hard_protocol / soft_alert / none / retract。
留痕(risk_events)由调用方按动作写,本组件不碰 IO。
"""
from __future__ import annotations

# 确认窗:开窗后 N 轮内无新证据则撤销;窗口最长 W 小时
CONFIRM_TURNS = 5
CONFIRM_WINDOW_H = 24.0

A_HARD = "hard_protocol"
A_SOFT = "soft_alert"
A_NONE = "none"
A_RETRACT = "retract"


def new_state() -> dict:
    return {"alert_ts": None, "turns": 0, "hits": 0, "frozen": False}


def window_open(state: dict | None) -> bool:
    """确认窗是否开着(存在未撤销的 soft 警铃)。

    判定口径与 update 内部一致:看 alert_ts,不看"状态记录是否存在" ——
    普通轮也会落盘空状态,不能用 None 哨兵判窗。
    """
    return bool(state) and state.get("alert_ts") is not None


def _expired(state: dict, now: float, window_h: float) -> bool:
    return (state.get("alert_ts") is not None
            and (now - float(state["alert_ts"])) > window_h * 3600.0)


def _reset(state: dict) -> dict:
    return new_state()


def update(state: dict | None, event: str, now: float,
           turns: int = CONFIRM_TURNS,
           window_h: float = CONFIRM_WINDOW_H) -> dict:
    """推进确认链状态,返回 {"state": 新状态, "action": 动作}。

    ``state`` 传 None/空按新警铃前的静默态处理。窗口过期先于事件处理
    (过期即撤销,本轮事件按无窗状态解释)。
    """
    s = dict(state or new_state())
    now = float(now)
    turns = max(1, int(turns))

    # 1. 窗口过期 → 撤销(过期后的本轮事件按无窗解释)
    if _expired(s, now, float(window_h)):
        s = _reset(s)
        return {"state": s, "action": A_RETRACT}

    # 2. hard 词命中:计划性证据,直接协议,清窗
    if event == "hard_hit":
        return {"state": _reset(s), "action": A_HARD}

    # 3. soft 词命中
    if event == "soft_hit":
        if s.get("alert_ts") is None or s.get("frozen"):
            # 开新警铃窗(冻结期内再命中亦然:按新警铃处理,不自动升级)
            s = {"alert_ts": now, "turns": 0, "hits": 1, "frozen": False}
            return {"state": s, "action": A_SOFT}
        # 窗内再表达 → 升级(冻结只挡自动升级,挡不住明确再表达)
        return {"state": _reset(s), "action": A_HARD}

    # 4. denied:明确否认 → 冻结自动升级至窗口结束(窗开着才有意义)
    if event == "denied":
        if s.get("alert_ts") is not None:
            s["frozen"] = True
        return {"state": s, "action": A_NONE}

    # 5. 普通轮:计数;窗内累计 N 轮无新证据 → 撤销
    if event == "idle_turn":
        if s.get("alert_ts") is None:
            return {"state": s, "action": A_NONE}
        s["turns"] = int(s.get("turns", 0)) + 1
        if s["turns"] >= turns:
            return {"state": _reset(s), "action": A_RETRACT}
        return {"state": s, "action": A_NONE}

    return {"state": s, "action": A_NONE}
