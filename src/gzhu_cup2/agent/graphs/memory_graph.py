"""后台「记忆图」:与主聊天图分离的第二个 compiled 图。

职责:固化一段待处理对话 —— extract(LLM 结构化总结/抽取) → commit(落库 + 推进 pending)。
不携带 checkpointer(无会话状态),只依赖 shared 的 long_memory.store 单例。
"""
import logging
import time
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END, START
from langgraph.types import RetryPolicy
from gzhu_cup2.agent.algo.components import crisis_chain
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory.consolidate import extract_memory_voted

logger = logging.getLogger(__name__)


class MemoryState(TypedDict, total=False):
    """记忆图内部状态(不参与主图 MentalHealthState)。"""
    user_id: str
    pending: Optional[dict]        # 待固化段快照(store 读出的副本)
    extraction: Optional[object]   # MemoryExtraction
    support: Optional[dict]        # {规范化事实文本: 被几个采样抽到}(弱证据标注用)


def _extract_node(state: MemoryState) -> dict:
    pending = state.get("pending")
    if not pending:
        return {}
    # 多采样抽取(采样次数由 cfg.MEMORY_CONSOLIDATE_VOTE_SAMPLES 控制,可设 1 关闭);
    # 合并口径为"并集 + 支持度",支持度经 state 传给 commit 节点落库(见 store.merge_fact)
    support: dict = {}
    # user_id 一并传入:抽取提示要注入「已记下的事」清单,否则同一件事的详略两版
    # 会各自新起一条(近义重复的根因,见 consolidate._existing_facts_brief)。
    extraction = extract_memory_voted(
        pending, support_out=support,
        user=str(state.get("user_id") or "").strip() or None)
    return {"extraction": extraction, "support": support}


def _crisis_hook(user: str, extraction, now: float) -> None:
    """固化后的危机确认链接线(确定性,零 LLM)。

    - intent/plan/behavior 任一命中(等级 ≥2)→ 写升级标记,
      前台下一轮开口即进硬协议(异步架构下固化的到达时机);
    - denied_quote 命中 → 推进 denied 事件(冻结自动升级至窗口结束);
    - uncertain_quote 命中 → 只留痕(风险分面 facet=uncertain),不升级。
    """
    c = getattr(extraction, "crisis", None)
    if c is None:
        return
    if any(len(str(getattr(c, f, "") or "").strip()) >= 2
           for f in ("intent", "plan", "behavior")):
        mstore.mark_crisis_escalated(user, now)
    denied = str(getattr(c, "denied_quote", "") or "").strip()
    if len(denied) >= 2:
        st = mstore.get_crisis_confirm(user)
        rep = crisis_chain.update(st, "denied", now)
        mstore.set_crisis_confirm(user, rep["state"] or None)
    uncertain = str(getattr(c, "uncertain_quote", "") or "").strip()
    if len(uncertain) >= 2:
        mstore.add_risk_event(user, "uncertain", uncertain, ts=now)


def _commit_node(state: MemoryState) -> dict:
    pending = state.get("pending")
    extraction = state.get("extraction")
    # 拒绝落库"空摘要":抽取实质失败时照常提交会推进/删除 pending,原话即丢失。
    # 不提交,让 worker 退避重试。
    digest = str(getattr(extraction, "digest", "") or "").strip() if extraction else ""
    if not pending or extraction is None or not digest:
        return {}
    # 不回落默认用户:缺少 user_id 时跳过固化,避免写进空名命名空间。
    user = str(state.get("user_id") or "").strip()
    if not user:
        logger.warning("固化被跳过:state 缺少 user_id(不回落默认用户,避免把记忆写错人)")
        return {}
    mstore.commit_segment(user, pending, extraction, support=state.get("support") or {})
    _crisis_hook(user, extraction, float(pending.get("last_ts", time.time())))
    return {}


def build_memory_graph():
    g = StateGraph(MemoryState)
    g.add_node("extract", _extract_node, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("commit", _commit_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "commit")
    g.add_edge("commit", END)
    return g.compile()