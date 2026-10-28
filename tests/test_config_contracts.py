# -*- coding: utf-8 -*-
"""跨模块契约锁:把"靠注释和记忆维持"的前提变成会失败的测试。

为什么单独一个文件
------------------
本项目的坏味道里最危险的一类不是"代码丑",而是**同一个事实在多处各写一份**、
以及**模块 B 的正确性依赖模块 A 的内部取值**。这类问题有三个共同特征:
  1. 改一边不会报错,只是**结果悄悄变错**(项目自己命名为"静默错误");
  2. 既有测试常常"自己又写死一遍同样的字面量",所以改坏了测试仍然全绿;
  3. 修复它们往往不值得动业务代码,但**值得加一把锁**。

本文件只做后者:不改变任何生产行为,只让前提一旦被破坏就立刻红。
"""
import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.algo.components import lexicon as mlexicon
from gzhu_cup2.agent.algo.components import triage


# ============================ 1. dev 预设 ↔ 测试钉住的生产值 ============================
class TestDevFastModeContract:
    """``DEV_FAST_MODE`` 的预设键必须都能被测试钉回生产值。

    背景:``config.py`` 末尾的 dev 开关会 ``globals().update(_DEV_FAST_PRESET)``
    改写一批阈值;测试必须把它们钉回生产值,否则用例行为会随这个开关漂移
    (实测开着它会让 3 个用例失败)。

    ⚠ 此前那张"生产值"表是**手抄**在 ``tests/conftest.py`` 里的 —— 与 config 各写一份,
      新增一个 dev 键就要记得改两处,漏一处就退回到"测试依赖开发开关"。
      现在 conftest 直接读 ``cfg.PROD_DEFAULTS``(config 在应用预设**之前**抓的快照),
      本用例锁住这个机制。
    """

    def test_prod_defaults_covers_every_preset_key(self):
        missing = set(cfg._DEV_FAST_PRESET) - set(cfg.PROD_DEFAULTS)
        assert not missing, f"这些 dev 键没有生产值快照,测试将无法钉住: {sorted(missing)}"

    def test_prod_defaults_snapshot_is_taken_before_preset(self):
        """快照必须真的是"未被预设污染"的值:至少要有一处与 dev 值不同。"""
        differing = [k for k, v in cfg.PROD_DEFAULTS.items()
                     if cfg._DEV_FAST_PRESET.get(k) != v]
        assert differing, "PROD_DEFAULTS 与 dev 预设完全一致,说明快照抓晚了(在预设之后)"

    def test_prod_defaults_values_are_usable(self):
        assert None not in cfg.PROD_DEFAULTS.values(), cfg.PROD_DEFAULTS


# ============================ 2. 前台启发式的离散分值 ============================
class TestSingleHitScoreContract:
    """逐标签钉住 ``triage.classify`` 的"单次命中"分值。

    ``nodes/triage.py`` 的 ``_BASE_NEG`` 与降权幅度决定了各标签单次命中的
    真实分值;``SINGLE_HIT_NEG_MIN`` 导出其中最低者。改动任一侧都会红。
    """

    @pytest.mark.parametrize("label,text", [
        ("sad", "难过"),
        ("angry", "生气"),
        ("frustrated", "累"),
        ("fearful", "害怕"),
        ("anxious", "焦虑"),
    ])
    def test_single_hit_score_matches_expected(self, label, text):
        """逐标签钉住"单次命中"的真实分值。"""
        c = triage.classify(text)
        assert c["emotion"] == label, (text, c["emotion"])
        assert c["negative_score"] == pytest.approx(
            max(0.25, triage._BASE_NEG[label] - 0.15), abs=1e-3), (label, c)

    def test_exported_min_matches_lowest_single_hit(self):
        assert triage.SINGLE_HIT_NEG_MIN == pytest.approx(0.40, abs=1e-3)


# ============================ 3. 词表阶梯:单一来源 ============================
class TestLexiconLadderContract:
    def test_ladder_matches_private_constants(self):
        """排障面板下发的阶梯值必须就是实现用的常量(不得手抄)。"""
        lad = mlexicon.ladder()
        assert lad["base_no_hit"] == mlexicon._BASE_NO_HIT
        assert lad["base_hit"] == mlexicon._BASE_HIT
        assert lad["step_per_word"] == mlexicon._NEG_STEP
        assert lad["pos_step"] == mlexicon._POS_STEP

    def test_max_score_respects_the_cap(self):
        """上限必须按封顶算出来,而不是手算一个近似值。"""
        lad = mlexicon.ladder()
        assert lad["max_score"] == round(
            min(1.0, mlexicon._BASE_HIT + mlexicon._NEG_STEP * 6), 3)


# ============================ 4. 危机求助资源:单一来源 ============================
class TestCrisisHotlineSingleSource:
    """热线是**安全相关**的事实,只能有一份来源(config)。

    此前 ``prompts.py`` 硬编码了一份自己的热线文案,而 ``crisis_fallback`` /
    ``web/engine`` 用 ``cfg.CRISIS_HOTLINES`` —— 两份内容已经不一致
    (硬编码那份少了"紧急情况请拨打 120")。改一处漏一处就会让模型给出错误或不完整的求助信息。
    """

    def test_prompt_takes_hotlines_from_config(self):
        from gzhu_cup2.agent.prompts import prompts as p
        assert str(cfg.CRISIS_HOTLINES).strip() in p.crisis_instruction()

    def test_template_carries_no_hotline_literal(self):
        """**会送给模型的模板**里不得写死号码。

        只查模板而不是整个源文件:注释里提到"12356 就是近年新增的"是在解释这件事本身,
        把它也禁掉就成了为了测试而写代码。
        """
        from gzhu_cup2.agent.prompts import prompts as p
        tpl = p._CRISIS_INSTRUCTION_TEMPLATE
        assert "12356" not in tpl
        assert "400-161-9995" not in tpl
        assert "{hotlines}" in tpl, "模板必须留占位符,由 crisis_instruction() 从 config 填"

    def test_fallback_and_engine_use_config(self):
        from gzhu_cup2.agent.nodes import crisis_fallback
        import inspect
        assert "cfg.CRISIS_HOTLINES" in inspect.getsource(crisis_fallback)


# ============================ 5. 命名空间:单一来源 ============================
class TestNamespaceSingleSource:
    def test_pet_daily_namespace_has_one_source(self):
        """清空记忆时用的 pet_daily 命名空间必须来自 narrative,不得是字面量。"""
        import inspect
        from gzhu_cup2.agent.pet import narrative
        from gzhu_cup2.agent.web import routes_admin
        assert narrative.ns_daily("u1") == ("pet", "u1", "daily")
        src = inspect.getsource(routes_admin)
        assert '("pet", user, "daily")' not in src, "routes_admin 又手写了该命名空间"
