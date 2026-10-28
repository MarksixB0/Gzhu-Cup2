from langchain_core.messages import AIMessage
from langgraph.errors import NodeError
from langgraph.types import Command

from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.config import config as cfg



def crisis_fallback(state: MentalHealthState, error: NodeError) -> Command:
    """当紧急干预节点失效后的兜底回复"""
    result = ("我感受到你现在的情绪非常强烈。请先联系以下专业热线获得即时支持：\n"
              f"{cfg.CRISIS_HOTLINES}"
              )
    return Command(
        goto="capture_memory",
        update={
            "messages": [AIMessage(
                content=result,
            )],
            "crisis_handled": True,
        }
    )