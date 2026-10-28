# -*- coding: utf-8 -*-
"""输入节点:清洗本轮输入,并处理**用户在对话里表达的记忆隐私意愿**。

隐私意愿必须在这里落地,原因有二:

1. 本节点在**生成与捕获之前**。开关一翻,``nodes/capture_memory.py`` 既有的早退
   (它是"暂停记录"的**唯一入口**)就会让**本轮**也不入库 —— 用户说"这个别记下来"
   时,那句话本身就不该被记住;
2. 意愿随 state 传给生成节点,回复层才能**如实告知**用户
   (``privacy.py`` 约束 #1:"暂停必须如实可见")。
"""
from gzhu_cup2.agent.core.identity import current_user
from gzhu_cup2.agent.core.state import MentalHealthState
from gzhu_cup2.agent.long_memory.consent import apply_intents, detect_privacy_intents


def receive_input(state: MentalHealthState) -> dict:
    """取最后一条用户消息,清洗后存入 process_input;并执行记忆隐私意愿。"""
    last_message = (state["messages"][-1]).content
    cleaned_input = last_message.strip()
    update = {"process_input": cleaned_input}

    intents = detect_privacy_intents(cleaned_input)
    if intents:
        update["privacy_intents"] = list(intents)
        update["privacy_outcome"] = apply_intents(current_user(state), intents)
    return update
