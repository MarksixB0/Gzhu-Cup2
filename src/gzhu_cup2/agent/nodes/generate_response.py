import time

from langchain_core.messages import AIMessage

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.core.models.LLM_base import get_llm
from gzhu_cup2.agent.prompts.prompts import build_generate_response_prompt


def _resolve_llm(state):
    """依据本轮 llm_params(可选)选择实例;无则用 config 默认。可按 (model,temp) 切换。

    守护分寸会收紧**输出上限**(多听少说),这不改变模型选择,只改变这次生成的
    长度余量;分寸判定失败一律回落到不施加额外约束(见 config 的同名常量)。
    """
    params = state.get("llm_params") or {}
    return get_llm(
        model_name=params.get("model") or params.get("model_name"),
        temperature=params.get("temperature"),
        max_tokens=_reply_max_tokens(state),
    )


def _reply_max_tokens(state: MentalHealthState) -> int:
    """按守护分寸档收紧回复上限:steady 不动,gentle/caring 逐级略收。"""
    if not getattr(cfg, "PET_CARE_TONE_ENABLED", True):
        return cfg.REPLY_MAX_TOKENS
    from gzhu_cup2.agent.care.mode import care_level
    lv = care_level(state.get("guard_tier"))
    return int({
        "gentle": getattr(cfg, "REPLY_MAX_TOKENS_GENTLE", cfg.REPLY_MAX_TOKENS),
        "caring": getattr(cfg, "REPLY_MAX_TOKENS_CARING", cfg.REPLY_MAX_TOKENS),
    }.get(lv, cfg.REPLY_MAX_TOKENS))


def generate_response(state: MentalHealthState, llm=None):
    """根据情绪+风险生成共情回复。"""
    llm = llm or _resolve_llm(state)
    messages = build_generate_response_prompt(state)
    result = llm.invoke(messages).content.strip()
    return {"messages": [AIMessage(content=result,
                                   additional_kwargs={"ts": time.time()})]}
