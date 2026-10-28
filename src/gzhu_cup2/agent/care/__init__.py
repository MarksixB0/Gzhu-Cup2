# -*- coding: utf-8 -*-
"""主动关怀子域(与 long_memory 对等的独立域)。

边界约定:
  - 本域只「读」long_memory 的对外 API(list_initiable / profile / ticks / trend /
    ledger 等),long_memory 不得反向依赖本域;
  - policy.py / content.py 为纯函数(零 IO、零 LLM),可离线单测;
  - runtime.py 只持久化「关怀自己的状态」(开关/计数/outbox,独立 JSON 文件),
    不读写长期记忆数据;
  - coordinator.py 负责把记忆只读 API + 关怀运行时组装成一次评估(供 server 调度)。
"""
