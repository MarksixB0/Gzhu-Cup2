# -*- coding: utf-8 -*-
"""算法层:确定性判定资产的唯一入口。

结构:
  skeleton.py    骨架:对外入口(analyze / tier_of)+ 流水线接线 + 逐段 trace
  contracts.py   数据契约:RawData / Features / Score / Decision 字段的唯一定义
  components/    组件:fetch(唯一 I/O)、features、score、machine、triage、
                 lexicon、structure、bocpd、crisis_chain、daytools、review

约定:
  - 组件只认识契约,不互相 import 对方内部;外部调用方只准 import 本包暴露的符号;
  - 判定语义零 LLM:LLM 只在证据抽取侧(维度账本),其结论必须过确定性账本。
"""
from gzhu_cup2.agent.algo.skeleton import analyze, tier_of

__all__ = ["skeleton", "analyze", "tier_of"]
