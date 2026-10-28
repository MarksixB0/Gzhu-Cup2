# -*- coding: utf-8 -*-
"""主动关怀(主动消息)的提示词构造。

文本来源(PET_POLICY.md 第三节):**角色卡可覆盖的"活"配置**。
所有模板都取自 ``pet/character/prompts.py::DEFAULTS``,并可被角色卡的
``data.extensions.companion.prompts`` 逐键覆盖。本模块只做**组装**:

    「人格(角色卡) + 本次关怀意图 + 上下文」→ [SystemMessage, HumanMessage]

安全:
  - 本模块不生成安全文案,但会强制追加 ``safety_tail``(代码常量,**卡不可覆盖**);
  - 真正的兜底是 ``care/content.py`` 的确定性禁词过滤(命中即丢弃该条消息)。

文案一律用宏(``{{char}}`` / ``{{user}}``),不含角色专名。
"""
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from gzhu_cup2.agent.pet.character import loader as pcloader
from gzhu_cup2.agent.pet.character import prompts as pprompts
from gzhu_cup2.agent.pet.character import renderer as prenderer
from gzhu_cup2.agent.prompts.prompts import DEFAULT_ROLE, now_text

# 各 kind 允许携带的上下文槽位(kind -> 允许注入的字段),留在代码里而不放进角色卡。
_CONTEXT_FIELDS = {
    "recall": ("topic",),
    "probe": ("topic",),
    "petlife": ("topic",),          # topic = 角色今日生活叙事文本
    "warm": ("mood_down", "quieter"),
    "reminder": ("topic",),         # topic = 到点的那件事(标题 + 时刻)
    "checkin": (),
    "soothe": (),
}


def _bundle(card=None):
    """取 (渲染好的人格, 提示词集)。card 为 None 时读当前启用角色卡。"""
    if card is None:
        return pprompts.active()
    resolved = pcloader.ResolvedCard(card, "card", "inline", "")
    return prenderer.render_persona(resolved), pprompts.resolve(card)


def _persona_system(rendered, pset) -> str:
    """系统提示:角色设定(来自角色卡)+ 安全收尾(代码常量,不可覆盖)。"""
    role = rendered.core or DEFAULT_ROLE
    return role + "\n" + str(pset.get("safety_tail") or "")


def _context_text(kind: str, context: Optional[dict], rendered, pset) -> str:
    """把本次上下文转成人类可读片段;只放入该 kind 允许的字段,其余忽略。"""
    ctx = dict(context or {})
    allowed = _CONTEXT_FIELDS.get(kind, ())
    labels = pset.get("labels") or {}

    def _r(text, **kw):
        return pprompts.render_text(text or "", rendered, **kw)

    if "topic" in allowed and ctx.get("topic"):
        if kind == "recall":
            return _r(labels.get("recall_topic"), topic=ctx["topic"])
        if kind == "probe":
            return _r(labels.get("probe_topic"), topic=ctx["topic"])
        if kind == "petlife":
            return _r(labels.get("petlife_topic"), topic=ctx["topic"])
        if kind == "reminder":
            return _r(labels.get("reminder_topic"), topic=ctx["topic"])

    if kind == "warm":
        obs_map = pset.get("observations") or {}
        obs = []
        if ctx.get("mood_down") and obs_map.get("mood_down"):
            obs.append(_r(obs_map["mood_down"]))
        if ctx.get("quieter") and obs_map.get("quieter"):
            obs.append(_r(obs_map["quieter"]))
        if obs:
            return _r(labels.get("warm_facts"), facts="、".join(obs))
    return ""


def probe_context_text(dim: Optional[str], rendered=None, pset=None) -> str:
    """probe 的维度上下文:给 LLM 一句该维度的「示例关心话」+ 禁止直呼维度名。

    维度 → 示例话术来自提示词集(角色卡可覆盖);未登记的维度走 probe_fallback。
    """
    if rendered is None or pset is None:
        rendered, pset = _bundle()
    example = (pset.get("probe_examples") or {}).get(str(dim or ""))
    if not example:
        return pprompts.render_text(pset.get("probe_fallback") or "", rendered)
    return pprompts.render_text(
        pset.get("probe_hint") or "", rendered,
        example=pprompts.render_text(example, rendered))


def _gap_text(minutes: float) -> str:
    """把"距上次对话"写成模型能直接用的口语尺度。"""
    m = max(0.0, float(minutes))
    if m < 5:
        return "刚刚(不到 5 分钟前)"
    if m < 60:
        return "约 %d 分钟前" % int(round(m))
    if m < 48 * 60:
        return "约 %d 小时前" % int(round(m / 60.0))
    return "约 %d 天前" % int(round(m / 1440.0))


