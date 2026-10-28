# -*- coding: utf-8 -*-
"""长时间间隔会切断 pending，避免跨天日期锚点串线。"""


def test_capture_turn_splits_after_session_gap(monkeypatch):
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store

    monkeypatch.setattr(cfg, "MEMORY_CAPTURE_SESSION_GAP_SECONDS", 10)
    clock = {"now": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: clock["now"])
    store.capture_turn("gap-user", "thread", "昨天说的事", "答复")
    clock["now"] = 2000.0
    store.capture_turn("gap-user", "thread", "今天说的事", "答复")

    current = store.get_item(store.ns_pending("gap-user"), "segment")
    ready = store.list_items(store.ns_pending_ready("gap-user"))
    assert [t["user"] for t in current["turns"]] == ["今天说的事"]
    assert len(ready) == 1
    assert [t["user"] for t in ready[0].value["turns"]] == ["昨天说的事"]

    # get_pending 优先返回旧段，供 worker 先固化；新段不会被覆盖。
    pending = store.get_pending("gap-user")
    assert pending["_ready_key"] == ready[0].key


def test_commit_ready_segment_keeps_new_current_segment(monkeypatch):
    """固化「切出去」的旧段时，不得顺手清掉间隙后新开的当前段。

    gap 切段让两段并存：旧段在 ``pending_ready`` 里等固化，新段在 ``pending``
    里继续累积。若 ``commit_segment`` 照旧走"推进 pending"那条路径，用户正在
    说的这一段会被一起删掉 —— 表现为"刚才聊的几句没进记忆"，而且没有任何报错。
    """
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.core.schemas import MemoryExtraction
    from gzhu_cup2.agent.long_memory import store

    monkeypatch.setattr(cfg, "MEMORY_CAPTURE_SESSION_GAP_SECONDS", 10)
    clock = {"now": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: clock["now"])
    store.capture_turn("gap-user-2", "thread", "昨天说的事", "答复")
    clock["now"] = 2000.0
    store.capture_turn("gap-user-2", "thread", "今天说的事", "答复")

    assert len(store.list_items(store.ns_pending_ready("gap-user-2"))) == 1
    store.commit_segment(
        "gap-user-2", store.get_pending("gap-user-2"),
        MemoryExtraction(digest="昨天那一小段", importance=0.5))

    assert store.list_items(store.ns_pending_ready("gap-user-2")) == [], "旧段应已被消费"
    cur = store.get_item(store.ns_pending("gap-user-2"), "segment")
    assert cur is not None, "当前段被误删 → 间隙之后的对话凭空消失"
    assert [t["user"] for t in cur["turns"]] == ["今天说的事"]
