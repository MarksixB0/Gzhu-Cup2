# -*- coding: utf-8 -*-
"""桌宠域(pet):桌宠自己的"人格 + 世界观 + 每日生活"记忆与分享。

与 long_memory(用户记忆)对等的独立域,但方向相反:
  - 本域把"桌宠是谁、桌宠今天发生了什么"沉淀为可替换的角色卡 + 按天叙事;
  - 主动关怀(petlife)用叙事作素材,以桌宠自己为主语与用户闲聊;
  - 本域只读 long_memory 的对外 API(供叙事自然提及用户近况),不反向依赖 care;
  - 所有持久化走 long_memory.store 的通用 KV(命名空间 ("pet", <user>, ...)),
    开关/最近生成日期等轻量状态走本包 runtime(独立 JSON,参照 care.runtime)。

角色卡:src/gzhu_cup2/agent/pet/character/*.json,整体可替换(可扩展成"用户可选桌宠"接口)。
"""
