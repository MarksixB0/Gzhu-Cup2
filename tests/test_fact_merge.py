# -*- coding: utf-8 -*-
"""事实近义合并测试(确定性;不触网)。

覆盖三层:
  1. similarity 纯函数:normalize / jaccard / containment / 极性守卫;
  2. 「极为相近」判定:真实语料里应合并的例子 **+ 对抗样例(必须不合并)**;
  3. store 落库行为:近义合并、保护类不合并、开关可关、段内去重。

对抗样例是这份测试的重点:字符 n-gram 很容易把「只差一个实词」的两条事实
误判成同一条(那是不可逆的事实丢失),所以必须逐条钉死。
"""
import time
from datetime import datetime

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact
from gzhu_cup2.agent.long_memory import similarity as sim
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import tags as mem_tags

USER = "u-merge"

# 真实库里的两条"极为相近"(只差一个虚词)
ANSWER_LONG = "用户下周四需参加毕业答辩，对此感到紧张和压力。"
ANSWER_SHORT = "用户下周四参加毕业答辩，对此感到紧张和压力。"
# 后者是前者的超集
NOISE_SHORT = "用户喜欢听下雨声的白噪音来助眠和平复情绪。"
NOISE_LONG = "用户在失眠或压力大时，喜欢听下雨声的白噪音来助眠和平复情绪。"


# ============================ 纯函数 ============================
class TestSimilarityBasics:
    def test_normalize_drops_punct_and_space(self):
        assert sim.normalize(" 用户 ，喜欢  跑步。 ") == "用户喜欢跑步"

    def test_jaccard_identical_and_disjoint(self):
        assert sim.jaccard("用户喜欢跑步", "用户喜欢跑步") == 1.0
        assert sim.jaccard("用户喜欢跑步", "今天天气很好") == 0.0

    def test_containment_detects_superset(self):
        # 「b」包含「a」→ 包含度接近 1
        assert sim.containment(NOISE_SHORT, NOISE_LONG) == pytest.approx(0.947, abs=0.01)
        assert sim.containment(NOISE_LONG, NOISE_SHORT) == pytest.approx(0.947, abs=0.01)

    def test_empty_inputs(self):
        assert sim.jaccard("", "x") == 0.0
        assert sim.containment("", "") == 0.0
        assert sim.is_similar("", "x") is False


# ============================ 应合并(真实语料) ============================
class TestShouldMerge:
    @pytest.mark.parametrize("a,b", [
        (ANSWER_LONG, ANSWER_SHORT),
        (NOISE_SHORT, NOISE_LONG),
    ])
    def test_real_corpus_duplicates_are_similar(self, a, b):
        assert sim.is_similar(a, b) is True
        assert sim.polarity_conflict(a, b) is False

    def test_identical_text_is_similar(self):
        assert sim.is_similar("用户养了一只猫", "用户养了一只猫") is True


# ============================ 不可合并(对抗样例) ============================
class TestMustNotMerge:
    """只差一个实词 = 两条不同的事实,合并即不可逆丢失。"""

    @pytest.mark.parametrize("a,b", [
        ("用户喜欢跑步", "用户喜欢看书"),
        ("用户爱吃辣", "用户爱吃甜"),
        ("用户比较喜欢吃辣的食物", "用户比较喜欢吃甜的食物"),
        ("用户住在北京", "用户住在上海"),
        ("用户在准备毕业答辩，压力很大", "用户在准备资格考试，压力很大"),
        ("用户想在周末去爬山放松一下", "用户想在周末去游泳放松一下"),
        ("用户养了一只猫", "用户养了一只狗"),
        ("用户最近心情不好", "用户最近身体不好"),
    ])
    def test_single_content_word_difference_not_similar(self, a, b):
        assert sim.is_similar(a, b) is False

    def test_paraphrase_with_different_content_word_not_similar(self):
        # 已知能力边界:换词改写抓不到 —— 这是刻意的取舍,不是 bug
        assert sim.is_similar("用户名为小林", "用户自称为小林") is False

    def test_same_template_different_topic_not_similar(self):
        assert sim.is_similar(
            "用户有失眠困扰，习惯听白噪音助眠",
            "用户有失眠困扰，压力大时容易过度思考",
        ) is False


