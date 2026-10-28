# -*- coding: utf-8 -*-
"""「只差虚词/语序」同义判定的正负例回归(确定性;不触网)。

为什么要单开一份测试
--------------------
`test_fact_merge.py` 钉的是"字符 n-gram 双阈值"那条老通道;本文件钉的是 2026-09-14
新增的**范畴式**通道(`function_word_only_diff`)。它的判据是**差异的性质**
(虚词/语序 vs 实词取值),不是相似度大小 —— 后者已被实测否决:

  · 正例来自**两份语料**:R2(42 天语料,被包含度守卫拦下 24%)与
    v1 留出集(林知远,拦下 43.5%);
  · 负例里 `咨询师/老师` 是**留出集枪毙过的反例** —— 只差一个实词,任何阈值型放宽
    都会误合并(把两个人的评价糊成一条),必须永远拒绝。
"""
import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import similarity as sim


class TestFunctionWordOnlyDiff:
    # ---- 正例:同一件事的虚词/语序差异(应合并) ----
    @pytest.mark.parametrize("a,b", [
        # R2 语料(42 天实测里被包含度守卫 0.90 拦下的那些)
        ("用户的外婆将做手术", "用户的外婆即将做手术。"),
        ("用户有一个叫沈嘉树的弟弟", "用户有一个弟弟叫沈嘉树"),
        ("用户今天实习带的是中班", "用户实习带的是中班"),
        ("用户凌晨醒来睡不着", "用户凌晨醒来后睡不着"),
        ("用户什么都提不起劲", "用户对什么都提不起劲"),
        # v1 留出集(另一条轨迹 + 另一个模型)
        ("用户有一个叫阿哲的朋友", "用户有一位名叫阿哲的朋友"),
        ("用户正在按时吃药", "用户最近正在按时吃药"),
    ])
    def test_same_proposition_with_function_word_diff_merges(self, a, b):
        assert sim.function_word_only_diff(a, b) is True
        assert sim.is_similar(a, b) is True

    # ---- 负例:实词取值不同 = 两条不同的事实(绝不合并) ----
    @pytest.mark.parametrize("a,b", [
        ("用户爱吃辣", "用户爱吃甜"),                       # 取值不同
        ("用户养了一只猫", "用户养了一只狗"),
        ("用户住在北京", "用户住在上海"),
        ("用户的咨询师评价用户正在慢慢变好", "用户的老师评价用户正在慢慢变好"),  # 留出集反例
        ("用户今天带班", "用户今天没带班"),                 # 否定翻转
        ("用户觉得很累", "用户感觉很有精神"),
        ("用户最近心情不好", "用户最近身体不好"),
    ])
    def test_content_word_difference_never_merges(self, a, b):
        assert sim.function_word_only_diff(a, b) is False
        assert sim.is_similar(a, b) is False

    def test_negation_guard_is_explicit(self):
        """否定字一旦出现,该通道整条拒绝(不做"少一个字"式的宽容)。"""
        assert sim.function_word_only_diff("用户想出门", "用户不想出门") is False
        assert sim.function_word_only_diff("用户爱吃辣", "用户不太爱吃辣") is False

    def test_too_short_is_rejected(self):
        assert sim.function_word_only_diff("累", "很累") is False
        assert sim.function_word_only_diff("", "用户觉得累") is False

    def test_content_multiset_ignores_order(self):
        assert sim.content_multiset("用户有一个弟弟叫沈嘉树") == \
               sim.content_multiset("用户有一个叫沈嘉树的弟弟")


class TestKnownUncovered:
    """**刻意不覆盖**的换词型同义:需要同义词表或句法能力,放宽必然误合并。

    把它们写成显式用例(而不是留着让人以为"漏了"),是因为实测的两份语料里
    这类改写占比不低,一旦有人想"顺手放宽阈值",这些断言会立刻拦住。
    """

    @pytest.mark.parametrize("a,b", [
        ("用户大学加了插画社", "用户大学加入了插画社"),          # 加 / 加入
        ("用户的导师认可其选题并要求补齐实验部分",
         "用户的导师认可其选题并建议补齐实验部分。"),           # 要求 / 建议
        ("用户名为小林", "用户自称为小林"),                      # 名为 / 自称为
    ])
    def test_paraphrases_stay_unmerged(self, a, b):
        assert sim.function_word_only_diff(a, b) is False
        assert sim.is_similar(a, b) is False


class TestSwitch:
    def test_flag_off_disables_only_the_new_channel(self, monkeypatch):
        a, b = "用户的外婆将做手术", "用户的外婆即将做手术。"
        assert sim.is_similar(a, b) is True
        monkeypatch.setattr(cfg, "MEMORY_FACT_FUNCTION_WORD_MERGE", False)
        assert sim.is_similar(a, b) is False
        # 老通道不受影响
        assert sim.is_similar("用户养了一只猫", "用户养了一只猫") is True
