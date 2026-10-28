# -*- coding: utf-8 -*-
"""读侧「记忆占比」控制测试(不触网)。

背景:实测旧配置注入 12 条 facts + 5 条 episodes 会渲染出 ~900 字符记忆块,
而 REPLY_LENGTH_RULE 只要求 50-150 字 —— 背景是目标回复的 6 倍,回复被记忆主导。
本文件钉死三处配合的收敛措施:
  1. 配额(6 facts / 3 episodes);
  2. 内部标记([类别])不进 reply/care;
  3. 系统提示词不再鼓励复述记忆 + 记忆块降到历史之后。
"""
import time
import types
from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory.retrieve import build_memory_context
from gzhu_cup2.agent.prompts.prompts import build_generate_response_prompt

USER = "u-budget"


@pytest.fixture
def cli():
    from gzhu_cup2.agent.tools import memory_dev
    return memory_dev

# 八条彼此不近义的事实(避免被近义合并吃掉,便于验证配额)
FACTS = [
    "用户养了一只猫", "用户喜欢跑步", "用户住在北京", "用户在读大学",
    "用户有个妹妹", "用户喜欢喝茶", "用户最近在学吉他", "用户周末常去爬山",
]
EPISODES = ["摘要甲", "摘要乙", "摘要丙", "摘要丁", "摘要戊"]


def _put_facts(texts=FACTS):
    for t in texts:
        mem_store.merge_fact(USER, t, "life", 0.5)


def _put_episodes(digests=EPISODES):
    now = time.time()
    for i, d in enumerate(digests):
        mem_store.put_item(mem_store.ns_episodes(USER), f"ep{i}", {
            "type": "episode", "digest": d, "importance": 0.5,
            "ts_start": now, "ts_end": now, "turn_count": 1, "threads": ["t"],
            "first_seen": now, "last_seen": now, "archived": False,
            "category": "episode", "protected": False,
        })


# ============================ 预算(字符闸门) ============================
# ⚠ 2026-09-13 口径变更(任务 C):闸门从「条数」改为「**字符预算**」。
#   旧契约钉住 facts ≤6 / episodes ≤3;实测该口径让"库里已有的答案"进不了提示词
#   (事实层 16 条取 6 条 = 可达率 37.5%,检索覆盖 37.5%),而"检索到即命中 100%"
#   说明瓶颈完全在读侧配额。新契约:字符预算为主闸门,条数降级为安全阀。
class TestReplyBudget:
    def test_facts_fill_the_character_budget_not_a_fixed_count(self):
        """8 条短事实应**全部**进提示词(旧口径只放 6 条),且总量不超预算。"""
        _put_facts()
        ctx = build_memory_context(USER, purpose="reply")
        n = len([t for t in FACTS if t in ctx])
        assert n == len(FACTS), "短事实应被字符预算全部容纳(不再是硬性 6 条)"
        # 容忍 +150:预算只计记忆条目行,分区标题 / 【近期状态】/ 未整理兜底都另有开销
        # (实测真实库 480 预算 → 整块 563 字符)。这条断言的作用是**防止预算失控**,
        # 而不是要求逐字符精确。
        assert len(ctx) <= cfg.MEMORY_RETRIEVE_MAX_CHARS + 150, "记忆块不得明显超预算"

    def test_long_facts_are_cut_by_budget(self):
        """预算才是真闸门:塞满长事实后必须被截断,且截断发生在预算附近。"""
        long_facts = ["用户第%d件事:%s" % (i, "很长的描述内容" * 12) for i in range(30)]
        _put_facts(long_facts)
        ctx = build_memory_context(USER, purpose="reply")
        got = [t for t in long_facts if t in ctx]
        assert 0 < len(got) < len(long_facts), "长事实必须被预算截断"
        assert len(got) < cfg.MEMORY_RETRIEVE_MAX_FACTS, "不应触到条数安全阀"

    def test_episodes_get_the_reserved_room(self):
        """事实层不得把预算吃干:会话回顾必须留出预留额度。"""
        _put_facts(["用户第%d件事" % i for i in range(40)])
        _put_episodes(EPISODES)
        ctx = build_memory_context(USER, purpose="reply")
        assert len([d for d in EPISODES if d in ctx]) >= 1, "预留额度应保证至少 1 条回顾"

    def test_safety_caps_are_generous_but_exist(self):
        """条数上限仍在,但已从"主闸门"降级为"安全阀"(防单条超长文本)。"""
        from gzhu_cup2.agent.long_memory import retrieve as mret
        sel = mret.select_context(USER, purpose="reply")
        assert len(sel["facts"]) <= cfg.MEMORY_RETRIEVE_MAX_FACTS
        assert len(sel["episodes"]) <= cfg.MEMORY_RETRIEVE_MAX_EPISODES
        assert cfg.MEMORY_RETRIEVE_MAX_FACTS > 6, "安全阀应远大于旧的 6 条闸门"
        assert cfg.MEMORY_RETRIEVE_MAX_EPISODES > 3

    def test_budget_is_the_gate_not_the_cap(self):
        """钉住新契约本身:字符预算是主闸门,且它直接对应"记忆不主导回复"。"""
        assert cfg.MEMORY_RETRIEVE_MAX_CHARS >= 300
        assert cfg.MEMORY_RETRIEVE_EPISODE_RESERVE_CHARS < cfg.MEMORY_RETRIEVE_MAX_CHARS

    def test_care_budget_is_tighter_than_reply(self):
        _put_facts(["用户第%d件事" % i for i in range(40)])
        reply = build_memory_context(USER, purpose="reply")
        care = build_memory_context(USER, purpose="care")
        assert len(care) <= len(reply), "care 用途应更省"

    def test_debug_view_is_not_business_capped(self):
        _put_facts(["用户第%d件事" % i for i in range(60)])
        _put_episodes(EPISODES)
        dbg = build_memory_context(USER, purpose="debug")
        assert len([d for d in EPISODES if d in dbg]) == len(EPISODES)
        assert len([t for t in FACTS if t in dbg]) <= 60


