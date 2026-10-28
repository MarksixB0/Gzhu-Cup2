from langgraph.graph import StateGraph, END, START
from langgraph.types import RetryPolicy
from langgraph.checkpoint.memory import InMemorySaver

from gzhu_cup2.agent.nodes.receive_input import receive_input
from gzhu_cup2.agent.nodes.triage import fast_triage
from gzhu_cup2.agent.nodes.retrieve_memory import retrieve_memory
from gzhu_cup2.agent.nodes.generate_response import generate_response
from gzhu_cup2.agent.nodes.handle_crisis import handle_crisis
from gzhu_cup2.agent.nodes.crisis_fallback import crisis_fallback
from gzhu_cup2.agent.nodes.capture_memory import capture_memory
from gzhu_cup2.agent.core.state import MentalHealthState


def build_graph():
    """前台主图:功能锁在「心理陪伴」上(回复提速版)。

    原主图里的情绪分析 / 风险分级节点已移除:
      - 前台只做 快闸分流(triage,零 LLM)+ 读长期记忆 + 一次生成 + 免费捕获;
      - 情绪/风险/量表深度分析移到后台记忆(固化标签卡 + trend/profile 窗口聚合)。
    """
    # 创建节点
    base_work_state = StateGraph(MentalHealthState)
    base_work_state.add_node("receive_input", receive_input)
    base_work_state.add_node("triage", fast_triage)
    base_work_state.add_node("retrieve_memory", retrieve_memory)
    base_work_state.add_node("generate_response",
                             generate_response,
                             retry_policy=RetryPolicy(max_attempts=3),
                             )
    base_work_state.add_node("handle_crisis",
                             handle_crisis,
                             retry_policy=RetryPolicy(max_attempts=3),
                             error_handler=crisis_fallback,
                             )
    # 记忆写节点:每轮回复后免费捕获(LLM 固化在后台记忆图,不进主图请求路径)
    base_work_state.add_node("capture_memory", capture_memory)

    # 创建边
    base_work_state.add_edge(START, "receive_input")
    base_work_state.add_edge("receive_input", "triage")

    # triage 以 Command 直跳:
    #   crisis   → handle_crisis(同步危机干预,安全红线,绝不移到主动回复)
    #   其它     → retrieve_memory(本地读记忆)→ generate_response(唯一前台 LLM 调用)
    base_work_state.add_edge("retrieve_memory", "generate_response")
    base_work_state.add_edge("generate_response", "capture_memory")
    base_work_state.add_edge("handle_crisis", "capture_memory")
    base_work_state.add_edge("capture_memory", END)

    memory = InMemorySaver()
    app = base_work_state.compile(checkpointer=memory)
    return app