def _variety_block(ctx: dict) -> str:
    """给模型看"你最近已经说过什么",并把上一版重复的角度点出来。

    为什么必须放进提示词:每次生成都是独立调用,模型不知道上一条说了什么,
    于是连续两条问候都以「好久没见到你了」开头 —— 用户看到的不是关心,是复读。
    另一个更隐蔽的复读是**同一角度换说法**:「都过十二点了 还不困呀」→
    「嗯？还醒着呀」,字面不同、说的却是同一件事(见 care/content._ANGLE_WORDS)。
    """
    parts = []
    recent = [str(x).strip() for x in (ctx.get("_recent") or ()) if str(x).strip()]
    if recent:
        lines = ["【你最近已经说过的话(不要复用它们的开头、句式和口头禅)】"]
        lines += ["· " + r[:60] for r in recent[:2]]
        parts.append("\n".join(lines))

    banned = [str(x).strip() for x in (ctx.get("_ban_angles") or ()) if str(x).strip()]
    if banned:
        parts.append(
            "【本次不要用的角度】（这一条优先于上面任何"
            "「深夜要把时间说进去」之类的要求）\n"
            + "、".join(banned)
            + " —— 最近三条已经在说这个了,换个说法也还是复读。\n"
              "改说别的:你自己今天的小事、手上正在做的东西,或者问一件**具体**的事"
              "(不是「最近怎么样」这种谁都能问的)。")

    avoid = str(ctx.get("_avoid") or "").strip()
    if avoid:
        parts.append("【换一个开场】\n上一版开头是「%s」,和最近说过的话重复了;"
                     "这次请用完全不同的开场,不要只换几个字。" % avoid[:20])

    avoid_angle = [str(x).strip() for x in (ctx.get("_avoid_angle") or ()) if str(x).strip()]
    if avoid_angle:
        parts.append("【必须换角度】\n刚才那一版又落回「%s」;这次换一个话题角度,"
                     "不要提时间、熬夜或睡觉。" % "、".join(avoid_angle))

    if ctx.get("_avoid_vague"):
        parts.append("【不要含糊指代】\n不要问「你上次说的那件事」这类问题 —— "
                     "这条消息没有具体话题,那样问是编出来的。要么说一件你自己的具体小事,"
                     "要么干脆不问。")

    # 自己的生活:提到"我在做什么"时只能用今日叙事里写到的。
    # 模型自由发挥会编出面板里没有的经历,而用户能在设置面板里看到真实的
    # 那一段 —— 对不上就是"它在骗我"。
    life = " ".join(str(ctx.get("_self_life") or "").split())
    if life:
        parts.append(
            "【你今天的生活】(提到自己在做什么时,只能从这里取)\n"
            "「%s」\n"
            "没写到的事不要编 —— 用户能在设置里看到这一段,对不上就露馅了。" % life[:200])

    # 自己刚说过什么:主动消息最容易"接着自己没说过的话往下演"。
    my_last = " ".join(str(ctx.get("_my_last_line") or "").split())
    if my_last:
        parts.append(
            "【你刚才在对话里说过】\n「%s」\n"
            "不要重复它,也不要装作还没说过 —— 上一句已经给了、答应了的事,"
            "这条不要再重新来一遍。" % my_last[:80])

    mins = ctx.get("_since_user_minutes")
    if mins is not None:
        parts.append("【距上次对话】\n" + _gap_text(mins))

    # 接话素材:用户最后一句。只用来"顺着说",不复述、不追问。
    # 没有这一块时,模型只能回到"这么晚还没睡"这类通用句 —— 人机感主要来自这里。
    last_user = " ".join(str(ctx.get("_last_user") or "").split())
    if last_user:
        parts.append(
            "【用户最后说的一句话】\n「%s」\n"
            "只作接话素材:顺着它说一句人话(可以调侃、可以应一声、可以说你自己的想法)。\n"
            "不要复述原句,不要追问这句话里可能沉重或私密的部分;接不上就聊你自己的事。"
            % last_user[:60])
    return "\n\n".join(parts)


def build_care_llm_prompt(kind: str, context: Optional[dict] = None,
                          card=None) -> list:
    """构造一次主动消息的 [SystemMessage, HumanMessage]。

    kind ∈ checkin / recall / petlife / soothe / probe / warm / ritual / reminder。

    文本全部来自角色卡可覆盖的提示词集;无角色卡时人格段回落为纯助手设定。
    """
    rendered, pset = _bundle(card)
    intents = pset.get("intents") or {}
    raw = intents.get(str(kind)) or intents.get("checkin") or ""
    human = pprompts.render_text(raw, rendered)

    # 当前时间:主动消息常涉及"晚安/早上好/这么晚"等时段表达,必须让模型知道现在几点。
    human += "\n\n【当前时间】\n" + now_text()

    ctx_text = _context_text(kind, context, rendered, pset)
    if ctx_text:
        human += "\n\n" + ctx_text

    # 反重复与距离感:这两块与 kind 无关,统一追加(角色卡不必逐类覆盖)。
    variety = _variety_block(dict(context or {}))
    if variety:
        human += "\n\n" + variety

    tail = pprompts.render_text(pset.get("output_tail") or "", rendered)
    if tail:
        human += "\n\n" + tail

    return [SystemMessage(content=_persona_system(rendered, pset)),
            HumanMessage(content=human)]
