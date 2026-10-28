# -*- coding: utf-8 -*-
"""主动关怀·内容层:把关怀决策转成一条 LLM 生成的口语化主动消息(无模板兜底)。

设计(方针见 PET_POLICY.md):
  - **每一条主动消息都必须跑一次 LLM**。所有 kind 的文案统一由 LLM 按**角色卡**
    组织语言(提示词见 prompts/care_prompts.py + pet/character/prompts.py 的
    卡可覆盖提示词集),不使用模板兜底 —— LLM 不可用/输出违规时该次主动消息
    【静默丢弃】(由调用方 coordinator 记冷却,不占当日额度);
  - 安全由两层保证:提示词层硬约束(care_prompts 的 safety_tail) + 本模块确定性
    禁词过滤(过滤不通过 → 返回 None,绝不让违规文本流向用户);
  - 本模块**不持有任何话术文本**:probe 的维度示例话术在角色卡可覆盖的
    提示词集里(``DEFAULTS["probe_examples"]``),角色卡可逐键替换。
"""
import logging
import re
from typing import Optional

from gzhu_cup2.agent.care.policy import CareKind
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt, probe_context_text

logger = logging.getLogger(__name__)

# ---- LLM 生成函数挂点:便于测试替换 / 降级 ----
_llm_generate = None

# ---- 禁词过滤:命中任一项 → 该条消息作废(返回 None)----
# 含内部标记 / 临床术语 / 评分索取 / 数字百分比等。
_FORBIDDEN_PATTERNS = (
    r"PHQ|GAD|C-?SSRS|BDI|SDS|SCL",          # 量表名
    r"抑郁(?:症|指数|风险)?|焦虑(?:症|指数|风险)?|心理(?:风险|指数)",  # 诊断/风险词
    r"风险(?:档|级|指数|分|等级|级别)?|危险(?:级|指数)",
    r"监测|检测到?|评估结果|诊断|病情|症状评分|量表|条目",
    r"\d+(?:\.\d+)?\s*%|(?:分(?:数|值|档)?\s*[:：为是]\s*\d)|打\s*分",
    r"low|medium|high|crisis|elevated|watch|stable",  # 内部档位(英文防漏)
    r"risk[_-]?level|tier|score",                     # 内部字段名
)
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)

# 数字百分比放行例外:像 "3% 的糖" 之类正常话不常见,保持从严。


def _generate_llm_text(messages: list) -> str:
    """默认实现:调配置模型,返回纯文本回复。"""
    from gzhu_cup2.agent.core.models.LLM_base import get_llm
    llm = get_llm(max_tokens=cfg.CARE_MESSAGE_MAX_TOKENS)
    msg = llm.invoke(messages)
    return str(getattr(msg, "content", msg) or "").strip()


def _clean(text: str) -> str:
    """压缩空白并截断(防模型输出冗长/换行堆叠);上限见 cfg.CARE_MESSAGE_MAX_LEN。"""
    s = " ".join(str(text or "").split())
    return s[: cfg.CARE_MESSAGE_MAX_LEN]


def _forbidden(text: str) -> bool:
    """确定性禁词扫描:命中任一模式 → True(该条作废)。"""
    return bool(_FORBIDDEN_RE.search(text or ""))


def _probe_context_text(dim: Optional[str]) -> str:
    """probe 的维度上下文:委托给角色卡可覆盖的提示词集(本模块不再持有话术)。"""
    return probe_context_text(dim)


def _opening(text: str, n: int = 6) -> str:
    """取开头 n 个字(去空白/标点),用于判断两条消息是不是同一个开场。"""
    s = re.sub(r"[\s,，。!！?？~～、…·—\-]+", "", str(text or ""))
    return s[:max(1, int(n))]


# ---- 角度去重 ----
# 开场去重只比对开头文本,拦不住"换措辞、同角度"的复读;
# 这里按角度去重:最近两条主动消息用过的角度,本次不再使用。
_ANGLE_WORDS = {
    "熬夜/时间": (
        "还没睡", "还不困", "还醒着", "这么晚", "熬夜", "夜深", "半夜",
        "凌晨", "过零", "不困", "困了", "犯困", "早点睡", "该睡",
        # "都这个点了" 是最常见的变体:不含"晚"字,但仍是同一个角度
        "这个点", "几点了", "这个钟点",
    ),
}
# 钟点表达单独用正则:穷举"十二点/零点半/3点"既写不完也容易漏。
# 必须**前面有数字** —— 否则「我点了一杯茶」会被误判成时间角度。
_ANGLE_REGEX = {
    "熬夜/时间": (r"[零一二三四五六七八九十两\d]{1,3}\s*点(?:半|多|钟)?",),
}
_ANGLE_RE = {name: [re.compile(p) for p in pats] for name, pats in _ANGLE_REGEX.items()}

# checkin 不带话题(有话题就走 recall 了),文案里任何"你上次说的那件事"
# 都没有出处,属于疑似虚构;命中这些措辞的文案按编造处理。
_VAGUE_REF = ("那个事", "那件事", "你说的那件", "你上次说的", "你上次提的",
              "之前说的那件", "之前提的那件")


