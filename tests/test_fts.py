# -*- coding: utf-8 -*-
"""SQLite FTS5 本地记忆索引的最小契约测试。"""
import os


def test_fts_search_is_local_and_user_scoped(monkeypatch, workdir):
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import fts, store

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    store.put_item(store.ns_facts("alice"), "fact-1", {
        "type": "fact", "text": "最近考试压力很大", "last_seen": 1,
    })
    store.put_item(store.ns_facts("bob"), "fact-2", {
        "type": "fact", "text": "最近考试压力很大", "last_seen": 1,
    })

    hits = fts.search("alice", "考试压力")
    assert [h["key"] for h in hits] == ["fact-1"]
    assert fts.search("alice", "不存在的内容") == []
    # 聊天读取侧会先用 FTS 缩小候选，再继续走原有安全排序/预算。
    from gzhu_cup2.agent.long_memory import retrieve
    context = retrieve.build_memory_context("alice", query="考试压力")
    assert "最近考试压力很大" in context
    store.reset_store()


def test_fts_narrowing_is_observable(monkeypatch, workdir):
    """初筛必须留下"前/后各多少候选"的痕迹 —— 否则召回损失对排障不可见。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    # 强制打开初筛:默认门槛 50 会让 3 条候选走"小库不筛"
    monkeypatch.setattr(cfg, "MEMORY_FTS_MIN_CANDIDATES", 1)
    monkeypatch.setattr(cfg, "MEMORY_RETRIEVE_MAX_FACTS", 1)
    store.reset_store()
    for key, text in (
        ("fact-1", "考研准备得差不多了"),
        ("fact-2", "答辩定在六月"),
        ("fact-3", "最近在图书馆自习"),
    ):
        store.put_item(store.ns_facts("alice"), key, {
            "type": "fact", "text": text, "last_seen": 1,
        })

    sel = retrieve.select_context("alice", query="考研准备")
    stats = sel["fts"]
    assert stats["engaged"] is True
    assert stats["applied"] is True
    assert stats["narrowed"] is True
    assert stats["fallback"] is False
    facts = stats["candidates"]["facts"]
    assert facts["raw"] == 3
    assert facts["eligible"] == 3
    assert facts["filtered"] is True
    assert facts["fallback"] is False
    # 命中集 ∪ top-1 仍少于全集,才叫"真的收窄"
    assert facts["after_fts"] < facts["eligible"]
    assert facts["kept"] == facts["after_fts"]
    store.reset_store()


def test_small_library_skips_fts_filter(monkeypatch, workdir):
    """P0:候选不超过门槛时不筛 —— 小库必须保持旧的相关性排序,不许 4→1。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    for key, text in (
        ("fact-1", "我下个月要考研，压力有点大"),
        ("fact-2", "答辩定在六月，还没开始准备"),
        ("fact-3", "考研准备得差不多了，但还是很慌"),
    ):
        store.put_item(store.ns_facts("alice"), key, {
            "type": "fact", "text": text, "last_seen": 1,
        })

    sel = retrieve.select_context("alice", query="我考研准备得怎么样了")
    stats = sel["fts"]
    assert stats["engaged"] is True          # FTS 确实命中了
    assert stats["applied"] is False         # 但小库不筛
    assert stats["narrowed"] is False
    assert stats["candidates"]["facts"] == {
        "raw": 3, "eligible": 3, "after_fts": 3, "kept": 3,
        "beyond_window": 0, "filtered": False, "fallback": False,
    }
    # 「答辩」必须还在候选里(这正是硬过滤会砍掉的那条)
    assert len(sel["facts"]) == 3
    assert any("答辩" in str(v.get("text")) for v in sel["facts"])
    store.reset_store()


def test_union_keeps_fts_hit_outside_top_n(monkeypatch, workdir):
    """P0:FTS 命中即使不在 memory_score top-N,也必须被并集捞回来。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    import time
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    now = time.time()
    ns = store.ns_facts("alice")
    for i in range(59):
        store.put_item(ns, "fact-%02d" % i, {
            "type": "fact", "text": "普通记忆 %d" % i,
            "last_seen": now, "importance": 0.9,
        })
    hit_text = "考研准备得怎么样了"
    store.put_item(ns, "fact-hit", {
        "type": "fact", "text": hit_text,
        "last_seen": now - 400 * 86400.0, "importance": 0.01,   # 分数远低于 top-40
    })

    sel = retrieve.select_context("alice", query="考研准备")
    facts = sel["fts"]["candidates"]["facts"]
    assert facts["raw"] == 60
    assert facts["eligible"] == 60
    assert facts["filtered"] is True
    # 1 条命中 + 40 条 top-N;命中不在 top-40,并集应补回它
    assert facts["kept"] == 41
    assert sel["fts"]["narrowed"] is True
    assert any(e["text"] == hit_text for e in sel["explain"])
    store.reset_store()


def test_debug_view_does_not_apply_fts_filter(monkeypatch, workdir):
    """debug 是排障视图:必须看全量,不能被 FTS 初筛裁掉候选。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    import time
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    now = time.time()
    ns = store.ns_facts("alice")
    for i in range(60):
        store.put_item(ns, "fact-%02d" % i, {
            "type": "fact", "text": "普通记忆 %d" % i,
            "last_seen": now, "importance": 0.9,
        })
    store.put_item(ns, "fact-hit", {
        "type": "fact", "text": "考研准备得怎么样了",
        "last_seen": now, "importance": 0.9,
    })

    sel = retrieve.select_context("alice", query="考研准备", purpose="debug")
    assert sel["fts"]["engaged"] is True
    assert sel["fts"]["applied"] is False
    assert sel["fts"]["candidates"]["facts"]["kept"] == 61
    store.reset_store()


