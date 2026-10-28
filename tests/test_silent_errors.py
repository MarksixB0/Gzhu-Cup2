# -*- coding: utf-8 -*-
"""「静默错误」回归测试:把"悄悄出错/悄悄丢数据"的路径逐条钉死。

为什么需要这个文件
------------------
项目里有一类 bug 的共同特征是**不报错、但结果变错或数据变少**。它们在
正常开发中极难被发现(测试全绿、日志干净),却会持续污染判定:

  1. 固化抽取全部失败时返回"空对象" → 空摘要入库 + pending 被删 + 退避被清
     (**用户原话被静默丢弃**);
  2. ``merge_fact`` 保留 ``archived=True`` 却丢掉 ``archived_at`` →
     事实既不可见又永远无法被物理清除;
  3. ``capture_turn`` 是对话链路上唯一无保护的写 → DB 被锁时四处 502
     (**回复已生成却报错**);
  4. ``list_items`` / ``list_memory_users`` 触顶后静默截断 → 检索/遗忘/统计
     悄悄漏掉旧数据与更多用户。

本文件每一条都对应上面一项;改动相关代码时若把行为改回去,这里应当失败。
"""
import logging

import pytest

from gzhu_cup2.agent.core.schemas import MemoryExtraction
from gzhu_cup2.agent.long_memory import consolidate as cons
from gzhu_cup2.agent.long_memory import store as mstore

USER = "u-silent"


# ==================== 1. 固化全失败:必须抛异常,不得丢 pending ====================
class TestConsolidationFailure:
    def test_all_samples_failed_raises(self):
        """全部采样失败 → 抛 ConsolidationFailed(而非返回空对象)。

        这是"用户原话被静默丢弃"的根因:空对象不是 None,
        下游的 None 守卫拦不住它,于是照样落库 + 推进 pending。
        """
        class Boom:
            def with_structured_output(self, cls, **kwargs):
                return self

            def invoke(self, messages):
                raise RuntimeError("LLM 不可用")

        pending = {"turns": [{"user": "我今天很难受", "ai": "我在听"}]}
        with pytest.raises(cons.ConsolidationFailed):
            cons.extract_memory_voted(pending, llm=Boom(), samples=2)

    def test_single_sample_mode_also_propagates(self):
        """samples=1 时走 extract_memory,异常同样向上传播(worker 会退避)。"""
        class Boom:
            def with_structured_output(self, cls, **kwargs):
                return self

            def invoke(self, messages):
                raise RuntimeError("LLM 不可用")

        pending = {"turns": [{"user": "我今天很难受", "ai": "我在听"}]}
        with pytest.raises(Exception):
            cons.extract_memory_voted(pending, llm=Boom(), samples=1)

    def test_partial_failure_still_merges(self):
        """部分成功仍应正常合并(不能因为要求健壮就变得过度敏感)。"""
        calls = {"n": 0}

        class Flaky:
            def with_structured_output(self, cls, **kwargs):
                return self

            def invoke(self, messages):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("第一次失败")
                return MemoryExtraction(digest="用户今天很难受")

        pending = {"turns": [{"user": "我今天很难受", "ai": "我在听"}]}
        out = cons.extract_memory_voted(pending, llm=Flaky(), samples=2)
        assert out is not None and out.digest


# ==================== 2. 空摘要不得落库 ====================
class TestEmptyDigestRejected:
    def test_commit_node_skips_empty_digest(self):
        """memory_graph 的提交节点必须拒绝空摘要(第二层防线)。

        即使上游没抛异常(例如单采样路径返回空对象),也绝不能:
        写一条空 digest 的垃圾 episode + 推进删除 pending。
        """
        from gzhu_cup2.agent.graphs import memory_graph as mg

        pending = {"turns": [{"user": "用户说压力很大", "ai": "我在"}],
                   "next_seq": 1, "started_at": 1.0}
        before = mstore.get_pending(USER)

        out = mg._commit_node({
            "user_id": USER,
            "pending": pending,
            "extraction": MemoryExtraction(digest="   "),   # 只有空白
        })
        assert out == {}
        # 不得产生任何 episode
        eps = [it for it in mstore.list_items(mstore.ns_episodes(USER))]
        assert eps == []

    def test_commit_node_accepts_real_digest(self):
        """正常摘要必须照常落库(别把防线做成拦路虎)。"""
        from gzhu_cup2.agent.graphs import memory_graph as mg

        pending = {"turns": [{"user": "用户说压力很大", "ai": "我在"}],
                   "next_seq": 1, "started_at": 1.0}
        mg._commit_node({
            "user_id": USER,
            "pending": pending,
            "extraction": MemoryExtraction(digest="用户提到最近压力很大。"),
        })
        eps = [it.value for it in mstore.list_items(mstore.ns_episodes(USER))]
        assert any("压力" in str(e.get("digest") or "") for e in eps)


