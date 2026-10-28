# -*- coding: utf-8 -*-
"""长期记忆子域。

边界约定:
  - 记忆代码与记忆数据都收在本包;主聊天图只通过 nodes/ 下的薄节点接入。
  - 本包只允许经由 long_memory.store 触碰 LangGraph Store,其余模块不得直接 import store。
  - 阈值等常量统一在 config/config.py 的「长期记忆」分区维护,不在此设第二套配置。
"""
