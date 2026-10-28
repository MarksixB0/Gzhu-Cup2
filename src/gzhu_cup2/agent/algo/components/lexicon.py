# -*- coding: utf-8 -*-
"""lexicon 组件:负面情绪词表与症状族词表(确定性、零 LLM、零 IO)。

只做粗负面打分与命中词回显,单测可穷举;数值只作证据、不作判定。

口径:
  - 对一段用户文本统计负面/正面情感词的**唯一命中数**;
  - 每个命中先过 structure 结构判定(否定辖域/他人主体/转述/假设),
    结构上不算说话人自己直陈事实的命中不计入;
  - 负面基线 + 增量 - 正面抵消 → 0-1 负面分(粗档即可,精度交给多通道聚合);
  - 不处理复杂反讽(那是 LLM 通道的职责),宁可漏不可错。
"""
from typing import List, Tuple

from gzhu_cup2.agent.algo.components import structure

NEG_WORDS = frozenset({
    "难过", "伤心", "想哭", "委屈", "难受", "郁闷", "低落", "沮丧", "痛苦",
    "心里堵", "烦", "烦躁", "焦虑", "紧张", "担心", "不安", "心慌", "压力",
    "大压力", "累", "心累", "疲惫", "没劲", "撑不住", "崩溃", "绝望", "无助",
    "孤独", "空虚", "没意思", "没意义", "无意义", "迷茫", "自卑", "内疚",
    "自责", "失眠", "睡不着", "早醒", "睡不好", "没胃口", "暴食", "提不起劲",
    "没精神", "没力气", "不想动", "不想说话", "被抽空",
    "心里空空的", "发呆", "乏力", "嗜睡", "没动力", "拖延", "逃避", "不想见人",
    "暴躁", "易怒", "生气", "愤怒",
    "害怕", "恐惧", "惊恐", "想死", "不想活", "自杀", "结束生命", "伤害自己",
    "划自己", "生不如死", "活着没意思", "撑不下去", "受不了", "好累", "烦死了",
    "很烦", "很难过", "好难受", "压力好大", "睡不着觉", "心里难受",
})

POS_WORDS = frozenset({
    "开心", "高兴", "快乐", "幸福", "愉快", "满足", "轻松", "舒服", "期待",
    "希望", "有信心", "会好的", "会好起来", "好转", "感激", "温暖", "安心",
    "平静", "放松", "太好了", "很棒", "加油",
})

# 词典覆盖度有限 → 不计命中时的保守负面基线(低于启发式同档,防把普通闲聊判负)
_BASE_NO_HIT = 0.10
_BASE_HIT = 0.30
_NEG_STEP = 0.09
_POS_STEP = 0.06


def ladder() -> dict:
    """词表打分的阶梯描述(排障面板与测试共用)。"""
    return {
        "base_no_hit": _BASE_NO_HIT,
        "base_hit": _BASE_HIT,
        "step_per_word": _NEG_STEP,
        "pos_step": _POS_STEP,
        "neg_word_cap": 6,
        "pos_word_cap": 3,
        "max_score": round(min(1.0, _BASE_HIT + _NEG_STEP * 6), 3),
    }


def _kept_hits(text: str, words, negation_only: bool = False) -> List[str]:
    """结构过滤后的命中词列表:任一出现位置通过结构判定即保留该词。

    ``negation_only`` 用于正面词:只拦否定(否定的正面不给抵消),
    主体/转述/假设放行 —— 负面误放行是漏报,正面误拦只是少一层抵消,
    代价不对称,正面侧从严拦会造成"和朋友玩得很开心"这种真实好话被吞。
    """
    t = str(text or "")
    kept = []
    for w in words:
        if w not in t:
            continue
        start = 0
        hit = False
        while True:
            idx = t.find(w, start)
            if idx == -1:
                break
            start = idx + len(w)
            if negation_only:
                if not structure.flags_for(t, idx, idx + len(w))["negated"]:
                    hit = True
                    break
            elif structure.is_evidence(t, idx, idx + len(w)):
                hit = True
                break
        if hit:
            kept.append(w)
    return kept


