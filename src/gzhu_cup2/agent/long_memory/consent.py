# -*- coding: utf-8 -*-
"""记忆知情同意:把用户在对话里表达的隐私意愿,翻译成后端已有的动作。

两级识别:
- 规则层 ``rule_intents``:高精度、零成本、离线可用,只认中文直白祈使写法;
- 模型层 ``_llm_intents``:规则未命中且文本沾话题边时调一次小模型做结构化判断,
  模型不可用时回落规则结果。

接线在 ``nodes/receive_input.py``,早于本轮生成与捕获;暂停记录由
``nodes/capture_memory.py`` 的早退生效。

覆盖范围:只认中文;"别记"只停未来写入,已固化内容需走 ``DELETE /api/memory/*``。
"""
import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from gzhu_cup2.agent.long_memory import privacy as mprivacy
from gzhu_cup2.agent.long_memory import store as mem_store

logger = logging.getLogger(__name__)

PAUSE = "pause"
RESUME = "resume"
FORGET = "forget"

# 忘记 / 删除类动词需带宾语才算意愿(避免"忘了吧"这类安慰语被误判)。
_OBJECT = (r"(?:刚才|刚刚|之前|上面|那段|这段|这些|那些|你说的|我说的|"
           r"我说过的话|说过的话|的话|说的|话|记忆|记录|对话|内容|事)")

_PAUSE_RE = re.compile("|".join((
    # 别/不要/不用(+给我/你/帮我) + 记|记录|存 + 补语。
    # 不含裸"写":"不要写了"是让 AI 停笔;写进记忆由下一条模式覆盖。
    r"(?:别|不要|不用|不需要|别再)(?:再)?(?:给我|你|帮我)?(?:记|记录|存)"
    r"(?:下来|下|住|录|了|进去|进来)",
    # 不要/别 把 … 记/存/写 下来
    r"(?:不要|别|不用)(?:把|将)[^。！？!?]{0,12}(?:记|存|写)(?:下来|下|住|进|入|到)",
    # 不/别 记/记录/存/写 进(长期)记忆
    r"(?:不|别)(?:要)?(?:记|记录|存|写)(?:进|入|到)(?:长期)?记忆",
    # 别/不要 记录/记忆 了
    r"(?:别|不要|不用)(?:再)?(?:记录|记忆)(?:了|我的)?",
    # 别/不要 记得（"这件事不要记得"）
    r"(?:别|不要|不用)(?:再)?记得",
)))

# 恢复类:必须带"了/吧/下来"这类收束,或直接跟"记录/记忆"。
_RESUME_RE = re.compile("|".join((
    r"(?:可以|能)(?:再)?(?:记录|记忆|记|存)(?:了|吧|下来)",
    r"(?:继续|恢复|重新|开始)(?:再)?(?:记录|记忆|记|存)",
    r"(?:打开|开启|恢复)(?:记录|记忆)",
)))

_FORGET_RE = re.compile("|".join((
    r"(?:忘掉|忘记|忘了|删掉|删了|删除|抹掉|清空)" + _OBJECT,
    r"(?:把|将)[^。！？!?]{0,10}?(?:忘掉|忘记|忘了|删掉|删除|抹掉)",
    r"(?:删除|清空|清除|抹掉)(?:所有|全部|我的)?(?:记忆|记录)",
)))

# 排除"我不用记/背了"这类讲自己的说法;中间隔了"你"的仍算暂停。
_SELF_NOT_NEEDED_RE = re.compile(
    r"我(?:也|都|还|就)?(?:不用|不需要|不必)(?:再)?(?:记|背)(?:了|这些|这个|这么多)?"
)


def _self_excluded(text: str, pos: int) -> bool:
    """命中的否定词是否属于"我不用记…"这种**用户自称**的句子。"""
    for m in _SELF_NOT_NEEDED_RE.finditer(text):
        if m.start() <= pos <= m.end() + 2:
            return True
    return False


def _pause_hit(text: str) -> bool:
    for m in _PAUSE_RE.finditer(text):
        if not _self_excluded(text, m.start()):
            return True
    return False


def rule_intents(text: str) -> tuple:
    """确定性规则层:高精度、零成本、离线可用,只认直白祈使写法。"""
    found = []
    if _pause_hit(text):
        found.append(PAUSE)
    if _FORGET_RE.search(text):
        found.append(FORGET)
    if _RESUME_RE.search(text):
        found.append(RESUME)
    return tuple(found)


# ---- 模型兜底层:规则漏掉的写法交给模型 ----
# 规则命中即短路;其余先过 _CUE_RE 粗筛;模型不可用时报错则回落规则结果。
_CUE_RE = re.compile(r"记|忘|删|清空|清除|隐私|记忆|留|存|撤|抹|秘密|别人知道")


