# -*- coding: utf-8 -*-
"""角色卡数据结构(Character Card V2)。

只做「结构校验 + 字段归类」:
  - 不做提示词拼装(那是 renderer 的职责);
  - 不做文件 IO(那是 loader 的职责)。

设计立场:
  - 只把本应用会读的字段声明为强类型;V2 的其余字段与全部未知字段一律原样保留
    (规范要求 "must never destroy unknown key-value pairs"),但不参与提示词;
  - 「给人看」与「进提示词」的字段在**类型层面**分开:
        PROMPT_FIELDS      -> renderer 只允许读这些
        NON_PROMPT_FIELDS  -> renderer 永不读取
    注释类字段在结构上无法进入提示词。
"""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# 规范标识(spec_v2.md:「spec MUST be "chara_card_v2"」「spec_version MUST be "2.0"」)
CARD_SPEC = "chara_card_v2"
CARD_SPEC_VERSION = "2.0"

# 允许进入提示词的字段(V2 中与 prompt engineering 相关的字段)
PROMPT_FIELDS = (
    "description",
    "personality",
    "scenario",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
)

# 明确「不得进入提示词」的字段:
#   creator_notes                   规范:"MUST NOT be used inside prompts"
#   tags/creator/character_version  规范:"MUST NOT be used for prompt engineering"
#   first_mes/alternate_greetings   客户端开场白,属于对话内容而非人格定义
#   extensions/character_book       结构化附属数据(本应用不读)
NON_PROMPT_FIELDS = (
    "creator_notes",
    "tags",
    "creator",
    "character_version",
    "first_mes",
    "alternate_greetings",
    "extensions",
    "character_book",
)


class CardError(Exception):
    """角色卡不可用(文件存在但结构不合法,或字段类型错误)。"""


def _brief(err: ValidationError) -> str:
    """把 pydantic 的报错压成一行,便于日志与接口回显。"""
    out = []
    for e in err.errors()[:4]:
        loc = ".".join(str(p) for p in e.get("loc", ()))
        out.append(f"{loc or '<root>'}: {e.get('msg')}")
    more = "" if len(err.errors()) <= 4 else f"(另有 {len(err.errors()) - 4} 条)"
    return "; ".join(out) + more


class CardData(BaseModel):
    """V2 的 data 段。未声明的字段一律保留(extra="allow")。"""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="角色名(必填,不可为空)")
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    character_book: Optional[dict[str, Any]] = None
    tags: list[str] = Field(default_factory=list)
    creator: str = ""
    character_version: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict)

    # ---------------- 供 renderer 使用的只读视图 ----------------

    def prompt_text(self, field: str) -> str:
        """取一个允许进提示词的字段;非白名单字段直接抛错(防误用)。"""
        if field not in PROMPT_FIELDS:
            raise CardError(
                f"{field!r} 不是可进提示词的字段;白名单为 {PROMPT_FIELDS}")
        return str(getattr(self, field, "") or "").strip()

    def has_persona(self) -> bool:
        """是否具备可用的人格定义(description 或 personality 至少一个非空)。"""
        return bool(self.prompt_text("description") or self.prompt_text("personality"))

    def ext(self, *path: str, default: Any = None) -> Any:
        """按命名空间读 extensions,例如 ext("companion", "user_default_name")。"""
        cur: Any = self.extensions or {}
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur


class CharacterCard(BaseModel):
    """V2 根节点:{spec, spec_version, data}。未知顶层字段同样保留。"""

    model_config = ConfigDict(extra="allow")

    spec: str
    spec_version: str = CARD_SPEC_VERSION
    data: CardData


def parse_card(raw: Any, *, origin: str = "") -> CharacterCard:
    """把已解析的 JSON 对象变成 CharacterCard;任何不合法都抛 CardError(带定位信息)。

    不做"缺字段就用内置默认补齐"的兜底:卡要么结构正确,要么明确失败,
    由调用方决定是否回落到 config 内置提示词。
    """
    where = f"[{origin}]" if origin else ""
    if not isinstance(raw, dict):
        raise CardError(f"{where}角色卡根节点必须是 JSON 对象,实际为 {type(raw).__name__}")

    spec = raw.get("spec")
    if spec != CARD_SPEC:
        raise CardError(
            f"{where}角色卡 spec 必须为 {CARD_SPEC!r},实际为 {spec!r};"
            f"若是 V1 老卡请先按 spec_v2.md 转成 V2({CARD_SPEC!r} + data 包裹)")

    data = raw.get("data")
    if not isinstance(data, dict):
        raise CardError(f"{where}角色卡缺少 data 对象(V2 的人格字段都在 data 下)")

    try:
        return CharacterCard(**raw)
    except ValidationError as e:
        raise CardError(f"{where}角色卡字段校验失败: {_brief(e)}") from e
