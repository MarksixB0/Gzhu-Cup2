# -*- coding: utf-8 -*-
"""score 组件:把特征压成有名字的分数。

  S_change   变化分(相对自身基线:绝对位移 + 效应量)
  S_severity 绝对分(典型一天有多低)
  S_floor    低水位分(每天状态最好的时段有多糟)
  S_chronic  慢性分(长期低位,需足够历史)
  S_behavior 症状族分(四条正交路径取最大)
  S_fast     快尺度分(近 3 天突变,封顶 0.75)
  S_dimension 维度分(维度账本,按当前档连续计入)
  S_precursor 前驱分(BOCPD 确认的参与度塌陷,封顶 0.80)
  S_total    = max(七条路径),再乘数据质量
"""
from __future__ import annotations

from gzhu_cup2.agent.algo.contracts import Score


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """夹到 [lo,hi];NaN 与不可转换值一律归到 lo。

    NaN 与任何阈值比较都是 False,不处理会静默掉进 stable。
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    if v != v:                      # NaN
        return lo
    return max(lo, min(hi, v))


# ------------------------------------------------------------------ 刻度
# 只负责"物理量 → 0-1 分数"的映射;判定阈值(进入/退出)全在 machine.py。
DELTA_FULL = 0.12      # 日级均值位移达到 0.12 → 变化分拉满
D_EFF_LO = 0.8         # 效应量 0.8 个典型日波动 = 小效应
D_EFF_HI = 2.5         # 2.5 个 = 大效应(因日序列自相关而上调)
SEV_FLOOR = 0.30       # 绝对分起点 = 无情绪词时的基分
SEV_FULL = 0.44        # ≈ 每天都有明确的负面表达
CHRONIC_MIN_DAYS = 14  # 慢性分要求的最少有记录历史天数

# 数据充足度门槛(与 data_quality 一一对应);洞察页引用同一组常量。
MIN_RECENT_DAYS = 2     # 少于 2 天有记录 → 完全不判(insufficient)
SPARSE_RECENT_DAYS = 4  # <4 天或覆盖率 <0.5 → 打折(0.75)
MIN_COVERAGE = 0.5

# 四个症状族的固定权重,按症状与抑郁核心的贴近程度排序。全部低于 elevated 线(0.78)。
FAMILY_WEIGHT = {"anhedonia": 0.75, "withdraw": 0.72,
                 "conflict": 0.72, "somatic": 0.62}
FAMILY_RATE_FLOOR = 0.35   # 一周内 <3 天有该族表述 → 不算"持续"
FAMILY_RATE_SPAN = 0.45    # 0.35 → 0.80 的映射区间

# 维度分:按维度账本的当前档连续计入(绝对腿),总分封顶 0.85 ——
# 用户自述的症状没有行为侧验证,不单独占满刻度。
DIM_PATH_K = 6.0           # 归一化分母:各腿合计达到 6 档即满分
DIM_BASE_MIN_HIST = 21     # 相对腿要求的最少记录天数;不足时基线不可信,只走绝对腿
DIM_SEVERE_FLOOR = 0.55    # 任一维度当前档 ≥2.5(近几日内"严重")时的分数下限
DIM_CAP = 0.85             # 自述症状无行为侧验证,不占满分


def change_score(f: dict) -> dict:
    """变化分:S_change ∈ [0,1]。"""
    delta = f.get("delta")
    d_eff = f.get("d_effect")
    if delta is None:
        return {"score": 0.0, "delta_term": 0.0, "effect_term": 0.0, "delta": None}
    delta_term = _clip(float(delta) / DELTA_FULL)
    effect_term = 0.0
    if d_eff is not None:
        effect_term = _clip((float(d_eff) - D_EFF_LO) / (D_EFF_HI - D_EFF_LO))
    s = 0.62 * delta_term + 0.38 * effect_term
    return {
        "score": round(_clip(s), 4),
        "delta_term": round(delta_term, 3),
        "effect_term": round(effect_term, 3),
        "delta": round(float(delta), 4),
    }


def severity_score(f: dict) -> dict:
    """绝对分:S_severity ∈ [0,1]。看"典型一天",不看变化。"""
    med = (f.get("recent") or {}).get("neg_median")
    if med is None:
        return {"score": 0.0, "median": None}
    s = _clip((float(med) - SEV_FLOOR) / (SEV_FULL - SEV_FLOOR))
    return {"score": round(s, 4), "median": round(float(med), 4)}


def floor_score(f: dict) -> dict:
    """低水位分:S_floor ∈ [0,1]。看"每天状态最好的时段有多糟"。

    输入是 recent 窗内各日低水位(分段 neg 均值最小值)的中位数;
    单时段日与旧桶回退为日均值,此时与 S_severity 同值。
    """
    med = (f.get("recent") or {}).get("floor_median")
    if med is None:
        return {"score": 0.0, "floor_level": None}
    s = _clip((float(med) - SEV_FLOOR) / (SEV_FULL - SEV_FLOOR))
    return {"score": round(s, 4), "floor_level": round(float(med), 4)}


def chronic_score(f: dict, floor_sev: float, s_change: float = 0.0) -> dict:
    """慢性分:长期低水位。

    严重度输入用低水位(每天状态最好的时段)而非日均值:慢性语义是
    "长期连最好的时候都不好";固定坏时段+其余正常的作息形状不计入。
    三道闸把"一直很差"与"最近变差"分开:历史 ≥14 天、覆盖度 ≥0.5、
    以及**与 S_change 互斥**(门形如 1−(s−0.25)/0.25)。第三条是必须的:
    急剧恶化时 recent 中位数同样高,不排除会把"变化"错记成"一直如此"。
    另乘覆盖度因子:同样丧,天天来说话的人比隔三差五来的更可信。
    """
    # 有记录历史的天数(features.dim_hist_days = 日桶总天数)。
    # 不用 base+recent 天数相加:隔离带放宽时两窗会重叠,相加会把重叠天算两遍。
    n_hist = int(f.get("dim_hist_days") or 0)
    cov = float(f.get("coverage") or 0.0)
    if n_hist < CHRONIC_MIN_DAYS or cov < MIN_COVERAGE:
        return {"score": 0.0, "n_hist": n_hist, "coverage": cov}
    cov_factor = 0.6 + 0.4 * _clip(cov)
    change_gate = 1.0 - _clip((float(s_change) - 0.25) / 0.25)
    s = floor_sev * cov_factor * change_gate
    return {"score": round(_clip(s), 4), "n_hist": n_hist,
            "coverage": round(cov, 3), "cov_factor": round(cov_factor, 3),
            "change_gate": round(change_gate, 3)}


def behavior_score(f: dict) -> dict:
    """症状族分:四条独立路径,取最高的一条。

    这四族必须进主路径而不能只做增益 —— 否认自身情绪的人格(退缩/易怒/躯体化)
    情绪 delta 几乎不动,若增益又要 S_change 开门,就永远加不上分。
    与 v1 的投票区别在于:四族词汇互不重叠、与情绪词表也不重叠(真正正交)。

    密度 = 有该族表述的天数 / 有记录天数,不是命中次数:一天说十句"不想出门"
    和十天各说一句,风险含义不同,后者才是退缩。
    """
    rates = {
        "withdraw": float(f.get("withdraw_rate") or 0.0),
        "conflict": float(f.get("conflict_rate") or 0.0),
        "anhedonia": float(f.get("anhedonia_rate") or 0.0),
        "somatic": float(f.get("somatic_rate") or 0.0),
    }
    n_recent = int(((f.get("recent") or {}).get("n_days") or 0))
    if n_recent < 4:
        return {"score": 0.0, "kind": None, "rates": rates,
                "reason": "too_few_days", "detail": {}}
    cov = float(f.get("coverage") or 0.0)
    cov_factor = 0.6 + 0.4 * _clip(cov) if cov < 1.0 else 1.0
    rates_fast = f.get("rates_fast") or {}
    detail = {}
    best = (0.0, None)
    for fam, w in FAMILY_WEIGHT.items():
        # 慢尺度(7 天密度)与快尺度(3 天密度)取大。快尺度乘 0.90:
        # 样本量小、可信度略低,但足以让"连续三天退缩"在第三天就进 watch。
        s_slow = 0.0
        r = rates[fam]
        if r >= FAMILY_RATE_FLOOR:
            s_slow = _clip((r - FAMILY_RATE_FLOOR) / FAMILY_RATE_SPAN) * w * cov_factor
        s_fast = 0.0
        rf = float(rates_fast.get(fam) or 0.0)
        if rf >= FAMILY_RATE_FLOOR:
            s_fast = _clip((rf - FAMILY_RATE_FLOOR) / FAMILY_RATE_SPAN) * w * 0.90
        s = max(s_slow, s_fast)
        if s <= 0:
            continue
        detail[fam] = round(s, 4)
        if s > best[0]:
            best = (s, fam)
    return {"score": round(_clip(best[0]), 4), "kind": best[1], "rates": rates,
            "rates_fast": rates_fast,
            "reason": "ok" if best[1] else "below_floor", "detail": detail}


def dimension_score(f: dict) -> dict:
    """维度分:用衰减折算后的维度证据打分,取各腿最大者,封顶 DIM_CAP。

      绝对腿   Σ max(0, level_w−1) / K  —— 每个维度超出"提及"档的部分按档位
               连续计入:长期 level 1 贡献 0,处在"提及与明显之间"按比例计
      相对腿   Σ shift / K  —— 当前档高出个人基线的部分;只在
               dim_hist_days ≥ DIM_BASE_MIN_HIST(基线可信)时参与
      快车道   任一维度 level_w ≥ 2.5(近几日内说过"严重")时分数下限
               提到 DIM_SEVERE_FLOOR,避免单点强证据被归一化稀释

    level_w / level_base / shift 来自 features._dim_decayed(5 天半衰期当前档、
    28 天半衰期基线档)。账本为空返回 0,不影响无证据口径。
    """
    dims = f.get("dims") or {}
    if not dims:
        return {"score": 0.0, "n_dim": 0, "n_counted": 0, "leg": "none", "detail": {}}
    hist = int(f.get("dim_hist_days") or 0)
    rel_sum = 0.0
    absol_sum = 0.0
    counted = {}
    severe = False
    for k, v in dims.items():
        try:
            lw = float((v or {}).get("level_w") or 0.0)
            sh = float((v or {}).get("shift") or 0.0)
        except (TypeError, ValueError):
            continue
        contrib = max(0.0, lw - 1.0)
        if contrib > 0:
            absol_sum += contrib
            counted[k] = round(lw, 2)
        if hist >= DIM_BASE_MIN_HIST and sh > 0:
            rel_sum += sh
        if lw >= 2.5:
            severe = True
    paths = {"absolute": absol_sum / DIM_PATH_K}
    if hist >= DIM_BASE_MIN_HIST:
        paths["relative"] = rel_sum / DIM_PATH_K
    if severe:
        paths["severe_floor"] = DIM_SEVERE_FLOOR
    leg, score = max(paths.items(), key=lambda kv: kv[1])
    return {
        "score": round(_clip(score, 0.0, DIM_CAP), 4),
        "n_dim": len(dims),
        "n_counted": len(counted),
        "leg": leg,
        "detail": counted,
    }


# 前驱路径的刻度(见 precursor_score)。
ENGAGE_DROP_FLOOR = 0.40  # 参与度降幅低于此不构成证据
ENGAGE_DROP_FULL = 0.80   # 降幅达到此值幅度项拉满
CP_P_MIN = 0.40           # BOCPD 变点后验低于此 = 平稳序列的噪声水平,不构成置信
PHASE_FLOOR = 2.0         # 作息后移低于 2 小时不构成证据
PHASE_FULL = 5.0          # 后移达到 5 小时幅度项拉满
PRECURSOR_CAP = 0.80      # 路径封顶:只够推进 watch,不推 elevated(与 fast 同策略)

# 低水位路径的封顶:无变化/症状互证时,绝对低水位只够推进 watch。
FLOOR_CAP = 0.70


def precursor_score(f: dict) -> dict:
    """前驱路径:BOCPD 确认的参与度塌陷 / 作息后移,取两腿最大者。

    "安静恶化型"(文字侧不动,只有参与度/作息在恶化)的主路径,
    不依赖症状族措辞自立分数。封顶 0.80,只推 watch 不推 elevated,
    与 fast 同策略(参与度塌陷有考试周/假期等良性解释)。
    作息是环形量,不做变点检测,幅度用位移映射。
    """
    legs = {}
    cp = f.get("activity_cp") or {}
    ed = f.get("engagement_drop")
    p_break = cp.get("p_break") if cp else None
    if (ed is not None and float(ed) >= ENGAGE_DROP_FLOOR
            and p_break is not None and float(p_break) >= CP_P_MIN):
        conf = _clip((float(p_break) - CP_P_MIN) / (1.0 - CP_P_MIN))
        mag = _clip((float(ed) - ENGAGE_DROP_FLOOR)
                    / (ENGAGE_DROP_FULL - ENGAGE_DROP_FLOOR))
        legs["engagement"] = round(PRECURSOR_CAP * conf * mag, 4)
    ph = f.get("phase_shift_h")
    if ph is not None and float(ph) >= PHASE_FLOOR:
        mag = _clip((float(ph) - PHASE_FLOOR) / (PHASE_FULL - PHASE_FLOOR))
        # 作息无变点置信度可用,幅度项直接打折(0.55/0.75)
        legs["phase"] = round(PRECURSOR_CAP * 0.55 * mag, 4)
    best = max(legs.items(), key=lambda kv: kv[1]) if legs else (None, 0.0)
    return {"score": min(best[1], PRECURSOR_CAP), "leg": best[0],
            "legs": legs}


def data_quality(f: dict) -> dict:
    """数据可信度:不足时**打折**而不是清零,更不是当成好转。

    例外(lapse_signal):参与度塌陷被 BOCPD 确认时,近期稀疏按 0.9
    打折继续判,不清零。
    """
    n_recent = int(((f.get("recent") or {}).get("n_days") or 0))
    cov = float(f.get("coverage") or 0.0)
    if n_recent >= SPARSE_RECENT_DAYS and cov >= MIN_COVERAGE:
        return {"factor": 1.0, "ok": True, "note": "ok"}
    cp = f.get("activity_cp") or {}
    ed = f.get("engagement_drop")
    if ((cp.get("p_break") or 0.0) >= CP_P_MIN
            and (ed or 0.0) >= ENGAGE_DROP_FLOOR):
        return {"factor": 0.9, "ok": True, "note": "lapse_signal"}
    if n_recent >= MIN_RECENT_DAYS:
        return {"factor": 0.75, "ok": True, "note": "sparse"}
    return {"factor": 0.0, "ok": False, "note": "insufficient"}


def fast_score(f: dict) -> dict:
    """快尺度分:近 3 天相对基线的突变。

    存在的理由只有一个:7 天窗会把恶化第 1 天稀释成 1/7,慢尺度要到第 4 天才够分。
    为防单日噪声打穿:要求近 3 天至少 2 天有记录,且贡献封顶 0.75 ——
    只够推进 watch,"持续关切"必须由慢尺度或症状族支撑。
    """
    d = f.get("delta_fast")
    if d is None:
        return {"score": 0.0, "delta_fast": None}
    sigma = float(f.get("sigma_shrunk") or 0.0)
    d_eff = (float(d) / sigma) if sigma > 1e-9 else 0.0
    s = 0.62 * _clip(float(d) / DELTA_FULL) \
        + 0.38 * _clip((d_eff - D_EFF_LO) / (D_EFF_HI - D_EFF_LO))
    return {"score": round(_clip(s), 4), "delta_fast": round(float(d), 4),
            "effect_fast": round(d_eff, 3)}


def compute(f: dict) -> Score:
    """总打分。返回完整可审计的分解(每个分项都写进 evidence)。"""
    ch = change_score(f)
    sv = severity_score(f)
    fl = floor_score(f)
    bh = behavior_score(f)
    ft = fast_score(f)
    dm = dimension_score(f)
    pc = precursor_score(f)
    sg = ch["score"]
    chronic = chronic_score(f, fl["score"], sg)
    emo_path = 0.62 * sg + 0.38 * sv["score"]
    behav_path = bh["score"]               # 已含族权重,最高不超过 0.80
    fast_path = 0.75 * ft["score"]          # 封顶 0.75:只推 watch,不推 elevated
    dim_path = dm["score"]
    prec_path = pc["score"]                 # 封顶 0.80:只推 watch,不推 elevated
    floor_path = FLOOR_CAP * fl["score"]    # 封顶 0.70:只推 watch,不推 elevated
    base = max(emo_path, chronic["score"], behav_path, fast_path, dim_path,
               prec_path, floor_path)
    q = data_quality(f)
    total = _clip(base * q["factor"])
    return {
        "s_change": sg,
        "s_severity": sv["score"],
        "s_floor": fl["score"],
        "s_chronic": chronic["score"],
        "s_behavior": bh["score"],
        "s_fast": ft["score"],
        "s_dimension": dm["score"],
        "s_precursor": pc["score"],
        "quality": q["factor"],
        "quality_note": q["note"],
        "s_total": round(total, 4),
        "detail": {
            "delta": ch["delta"],
            "delta_term": ch["delta_term"],
            "effect_term": ch["effect_term"],
            "effect_d": f.get("d_effect"),
            "delta_fast": ft["delta_fast"],
            "effect_fast": ft.get("effect_fast"),
            "floor_level": fl["floor_level"],
            "sigma": f.get("sigma_shrunk"),
            "z": f.get("z"),
            "median": sv["median"],
            "chronic_n_hist": chronic.get("n_hist"),
            "behavior_kind": bh.get("kind"),
            "behavior_detail": bh.get("detail"),
            "dim_n": dm["n_dim"],
            "dim_n_counted": dm["n_counted"],
            "dim_leg": dm["leg"],
            "dim_detail": dm["detail"],
            "withdraw_rate": f.get("withdraw_rate"),
            "conflict_rate": f.get("conflict_rate"),
            "anhedonia_rate": f.get("anhedonia_rate"),
            "somatic_rate": f.get("somatic_rate"),
            "engagement_drop": f.get("engagement_drop"),
            "phase_shift_h": f.get("phase_shift_h"),
            "activity_cp": f.get("activity_cp"),
            "precursor_leg": pc["leg"],
            "precursor_legs": pc["legs"],
            "coverage": f.get("coverage"),
            "idle_days": f.get("idle_days"),
            "paths": {"emotion": round(emo_path, 4),
                      "chronic": chronic["score"],
                      "behavior": round(behav_path, 4),
                      "fast": round(fast_path, 4),
                      "dimension": round(dim_path, 4),
                      "precursor": round(prec_path, 4),
                      "floor": round(floor_path, 4)},
        },
    }
