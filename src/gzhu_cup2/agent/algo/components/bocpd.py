# -*- coding: utf-8 -*-
"""bocpd 组件:Gamma-Poisson 贝叶斯变点检测(纯函数、零 IO、确定性)。

Adams & MacKay (2007) 的 BOCPD,计数序列(日轮次)配 Gamma 共轭先验,
预测分布是负二项 —— 只用 math.lgamma 实现,不依赖 scipy。本项目的
用法是**事后回看**:对近窗序列一次性跑完,置信度读末步 run-length
后验的段龄 P(r ≤ CP_RECENT)。
"""
from __future__ import annotations

import math
from typing import Optional

# ------------------------------------------------------------------ 参数
HAZARD = 1.0 / 28.0   # 每步断点先验概率(期望段长 28 天;日粒度下的"最近"尺度)
MAX_RUN = 56          # 段长剪枝上限:超出部分后验质量可忽略,剪掉保确定性开销
CP_RECENT = 21        # 段龄窗:变点发生在最近 N 步内才算"最近的变化"(与 lapse 保持口径同量级)
PA0 = 1.0             # Gamma 形状先验(弱)
PB0 = 1.0             # Gamma 速率先验(E[λ]=a/b=1,几步观测即被数据主导)
MIN_POINTS = 5        # 序列点数下限:少于此不成摘要


def _logsumexp(vals: list) -> float:
    m = max(vals)
    return m + math.log(sum(math.exp(v - m) for v in vals))


def _nb_logpmf(x: int, a: float, b: float) -> float:
    """Gamma(a,b) 先验下 Poisson 的负二项预测对数概率。"""
    return (math.lgamma(a + x) - math.lgamma(a) - math.lgamma(x + 1)
            + a * math.log(b / (b + 1.0)) + x * math.log(1.0 / (b + 1.0)))


def _final_runs(series: list, hazard: float = HAZARD,
                max_run: int = MAX_RUN) -> list:
    """跑 BOCPD,返回**末步**归一化 run-length 后验 [(r, p)](按概率降序)。

    递推:每来一个观测,各候选段长 r 按负二项预测概率生长,并以 hazard h
    断点重启(r→0,新段以先验预测吸收首点)。
    """
    runs = {0: [0.0, PA0, PB0]}          # r -> [log prob, a, b]
    for x in series:
        x = max(0, int(round(x)))
        preds = {r: _nb_logpmf(x, a, b) for r, (lp, a, b) in runs.items()}
        log_sum_J = _logsumexp([v[0] for v in runs.values()])
        log_pi0 = _nb_logpmf(x, PA0, PB0)
        log_p0 = math.log(hazard) + log_pi0 + log_sum_J
        # 新段参数 = 含首点 x 的 Gamma 后验
        new_runs = {0: [log_p0, PA0 + x, PB0 + 1.0]}
        for r, (lp, a, b) in runs.items():
            new_runs[r + 1] = [lp + preds[r] + math.log(1.0 - hazard),
                               a + x, b + 1.0]
        z_norm = _logsumexp([v[0] for v in new_runs.values()])
        items = sorted(((r, [v[0] - z_norm] + v[1:]) for r, v in new_runs.items()),
                       key=lambda kv: -kv[1][0])[:max_run]
        runs = {r: v for r, v in items}
    # 按后验概率降序返回(final[0] 即众数段);段龄由调用方按 r 阈值求和。
    return [(r, math.exp(v[0])) for r, v in runs.items()]


def change_summary(series: list, recent: int = CP_RECENT) -> Optional[dict]:
    """计数序列的变点摘要;少于 MIN_POINTS 个点返回 None。

    p_break = P(当前段年龄 ≤ recent-1),即"变点发生在近 ``recent`` 步内"
    的概率(0-1)。窗取 21 天,与 lapse 保持口径同量级 —— 变点发生已有
    一段日子但新水平仍在持续的,证据不应因"不在最近 7 天"而丢失。

    返回:
      p_break  上述段龄置信度
      cp_idx   众数段起点(变点位置的 MAP 估计)
      shift    变点后均值 − 变点前均值(两侧各 ≥2 点才有;方向由调用方解释)
      n        序列长度
    """
    vals = [float(v) for v in series]
    if len(vals) < MIN_POINTS:
        return None
    n = len(vals)
    final = _final_runs(vals)
    p_break = sum(p for r, p in final if r <= max(1, int(recent)) - 1)
    cp_idx = max(0, n - int(final[0][0])) if final else 0
    shift = None
    if cp_idx >= 2 and (n - cp_idx) >= 2:
        head = vals[:cp_idx]
        tail = vals[cp_idx:]
        shift = sum(tail) / len(tail) - sum(head) / len(head)
    return {
        "p_break": round(float(p_break), 4),
        "cp_idx": int(cp_idx),
        "shift": round(float(shift), 4) if shift is not None else None,
        "n": n,
    }
