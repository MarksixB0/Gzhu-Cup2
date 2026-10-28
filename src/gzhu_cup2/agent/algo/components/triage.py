# -*- coding: utf-8 -*-
"""triage 组件:前台快速分流用的确定性粗分类(零 LLM、毫秒级)。

职责:
  1. 危机快闸:显式危机词命中 → crisis_level = hard/soft,走危机协议;
  2. 粗粒度情绪打标:词表启发式给 8 标签 + 0-1 负面分 + 强度;
  3. 粗风险档:crisis / high / medium / low(绝不向用户暴露)。

全部启发式确定性、可单测;宁可把「弱负面」判低,也不让前台误触发危机。

词表命中一律先过 structure 结构判定:否定辖域/他人主体/转述/假设的
命中不计入情绪证据;危机词只看否定(其余结构不拦危机,宁误触不漏报)。
"""
from typing import Tuple

import re

from gzhu_cup2.agent.algo.components import structure

# ============================== 词表(粗粒度启发式) ==============================
# 情绪短语(长词在前,计数式匹配)
_EMO_PHRASES: list[Tuple[str, Tuple[str, ...]]] = [
    ("sad", ("伤心欲绝", "好难过", "难过", "伤心", "想哭", "委屈", "难受",
             "郁闷", "低落", "沮丧", "痛苦", "心里堵")),
    ("fearful", ("很害怕", "害怕", "恐惧", "好怕", "惊恐", "吓死")),
    ("anxious", ("压力很大", "压力大", "焦虑", "紧张", "担心", "不安", "心慌",
                 "慌张", "静不下来", "睡不着", "失眠", "压力")),
    ("angry", ("很生气", "生气", "愤怒", "气死", "气人", "讨厌", "发火", "火大", "恨")),
    ("frustrated", ("撑不住", "心累", "好累", "没劲", "无力", "无奈", "崩溃",
                    "厌倦", "烦死了", "烦", "累",
                    # 快感缺失/乏力词,归入本标签
                    "提不起劲", "没精神", "没力气", "没意思", "不想动",
                    "不想说话", "不想见人", "被抽空", "心里空空的", "发呆",
                    "乏力", "嗜睡",
                    # 同类换词表达
                    "缓不过来", "撑得很勉强", "硬撑", "强撑", "提不起兴趣",
                    "空落落", "心里空", "喘不过气", "麻木", "没盼头",
                    "看不到头", "不想睁眼", "熬不住")),
    ("hopeful", ("有希望", "希望", "会好的", "会好起来", "加油", "期待", "有信心", "好转")),
    ("happy", ("好开心", "开心", "高兴", "快乐", "太好了", "幸福", "愉快", "棒")),
]
# 情感极性强度基分(负面倾向 0-1)
_BASE_NEG = {
    "happy": 0.10, "hopeful": 0.22, "neutral": 0.30,
    "anxious": 0.55, "angry": 0.60, "frustrated": 0.60,
    "sad": 0.65, "fearful": 0.75,
}
_ZH = {
    "happy": "开心", "sad": "悲伤", "anxious": "焦虑", "angry": "愤怒",
    "frustrated": "沮丧", "hopeful": "抱有希望", "fearful": "恐惧", "neutral": "平静",
}

# ---- 单次命中(命中 1 个情绪短语)时的最低负面分 ----
# 导出 SINGLE_HIT_NEG_MIN 供契约测试钉住各标签的离散分值。最低的是 anxious(0.40)。
_NEGATIVE_LABELS = ("sad", "anxious", "angry", "frustrated", "fearful")


def _single_hit_neg(label: str) -> float:
    """单次弱命中的降权分(行为与契约常量共用这一份实现)。"""
    return round(max(0.25, _BASE_NEG.get(label, 0.30) - 0.15), 3)


SINGLE_HIT_NEG_MIN = min(_single_hit_neg(l) for l in _NEGATIVE_LABELS)

