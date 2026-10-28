# -*- coding: utf-8 -*-
"""care 内容层 LLM 生成入口的聚焦离线测试(不触网)。

覆盖:generate_message 各 kind 契约、禁词拒绝、异常/空输出、warm 无事实、
mark_blocked 冷却语义、以及 care_prompts 提示词构造无禁词泄漏。
LLM 生成统一用 monkeypatch 注入假生成器(ccontent._llm_generate),自动还原。
"""
import os

import pytest

from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt

SAFE = "团团一直陪着你，最近过得怎么样呀？"


@pytest.fixture
def llm(monkeypatch):
    """注入返回固定安全文案的假生成器;用例结束自动还原为 None。"""
    monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: SAFE)
    return SAFE


@pytest.fixture
def state_file(workdir):
    """把关怀运行时状态指到工作区临时目录里的文件并清空。

    注意:care.runtime 已改用 common.json_state.JsonStateFile 单例,
    通过 set_state_path() 重定向(不再是模块级 _STATE_PATH)。

    临时目录夹具 ``workdir`` 由 tests/conftest.py 统一提供 —— 此前本文件自己写了
    一个"在 test/ 下放固定文件名"的版本,与另两处几乎相同(同一件事多份实现),
    而且崩溃时会往仓库里留下文件。
    """
    path = os.path.join(workdir, "care_llm_runtime_test.json")
    cruntime.set_state_path(path)
    cruntime.reset()
    yield path
    cruntime.set_state_path(None)  # 还原为 config 默认路径


# ============================ generate_message:happy path ============================
class TestGenerateHappyPath:
    @pytest.mark.parametrize("kind,topic", [
        ("checkin", None),
        ("recall", "用户喜欢打篮球"),
        ("soothe", None),
        ("petlife", "晒了一下午太阳"),
    ])
    def test_returns_injected_text(self, llm, kind, topic):
        assert ccontent.generate_message(kind, topic=topic) == SAFE

    def test_probe_returns_injected_text(self, llm):
        assert ccontent.generate_message("probe", topic="phq_sleep") == SAFE

    def test_warm_returns_injected_text(self, llm):
        assert ccontent.generate_message("warm", warm_facts={"mood_down": True}) == SAFE


# ============================ generate_message:禁词拒绝 ============================
class TestForbiddenRejection:
    @pytest.mark.parametrize("kind,kwargs", [
        ("checkin", {}),
        ("recall", {"topic": "用户喜欢打篮球"}),
        ("soothe", {}),
        ("probe", {"topic": "phq_sleep"}),
        ("petlife", {"topic": "晒了一下午太阳"}),
        ("warm", {"warm_facts": {"mood_down": True}}),
    ])
    def test_forbidden_token_returns_none(self, monkeypatch, kind, kwargs):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "你的风险等级是 high")
        assert ccontent.generate_message(kind, **kwargs) is None


# ============================ generate_message:生成失败 ============================
class TestGeneratorFailure:
    def test_exception_returns_none(self, monkeypatch):
        def _boom(messages):
            raise RuntimeError("llm down")

        monkeypatch.setattr(ccontent, "_llm_generate", _boom)
        assert ccontent.generate_message("checkin") is None

    def test_empty_output_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "")
        assert ccontent.generate_message("checkin") is None

    def test_none_output_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: None)
        assert ccontent.generate_message("checkin") is None


# ============================ warm:无观察事实 ============================
class TestWarmNoFacts:
    def test_warm_without_facts_returns_none(self, llm):
        assert ccontent.generate_message("warm") is None
        assert ccontent.generate_message("warm", warm_facts={}) is None
        assert ccontent.generate_message("warm", warm_facts={"mood_down": False}) is None
        assert ccontent.generate_message("warm", warm_facts={"quieter": False}) is None


# ============================ mark_blocked:冷却语义 ============================
class TestMarkBlocked:
    def test_advances_cooldown_without_sending(self, state_file):
        user = "u-blocked"
        c0 = cruntime.counters(user)
        assert c0["date_count"] == 0 and c0["last_proactive_ts"] is None
        assert cruntime.pending(user) == []

        now = 1_700_000_000.0
        cruntime.mark_blocked(user, "probe", now=now)

        c1 = cruntime.counters(user)
        assert c1["date_count"] == 0                 # 不占当日额度
        assert c1["last_proactive_ts"] == now        # 冷却推进到当前时刻
        assert cruntime.pending(user) == []          # 不入 outbox

    def test_mark_blocked_leaves_dims_untouched(self, state_file):
        user = "u-blocked2"
        cruntime.mark_blocked(user, "probe", now=1_700_000_000.0)
        assert cruntime.dims_map(user) == {}
        assert cruntime.pending(user) == []


# ============================ 提示词构造:无禁词泄漏 ============================
class TestPromptConstruction:
    FORBIDDEN = ("PHQ", "GAD", "C-SSRS", "风险", "等级", "量表", "分数", "危急",
                 "热线", "检测", "评估结果", "抑郁", "焦虑")

    def _contexts(self):
        return {
            "checkin": {},
            "recall": {"topic": "用户上次说喜欢打篮球"},
            "petlife": {"topic": "晒了一下午太阳"},
            "soothe": {},
            "probe": {"topic": "最近睡得还好吗？"},
            "warm": {"mood_down": True, "quieter": True},
        }

    def test_returns_system_and_human(self):
        from langchain_core.messages import SystemMessage, HumanMessage
        for kind, ctx in self._contexts().items():
            msgs = build_care_llm_prompt(kind, context=ctx)
            assert len(msgs) == 2
            assert isinstance(msgs[0], SystemMessage)
            assert isinstance(msgs[1], HumanMessage)

    def test_human_free_of_forbidden_tokens(self):
        for kind, ctx in self._contexts().items():
            msgs = build_care_llm_prompt(kind, context=ctx)
            human = msgs[1].content
            for token in self.FORBIDDEN:
                assert token not in human, f"{kind} 提示词泄漏禁词 {token}: {human}"
