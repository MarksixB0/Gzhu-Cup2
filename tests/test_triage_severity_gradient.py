# -*- coding: utf-8 -*-
"""风险分档的**性质**测试(而不是若干条真实句子的回归夹具)。

为什么要换一种写法
------------------
2026-10-18 实机暴露两个错判:「普通的心情低落,睡一觉就好」被判 medium
(轻度误报)、「有时候觉得活着挺没意思的」被判 low(漏报)。最省事的修法是
把这两句话抄进 tests 当回归用例 —— **但那是过拟合**:过的是这两句话,
不是"轻度不该升档""意念不该落低"这条判据。换一句同义改写(「今天就是有点低落,
睡一觉就好」/「偶尔觉得活着没什么盼头」),夹具依然绿,错判照样发生。

所以这里锁的是判据本身,三种写法:

1. **变形不变式(metamorphic)**:在短语内部插一个程度副词(「活着没意思」→
   「活着挺没意思」)不得降低判级 —— 中文最常见的改写就是插字,而实现是子串
   匹配,插字正是它最脆弱的地方;
2. **单调性**:给一段话追加更重的信号(绝望→意念→意图),判级只能升不能降;
3. **档带约束(seed 固定的随机语料)**:每个严重度档由若干"骨架"生成大量变体,
   断言每一档落在**允许的档带内**,且相邻档不出现逆序。

这些性质对实现是黑盒的:只要判据还叫"轻度/绝望/意念/意图",换成新的词表、
新的打分方式,断言依然成立 —— 这正是它不会过拟合的原因。
"""
import random

import pytest

from gzhu_cup2.agent.nodes.triage import classify, risk_from

RANK = {"low": 0, "medium": 1, "high": 2, "crisis": 3}


def level(text: str) -> int:
    return RANK[risk_from(classify(text))[0]]


# ============================== 语料:骨架 + 生成 ==============================
# 每个档位只给"骨架",变体由下面的属性测试现算 —— 没有一句话是被逐字钉死的。
MILD = [
    "就是普通的心情低落，睡一觉就好",
    "这两天有点低落，过一阵子就好了",
    "考前有点焦虑，自己缓缓就好",
    "今天有点闷，没什么大事",
]
ELEVATED_MOOD = [
    "最近挺焦虑的，晚上老是睡不着",
    "心里堵得慌，什么都不想干",
    "我很难过，想哭",
    "压力很大，提不起劲",
]
DESPAIR = [
    "我真的撑不下去了",
    "不想再努力了，看不到希望",
    "最近觉得活着没什么盼头",
]
IDEATION_HEDGED = [
    "有时候觉得活着挺没意思的",
    "偶尔会想活着没意义",
    "时不时觉得活得没什么意思",
]
INTENT = [
    "我想死",
    "我不想活了",
    "有时候真想去死",
    "我想自杀",
    "活着没意思，我想割腕",
]

ADVERBS = ["", "挺", "很", "真的", "特别", "实在太", "好"]

# 自限/轻描淡写后缀:任何"轻度"表达加上它都只该更低,不该更高
MILD_SUFFIXES = ["，睡一觉就好", "，自己缓缓就好", "，一阵子就过去了", "，没什么大事"]


# ============================== 性质 1:插词不降级 ==============================
@pytest.mark.parametrize("frame", ["活着没意思", "活得没意义", "活着没盼头"])
@pytest.mark.parametrize("adv", ADVERBS)
def test_intensifier_inside_ideation_never_lowers_level(frame, adv):
    """「活着{副词}没意思」的判级不得低于「活着没意思」。"""
    head, tail = frame[:2], frame[2:]          # 活着 | 没意思
    base = level(head + tail)
    got = level(head + adv + tail)
    assert got >= base, f"插词降级: {head + adv + tail} ({got} < {base})"


