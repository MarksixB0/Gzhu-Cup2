# -*- coding: utf-8 -*-
"""图内用户键解析:节点取"这段记忆算谁的"的唯一出口。

只处理图内部 state 里已经解析好的 user_id,不做校验、不碰请求头
(请求头解析与合法性校验在 ``web.sessions.resolve_user``)。

取不到 user_id 时回落 ``cfg.MEMORY_USER_KEY``;若某处不应回落,
请在调用处显式拒绝。
"""
from gzhu_cup2.agent.config import config as cfg


def current_user(state) -> str:
    """取本轮用户键:优先 state 中已解析的 ``user_id``,否则回落 config 默认。

    对 ``state`` 为 None / 缺键 / 空串都容错 —— 这三个场景在各节点都真实出现过。
    """
    raw = ""
    if state:
        try:
            raw = str(state.get("user_id") or "")
        except AttributeError:   # 传进来的不是 mapping(容错,不抛)
            raw = ""
    return raw.strip() or str(cfg.MEMORY_USER_KEY)