# ============================ 极性守卫 ============================
class TestPolarityGuard:
    def test_direction_detection(self):
        assert sim.direction("用户失眠好多了") == -1
        assert sim.direction("用户失眠加重了") == 1
        assert sim.direction("用户有失眠困扰") == 0

    def test_opposite_directions_conflict(self):
        assert sim.polarity_conflict("用户不再失眠了", "用户失眠很严重") is True

    def test_direction_vs_neutral_conflicts(self):
        # 带方向 vs 不带方向也视为冲突:避免把"加重"和中性描述糊成一条
        assert sim.polarity_conflict("用户有失眠困扰", "用户失眠加重了") is True

    def test_both_neutral_no_conflict(self):
        assert sim.polarity_conflict(ANSWER_LONG, ANSWER_SHORT) is False


# ============================ 段内去重 ============================
class TestDedupe:
    def test_keeps_longest_and_preserves_order(self):
        """同一簇留最长的那条,但**位置沿用首次出现处**(保序语义)。"""
        items = [NOISE_SHORT, "用户养了一只猫", NOISE_LONG]
        out = sim.dedupe(items, key_fn=lambda x: x)
        assert out == [NOISE_LONG, "用户养了一只猫"]

    def test_keeps_distinct_items(self):
        items = ["用户喜欢跑步", "用户喜欢看书"]
        assert sim.dedupe(items, key_fn=lambda x: x) == items

    def test_polarity_blocks_dedupe(self):
        items = ["用户失眠加重了", "用户失眠好多了"]
        assert sim.dedupe(items, key_fn=lambda x: x) == items

    def test_blank_items_skipped(self):
        assert sim.dedupe(["", "  ", "用户有猫"], key_fn=lambda x: x) == ["用户有猫"]


