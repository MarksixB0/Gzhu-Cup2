# -*- coding: utf-8 -*-
"""structure 组件:词表命中的结构特征判定(纯函数、零 IO、零 LLM)。

它不自己打分,只回答一个问题:**这个命中,能不能算说话人自己的、
直陈的、当下的事实?**

四特征:
  negated        否定辖域 ——「我不难过」「我没有不开心」
  other_subject  主体归属他人 ——「我室友很难受」「她天天失眠」
  reported       转述 ——「我妈说我看起来很累」
  hypothetical   假设/情态 ——「万一我真撑不住怎么办」「要是考砸了」

消费口径(写侧词表打分与危机召回层都走这里):
  · 情绪词表/行为词表:四特征任一命中 → 该命中不计入证据;
  · 危机词:**只看 negated**。主体/转述/假设不拦危机 ——
    身边人的危机信号或假设性表达宁可误触 soft 协议,不冒漏报的险。
  · 宁漏勿错:线索不明确时一律放行(判为可用证据)。
"""
import re

# ---- 判定窗口:命中词左侧最多看多少字 ----
_LEFT = 12

# ---- 否定线索 ----
# 多字线索:出现在左窗口任意位置即算(语义完整,不怕误伤)
_NEG_LONG = (
    "没有", "不是", "并不", "根本不", "完全不", "从来不", "从没",
    "谈不上", "算不上", "称不上", "无所谓", "并不觉得", "没觉得",
    "不觉得", "没感到", "没感觉到", "没怎么", "没那么", "毫无", "毫不",
    "一点也不", "一点都不", "并没有", "没什么",
)
# 单字否定:只允许紧邻命中词(间隔 ≤1 字,容纳"太/真/好"等衬字),
# 覆盖「不难过」「没难过」「不太难过」;隔得远的单字不采(避免误伤)。
_NEG_SHORT = ("不", "没", "无")

# ---- 他人指示(主体归属) ----
# 人称 + 关系词;最长优先,防止"我妈"被拆成"我"。
_OTHERS = (
    "他们", "她们", "她们", "男朋友", "女朋友", "前男友", "女朋友",
    "老公", "老婆", "我妈", "我爸", "我妈", "爸妈", "父母", "妈妈", "爸爸",
    "哥哥", "姐姐", "弟弟", "妹妹", "奶奶", "爷爷", "外婆", "外公",
    "室友", "同学", "朋友", "闺蜜", "同事", "老师", "导师", "领导",
    "医生", "家人", "亲戚", "邻居", "别人", "大家", "有人", "他", "她", "它",
)
# 单字人称豁免上下文:「其他」「其它」中的他/它不是人称
_SINGLE_OWNERSHIP_PRECEDING = "其"
# 并列连词:他人锚点紧邻左侧出现 → 可能是省略主语的"（我）跟X"结构
_CONJ_TAIL = ("跟", "和", "与", "同")

# ---- 转述句式:他人言者 + 言说动词(在命中词左侧) ----
_REPORT_RE = re.compile(
    r"(妈|爸|他|她|医生|老师|同学|朋友|室友|大家|领导|闺蜜|家人|爸妈|父母|导师)"
    r"(们)?(都)?(一直|总是|老是|经常)?(说|讲|跟我|告诉我|觉得|认为|念叨|吐槽)"
)

# ---- 假设/情态线索 ----
# 不收"怎么办":「我该怎么办」是求助不是假设。
_HYPO = (
    "万一", "要是", "假如", "如果", "会不会", "怕是",
    "说不定", "搞不好", "恐怕",
)


def flags_for(text: str, start: int, end: int) -> dict:
    """对文本中 [start, end) 的一个词表命中判定四特征。

    返回 {negated, other_subject, reported, hypothetical},布尔值。
    """
    t = str(text or "")
    lo = max(0, start - _LEFT)
    left = t[lo:start]

    # 1) 否定:多字线索任意位置;单字线索须紧邻(允许 ≤1 字衬字间隔)
    negated = any(c in left for c in _NEG_LONG)
    if not negated and t[start:start + 1] not in _NEG_SHORT:
        tail = left.rstrip("太真好也")
        # 紧邻判定:去掉衬字后,命中词前最后一个字符是否为单字否定
        if tail and tail[-1] in _NEG_SHORT:
            negated = True

    # 2) 转述:他人言者 + 言说动词(优先于主体归属,防止引语内的"我"干扰)
    reported = bool(_REPORT_RE.search(left))

    # 3) 主体归属:左窗口内最靠近命中词的他人锚点是否为有效主语。
    #    并列句("我和我妈都…")由两处保护放行:锚点左侧的"我和X"结构、
    #    锚点与命中词之间出现的独立"我"。
    other_subject = False
    if not reported:
        anchor = -1
        for w in _OTHERS:
            p = left.rfind(w)
            if p <= anchor:
                continue
            # 单字人称豁免:「其他」「其它」里的他/它不是人称
            if w in ("他", "她", "它") and p > 0 and left[p - 1] in _SINGLE_OWNERSHIP_PRECEDING:
                continue
            anchor = p
        if anchor != -1:
            before = left[:anchor]
            between = left[anchor + 1:]
            # 并列保护:锚点左侧是"我和X"或省略主语的"跟X/和X/与X"
            # (中文"跟室友闹掰了"默认"我跟室友",用户是卷入方)。
            # 注意"我X"(「我室友」= 我的室友)是从属不是并列,不保护。
            stem = before
            for c in _CONJ_TAIL:
                if stem.endswith(c):
                    stem = stem[:-len(c)]
                    conj = True
                    break
            else:
                conj = False
            if ("我" in between
                    or (conj and (stem == "" or stem.endswith("我")))
                    or before.endswith(("我和", "我跟", "我与"))):
                other_subject = False
            else:
                other_subject = True

    # 4) 假设/情态:左窗口出现假设词即算(整句级语气,不做辖域切分)
    hypothetical = any(h in left or h in t[start:end] for h in _HYPO)

    return {"negated": negated, "other_subject": other_subject,
            "reported": reported, "hypothetical": hypothetical}


def is_evidence(text: str, start: int, end: int) -> bool:
    """该命中能否计入情绪/行为证据:四特征任一为真 → 不能。"""
    f = flags_for(text, start, end)
    return not (f["negated"] or f["other_subject"]
                or f["reported"] or f["hypothetical"])


def scan(text: str) -> dict:
    """整句级结构标记(供 meta 落库与排障面板)。

    与 flags_for 的区别:这是"这句话里有没有出现这类结构",
    不是针对某个具体命中;逐命中的过滤一律走 flags_for/is_evidence。
    """
    t = str(text or "").strip()
    if not t:
        return {"negated": False, "other_subject": False,
                "reported": False, "hypothetical": False}
    negated = any(c in t for c in _NEG_LONG)
    reported = bool(_REPORT_RE.search(t))
    other_subject = any(w in t for w in _OTHERS) and not reported
    hypothetical = any(h in t for h in _HYPO)
    return {"negated": negated, "other_subject": other_subject,
            "reported": reported, "hypothetical": hypothetical}
