from typing_extensions import TypedDict
from typing import  Annotated, Sequence, Optional, Literal
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from gzhu_cup2.agent.core.schemas import AnalyzeEmotionResult


class MentalHealthState(TypedDict):
    """创建状态图"""
    #全局状态
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_id: str
    # 每轮 LLM 覆盖参数(可选):{"model": str, "temperature": float};空表示用 config 默认
    llm_params: Optional[dict]

    #receive_input节点
    process_input: Optional[str]
    # 对话里表达的记忆隐私意愿(见 long_memory/consent.py):
    #   privacy_intents: ("pause"/"resume"/"forget") 元组转成的 list;空/None = 无意愿
    #   privacy_outcome: 实际执行结果 {"recording": bool, "pending_turns_deleted": int,
    #                    "applied": [...]},供回复层**如实告知**用户(privacy.py 约束 #1/#3)
    # ⚠ 二者只描述"系统做了什么",不是对用户的判断,故可安全进入提示词。
    privacy_intents: Optional[list]
    privacy_outcome: Optional[dict]

    #triage节点
    emotional_analysis: Optional[AnalyzeEmotionResult]

    risk_level: Optional[Literal["low", "medium", "high", "crisis"]]

    #retrieve_memory节点
    memory_context: Optional[str]  # 读取到的长期记忆纯文本(空/None = 无)
    # 近期日程提示块(agenda/brief.render):用户自己在日历里写的安排。
    # 只读渲染、已带「今天/明天/N 天后」,空/None = 窗口内没有日程(提示词不出现该块)。
    # 与长期记忆分开放:它不是"记忆",不需要检索与衰减,每轮按当下时间重算。
    agenda_context: Optional[str]
    # 纵向守护档位(algo.tier_of):stable/watch/elevated。
    # ⚠ 只作**内部分寸输入**,由 care.mode 映射成场合词后才进入提示词;
    #   档位名本身绝不写进任何给模型的文本(见 care/mode.py care_level 的说明)。
    guard_tier: Optional[str]

    #handle_crisis节点
    crisis_handled: Optional[bool]
    # 危机分级(triage.crisis_level):"hard" = 明确意图/方法/手段/告别,
    # "soft" = 死亡意象但无计划。只作**协议强度**的分支依据:
    #   hard → 共情 + 安全确认 + 求助资源(完整协议)
    #   soft → 共情 + 安全确认 + 陪伴,不直接甩热线
    # 该字段不给模型看,也不写进任何用户可见文本。
    crisis_level: Optional[Literal["hard", "soft"]]
    # 危机确认链:soft 词命中但未升级时为 True —— 本轮走正常
    # 回复流,回复层给"共情+留门、零提问"指令;硬协议轮为 False/None。
    crisis_soft: Optional[bool]