# 显式危机短语,分两级(见 crisis_level)。
#   hard —— 意图明确,或已有方法/手段/时间/告别行为,走完整危机协议。
#   soft —— 有死亡意象但无计划,仍走危机协议,但语气更轻。
# 无望但无死亡意象的话(如"撑不下去")不进危机,走 high 档。
# 「活着没意思」类被动意念不在本表,单独归 ideation 族(见下)。
_CRISIS_HARD = (
    # —— 明确意图 ——
    "我想死", "想去死", "想死", "不想活了", "不想活", "活不下去",
    "没有活下去的意义", "结束生命", "结束自己",
    "自杀", "割腕", "跳楼", "一了百了", "离开这个世界", "让我去死",
    "不如死了", "死了算了", "没必要活着", "活着是负担",
    # —— 方法 / 手段(计划性最直接的证据) ——
    "从楼上跳", "从楼顶跳", "站在天台", "站在楼顶", "从天台", "烧炭",
    "买好了绳", "准备了刀", "攒了安眠", "买了安眠", "存了安眠", "安眠药",
    "农药", "割腕", "怎么走最不疼", "怎么死不疼", "查过怎么死", "查过怎么走",
    # —— 告别 / 准备行为 ——
    "遗书", "写好了信", "东西都送人", "都送出去了", "交代好后事",
    "跟所有人道别", "跟大家道别", "见最后一面", "最后一次见",
)

# ---- 严重度梯度:被动意念 / 绝望(中间档判据) ----
# 判据三族:crisis(主动意图/计划/方式) > ideation(被动意念) > despair(耗尽/绝望)。
# 裸说「活着没意思」仍按危机处理;带「有时候/偶尔」等间歇限定时落 high ——
# 间歇性被动意念的急迫度低于持续性陈述,这一档是此前缺位的中间档。
_IDEATION_PHRASES = (
    "活着没意思", "活着没意义", "活得没意思", "活得没意义",
    "活着没盼头", "活着干什么", "活着有什么意思",
)
# 字符串表只收固定说法;真正的中文改写是短语内部插字(「活着挺没意思」),
# 由正则覆盖 —— 依赖「一个语义框架 + 有限间隙」而不是穷举变体。
_RE_IDEATION = re.compile(
    r"(?:活着|活得|活下去)[^，。！？；\s]{0,5}(?:没|没有)(?:什么|啥)?(?:意思|意义|盼头|希望|劲)")
# 绝望/耗尽:不含死意,但已是"撑不住"这一档 → 至少 high
_DESPAIR_PHRASES = (
    "撑不下去了", "撑不下去", "撑不住了", "快要撑不住",
    "不想再努力", "不想努力了", "看不到希望", "没盼头", "活得好累", "活得累",
    "熬不下去", "受够了", "一切都没意义", "什么都没意义", "活着太累",
)
# 间歇/频率限定词:出现在意念短语**左侧邻域**内 → 视为间歇性被动意念(high 而非 crisis)
_HEDGES = ("有时候", "有时", "偶尔", "经常", "常常", "时不时")
_HEDGE_LEFT = 14

# 轻描淡写的自限标志:显式说明"会自己过去"时不再按中档处理。
# 只在**无任何**危机/意念/绝望命中时生效 —— 绝不用它去压安全信号。
_MILD_CUES = ("睡一觉就好", "睡一觉就好了", "睡一觉就没事", "睡一觉",
              "就没事了", "缓缓就好", "一阵子就好", "自己能缓过来",
              "普通的心情", "普通", "还好")
_MILD_NEG_CAP = 0.45      # < 0.50 → 落 low(见 risk_from)

# 程度副词:匹配前统一剥掉的"填充词"。中文最常见的改写就是在短语内部插副词
# (「活着没意思」→「活着挺没意思」),剥掉后"插词不改变判级"成为可锁住的不变式。
_INTENSIFIERS = ("真的挺", "真的是", "真的", "特别", "非常", "超级", "实在", "挺", "太", "好")


def _squeeze(text: str) -> str:
    """剥掉程度副词填充词(只用于危机/意念/绝望匹配,不用于情绪短语计数)。"""
    out = str(text or "")
    for w in sorted(_INTENSIFIERS, key=len, reverse=True):
        out = out.replace(w, "")
    return out