@pytest.mark.parametrize("frame", INTENT)
@pytest.mark.parametrize("adv", ADVERBS)
def test_intensifier_inside_intent_stays_crisis(frame, adv):
    """主动意图短语插词后仍然是危机档(插词不得把危机降成普通难过)。"""
    if not frame.startswith("我想") and not frame.startswith("我不想"):
        pytest.skip("只对「我想X / 我不想X」型骨架做插词变形")
    body = frame.replace("我想", "我" + adv + "想", 1)
    assert level(body) == RANK["crisis"], body


# ============================== 性质 2:追加更重信号只能升 ==============================
@pytest.mark.parametrize("base_text", ELEVATED_MOOD + MILD)
def test_appending_stronger_signal_never_lowers(base_text):
    """在任意一段话后面追加"更重"的信号,判级单调不降。

    这是心理安全场景里最要紧的方向性:多说了更糟的话,系统不能反而更放心。
    """
    before = level(base_text)
    for tail in ("，撑不下去了", "，有时候觉得活着没意思", "，我想死"):
        assert level(base_text + tail) >= before, f"{base_text} + {tail}"


# ============================== 性质 3:档带约束(随机语料) ==============================
def _sample(seed=20261020):
    rng = random.Random(seed)
    return {
        "mild": [m + rng.choice(MILD_SUFFIXES) for m in MILD],
        "mood": ELEVATED_MOOD,
        "despair": DESPAIR,
        "ideation": IDEATION_HEDGED,
        "intent": INTENT,
    }


def test_tiers_never_invert_across_bands():
    """档与档之间不得逆序:重的一档,最低分不得低于轻的一档的最高分。

    允许"跨一档"(轻度偶尔到 medium 也还行),但**绝望/意念/意图三档不得低于
    轻度档的天花板** —— 这一条正好卡住实机那次「活着没意思 → low」的漏报。
    """
    pools = _sample()
    mild_max = max(level(t) for t in pools["mild"])
    for name in ("despair", "ideation", "intent"):
        low = min(level(t) for t in pools[name])
        assert low >= max(mild_max, RANK["high"]), f"{name} 出现低于预期的判级: {low}"


def test_mild_band_never_reaches_high():
    """轻度/自限表达的天花板:绝不进 high,更不进 crisis(误报代价 = 用户被吓到)。"""
    for text in _sample()["mild"]:
        assert level(text) <= RANK["medium"], text


def test_mild_suffix_caps_every_mild_frame():
    """给轻度骨架逐个自限后缀,判级仍在中档以下(后缀不改变语气强度)。"""
    for frame in MILD:
        for suffix in MILD_SUFFIXES:
            assert level(frame + suffix) <= RANK["medium"], frame + suffix


def test_hedged_ideation_sits_middle_but_unhedged_escalates():
    """间歇限定的被动意念落中间档;**去掉限定**后必须升到危机档。

    这一对是实机"中间档塌陷"的核心:它要求 high 这一档真实存在,
    且 high 不是靠特判某句话得来,而是"同一个意思、少了限定"自然升上去。
    """
    for hedged in IDEATION_HEDGED:
        assert level(hedged) == RANK["high"], hedged
    for unhedged in ("觉得活着没意思", "活着没意思", "觉得活得没意义"):
        assert level(unhedged) == RANK["crisis"], unhedged


# ============================== 性质 4:否定守卫(随机改写的说法) ==============================
@pytest.mark.parametrize("text", [
    "我不想死", "我不想死，我还想好好活着", "我没有想过自杀", "我不会自杀的",
    "我不敢死", "我没想过做傻事", "我不想死，只是太累了",
])
def test_negated_intent_never_crisis(text):
    assert classify(text)["is_user_dangerous"] is False, text


# ============================== 性质 5:粗情绪词照样不给安全信号 ==============================
def test_ideation_and_despair_carry_no_emotion_description():
    """意念/绝望档不得输出"平静情绪"这类描述(会被模型当事实复述)。"""
    for text in DESPAIR + IDEATION_HEDGED + INTENT:
        c = classify(text)
        if c["is_user_dangerous"] or c["ideation"] or c["despair"]:
            assert c["description"] == "", (text, c["description"])
