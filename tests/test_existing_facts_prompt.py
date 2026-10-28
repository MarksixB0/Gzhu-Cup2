# -*- coding: utf-8 -*-
"""抽取端「已记下的事」清单测试(确定性;不触网)。

为什么要这份测试
----------------
近义重复(阿杰 ×2 / 师大 ×2)的根因**不是合并阈值** —— 实测两条例的二元组
Jaccard 只有 0.278 / 0.500,把阈值降到能判出来的水平就会误合并(不可逆的事实丢失)。
真正的缺口是:抽取提示词看不到库里已有什么,模型每段只能"新起一条"。
这里钉死三件事:
  1. 给了清单 → 提示词里确实出现清单,且带上"沿用原句"的规则(2c);
  2. 没给/开关关 → 提示词结构与老版本一致(不含清单区块);
  3. 清单本身的选取口径:只取未归档、按 mentions 热度排序、限条数、截断。
"""
import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import consolidate as cons
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.prompts.long_memory_prompts import build_memory_extraction_prompt

USER = "u-existing"

_HEADER = "已记下的事"


def _system_and_human(messages):
    return messages[0].content, messages[1].content


# ============================ 提示词注入 ============================
class TestPromptInjection:
    def test_existing_facts_rendered_as_list(self):
        msgs = build_memory_extraction_prompt(
            "用户：我室友阿杰又在打游戏",
            anchor_date_text="2026年10月19日 星期一",
            existing_facts=["用户有一个室友叫阿杰", "用户养了一只叫年糕的橘猫"])

        system, human = _system_and_human(msgs)
        assert _HEADER in human
        assert "- 用户有一个室友叫阿杰" in human
        assert "- 用户养了一只叫年糕的橘猫" in human
        # 清单必须出现在对话记录之前,模型先读参照物再读本段
        assert human.index(_HEADER) < human.index("---- 对话记录 ----")

    def test_reuse_original_wording_rule_is_present(self):
        """规则 2c 是这份改动的核心指令 —— 缺了它前缀判据就没有命中面。"""
        system, _ = _system_and_human(
            build_memory_extraction_prompt("x", existing_facts=["用户有一个室友叫阿杰"]))
        assert "2c." in system
        assert "原句开头、一字不改" in system

    def test_no_block_when_list_empty(self):
        _, human = _system_and_human(build_memory_extraction_prompt("x"))
        assert _HEADER not in human

        _, human_empty = _system_and_human(
            build_memory_extraction_prompt("x", existing_facts=[]))
        assert _HEADER not in human_empty

    def test_blank_entries_are_ignored(self):
        _, human = _system_and_human(
            build_memory_extraction_prompt("x", existing_facts=["  ", "", None]))
        assert _HEADER not in human


# ============================ 清单选取口径 ============================
class TestExistingFactsBrief:
    def test_no_user_means_no_list(self):
        mem_store.merge_fact(USER, "用户养了一只叫年糕的橘猫", "life", 0.7)
        assert cons._existing_facts_brief(None) == []
        assert cons._existing_facts_brief("  ") == []

    def test_switch_off_returns_empty(self, monkeypatch):
        mem_store.merge_fact(USER, "用户养了一只叫年糕的橘猫", "life", 0.7)
        monkeypatch.setattr(cfg, "MEMORY_PROMPT_EXISTING_FACTS", False)
        assert cons._existing_facts_brief(USER) == []

    def test_archived_facts_excluded(self):
        mem_store.merge_fact(USER, "用户养了一只叫年糕的橘猫", "life", 0.7)
        key = list(mem_store.list_items(mem_store.ns_facts(USER)))[0].key
        mem_store.put_item(mem_store.ns_facts(USER), key,
                           {"type": "fact", "text": "用户养了一只叫年糕的橘猫",
                            "category": "life", "importance": 0.7, "mentions": 1,
                            "archived": True, "archived_at": 1.0})

        assert cons._existing_facts_brief(USER) == []

    def test_sorted_by_mentions_and_capped(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_PROMPT_EXISTING_FACTS_MAX", 2)
        # 低热度
        mem_store.merge_fact(USER, "用户吃火锅喜欢辣的", "life", 0.9)
        # 高热度:重复三次
        for _ in range(3):
            mem_store.merge_fact(USER, "用户养了一只叫年糕的橘猫", "life", 0.5)
        mem_store.merge_fact(USER, "用户叫小宇", "life", 0.4)

        brief = cons._existing_facts_brief(USER)
        assert len(brief) == 2
        assert brief[0] == "用户养了一只叫年糕的橘猫"     # mentions 最高者排第一

    def test_each_entry_truncated(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_PROMPT_EXISTING_FACT_CHARS", 8)
        mem_store.merge_fact(USER, "用户最近压力很大晚上翻来覆去睡不着", "health", 0.8)

        brief = cons._existing_facts_brief(USER)
        assert brief and len(brief[0]) == 8

    def test_read_failure_degrades_to_empty_list(self, monkeypatch):
        """读清单失败只能降级,不能让整段固化失败(否则 pending 一直退避)。"""
        def _boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(mem_store, "list_items", _boom)

        assert cons._existing_facts_brief(USER) == []


# ============================ 端到端:抽取确实收到清单 ============================
class _CaptureLLM:
    """假 LLM:只记录收到的 messages,返回固定结构化结果。"""

    def __init__(self):
        self.messages = None

    def with_structured_output(self, schema, **kwargs):
        outer = self

        class _W:
            def invoke(self, messages):
                outer.messages = messages
                return schema(digest="用户在聊室友")
        return _W()


class TestExtractMemoryWiring:
    def test_extract_memory_passes_existing_facts(self):
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰", "relationship", 0.6)
        pending = {
            "turns": [{"seq": 0, "ts": 1.0, "thread": "t1",
                       "user": "阿杰又在打游戏", "ai": "嗯",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": 1.0, "last_ts": 1.0, "threads": ["t1"],
        }
        llm = _CaptureLLM()

        cons.extract_memory(pending, llm=llm, user=USER)

        _, human = _system_and_human(llm.messages)
        assert _HEADER in human
        assert "- 用户有一个室友叫阿杰" in human

    def test_extract_memory_without_user_skips_list(self):
        """老的调用方式(不传 user)必须照旧可用 —— 不能因为缺参就崩。"""
        pending = {
            "turns": [{"seq": 0, "ts": 1.0, "thread": "t1",
                       "user": "阿杰又在打游戏", "ai": "嗯",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": 1.0, "last_ts": 1.0, "threads": ["t1"],
        }
        llm = _CaptureLLM()

        cons.extract_memory(pending, llm=llm)

        _, human = _system_and_human(llm.messages)
        assert _HEADER not in human