# 语义框架:一条规则覆盖一族表达(与 _RE_IDEATION 同性质),不穷举变体。
# 手段/准备物宾语前置(「刀都准备好了」「绳子我已经买好了」)按 hard 计。
_RE_MEANS = re.compile(
    r"(?:刀|绳子|安眠药|遗书|炭)[^，。！？；\s]{0,6}都?(?:准备好了|买好了|备好了|攒好了|写好了)")
# 绝望族正则:固定说法与语义框架并存(「看不到…希望」)。
_RE_DESPAIR = re.compile(r"看不到[^，。！？；\s]{0,3}希望")
# 假设性死亡意念(「假如我死了」「哪天真没了」)按 soft 计。
_RE_SOFT = re.compile(
    r"(?:假如|要是|如果|万一|哪天)[^，。！？；\s]{0,4}(?:死了|不在了|没了)")


def _hit_frame(text: str, pattern: "re.Pattern", crisis_guard: bool = False) -> bool:
    """语义框架命中:正则匹配 + 与词表一致的守卫口径(见 _hit_any)。"""
    t = str(text or "")
    for m in pattern.finditer(t):
        if crisis_guard:
            if _negated(t, m.start(), m.end() - m.start()):
                continue
            if structure.flags_for(t, m.start(), m.end())["negated"]:
                continue
            return True
        if not structure.is_evidence(t, m.start(), m.end()):
            continue
        return True
    return False


def _hedged(text: str, idx: int) -> bool:
    """意念短语左侧邻域内是否有间歇限定词(见 _HEDGES)。"""
    lo = max(0, idx - _HEDGE_LEFT)
    return any(h in text[lo:idx] for h in _HEDGES)
# 死亡意象(无计划):不进危机协议的 hard 档,仍走 soft 危机协议。
_CRISIS_SOFT = (
    # 死亡意象(无计划):"不想再醒来"是明确的死亡愿望,但不是计划
    "不想再醒", "不想醒来", "不想睁眼", "永远睡过去", "醒不过来",
    "想消失", "希望我不存在", "希望自己不存在", "我不该存在",
    "想一直睡下去", "想彻底消失",
)

# ---- 否定守卫:排除"不想死/不会自杀"这类表述求生的话 ----
# 只在命中短语邻域(左 6 / 右 2 字)找否定线索,不做整句分析;
# 不认"但是/可是"这类转折;第三人称不适用本守卫。
_NEG_CUES = (
    "不想死", "不想自杀", "不想去死", "不会死", "不会自杀", "不会去死",
    "没有想死", "没想死", "没有想过自杀", "没想过自杀", "不敢死", "不敢自杀",
    "不自杀", "不会做傻事", "不想做傻事",
)
# 短语左侧要看多远:必须覆盖 "没有想过自杀" 落在 "自杀" 左侧的全部字符
_NEG_LEFT = 6
_NEG_RIGHT = 2


def _negated(text: str, idx: int, plen: int) -> bool:
    """命中短语周围是否有明确的否定线索(仅邻域窗口,不做整句否定分析)。"""
    lo = max(0, idx - _NEG_LEFT)
    hi = min(len(text), idx + plen + _NEG_RIGHT)
    window = text[lo:hi]
    return any(cue in window for cue in _NEG_CUES)


# 强度助推词(不包含"很",避免"很大"这类中性词组被放大)
_BOOSTERS = ("特别", "非常", "超级", "极其", "太", "真的")


def _count_phrases(text: str, phrases: Tuple[str, ...]) -> int:
    """统计**计入证据**的短语命中数。

    长短语优先,避免嵌套短语重复计数(例:'压力很大' 命中后,内部的
    '压力' 不再二次计入);每个命中先过 structure 结构判定,
    被否定/他人主体/转述/假设拦下的不计入。
    """
    t = str(text)
    cnt = 0
    occupied = []  # 已被更长命中占据的 (start, end) 区间
    for p in sorted({p for p in phrases if p}, key=len, reverse=True):
        start = 0
        while True:
            idx = t.find(p, start)
            if idx == -1:
                break
            start = idx + len(p)
            if any(idx < e and idx + len(p) > s0 for s0, e in occupied):
                continue
            if not structure.is_evidence(t, idx, idx + len(p)):
                continue
            occupied.append((idx, idx + len(p)))
            cnt += 1
    return cnt