# ============================ 冷记忆兜底(归档可召回) ============================
class TestArchivedRecall:
    def test_archived_fact_is_recallable_when_room_left(self):
        """归档 = 提得少,不是彻底想不起来:有余量时带 1 条并单独标注。"""
        _put_facts(["用户养了一只猫"])
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), "k-old", {
            "type": "fact", "text": "用户以前住在广州", "category": "life",
            "importance": 0.8, "mentions": 1, "first_seen": now, "last_seen": now,
            "archived": True, "archived_at": now, "protected": False,
        })
        ctx = build_memory_context(USER, purpose="reply")
        assert "用户以前住在广州" in ctx
        assert "【更早的记忆" in ctx

    def test_archived_is_not_used_for_care(self):
        """主动关怀不做冷记忆召回(宁可少说不可说错)。"""
        _put_facts(["用户养了一只猫"])
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), "k-old", {
            "type": "fact", "text": "用户以前住在广州", "category": "life",
            "importance": 0.8, "mentions": 1, "first_seen": now, "last_seen": now,
            "archived": True, "archived_at": now, "protected": False,
        })
        assert "用户以前住在广州" not in build_memory_context(USER, purpose="care")


# ============================ 内部标记不外泄 ============================
class TestInternalMarkers:
    def test_reply_hides_category_tag(self):
        _put_facts(["用户养了一只猫"])
        assert "[life]" not in build_memory_context(USER, purpose="reply")

    def test_care_hides_category_tag(self):
        _put_facts(["用户养了一只猫"])
        assert "[life]" not in build_memory_context(USER, purpose="care")

    def test_debug_keeps_category_tag(self):
        _put_facts(["用户养了一只猫"])
        assert "[life]" in build_memory_context(USER, purpose="debug")


# ============================ 提示词布局 ============================
def _state(memory_context="【用户档案】\n- 用户叫小林"):
    return {
        "messages": [
            HumanMessage(content="第一句"), AIMessage(content="嗯"),
            HumanMessage(content="第二句"), AIMessage(content="在的"),
            HumanMessage(content="第三句"),
        ],
        "process_input": "我今天有点累",
        "emotional_analysis": types.SimpleNamespace(emotion="frustrated", description="疲惫"),
        "risk_level": "low",
        "memory_context": memory_context,
        "crisis_handled": False,
    }


