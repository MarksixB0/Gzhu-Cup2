# -*- coding: utf-8 -*-
"""事实近似度判定(确定性纯函数:零 IO、零 LLM、可离线穷举单测)。

用途:把「同一件事的不同措辞」合并成一条,避免读侧配额被近义重复项占满。

口径:
  - 双字 Jaccard 与包含度双阈值同时满足才合并;
  - 包含度识别"后者是前者的超集",同时挡住只差一个实词的表述;
  - 极性守卫:一方"好转"、另一方"加重"时不合并。

字符 n-gram 不识别换词型同义改写;只差虚词/语序的表述由
`function_word_only_diff` 单独处理。
"""
import re

from gzhu_cup2.agent.config import config as cfg

# 参与比较前去掉的字符:空白 + 中英文标点(标点变化不代表语义变化)
_PUNCT = set(
    "，。！？、；：“”‘’（）《》〈〉【】…—～·"
    ",.!?;:\"'()[]{}<>-_/\\|@#$%^&*+=~` "
)
_WS = re.compile(r"\s+")


def normalize(text) -> str:
    """规范化:压缩空白、去掉标点 —— 只保留语义字符。"""
    t = _WS.sub("", str(text or ""))
    return "".join(ch for ch in t if ch not in _PUNCT)


def _grams(text: str, n: int) -> frozenset:
    s = normalize(text)
    if not s:
        return frozenset()
    if len(s) < n:
        return frozenset([s])
    return frozenset(s[i:i + n] for i in range(len(s) - n + 1))


def jaccard(a, b, n: int = 2) -> float:
    """字符 n-gram 的 Jaccard 相似度(交集/并集)。"""
    A, B = _grams(a, n), _grams(b, n)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def containment(a, b, n: int = 2) -> float:
    """字符 n-gram 包含度(交集/较小者):识别"一条是另一条的超集"。"""
    A, B = _grams(a, n), _grams(b, n)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def similarity_score(a, b) -> float:
    """单一排序分(用于在多条候选中挑最像的一条)。"""
    return jaccard(a, b, 2)


# ============================== 极性守卫 ==============================
# 方向词:出现即代表"这条事实讲了变化方向",合并前必须两边方向一致。
_IMPROVE_WORDS = (
    "好转", "变好", "改善", "缓解", "减轻", "恢复", "好多了", "好一些",
    "不再", "没了", "消失", "稳定了", "睡得着", "入睡好",
)
_WORSEN_WORDS = (
    "加重", "严重", "恶化", "更差", "变差", "加剧", "越来越",
    "总是", "一直", "频繁", "整晚", "通宵",
)


def direction(text) -> int:
    """粗方向:改善=-1,恶化=+1,未提及方向=0。"""
    t = str(text or "")
    if any(w in t for w in _IMPROVE_WORDS):
        return -1
    if any(w in t for w in _WORSEN_WORDS):
        return 1
    return 0


def polarity_conflict(a, b) -> bool:
    """两条事实方向是否不一致 —— 不一致就不允许合并。

    例:「不再失眠」(-1) 与「失眠严重」(+1) 字面很像但语义相反;
        「有失眠困扰」(0) 与「失眠加重了」(+1) 也不该糊成一条(会丢严重度)。
    """
    return direction(a) != direction(b)


# ============================== 判定与去重 ==============================
DEFAULT_BI_JACCARD = 0.55           # 双字 Jaccard 阈值
DEFAULT_BI_CONTAINMENT = 0.90       # 双字包含度阈值


# ============================== 虚词/语序差异(范畴式判据) ==============================
# 做法:剥掉虚词后比较内容字的多重集合(对语序不敏感);否定模式不一致则拒绝。
_FUNCTION_CHARS = frozenset(
    "的了着过是在有和与及以及之其即将会要可就都也很还又而但却并把被给对从向往"
    "一两个名叫作做于当为所等些这那些只位名份次"
    # 程度/时间副词,对"是否同一件事"不构成差异,一并剥离
    "最近今天前目前正比较太更挺蛮超极后"
)
# 否定字不按普通虚词剥离;判据是两侧否定模式必须一致。
_NEGATION_CHARS = frozenset("没不无未别非莫勿否")
_MIN_CONTENT_LEN = 2


def content_multiset(text) -> str:
    """剥掉虚词后的内容字多重集合(排序后拼接),故对语序不敏感。

    用多重集合而非去重集合,以保留"弟弟"这类重复实词的数量信息。
    """
    s = normalize(text)
    kept = [ch for ch in s if ch not in _FUNCTION_CHARS]
    return "".join(sorted(kept))


def _negation_pattern(text) -> tuple:
    """否定模式:出现的否定字(去重、排序)。

    只比较模式是否一致 —— "出现即拒绝"会误伤「什么都提不起劲 / 对什么都提不起劲」。
    """
    return tuple(sorted({ch for ch in normalize(text) if ch in _NEGATION_CHARS}))


def function_word_only_diff(a, b) -> bool:
    """两条是否「只差虚词/语序」(纯函数;调用方仍需自行做极性守卫)。

    判据:否定模式一致,且剥虚词后的内容多重集合完全相同、长度达标。
    实词取值不同(辣/甜、猫/狗、咨询师/老师)时返回 False。
    不覆盖换词型同义改写与近义动词替换。
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if _negation_pattern(a) != _negation_pattern(b):
        return False
    ca, cb = content_multiset(a), content_multiset(b)
    if len(ca) < _MIN_CONTENT_LEN or len(cb) < _MIN_CONTENT_LEN:
        return False
    return ca == cb


def is_similar(a, b,
               bi_jaccard: float = DEFAULT_BI_JACCARD,
               bi_containment: float = DEFAULT_BI_CONTAINMENT) -> bool:
    """两条文本是否视为"极为相近"(可安全合并)。两条通道取"或":

      1. 字符 n-gram 双阈值(高精度);
      2. 虚词/语序差异通道(由 cfg.MEMORY_FACT_FUNCTION_WORD_MERGE 控制,默认开)。

    本函数**不含**极性判断,调用方需先用 polarity_conflict 把关。
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if (jaccard(na, nb, 2) >= bi_jaccard
            and containment(na, nb, 2) >= bi_containment):
        return True
    if getattr(cfg, "MEMORY_FACT_FUNCTION_WORD_MERGE", True) and function_word_only_diff(a, b):
        return True
    return False


def dedupe(items, key_fn, **kwargs) -> list:
    """列表内近义去重(保序):同一簇只留一条代表。

    代表 = 该簇中规范化后最长的条目(信息最多),位置沿用首次出现处。
    """
    kept: list = []          # [[item, norm_text], ...]
    for it in items or []:
        n = normalize(key_fn(it))
        if not n:
            continue
        for slot in kept:
            if not polarity_conflict(slot[1], n) and is_similar(slot[1], n, **kwargs):
                if len(n) > len(slot[1]):
                    slot[0], slot[1] = it, n
                break
        else:
            kept.append([it, n])
    return [it for it, _ in kept]