def _angles(text: str) -> set:
    """这条消息落在哪些"角度"上(用于判断是不是换个说法重复同一件事)。"""
    s = str(text or "")
    out = {name for name, keys in _ANGLE_WORDS.items() if any(k in s for k in keys)}
    for name, regs in _ANGLE_RE.items():
        if any(r.search(s) for r in regs):
            out.add(name)
    return out


def _recent_angles(recent) -> list:
    """最近三条消息用过的角度。

    取 3 而不是 2:回看窗口过短时,连发消息会绕回更早用过的角度,放过复读。
    """
    used = set()
    for t in list(recent or ())[:3]:
        used |= _angles(str(t))
    return sorted(used)



def generate_message(kind: CareKind, topic: Optional[str] = None,
                     warm_facts: Optional[dict] = None,
                     extra: Optional[dict] = None) -> Optional[str]:
    """LLM 生成一条主动消息;不可用/违规 → None(调用方丢弃该次)。

    参数:
      kind       checkin / recall / petlife / soothe / probe / warm
      topic      recall:用户安全话题;probe:维度 key;petlife:角色今日叙事
      warm_facts warm:布尔观察 {"mood_down": bool, "quieter": bool}
      extra      反重复素材(下划线键):``_recent`` 最近说过的话、
                 ``_since_user_minutes`` 距上次对话的分钟数、
                 ``_last_user`` 用户最后说的一句话(只作接话素材)
    """
    context: dict = {}
    if kind == "probe":
        ctx = _probe_context_text(topic)
        if ctx:
            context["topic"] = ctx
    elif kind in ("recall", "petlife") and topic:
        context["topic"] = str(topic).strip()
    elif kind == "warm":
        facts = warm_facts or {}
        if not (facts.get("mood_down") or facts.get("quieter")):
            return None  # warm 没有任何观察事实 → 不生成(不编造)
        context["mood_down"] = bool(facts.get("mood_down"))
        context["quieter"] = bool(facts.get("quieter"))
    if extra:
        context.update({k: v for k, v in extra.items() if str(k).startswith("_")})

    recent = [str(x) for x in (extra or {}).get("_recent") or () if str(x).strip()]
    banned = _recent_angles(recent)
    if banned:
        context["_ban_angles"] = banned

    gen = _llm_generate or _generate_llm_text
    try:
        raw = _clean(gen(build_care_llm_prompt(kind, context=context or None)))
    except Exception as e:  # noqa: BLE001 - LLM 故障 = 该次静默丢弃
        logger.warning("care LLM 生成失败 kind=%s: %s", kind, e)
        return None
    if not raw or _forbidden(raw):
        if raw:
            logger.warning("care 生成命中禁词,丢弃 kind=%s", kind)
        return None

    # 复读拦截:开场撞车 **或** 落在最近说过的角度上 → 带明确指令重试一次。
    # 提示词已经给了"最近说过什么",但模型仍会复读(短问候尤其明显);
    # 代码这层兜底比反复调提示词可靠。
    retry_ctx = dict(context)
    retry = False
    if recent and _opening(raw) and any(_opening(raw) == _opening(r) for r in recent):
        retry_ctx["_avoid"] = _opening(raw)
        retry = True
    repeat_angles = sorted(_angles(raw) & set(banned))
    if repeat_angles:
        retry_ctx["_avoid_angle"] = repeat_angles
        retry = True
    vague = bool(kind == "checkin" and any(k in raw for k in _VAGUE_REF))
    if vague:
        retry_ctx["_avoid_vague"] = True
        retry = True
    if retry:
        try:
            raw2 = _clean(gen(build_care_llm_prompt(kind, context=retry_ctx)))
        except Exception as e:  # noqa: BLE001 - 重试失败就沿用第一版
            logger.warning("care 重试生成失败 kind=%s: %s", kind, e)
            raw2 = ""
        # 只有"确实换掉了角度、也没编造指代"才采纳重试结果:否则第一版至少是条完整消息,
        # 用户点了按钮却什么都不发,比重复更糟。
        if raw2 and not _forbidden(raw2) \
                and not (_angles(raw2) & set(repeat_angles)) \
                and not (vague and any(k in raw2 for k in _VAGUE_REF)):
            raw = raw2
    return raw


# ============================ 日程提醒 ============================
# 与主动关怀共用模型调用、清洗与禁词过滤;返回 None 表示应退模板。
_REMINDER_MAX_LEN = 60


def generate_reminder(title: str, when_text: str = "") -> Optional[str]:
    """以角色卡口吻生成一句日程提醒;LLM 不可用或输出违规 → None。"""
    topic = " ".join(str(title or "").split())
    if not topic:
        return None
    if when_text:
        topic = "%s(定在 %s)" % (topic, when_text)

    gen = _llm_generate or _generate_llm_text
    try:
        raw = _clean(gen(build_care_llm_prompt("reminder", context={"topic": topic})))
    except Exception as e:  # noqa: BLE001 - LLM 故障由调用方退模板
        logger.warning("日程提醒生成失败: %s", e)
        return None
    if not raw:
        return None
    if _forbidden(raw):
        logger.warning("日程提醒生成命中禁词,丢弃")
        return None
    return raw[:_REMINDER_MAX_LEN]