def test_topic_terms_expand_fts_recall(monkeypatch, workdir):
    """问"考研"时,同主题的"答辩"必须能通过主题词扩展被召回。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import fts, store

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    ns = store.ns_facts("alice")
    store.put_item(ns, "fact-1", {"type": "fact", "text": "答辩定在六月", "last_seen": 1})
    store.put_item(ns, "fact-2", {"type": "fact", "text": "考研准备得差不多了", "last_seen": 1})

    hits = fts.search("alice", "考研", extra_terms=["答辩"])
    assert {h["text"] for h in hits} == {"答辩定在六月", "考研准备得差不多了"}
    store.reset_store()


def test_topic_expansion_keeps_synonym_fact(monkeypatch, workdir):
    """端到端:主题扩展后的 FTS 命中集必须把同主题近义事实带进候选。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    import time
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    # 强制启用初筛,且 top-N 只留 1 条:若没有主题扩展,答辩会被并集漏掉
    monkeypatch.setattr(cfg, "MEMORY_FTS_MIN_CANDIDATES", 1)
    monkeypatch.setattr(cfg, "MEMORY_RETRIEVE_MAX_FACTS", 1)
    store.reset_store()
    now = time.time()
    ns = store.ns_facts("alice")
    store.put_item(ns, "fact-1", {
        "type": "fact", "text": "我下个月要考研，压力有点大",
        "last_seen": now, "importance": 0.9,
    })
    store.put_item(ns, "fact-2", {
        "type": "fact", "text": "考研准备得差不多了",
        "last_seen": now, "importance": 0.9,
    })
    store.put_item(ns, "fact-3", {
        "type": "fact", "text": "答辩定在六月",
        "last_seen": now - 400 * 86400.0, "importance": 0.01,
    })

    sel = retrieve.select_context("alice", query="考研")
    assert sel["fts"]["applied"] is True
    assert sel["fts"]["candidates"]["facts"]["kept"] == 3
    assert any(e["text"] == "答辩定在六月" for e in sel["explain"])
    store.reset_store()


def test_fts_hit_outside_list_window_is_fetched_by_key(monkeypatch, workdir):
    """P2:FTS 命中落在 list_items 的 1000 条窗口之外时,必须按 key 直取回来。"""
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    import time
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import fts, store, retrieve

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_MIN_CANDIDATES", 1)
    monkeypatch.setattr(cfg, "MEMORY_RETRIEVE_MAX_FACTS", 1)
    store.reset_store()
    now = time.time()
    ns = store.ns_facts("alice")
    store.put_item(ns, "fact-1", {
        "type": "fact", "text": "我下个月要考研，压力有点大",
        "last_seen": now, "importance": 0.9,
    })
    store.put_item(ns, "fact-2", {
        "type": "fact", "text": "考研准备得差不多了",
        "last_seen": now, "importance": 0.9,
    })
    store.put_item(ns, "fact-3", {
        "type": "fact", "text": "答辩定在六月",
        "last_seen": now - 400 * 86400.0, "importance": 0.01,
    })
    fts.search("alice", "warmup")          # 先建好索引(此后 list_items 被裁窗口)

    real_list = store.list_items
    def _window(namespace, limit=1000):
        return [it for it in real_list(namespace, limit=limit) if it.key != "fact-3"]
    monkeypatch.setattr(store, "list_items", _window)

    sel = retrieve.select_context("alice", query="考研")
    facts = sel["fts"]["candidates"]["facts"]
    assert facts["eligible"] == 2           # 窗口里只剩两条
    assert facts["kept"] == 3               # 第三条靠 get_item 直取回来
    assert facts["beyond_window"] == 1
    assert any(e["text"] == "答辩定在六月" for e in sel["explain"])
    store.reset_store()


def test_fts_failure_does_not_block_memory(monkeypatch, workdir):
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import fts, store

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "missing", "index.sqlite3"))
    store.reset_store()
    original = fts._connect
    monkeypatch.setattr(fts, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("broken index")))
    store.put_item(store.ns_facts("alice"), "fact-1", {
        "type": "fact", "text": "记忆仍应保存", "last_seen": 1,
    })
    assert store.get_item(store.ns_facts("alice"), "fact-1")["text"] == "记忆仍应保存"
    monkeypatch.setattr(fts, "_connect", original)
    store.reset_store()


def test_reset_does_not_disable_future_indexing(monkeypatch, workdir):
    """``reset()`` 之后必须还能惰性重建索引。

    回归：``reset()`` 早先只删文件、不清 ``_READY``（进程内"已补建过"标记），
    于是同一进程里之后所有检索都认为索引仍就绪、不再 rebuild —— 索引恒空，
    检索静默退化成"永远无 FTS 命中"，而主记忆明明完好。
    """
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "sqlite")
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.long_memory import fts, store

    monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(workdir, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "MEMORY_FTS_DB_PATH", os.path.join(workdir, "memory_fts.sqlite3"))
    store.reset_store()
    store.put_item(store.ns_facts("alice"), "fact-1", {
        "type": "fact", "text": "最近考试压力很大", "last_seen": 1,
    })

    assert [h["key"] for h in fts.search("alice", "考试压力")] == ["fact-1"], "首次搜索应惰性补建"
    fts.reset()  # 删派生索引;主记忆不受影响
    assert [h["key"] for h in fts.search("alice", "考试压力")] == ["fact-1"], "reset 后应能重建"
    store.reset_store()