def _hit_any(text: str, phrases: Tuple[str, ...],
             crisis_guard: bool = False) -> bool:
    """短语表中是否有**可作为证据**的一条命中。

    crisis_guard=True 用于危机词:只做否定撤回(原 _NEG_CUES 特例 +
    structure 通用否定辖域),主体/转述/假设不拦 —— 危机召回宁误触不漏报。
    其余用途走 structure 全特征过滤。
    """
    t = str(text)
    for p in sorted(set(phrases), key=len, reverse=True):
        start = 0
        while True:
            idx = t.find(p, start)
            if idx == -1:
                break
            start = idx + len(p)
            if crisis_guard:
                if _negated(t, idx, len(p)):
                    continue
                if structure.flags_for(t, idx, idx + len(p))["negated"]:
                    continue
                return True
            if not structure.is_evidence(t, idx, idx + len(p)):
                continue
            return True
    return False


def _scan_ideation(text: str) -> tuple:
    """被动意念族 → (硬命中, 间歇限定命中)。

    两路来源合流:固定说法表 + 「活着…没(什么)意思/意义/盼头」语义框架。
    同一处命中会被两条规则各数一次,故先把**位置相近**的命中合并成一"处" ——
    否则一句话会被算成多处意念,凭空升档。
    """
    spans = []
    for p in _IDEATION_PHRASES:
        start = 0
        while True:
            idx = text.find(p, start)
            if idx == -1:
                break
            spans.append((idx, len(p)))
            start = idx + len(p)
    for m in _RE_IDEATION.finditer(text):
        spans.append((m.start(), m.end() - m.start()))

    hits = []
    for idx, plen in sorted(spans):
        if hits and idx - hits[-1] < 6:          # 同一处,只留覆盖最宽的那条
            hits[-1] = max(hits[-1], idx)
        else:
            hits.append(idx)

    hard = hedged = False
    for idx in hits:
        if _negated(text, idx, 6):
            continue
        if _hedged(text, idx):
            hedged = True
        else:
            hard = True
    return hard, hedged


def _severity_once(text: str) -> tuple:
    """三族判据的单遍扫描(给定文本,不做 squeeze 变体)。"""
    hard = (_hit_any(text, _CRISIS_HARD, crisis_guard=True)
            or _hit_frame(text, _RE_MEANS, crisis_guard=True))
    id_hard, id_hedged = _scan_ideation(text)
    despair_hard = (_hit_any(text, _DESPAIR_PHRASES)
                    or _hit_frame(text, _RE_DESPAIR))
    return (hard or id_hard), id_hedged, despair_hard


def _severity(text: str) -> tuple:
    """三族判据 → (危机/意念硬命中, 间歇性被动意念, 绝望)。

    squeeze 剥副词与原文双路扫描取并集:剥词让"短语内插副词"命中,
    原文兜住"短语自带程度副词被误剥"的情形(如「活着太累」剥掉「太」后
    不再匹配任何词条)。
    """
    t = str(text or "")
    sq = _severity_once(_squeeze(t))
    raw = _severity_once(t)
    return ((sq[0] or raw[0]), (sq[1] or raw[1]), (sq[2] or raw[2]))


def _soft_hit(text: str) -> bool:
    """soft 族命中判定:死亡意象词表 + 假设性死亡框架,classify 与
    crisis_level 共用这一份实现。"""
    return (_hit_any(text, _CRISIS_SOFT, crisis_guard=True)
            or _hit_frame(text, _RE_SOFT, crisis_guard=True))


def crisis_level(text: str) -> str | None:
    """返回 "hard" / "soft" / None。**唯一**的危机分级入口。

    hard 优先:一句话同时含"想死"和"遗书"时按 hard 处理(取更严的一档);
    无间歇限定的被动意念(「活着没意思」)同样按 hard 处理。
    """
    t = str(text or "")
    if not t.strip():
        return None
    hard, _id_hedged, _despair = _severity(t)
    if hard:
        return "hard"
    return "soft" if _soft_hit(t) else None


