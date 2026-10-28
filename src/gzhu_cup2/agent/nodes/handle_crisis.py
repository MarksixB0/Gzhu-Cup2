from langchain_core.messages import AIMessage

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.models.LLM_base import get_llm
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.prompts.prompts import build_handle_crisis_prompt


def _resolve_llm(state):
    """依据本轮 llm_params(可选)选择实例;无则用 config 默认。可按 (model,temp) 切换。"""
    params = state.get("llm_params") or {}
    return get_llm(
        model_name=params.get("model") or params.get("model_name"),
        temperature=params.get("temperature"),
        max_tokens=cfg.REPLY_MAX_TOKENS,
    )


def handle_crisis(state: MentalHealthState, llm=None):
    llm = llm or _resolve_llm(state)
    messages = build_handle_crisis_prompt(state)
    result = llm.invoke(messages).content.strip()
    return {"messages": [AIMessage(content=result)],
            "crisis_handled": True,
            }
