# -*- coding: utf-8 -*-
"""状态图单例 + 一轮对话执行 + 图输出→前端摘要(零 FastAPI 依赖)。

**单例**:图的短期记忆挂在 ``InMemorySaver`` 上,必须复用同一实例,
否则每个请求都会拿到一份空的对话上下文。

**并发上限**:``lock`` 串行化**整轮**图执行(LLM 调用也在锁内),同一时刻
只能跑一轮对话,第二个请求会等待。
"""
import logging
import threading
import time
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage

from gzhu_cup2.agent.graphs.main_graph import build_graph
from gzhu_cup2.agent.web import sessions

logger = logging.getLogger(__name__)

# 图的执行锁:同一时刻只跑一轮(见模块 docstring)
lock = threading.Lock()

_graph = None


def graph():
    """图实例单例(惰性构建)。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset() -> None:
    """丢弃图单例(测试隔离用:下一次调用会重建)。

    测试会先把节点的 ``get_llm`` 换成假引擎,再重置单例,
    以确保首次请求时用的是假引擎。
    """
    global _graph
    _graph = None


def run_turn(thread_id: str, user_message: str,
             llm_params: Optional[dict] = None,
             user: Optional[str] = None) -> tuple:
    """线程安全地执行一轮完整对话,返回 ``(reply, state)``。

    ``user`` = 长期记忆归属的用户键;None 时回落到默认用户
    (见 ``web.sessions.resolve_user``)。**必须由调用方显式传入**才能区分用户。
    """
    config = {"configurable": {"thread_id": thread_id}}
    with lock:
        result = graph().invoke(
            {"messages": [HumanMessage(content=user_message,
                                       additional_kwargs={"ts": time.time()})],
             # 记忆按"用户"划分(见 web/sessions.resolve_user);thread 仅作会话记忆键
             "user_id": user or sessions.resolve_user(),
             "llm_params": llm_params or {}},
            config=config,
        )
    reply = result.get("messages", [])[-1].content if result.get("messages") else ""
    return reply, result


def push_assistant_line(thread_id: str, text: str,
                        ts: Optional[float] = None) -> bool:
    """把一条**不是 LLM 当场生成**的助手消息写进图状态(主动消息 / 定时仪式)。

    为什么必须写:主动消息只落 ``sessions`` 时,前端和数据库里都有它,但模型看的
    是图里的短期记忆 —— 里面没有这一句。用户接着回应时,模型不知道自己刚说过
    什么,整段对话会前言不搭后语。

    图还没建(本进程从未聊过天)时直接跳过:没有状态可补,也不需要为一条主动
    消息去构建整张图。返回是否写入成功。
    """
    if _graph is None or not str(text or "").strip():
        return False
    try:
        _graph.update_state(
            {"configurable": {"thread_id": str(thread_id)}},
            {"messages": [AIMessage(
                content=str(text),
                additional_kwargs={"ts": float(ts or time.time()), "proactive": True},
            )]},
        )
        return True
    except Exception as e:  # noqa: BLE001 - 补上下文失败不得影响消息投递
        logger.warning("主动消息未同步进对话图 thread=%s: %s", thread_id, e)
        return False


# ============================ 图输出 → 前端摘要 ============================

def extract_analysis(state: dict) -> dict:
    """把一轮 langgraph 输出转成调用方摘要。

    前端/桌宠唯一消费的是 ``tone``(桌宠据此选待机表情)。其余内部标记
    (risk_level、情绪明细、危机处置)不进 API 响应、不落盘 —— 内部标签不外泄。
    """
    emo = state.get("emotional_analysis")
    risk_level = state.get("risk_level") or "low"
    emo_name = emo.emotion if emo else "neutral"

    # 对话基调:由「当轮情绪 + 风险档」压成的四个表达方式(care.mode.dialogue_mode)。
    from gzhu_cup2.agent.care.mode import dialogue_mode
    return {"tone": dialogue_mode(emo_name, risk_level)}