# ==================== 3. 重新被提及的事实必须解除归档 ====================
class TestArchivedFactRevival:
    def _archive(self, text: str) -> None:
        """把一条事实手工置为已归档(模拟遗忘层做过的事)。"""
        ns = mstore.ns_facts(USER)
        key = mstore._canonical(text)
        v = dict(mstore.get_item(ns, key) or {})
        assert v, "事实应先存在"
        v["archived"] = True
        v["archived_at"] = 1000.0        # 一个很早的归档时刻
        mstore.put_item(ns, key, v)

    def test_remention_unarchives_and_clears_archived_at(self):
        """再次提及 → 归档被解除,且 archived_at 被清掉。

        旧实现保留 archived=True 却丢掉 archived_at,后果是:
          · 这条事实对检索永远不可见(即使用户又提到了);
          · forget 用 `archived_at or now` 兜底 → 宽限期每次重置 → **永远删不掉**。
        """
        mstore.merge_fact(USER, "用户养了一只叫年糕的猫。", "life", 0.8)
        self._archive("用户养了一只叫年糕的猫。")

        # 用户又提到同一件事
        mstore.merge_fact(USER, "用户养了一只叫年糕的猫。", "life", 0.8)

        ns = mstore.ns_facts(USER)
        v = mstore.get_item(ns, mstore._canonical("用户养了一只叫年糕的猫。"))
        assert v is not None
        assert v["archived"] is False, "重新提及应当解除归档"
        assert v.get("archived_at") in (None,), "archived_at 应被清空"
        assert int(v.get("mentions", 0)) >= 2


# ==================== 5. 捕获失败不得影响对话 ====================
class TestCaptureFailureContained:
    def test_capture_turn_failure_does_not_raise(self, monkeypatch):
        """capture_turn 抛异常时,节点必须吞掉并返回 {},而不是让请求 502。

        背景:捕获在回复生成**之后**执行,一旦抛出,/api/chat 会 502,
        而回复其实已经生成 —— 用户看到报错、这一轮记忆也丢了。
        """
        from gzhu_cup2.agent.nodes import capture_memory as cm

        def boom(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(cm.mem_store, "capture_turn", boom)
        out = cm.capture_memory({
            "user_id": USER,
            "process_input": "今天有点累",
            "messages": [],
        })
        assert out == {}
        assert cm.mem_store.capture_turn is boom   # 未被换成其它实现


# ==================== 6. 静默截断必须变可见 ====================
class TestTruncationVisibility:
    def test_list_items_warns_when_limit_reached(self, caplog):
        """条目数触达 limit 时告警一次(把"静默截断"变成"已知边界")。"""
        ns = mstore.ns_facts(USER)
        for i in range(5):
            mstore.merge_fact(USER, "事实编号 %d 号。" % i, "other", 0.5)
        mstore._TRUNCATION_WARNED.discard(".".join(str(p) for p in ns))
        with caplog.at_level(logging.WARNING, logger="gzhu_cup2.agent.long_memory.store"):
            mstore.list_items(ns, limit=5)
        assert any("取满" in r.message or "limit" in r.message for r in caplog.records)

    def test_no_warning_when_under_limit(self, caplog):
        """未触顶时不得刷日志。"""
        ns = mstore.ns_facts(USER)
        mstore.merge_fact(USER, "唯一的一条事实。", "other", 0.5)
        mstore._TRUNCATION_WARNED.discard(".".join(str(p) for p in ns))
        with caplog.at_level(logging.WARNING, logger="gzhu_cup2.agent.long_memory.store"):
            mstore.list_items(ns, limit=1000)
        assert not any("取满" in r.message for r in caplog.records)


# ==================== 7. 风险标记:内部留痕,不接外部 ====================
class TestRiskMarking:
    def test_marking_records_state_and_history(self):
        """标记落库,并在档位变化时追加历史。"""
        from gzhu_cup2.agent.long_memory import risk_state as mrisk

        assert mrisk.read(USER) is None
        st = mrisk.update(USER, now=1000.0, reason="test")
        assert st is not None and st["tier"] in ("stable", "watch", "elevated")
        assert st["since"] == 1000.0
        hist = mrisk.read_history(USER)
        assert len(hist) == 1 and hist[0]["from"] is None

        # 同一档位重复刷新不应产生新历史(只记跃迁点)
        mrisk.update(USER, now=2000.0, reason="test")
        assert len(mrisk.read_history(USER)) == 1

    def test_marking_imports_no_external_channel(self):
        """标记模块不得**引入**任何对外通道 —— 边界用 AST 检查,而不是搜字符串
        (文档里会说明"不推送webhook",那是声明,不是实现)。"""
        import ast
        import inspect

        from gzhu_cup2.agent.long_memory import risk_state as mrisk

        tree = ast.parse(inspect.getsource(mrisk))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "urllib2", "http", "httpx",
                     "smtplib", "socket", "aiohttp", "websocket", "subprocess"}
        assert not (imported & forbidden), \
            "风险标记层不得引入对外通道,发现: %s" % (imported & forbidden)

    def test_marking_layer_only_used_for_internal_tone(self):
        """标记目前只被"内部留痕 + 关怀语气"消费,没有对外出口。

        锁住这一点:若将来有人给 guard_tier / risk_state 接上外部通知,
        本用例应失败并强制其先补授权与数据治理。
        """
        import inspect

        from gzhu_cup2.agent.care import coordinator as ccoord
        from gzhu_cup2.agent.algo import skeleton as guard_mod

        # guard 层不引入任何对外通道
        for mod in (ccoord, guard_mod):
            src = inspect.getsource(mod)
            assert "webhook" not in src
            assert "requests." not in src
