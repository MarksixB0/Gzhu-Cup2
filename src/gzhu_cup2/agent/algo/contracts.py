# -*- coding: utf-8 -*-
"""算法层数据契约:各阶段输入/输出字段的唯一定义。

RawData 用 dataclass(fetch 构造);Features / Score / Decision 用 TypedDict
(与既有 dict 键逐一对应,函数体不改字段访问方式)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from typing_extensions import TypedDict


@dataclass
class RawData:
    """fetch 阶段的产出:一次判定所需的全部原始观测,不含任何加工。"""
    now: float
    buckets: dict = field(default_factory=dict)     # {day: 日桶}
    dims_daily: dict = field(default_factory=dict)  # {维度: [(age_days, level), ...]}
    last_ts: Optional[float] = None                 # 最近一次活跃时刻


class Features(TypedDict, total=False):
    """features 阶段的产出:全部为原始量,不做阈值判断。"""
    recent: dict
    fast: dict
    base: dict
    delta: Optional[float]
    delta_fast: Optional[float]
    sigma_shrunk: float
    d_effect: Optional[float]
    z: Optional[float]
    coverage: float
    idle_days: Optional[int]
    engagement_drop: Optional[float]
    phase_shift_h: Optional[float]
    activity_cp: Optional[dict]   # 参与度序列的 BOCPD 变点摘要(bocpd.change_summary)
    night_rate: float
    density_span: int
    withdraw_rate: float
    conflict_rate: float
    anhedonia_rate: float
    somatic_rate: float
    rates_fast: dict
    dims: dict
    dim_hist_days: int


class Score(TypedDict, total=False):
    """score 阶段的产出:七路径分数 + 数据质量的完整分解。"""
    s_change: float
    s_severity: float
    s_floor: float
    s_chronic: float
    s_behavior: float
    s_fast: float
    s_dimension: float
    s_precursor: float
    quality: float
    quality_note: str
    s_total: float
    detail: dict


class Decision(TypedDict, total=False):
    """machine 阶段的产出:档位与成因。"""
    tier: str
    mode: str
    reason: str
    score: float
    hold: bool
    params: dict


def to_plain(obj: Any) -> Any:
    """dataclass → dict 的浅转换(对 dict/TypedDict 原样返回)。"""
    if hasattr(obj, "__dataclass_fields__"):
        return dict(obj.__dict__)
    return obj
