# -*- coding: utf-8 -*-
"""角色卡 → 提示词片段。

本模块只做「拼装」:不读文件(loader)、不校验结构(schema)、不决定最终插入位置
(prompts.py 等调用方)。产出 RenderedPersona,由调用方按各自的布局放置。

优先级语义:
    角色卡 > config 内置默认值;没有角色卡则**完整回落**到 config,
    得到无人格的"最纯净的 AI"。落到字段上:
        core           有卡 = 卡的人格定义(description/personality + scenario)
                       无卡 = ""(调用方沿用 config 自带的助手设定)
        post_history   有卡 = 卡的 post_history_instructions(说话风格硬约束)
                       无卡 = config 的长度规则(调用方通过参数传入)
        card_system_prompt
                       卡自带的 system_prompt。**不替换**应用侧 system prompt,
                       由调用方追加在安全块之后。

安全块不在这里生成:它是应用侧常量,由调用方强制拼接,角色卡无法覆盖。
`{{original}}` 占位符不实现:本应用是"把卡追加进既有提示词",没有需要保留的原值可插。
"""
import re
from dataclasses import dataclass
from typing import Optional

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet.character.loader import ResolvedCard
from gzhu_cup2.agent.pet.character.schema import CardData

# V2/SillyTavern 宏:{{char}} / {{user}},以及调用点自带的自定义宏({{topic}} 等)。
# 未知宏**原样保留**(便于发现拼写错误,而不是被静默清空)。
_MACRO_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# 回落时 {{char}} 的替换名(无人格纯助手)
_FALLBACK_CHAR_NAME = "助手"
# 卡内没有 user_default_name 且 config 也没配时的兜底称呼
_FALLBACK_USER_NAME = "你"


def substitute_macros(text: str, *, char_name: str, user_name: str, **extra) -> str:
    """替换 {{char}} / {{user}} 以及调用点自带的额外宏(如 {{topic}} / {{facts}})。

    未在 table 中登记的宏原样保留:拼错宏名时提示词里会留下可见的 ``{{xxx}}``。
    """
    table = {"char": char_name, "user": user_name}
    for key, value in (extra or {}).items():
        table[str(key).lower()] = "" if value is None else str(value)
    return _MACRO_RE.sub(
        lambda m: table.get(m.group(1).lower(), m.group(0)), str(text or ""))


def resolve_user_name(card: Optional[object] = None, override: Optional[str] = None) -> str:
    """{{user}} 的替换名。

    优先级:**显式 override > 用户自述资料 > config > 卡内声明 > 「你」**。

    用户资料排在 config 之前,是因为它来自用户自己在「设置 → 用户」里的填写;
    ``PET_USER_DISPLAY_NAME`` 只是部署方给的空默认值。两者都空才去看角色卡里
    声明的 ``user_default_name``(那是人格文本的一部分,优先级最低)。
    """
    if override and str(override).strip():
        return str(override).strip()
    from gzhu_cup2.agent.core import user_profile as uprofile
    saved = uprofile.display_name()
    if saved:
        return saved
    configured = str(getattr(cfg, "PET_USER_DISPLAY_NAME", "") or "").strip()
    if configured:
        return configured
    data = getattr(card, "data", None)
    if data is not None:
        from_card = str(data.ext("companion", "user_default_name", default="") or "").strip()
        if from_card:
            return from_card
    return _FALLBACK_USER_NAME


@dataclass(frozen=True)
class RenderedPersona:
    """一次渲染的产物。字段名即"放哪里",调用方按自己的布局取用。"""

    active: bool                 # 是否真的用上了角色卡
    source: str                  # "card" | "fallback"
    requested: str               # 请求的卡名
    reason: str                  # 回落原因(active 时为空串)
    char_name: str               # 角色名(回落时为「助手」)
    user_name: str               # 用户称呼({{user}} 的替换值)
    core: str                    # 人格定义段;回落时为空串
    card_system_prompt: str      # 卡自带 system prompt(须置于安全块之后)
    post_history: str            # 尾部说话风格约束
    mes_example: str             # 对话示例(建议放 system 段,标注为示例)


def _compose_core(data: CardData) -> str:
    """人格定义段 = description(缺则 personality)+ scenario。

    description 非空时不再拼接 personality(personality 通常是它的一句话摘要,
    同时注入会重复);只填了 personality 的卡则用它顶上。
    """
    core = data.prompt_text("description") or data.prompt_text("personality")
    scenario = data.prompt_text("scenario")
    if not scenario:
        return core
    block = "【场景】\n" + scenario
    return f"{core}\n\n{block}" if core else block


def render_persona(
    resolved: ResolvedCard,
    *,
    fallback_length_rule: str = "",
    user_name: Optional[str] = None,
) -> RenderedPersona:
    """把解析结果渲染成提示词片段。

    fallback_length_rule: 没有角色卡时使用的长度规则(调用方传 config 内置值)。
                          有角色卡时**被卡的 post_history_instructions 取代** ——
                          即"角色卡优先于配置文件"。
    """
    card = resolved.card
    if card is None:
        # 无角色卡:完整回落,不残留任何人格文本,调用方沿用 config 自带助手设定
        return RenderedPersona(
            active=False,
            source="fallback",
            requested=resolved.requested,
            reason=resolved.reason,
            char_name=_FALLBACK_CHAR_NAME,
            user_name=resolve_user_name(None, user_name),
            core="",
            card_system_prompt="",
            post_history=str(fallback_length_rule or "").strip(),
            mes_example="",
        )

    data = card.data
    char_name = str(data.name or _FALLBACK_CHAR_NAME).strip()
    uname = resolve_user_name(card, user_name)
    example = data.prompt_text("mes_example")
    return RenderedPersona(
        active=True,
        source="card",
        requested=resolved.requested,
        reason="",
        char_name=char_name,
        user_name=uname,
        core=substitute_macros(_compose_core(data), char_name=char_name, user_name=uname),
        card_system_prompt=substitute_macros(
            data.prompt_text("system_prompt"), char_name=char_name, user_name=uname),
        post_history=substitute_macros(
            data.prompt_text("post_history_instructions"),
            char_name=char_name, user_name=uname),
        mes_example=substitute_macros(example, char_name=char_name, user_name=uname),
    )


def render_greetings(resolved: ResolvedCard, *, user_name: Optional[str] = None) -> list:
    """客户端开场白候选(first_mes + alternate_greetings),已替换宏。

    不进提示词;供具备开场白能力的客户端使用。无卡时返回空列表。
    """
    card = resolved.card
    if card is None:
        return []
    data = card.data
    char_name = str(data.name or _FALLBACK_CHAR_NAME).strip()
    uname = resolve_user_name(card, user_name)
    raw = [data.first_mes] + list(data.alternate_greetings)
    return [substitute_macros(t, char_name=char_name, user_name=uname)
            for t in raw if str(t or "").strip()]
