# -*- coding: utf-8 -*-
"""主图读节点:把该用户的长期记忆读进 state["memory_context"]。

仅走非危机分支(边见 graphs/main_graph.py);纯本地读取,不调 LLM。
"""
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.identity import current_user
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.long_memory import retrieve as mem_retrieve


def _current_thread_id() -> str | None:
    try:
        from langgraph.config import get_config
        return (get_config().get("configurable") or {}).get("thread_id")
    except Exception:  # noqa: BLE001 - 无运行上下文时降级
        return None


def retrieve_memory(state: MentalHealthState) -> dict:
    """读取长期记忆(档案事实 + 会话回顾),格式化成一段文本写回 state。

    把用户本轮输入(query)传给读侧,用于按问题取用。

    顺带算一次纵向守护档位,作为对话分寸的输入(档位名不进入任何提示词);
    失败时静默退回 stable。
    """
    user = current_user(state)
    query = (state.get("process_input") or "").strip() or None
    text = mem_retrieve.build_memory_context(user, exclude_thread=_current_thread_id(),
                                             query=query)

    guard_tier = "stable"
    if getattr(cfg, "PET_CARE_TONE_ENABLED", True):
        try:
            from gzhu_cup2.agent.long_memory import risk_state as mrs
            guard_tier = mrs.effective_tier(user)
        except Exception:  # noqa: BLE001 - 守护判定失败不阻断对话
            guard_tier = "stable"

    # 近期日程:用户自己在日历里写的安排(只读,见 agenda/brief.py)。
    # 只在本节点取 —— 危机分支不经过这里,危机回复天然不会去聊日程。
    agenda_context = None
    try:
        from gzhu_cup2.agent.agenda import brief as abrief
        agenda_context = abrief.render(user)
    except Exception:  # noqa: BLE001 - 日程读取失败不得阻断对话
        agenda_context = None

    return {"memory_context": text or None, "guard_tier": guard_tier,
            "agenda_context": agenda_context}