def classify(text: str) -> dict:
    """对单条用户文本做确定性粗分类。

    返回 {emotion, intensity, negative_score, is_user_dangerous,
          crisis_level, ideation, despair, description}
    """
    text = str(text or "").strip()
    counts = {label: _count_phrases(text, ps) for label, ps in _EMO_PHRASES}
    if not any(counts.values()):
        label = "neutral"
    else:
        label = max(counts, key=counts.get)
    neg_count = counts.get(label, 0)

    hard, id_hedged, despair = _severity(text)
    level = "hard" if hard else ("soft" if _soft_hit(text) else None)
    danger = level is not None

    neg = _BASE_NEG.get(label, 0.30)
    # 单次弱命中降权(一句"有点累"不该升档);多命中微升。
    # 降权走 _single_hit_neg,与契约常量 SINGLE_HIT_NEG_MIN 同一份实现。
    if neg_count == 1:
        neg = _single_hit_neg(label)
    elif neg_count >= 2:
        neg = min(0.95, neg + 0.05 * min(neg_count - 1, 3))
    # 无望/放弃类兜底:未被三族判据捕获的孤立命中仍抬到 high 档(0.72)
    if _hit_any(text, _DESPAIR_PHRASES):
        neg = max(neg, 0.72)
    # 助推词与情绪化标点微调
    if any(b in text for b in _BOOSTERS):
        neg = min(0.95, neg + 0.08)
    if text.count("！") + text.count("!!!") > 0:
        neg = min(0.95, neg + 0.05)
    if label in ("happy", "hopeful"):
        neg = min(neg, 0.45)
    # 轻描淡写的自限标志:只在**完全没有**安全类信号时把分压回 low。
    if not (danger or id_hedged or despair) and any(c in text for c in _MILD_CUES):
        neg = min(neg, _MILD_NEG_CAP)

    # 强度:中性弱,负面按档;助推/标点再抬
    intensity = 0.25 if label == "neutral" else 0.55
    if neg_count >= 2:
        intensity = min(1.0, intensity + 0.15)
    if any(b in text for b in _BOOSTERS):
        intensity = min(1.0, intensity + 0.15)
    if text.count("！") > 0:
        intensity = min(1.0, intensity + 0.1)
    # 意念/绝望档:情绪强度不该停在"普通难过"的 0.55(它会被趋势层当弱信号)
    if danger or id_hedged or despair:
        intensity = max(intensity, 0.8)

    # 危机/意念/绝望句不给情绪描述(label 常为 neutral,会被误读成"平静")。
    description = "" if (danger or id_hedged or despair) else f"{_ZH.get(label, label)}情绪"

    return {
        "emotion": label,
        "intensity": round(intensity, 3),
        "negative_score": round(neg, 3),
        "is_user_dangerous": bool(danger),
        "crisis_level": level,
        # 中间档信号:被动意念 / 绝望。只用于风险分档,绝不进任何对外文案。
        "ideation": bool(id_hedged),
        "despair": bool(despair),
        "description": description,
    }


def risk_from(classified: dict) -> Tuple[str, float]:
    """粗风险档:crisis > high(意念/绝望 0.85) > high(强负向 ≥0.8) > medium(≥0.5) > low。

    「意念 / 绝望」单独占一档(0.85)而不是并进危机:
    危机档会短路到 handle_crisis 固定协议(热线 + 固定话术),用在一句
    「撑不下去了,不想再努力」上就是**协议过重**;而按情绪分又只能到 medium,
    于是在「普通难过」和「立刻干预」之间没有台阶 —— 正是实机暴露的塌陷。
    """
    if classified.get("is_user_dangerous"):
        return "crisis", 1.30
    if classified.get("ideation") or classified.get("despair"):
        return "high", 0.85
    neg = float(classified.get("negative_score") or 0.0)
    if neg >= 0.80:
        return "high", 0.90
    if neg >= 0.50:
        return "medium", 0.55
    return "low", 0.15
