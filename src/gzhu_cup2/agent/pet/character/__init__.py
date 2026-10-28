# -*- coding: utf-8 -*-
"""角色卡域(pet.character):**可选**人格层。

三段职责,彼此单向依赖(loader -> schema,renderer -> loader+schema),无环:
  - schema.py    结构校验(Character Card V2),并区分「进提示词」与「只给人看」的字段
  - loader.py    目录扫描 / 单卡加载 / 卡的增删改 / 启用卡解析(含"没有卡就回落"的可诊断语义)
  - renderer.py  角色卡 → 提示词片段(卡优先于 config;无卡则回落到 config)

人格来源:本包人格**只来自角色卡文件**,没有 Python 副本;世界观由卡的 scenario 承担。
仓库内**不附带任何预设卡** —— 卡由用户在界面上创建,落在可写的数据目录
(见 config.PET_CARD_DIR)。

优先级:**角色卡 > config 内置默认值**;启用卡为空即"不搭载角色卡",前台回到 config
内置的纯助手提示词(最纯净的 AI,无人格)。启用哪张卡是运行时可持久化的选择
(active_card.json),不写回 config。
"""
from gzhu_cup2.agent.pet.character.loader import (  # noqa: F401
    ResolvedCard,
    active_card_name,
    active_state_path,
    card_dir,
    clear_cache,
    create_card,
    delete_card,
    list_cards,
    load_card,
    resolve_active,
    resolve_card,
    safe_card_name,
    set_active_card,
    update_card,
)
from gzhu_cup2.agent.pet.character.prompts import (  # noqa: F401
    DEFAULTS as PROMPT_DEFAULTS,
    active_prompts,
    describe_overrides,
    has_override,
    render_text,
)
from gzhu_cup2.agent.pet.character.renderer import (  # noqa: F401
    RenderedPersona,
    render_greetings,
    render_persona,
    resolve_user_name,
    substitute_macros,
)
from gzhu_cup2.agent.pet.character.schema import (  # noqa: F401
    CARD_SPEC,
    CARD_SPEC_VERSION,
    NON_PROMPT_FIELDS,
    PROMPT_FIELDS,
    CardData,
    CardError,
    CharacterCard,
    parse_card,
)

__all__ = [
    # schema
    "CARD_SPEC", "CARD_SPEC_VERSION", "PROMPT_FIELDS", "NON_PROMPT_FIELDS",
    "CardData", "CardError", "CharacterCard", "parse_card",
    # loader:读与解析
    "ResolvedCard", "active_card_name", "card_dir", "clear_cache",
    "list_cards", "load_card", "resolve_active", "resolve_card",
    # loader:卡的内容与启用(界面用)
    "active_state_path", "create_card", "delete_card", "safe_card_name",
    "set_active_card", "update_card",
    # renderer
    "RenderedPersona", "render_greetings", "render_persona",
    "resolve_user_name", "substitute_macros",
    # prompts(卡可覆盖的"活"提示词集)
    "PROMPT_DEFAULTS", "active_prompts", "active_prompts_bundle",
    "resolve_prompts", "render_text", "describe_overrides", "has_override",
]