# ============================ store 落库行为 ============================
class TestMergeFactBehavior:
    def test_similar_fact_merges_into_one_entry(self):
        mem_store.merge_fact(USER, ANSWER_SHORT, "work", 0.9)
        mem_store.merge_fact(USER, ANSWER_LONG, "work", 0.8)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["mentions"] == 2
        assert facts[0]["text"] == ANSWER_LONG      # 取更长的那条
        assert facts[0]["importance"] == 0.9        # 重要度取 max

    def test_distinct_facts_stay_separate(self):
        mem_store.merge_fact(USER, "用户喜欢跑步", "preference", 0.6)
        mem_store.merge_fact(USER, "用户喜欢看书", "preference", 0.6)
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2

    def test_protected_fact_never_near_merged(self):
        mem_store.put_item(mem_store.ns_facts(USER), "k-prot", {
            "type": "fact", "text": ANSWER_SHORT, "category": "risk",
            "importance": 0.9, "mentions": 1,
            "first_seen": time.time(), "last_seen": time.time(),
            "archived": False, "protected": True,
        })
        mem_store.merge_fact(USER, ANSWER_LONG, "work", 0.9)
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2

    def test_archived_fact_not_merge_target(self):
        mem_store.put_item(mem_store.ns_facts(USER), "k-arch", {
            "type": "fact", "text": ANSWER_SHORT, "category": "work",
            "importance": 0.9, "mentions": 1,
            "first_seen": time.time(), "last_seen": time.time(),
            "archived": True, "protected": False,
        })
        mem_store.merge_fact(USER, ANSWER_LONG, "work", 0.9)
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2

    def test_switch_off_disables_near_merge(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_FACT_SIMILARITY_ENABLED", False)
        mem_store.merge_fact(USER, ANSWER_SHORT, "work", 0.9)
        mem_store.merge_fact(USER, ANSWER_LONG, "work", 0.9)
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2

    def test_exact_duplicate_still_merges_when_switch_off(self, monkeypatch):
        monkeypatch.setattr(cfg, "MEMORY_FACT_SIMILARITY_ENABLED", False)
        mem_store.merge_fact(USER, "用户有只猫", "life", 0.5)
        mem_store.merge_fact(USER, "用户有只猫", "life", 0.5)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1 and facts[0]["mentions"] == 2


# ============================ 事件日期(event_date) ============================
class TestEventDatePersistence:
    def test_new_fact_persists_event_date(self):
        mem_store.merge_fact(USER, "用户明天要考试", "plan", 0.8,
                             event_date="2026-09-10")
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["event_date"] == "2026-09-10"

    def test_merge_keeps_latest_non_empty(self):
        mem_store.merge_fact(USER, "用户明天要考试", "plan", 0.8,
                             event_date="2026-09-10")
        mem_store.merge_fact(USER, "用户明天要考试", "plan", 0.8,
                             event_date="2026-09-11")
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["event_date"] == "2026-09-11"

    def test_merge_with_none_keeps_old_event_date(self):
        mem_store.merge_fact(USER, "用户明天要考试", "plan", 0.8,
                             event_date="2026-09-10")
        mem_store.merge_fact(USER, "用户明天要考试", "plan", 0.8,
                             event_date=None)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["event_date"] == "2026-09-10"

    def test_date_variant_merges_and_keeps_first_assertion(self):
        """同一件事被重新锚定日期 → 合并为一条,保留**首次断言**。

        回归:Day1「下周三」(10-21) 与 Day2 回忆同一句(被重算成 10-28)曾落成
        两条 active 事实,提示词同时注入,桌宠会说错日期。
        """
        mem_store.merge_fact(USER, "用户在2026-10-21有个模拟考", "school", 0.7,
                             event_date="2026-10-21")
        mem_store.merge_fact(USER, "用户在2026-10-28有模拟考", "school", 0.7,
                             event_date="2026-10-28")

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["event_date"] == "2026-10-21"      # 首次断言胜出
        assert "2026-10-21" in facts[0]["text"]
        assert facts[0]["mentions"] == 2

    def test_date_variant_does_not_merge_different_events(self):
        """只差日期但事件不同 → 不得合并(防误合并)。"""
        mem_store.merge_fact(USER, "用户在2026-10-21有个模拟考", "school", 0.7,
                             event_date="2026-10-21")
        mem_store.merge_fact(USER, "用户在2026-10-21有个面试", "school", 0.7,
                             event_date="2026-10-21")

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 2


# ============================ 日期掩码边界 ============================
class TestDateMaskBoundary:
    """掩码只吃真日期。掩码放宽后「2026-2027学年」曾被吃成「2026-20」只剩
    「27学年」,文本因此被判成"带日期"、走进放宽阈值 —— 两条不同的事实被
    静默合并成一条,属于不可逆的事实丢失。
    """

    @pytest.mark.parametrize("text", [
        "用户在2026-2027学年要换宿舍",
        "用户2026-2027赛季没再打球",
        "用户学号2026-1234",
        "用户考了95-100分",
        "用户还有10-20个题不会",
    ])
    def test_non_date_number_span_is_not_masked(self, text):
        assert mem_store._strip_dates(text) == text

    @pytest.mark.parametrize("text", [
        "用户在2026-11月要搬家",
        "用户在2026-11-01那一周有考试",
    ])
    def test_real_dates_are_still_masked(self, text):
        assert mem_store._strip_dates(text) != text

    def test_school_year_facts_stay_separate(self):
        mem_store.merge_fact(USER, "用户在2026-2027学年要申请国家奖学金", "life", 0.6)
        mem_store.merge_fact(USER, "用户在2026-2027学年要申请校级奖学金", "life", 0.6)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 2

    def test_month_granularity_reanchor_still_merges(self):
        # 月粒度是"同一件事被重新锚定",仍只留一条(保留首次说出的日期)
        mem_store.merge_fact(USER, "用户在2026-11月要搬家", "life", 0.6,
                             event_date="2026-11-01")
        mem_store.merge_fact(USER, "用户在2026-12月要搬家", "life", 0.6,
                             event_date="2026-12-01")
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 1


# ============================ 措辞扩展合并(详略两版) ============================
class TestWordingExtensionMerge:
    """「沿用原句 + 补细节」→ 收成一条并保留详版。

    真实库里的形态(不是阈值问题,是抽取端看不到已有什么):
      「用户有一个室友叫阿杰」 / 「用户有一个室友叫阿杰，天天打游戏很吵」
    抽取提示(规则 2c)现在要求"要补充就以原句开头、一字不改",于是落库时能命中
    这条**纯结构**判据:短的是长的严格前缀。
    """

    def test_extension_merges_and_keeps_detailed_version(self):
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰", "relationship", 0.6)
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰，天天打游戏很吵",
                             "relationship", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["text"] == "用户有一个室友叫阿杰，天天打游戏很吵"
        assert facts[0]["mentions"] == 2

    def test_reverse_order_keeps_detailed_version(self):
        """先详后略(抽取器抖出截短版)也不得把细节弄丢。"""
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰，天天打游戏很吵",
                             "relationship", 0.6)
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰", "relationship", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert "天天打游戏很吵" in facts[0]["text"]

    def test_short_generic_prefix_is_not_an_extension(self):
        """过短的泛句做前缀会吞掉任何事实 —— 「用户叫小宇」不得吸收长句。"""
        mem_store.merge_fact(USER, "用户叫小宇", "life", 0.8)
        mem_store.merge_fact(USER, "用户叫小宇的室友阿杰天天打游戏很吵",
                             "relationship", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 2

    def test_length_ratio_guard_blocks_swallowing(self):
        """短句 + 一长段新内容 ≠ 扩写(长度比 < 0.5)。"""
        mem_store.merge_fact(USER, "用户害怕考研失败", "school", 0.7)
        mem_store.merge_fact(
            USER,
            "用户害怕考研失败所以每天学到凌晨两点还失眠掉头发不敢跟家里说",
            "health", 0.7)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 2

    def test_extension_catches_what_fuzzy_similarity_misses(self):
        """这条判据不是近义阈值的重复:存在模糊判据漏掉、前缀判据接住的形态。

        前缀扩写时 Jaccard ≈ 短串 n-gram 数 / 长串 n-gram 数,补的细节越多越低。
        下面这一对 Jaccard 恰好 0.500(<0.55)、包含度 1.0 —— 模糊判据判不出来,
        而长度比 0.526(≥0.5)仍在扩写区间内,前缀判据应当接住。
        """
        short = "用户和室友阿杰一起住"
        long_ = "用户和室友阿杰一起住宿舍四人间经常吵架"
        assert sim.is_similar(short, long_) is False
        assert mem_store._is_wording_extension(short, long_) is True

        mem_store.merge_fact(USER, short, "relationship", 0.6)
        mem_store.merge_fact(USER, long_, "relationship", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["text"] == long_

    def test_switch_off_disables_extension_rule(self, monkeypatch):
        """开关只关掉"前缀扩展"这一路;模糊判据照旧(两者是独立通道)。"""
        a, b = "用户有一个室友叫阿杰", "用户有一个室友叫阿杰，天天打游戏很吵"
        assert mem_store._is_wording_extension(a, b) is True

        monkeypatch.setattr(cfg, "MEMORY_FACT_EXTENSION_ENABLED", False)
        assert mem_store._is_wording_extension(a, b) is False

    def test_paraphrase_is_still_not_merged(self):
        """能力边界:换了说法的同义改写仍不会合并(靠抽取端沿用原句解决)。

        这条用例是**故意钉死的边界**:如果哪天有人把前缀判据放宽成"内容词包含",
        它会先在这里红掉 —— 因为「用户的室友叫阿杰，天天打游戏很吵」和
        「用户有一个室友叫阿杰」不是前缀关系。
        """
        mem_store.merge_fact(USER, "用户有一个室友叫阿杰", "relationship", 0.6)
        mem_store.merge_fact(USER, "用户的室友叫阿杰，天天打游戏很吵",
                             "relationship", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 2


# ============================ 安全类事实的分类守卫 ============================
class TestSafetyCategoryGuard:
    def test_self_harm_fact_forced_to_health_and_protected(self):
        """LLM 曾把「明确表示没有自伤意图」标成 school —— 落库层必须兜底。"""
        mem_store.merge_fact(USER, "用户明确表示没有自伤意图", "school", 0.6)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["category"] == "health"
        assert facts[0]["protected"] is True

    def test_normal_fact_keeps_category(self):
        mem_store.merge_fact(USER, "用户养了一只叫年糕的橘猫", "life", 0.7)

        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert facts[0]["category"] == "life"
        assert facts[0]["protected"] is False

    def test_commit_segment_absolutizes_text_and_resolves_event_date(self):
        """写入时锚定(计划 A):落库 text 本身携带绝对日期,event_date 照常落。

        「用户明天要考试」@ 2026-09-09 锚点 → 存储 text=「用户在2026-09-10有考试」,
        不再是会自相矛盾的「明天」。"""
        anchor = datetime(2026, 9, 9, 10, 0)
        pending = {
            "turns": [{"seq": 0, "ts": anchor.timestamp(), "thread": "t1",
                       "user": "我明天要考试", "ai": "加油",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": anchor.timestamp(),
            "last_ts": anchor.timestamp(), "threads": ["t1"],
        }
        mem_store.commit_segment(USER, pending, MemoryExtraction(
            digest="x",
            facts=[MemoryFact(text="用户明天要考试", category="plan", importance=0.8)],
        ))
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert any(f["text"] == "用户在2026-09-10要考试"
                   and f["event_date"] == "2026-09-10" for f in facts)

    def test_commit_segment_fact_without_time_word_gets_none(self):
        anchor = datetime(2026, 9, 9, 10, 0)
        pending = {
            "turns": [{"seq": 0, "ts": anchor.timestamp(), "thread": "t1",
                       "user": "我喜欢跑步", "ai": "好的",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": anchor.timestamp(),
            "last_ts": anchor.timestamp(), "threads": ["t1"],
        }
        mem_store.commit_segment(USER, pending, MemoryExtraction(
            digest="x",
            facts=[MemoryFact(text="用户喜欢跑步", category="preference", importance=0.6)],
        ))
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert any(f["text"] == "用户喜欢跑步"
                   and f["event_date"] is None for f in facts)


# ============================ 固化路径 ============================
def _pending(*texts):
    now = time.time()
    return {
        "turns": [{"seq": i, "ts": now, "thread": "t1", "user": t, "ai": "嗯",
                   "meta": {"risk_level": "low"}} for i, t in enumerate(texts)],
        "next_seq": len(texts), "started_at": now, "last_ts": now, "threads": ["t1"],
    }


class TestCommitSegmentMerging:
    def test_within_segment_dedupe_avoids_inflated_mentions(self):
        """同一段里两条近义事实只能算 1 次提及(否则 memory_score 被虚增)。"""
        mem_store.commit_segment(USER, _pending("聊了白噪音"), MemoryExtraction(
            digest="x",
            facts=[MemoryFact(text=NOISE_SHORT, category="health", importance=0.7),
                   MemoryFact(text=NOISE_LONG, category="health", importance=0.7)],
        ))
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["mentions"] == 1
        assert facts[0]["text"] == NOISE_LONG

    def test_cross_segment_similar_facts_merge(self):
        mem_store.commit_segment(USER, _pending("a"), MemoryExtraction(
            digest="x", facts=[MemoryFact(text=NOISE_SHORT, category="health", importance=0.7)]))
        mem_store.commit_segment(USER, _pending("b"), MemoryExtraction(
            digest="y", facts=[MemoryFact(text=NOISE_LONG, category="health", importance=0.7)]))
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert len(facts) == 1
        assert facts[0]["mentions"] == 2

    def test_distinct_facts_in_one_segment_kept(self):
        mem_store.commit_segment(USER, _pending("a"), MemoryExtraction(
            digest="x",
            facts=[MemoryFact(text="用户喜欢跑步", category="preference", importance=0.6),
                   MemoryFact(text="用户喜欢看书", category="preference", importance=0.6)],
        ))
        assert len(list(mem_store.list_items(mem_store.ns_facts(USER)))) == 2


class TestFactCategoryNormalization:
    """事实分类收敛:词表外的值只降级为分类偏差,不再使整段抽取失败。

    模型曾给出 category="sleep"(词表外),严格枚举使整段 MemoryExtraction
    校验失败,该段记忆进入退避重试直至丢弃。
    """

    def test_legal_value_passthrough(self):
        assert mem_tags.clean_fact_category("school") == "school"

    def test_out_of_vocabulary_is_mapped(self):
        assert mem_tags.clean_fact_category("sleep") == "health"

    def test_chinese_synonym_is_mapped(self):
        assert mem_tags.clean_fact_category("学业") == "school"

    def test_text_used_when_raw_is_useless(self):
        assert mem_tags.clean_fact_category("unknown", "用户下周要答辩") == "school"

    def test_unclassifiable_falls_back_to_other(self):
        assert mem_tags.clean_fact_category("", "嗯") == "other"

    def test_schema_accepts_out_of_vocabulary(self):
        """根因在 schema 层:构造不抛异常,校验推迟到落库前的收敛。"""
        assert MemoryFact(text="用户昨晚三点才睡", category="sleep").category == "sleep"

    def test_commit_segment_normalizes_category(self):
        """落库后读到的是词表内取值,而不是模型原样给出的词。"""
        mem_store.commit_segment(USER, _pending("a"), MemoryExtraction(
            digest="x",
            facts=[MemoryFact(text="用户昨晚三点才睡", category="sleep", importance=0.6)],
        ))
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert facts[0]["category"] == "health"