class PrivacyIntent(BaseModel):
    """结构化输出:用户这一句是否在要求系统改变记忆行为。"""

    stop_recording: bool = Field(
        default=False, description="用户要求停止或暂停记录他说的内容/他的记忆")
    resume_recording: bool = Field(
        default=False, description="用户要求恢复记录")
    forget_recent: bool = Field(
        default=False, description="用户要求删除或忘掉已经记下的内容")


_CLASSIFY_SYSTEM = (
    "你在判断用户这一句话**是否在要求系统改变记忆行为**。只输出结构化结果。\n"
    "宁可多判:只要用户表达了「这段别留下 / 别往心里去 / 当没说过 / 清掉 / "
    "别让人知道」这类意思,即使他没用到『记』『忘』『删』这些字,也要标出来。\n"
    "以下**不算**:① 用户在说自己记性差(『我记不住』『我忘了带钥匙』);"
    "② 用户在安慰你(『忘了吧』『别放在心上』指的是别难过,不是要删数据);"
    "③ 泛用(『记得吃饭』);④ 只是在聊记忆这个词本身。"
)

# 隐私意图识别的输出上限(思考型模型的推理 token 与 JSON 共用该额度)。
_INTENT_MAX_TOKENS = 1500


def _default_classify(text: str) -> dict:
    """默认实现:调一次小模型做结构化判断。

    温度取 0 求稳定。输出本身很小(只有三个布尔),但上限按 _INTENT_MAX_TOKENS 给足
    思考型模型的推理开销(见该常量的说明)。
    测试通过替换模块属性 ``_classify_llm`` 挂点实现离线。
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from gzhu_cup2.agent.core.models.LLM_base import get_llm, structured_llm

    llm = get_llm(temperature=0.0, max_tokens=_INTENT_MAX_TOKENS)
    result = structured_llm(llm, PrivacyIntent).invoke([
        SystemMessage(content=_CLASSIFY_SYSTEM),
        HumanMessage(content="用户这一句话是：%s" % text),
    ])
    if result is None:
        raise RuntimeError("隐私意愿判定未取得结构化结果:模型未按 schema 返回")
    return {
        PAUSE: bool(getattr(result, "stop_recording", False)),
        RESUME: bool(getattr(result, "resume_recording", False)),
        FORGET: bool(getattr(result, "forget_recent", False)),
    }


# 挂点(与 care/content.py 的 _llm_generate 同风格):测试直接替换本属性
_classify_llm = None


def _llm_intents(text: str) -> tuple:
    fn = _classify_llm or _default_classify
    try:
        flags = fn(text) or {}
    except Exception as e:  # noqa: BLE001 - 模型不可用不得影响对话与规则结果
        logger.warning("记忆隐私意愿的模型判断失败(已忽略): %s", e)
        return ()
    return tuple(k for k in (PAUSE, FORGET, RESUME) if flags.get(k))


def detect_privacy_intents(text: Optional[str]) -> tuple:
    """检出记忆隐私意愿,返回 ``("pause" / "resume" / "forget")`` 的元组。

    两级:确定性规则(高精度、离线)→ 粗筛 →模型(兜覆盖率)。
    一句话可同时含多个意愿("别记了,把刚才的删掉" → pause + forget),故返回元组。

    返回空元组 = 没有意愿(绝大多数轮次)。
    """
    t = " ".join(str(text or "").split())
    if not t:
        return ()
    found = rule_intents(t)
    if found:
        return found
    if not _CUE_RE.search(t):
        return ()          # 连话题边都没沾,不必问模型
    return _llm_intents(t)


def apply_intents(user: str, intents) -> dict:
    """执行意愿,并**如实返回实际做了什么**(供回复层如实告知用户)。

    返回::

        {"recording": bool,          # 执行后的记录开关
         "pending_turns_deleted": int,  # 实际删掉的待固化轮数
         "applied": ["pause", ...]}     # 实际执行的意愿
    """
    outcome = {
        "recording": bool(mprivacy.recording_enabled(user)),
        "pending_turns_deleted": 0,
        "applied": [],
    }
    for intent in tuple(intents or ()):
        if intent == PAUSE:
            mprivacy.set_recording(user, False)
            outcome["recording"] = False
            outcome["applied"].append(PAUSE)
        elif intent == RESUME:
            mprivacy.set_recording(user, True)
            outcome["recording"] = True
            outcome["applied"].append(RESUME)
        elif intent == FORGET:
            removed = mem_store.clear_pending(user)
            outcome["pending_turns_deleted"] += int(removed.get("turns") or 0)
            outcome["applied"].append(FORGET)
    return outcome
