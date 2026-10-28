# -*- coding: utf-8 -*-
"""主图写节点:每轮回复生成后,把本轮对话免费追加进该用户待固化段。

无 LLM、纯 Store KV 写入(毫秒级);LLM 级的总结固化由后台记忆图/worker 承担。
"""
import logging

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.identity import current_user
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.care.mode import classify_affect_reply
from gzhu_cup2.agent.long_memory import privacy as mprivacy
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.algo.components.lexicon import behavior_flags, neg_channel
from gzhu_cup2.agent.algo.components import structure

logger = logging.getLogger(__name__)

# 镜像确认口径:仅当"上一轮用户负面分 ≥ 该值"且本轮是短确认/否认时,才计一次信号
_MIRROR_PREV_NEG_FLOOR = 0.55


def _current_thread_id() -> str | None:
    try:
        from langgraph.config import get_config
        return (get_config().get("configurable") or {}).get("thread_id")
    except Exception:  # noqa: BLE001 - 无运行上下文时降级
        return None


def _turn_meta(state: MentalHealthState) -> dict:
    """抽取便于后续固化的轻量元信息(情绪/风险/所用模型),与 server 的展示摘要解耦。

    注意:不再把逐轮 phq/gad/cssrs 伪量表分写进记忆(噪声源)——
    量表推断改由固化时的维度证据(episode 标签卡)与 trend 窗口聚合承担。
    """
    emo = state.get("emotional_analysis")
    llm_params = state.get("llm_params") or {}
    return {
        "risk_level": state.get("risk_level"),
        "emotion": (emo.emotion if emo else None),
        "intensity": (emo.intensity if emo else None),
        "negative_score": (emo.negative_score if emo else None),
        # 固化时跟随本轮实际使用的模型/温度,避免后台默认模型与聊天模型不一致
        "model": llm_params.get("model") or llm_params.get("model_name"),
        "temperature": llm_params.get("temperature"),
    }


def _mirror_marker(user: str, user_text: str) -> str | None:
    """镜像确认:上一轮用户负面表达 + 本轮为短确认/否认 → 计一个对话内纠错信号。

    返回 "confirm" / "deny" / None(仅统计用,不影响对话本身)。
    """
    try:
        pending = mem_store.get_pending(user) or {}
    except Exception:  # noqa: BLE001 - 读不到 pending 就不计信号,不干扰捕获
        pending = {}
    turns = pending.get("turns") or []
    prev_neg = None
    for t in reversed(turns):
        m = dict(t.get("meta") or {})
        if not (t.get("user") or "").strip():
            continue
        try:
            prev_neg = float(m.get("negative_score"))
        except (TypeError, ValueError):
            prev_neg = None
        break  # 只看最近一条"用户"轮
    if prev_neg is None or prev_neg < _MIRROR_PREV_NEG_FLOOR:
        return None
    return classify_affect_reply(user_text)


def capture_memory(state: MentalHealthState) -> dict:
    """本轮对话(用户输入 + AI 回复 + 分析元信息)入库待固化段。"""
    user = current_user(state)
    # 用户暂停记录时本轮不写任何记忆(对话不受影响);此为该开关的唯一入口。
    if not mprivacy.recording_enabled(user):
        return {}
    thread_id = _current_thread_id() or ""
    messages = state.get("messages") or []
    ai_text = messages[-1].content if messages else ""
    user_text = (state.get("process_input") or "").strip()
    if not user_text and not ai_text:
        return {}
    meta = _turn_meta(state)
    marker = _mirror_marker(user, user_text)
    if marker:
        meta["affect_reply"] = marker
    # 词表旁路通道(确定性):独立于启发式的负面口径,只作观察面并入日桶,不作判定。
    # 词表命中在组件内部先过结构判定(否定/他人主体/转述/假设不计入)。
    if cfg.MEMORY_LEXICON_CHANNEL_ENABLED:
        meta["lex_neg"] = neg_channel(user_text)
        # 行为/症状族(退缩/冲突/快感缺失/躯体化):与情绪独立的证据族。
        # 在此计算,因为原文不保留、事后无法回溯。
        meta["behavior"] = sorted(behavior_flags(user_text))
        # 整句级结构标记:排障面板解释"这句为什么没算证据"
        meta["structure"] = structure.scan(user_text)
    # 记忆落库失败不影响本轮对话:记日志、不抛。
    try:
        mem_store.capture_turn(user, thread_id, user_text, str(ai_text), meta=meta)
    except Exception as e:  # noqa: BLE001 - 记忆落库失败不得影响对话可用性
        logger.warning("本轮捕获失败 user=%s(回复不受影响): %s", user, e)
    return {}