def score_neg(text: str | None) -> Tuple[float, List[str], List[str]]:
    """对文本做确定性负面打分。

    返回 (score 0-1, 计入证据的负面词列表, 计入证据的正面词列表)。
    被结构特征拦下的命中(否定/他人/转述/假设)不出现也不计分。
    纯函数,可离线单测。
    """
    t = str(text or "")
    if not t.strip():
        return 0.0, [], []
    neg_hits = _kept_hits(t, NEG_WORDS)
    pos_hits = _kept_hits(t, POS_WORDS, negation_only=True)
    n = len(neg_hits)
    m = len(pos_hits)
    if n == 0 and m == 0:
        return round(_BASE_NO_HIT, 3), [], []
    score = _BASE_HIT + _NEG_STEP * min(n, 6) - _POS_STEP * min(m, 3)
    if n == 0:
        score = max(0.0, _BASE_NO_HIT - _POS_STEP * min(m, 3))
    return round(max(0.0, min(1.0, score)), 3), neg_hits, pos_hits


def neg_channel(text: str | None) -> float:
    """只取分值的便捷入口(供 capture 旁路写 meta)。"""
    s, _, _ = score_neg(text)
    return s


# ============================ 行为通道(确定性词表) ============================
# 行为词表描述"最近在做什么/不做什么"(模式、前驱),与情绪词表(NEG_WORDS)不重叠:
# 只认明确的回避/推拒/隔离表述,不认"我今天没出门"。

WITHDRAW_WORDS = frozenset({
    "不想出门", "不想见人", "不想见", "不想去", "不想回", "谁都不想",
    "谁都不", "别来了", "别找我", "推掉了", "推掉了聚会", "推了", "推掉",
    "没去", "没出门", "不出门", "不见人", "不想说话", "不想讲话",
    "一个人待", "一个人呆", "躲着", "避开", "谢绝", "取消了", "取消见面",
    "窗帘", "拉上窗帘", "关在房间", "待在房间", "不下床", "起不来",
    "不回消息", "没回消息", "失联", "断了联系", "不联系",
})

CONFLICT_WORDS = frozenset({
    "吵了一架", "吵架", "大吵", "杠上", "翻脸", "闹掰", "动手", "砸了",
    "摔东西", "摔门", "发火", "火气", "火药桶", "脾气", "别来烦",
    "看不顺眼", "不顺眼", "控制不住", "吼", "骂人", "骂了", "争执",
    "跟人起冲突", "冲突",
})

# ---- 症状族三:快感缺失 / 动力缺失(anhedonia) ----
# 只收"无兴趣/无动力"表述;"烦/累/压力大"这类抱怨不计入本族。
ANHEDONIA_WORDS = frozenset({
    "没意思", "没有意思", "没意义", "没意义了", "没劲", "提不起劲",
    "提不起兴趣", "不想动", "什么都不想做", "什么都不想干", "什么都没做",
    "什么都没做成", "什么都不想", "什么都没干", "没动力", "没兴趣",
    "没感觉", "麻木", "空虚", "心里空", "空空", "被抽空", "抽空了",
    "发呆", "不想说话", "混过去", "无所事事", "没盼头", "看不到头",
    "没精神", "打不起精神", "算了",
})

# ---- 症状族四:躯体化(somatic) ----
# 只收持续反复才有意义的躯体主诉;偶发感冒发烧达不到密度阈值(一周 ≥3 天)。
SOMATIC_WORDS = frozenset({
    "头疼", "头痛", "头晕", "胃疼", "胃痛", "胃不舒服", "胃又", "反酸",
    "恶心", "想吐", "干呕", "心慌", "心跳快", "心跳得", "胸闷", "胸口发闷",
    "吃不下", "没胃口", "吃一点就", "睡不着", "翻来覆去", "浑身没力气",
    "浑身都", "出冷汗", "手抖", "手都在抖", "瘦了好几斤", "瘦了", "站不稳",
    "发花", "一抽一抽", "胀得慌", "顶得慌", "坐不住", "提不起力气",
})

_FAMILY_WORDS = (
    ("withdraw", WITHDRAW_WORDS), ("conflict", CONFLICT_WORDS),
    ("anhedonia", ANHEDONIA_WORDS), ("somatic", SOMATIC_WORDS),
)


def behavior_flags(text: str | None) -> frozenset:
    """行为/症状族判定:**唯一**入口(纯函数、零 IO、可穷举单测)。

    返回四族的子集(可能为空)。命中词先过 structure 结构判定,
    他人主体/否定/转述/假设的命中不计入本族。调用方把它写进
    meta["behavior"],由 ticks 落进当日桶;趋势层只读计数不看原文。
    """
    t = str(text or "")
    if not t.strip():
        return frozenset()
    out = []
    for fam, words in _FAMILY_WORDS:
        if _kept_hits(t, words):
            out.append(fam)
    return frozenset(out)