class TestPromptLayout:
    def test_system_prompt_no_longer_encourages_reciting(self):
        system = build_generate_response_prompt(_state())[0].content
        assert "并说出具体内容" not in system      # 旧的鼓励性措辞必须消失
        assert "不要主动复述" in system

    def test_memory_block_after_history(self):
        human = build_generate_response_prompt(_state())[1].content
        assert human.index("【最近对话背景】") < human.index("【用户背景记忆")

    def test_memory_header_marks_background_only(self):
        human = build_generate_response_prompt(_state())[1].content
        assert "不要复述" in human

    def test_no_memory_means_no_block(self):
        human = build_generate_response_prompt(_state(memory_context=None))[1].content
        assert "【用户背景记忆" not in human
        assert "【最近对话背景】" in human

    def test_user_input_still_last(self):
        """收敛记忆占比不能动摇尾部锚定:用户本轮输入仍须贴近输出位。"""
        human = build_generate_response_prompt(_state())[1].content
        assert human.index("【用户本轮输入") > human.index("【用户背景记忆")


# ============================ 存量数据清理 ============================
def _put_raw(key, text, mentions=1):
    now = time.time()
    mem_store.put_item(mem_store.ns_facts(USER), key, {
        "type": "fact", "text": text, "category": "work",
        "importance": 0.9, "mentions": mentions,
        "first_seen": now, "last_seen": now,
        "archived": False, "protected": False,
    })


class TestRemergeCommand:
    A = "用户下周四参加毕业答辩，对此感到紧张和压力。"
    B = "用户下周四需参加毕业答辩，对此感到紧张和压力。"

    def test_dry_run_reports_but_does_not_write(self, cli, capsys):
        _put_raw("k1", self.A)
        _put_raw("k2", self.B)
        assert cli.main(["remerge", "--user", USER]) == 0
        out = capsys.readouterr().out
        assert "发现 1 组" in out and "预演模式" in out
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2

    def test_apply_merges_and_sums_mentions(self, cli, capsys):
        _put_raw("k1", self.A)
        _put_raw("k2", self.B)
        assert cli.main(["remerge", "--user", USER, "--apply"]) == 0
        capsys.readouterr()
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["mentions"] == 2
        assert facts[0]["text"] == self.B          # 取最长
        assert facts[0]["importance"] == 0.9

    def test_no_duplicates_is_a_noop(self, cli, capsys):
        _put_facts(["用户养了一只猫"])
        assert cli.main(["remerge", "--user", USER, "--apply"]) == 0
        assert "没有需要合并" in capsys.readouterr().out
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 1


# ============================ 历史轮次带「发生时刻」 ============================
class TestHistoryCarriesTurnTime:
    """历史轮次必须带上它**发生的时间**,否则跨天的旧话会被模型当成"刚说的"
    (day1「明天要考试」到 day2 仍被读成明天 —— 与摘要那处是同一类问题)。"""

    def test_history_lines_include_turn_time(self):
        from gzhu_cup2.agent.prompts.prompts import _fmt_history
        ts = datetime(2026, 9, 9, 21, 0).timestamp()
        out = _fmt_history([HumanMessage(content="明天我要考试",
                                        additional_kwargs={"ts": ts})])
        assert "09-09 21:00" in out
        assert "明天我要考试" in out

    def test_history_without_ts_falls_back_to_old_format(self):
        from gzhu_cup2.agent.prompts.prompts import _fmt_history
        assert _fmt_history([HumanMessage(content="你好")]) == "用户：你好"

    def test_history_bad_ts_does_not_raise(self):
        from gzhu_cup2.agent.prompts.prompts import _fmt_history
        out = _fmt_history([HumanMessage(content="你好",
                                        additional_kwargs={"ts": "not-a-number"})])
        assert "你好" in out
