# -*- coding: utf-8 -*-
"""gzhu_cup2.agent.core.models.LLM_base.get_llm:按 (model, temp) 键控缓存。

不触网:patch build_llm 为计数器工厂;验证相同参数复用实例、换参新建。
"""
from gzhu_cup2.agent.core.models import LLM_base


def test_get_llm_cache_reuses_same_params(monkeypatch):
    calls = []
    factory_instances = {}

    def fake_build(**kw):
        calls.append(kw)
        key = kw.get("model_name"), kw.get("temperature")
        if key not in factory_instances:
            factory_instances[key] = object()
        return factory_instances[key]

    monkeypatch.setattr(LLM_base, "build_llm", fake_build)
    # 清缓存,保证独立
    LLM_base._llm_cache.clear()
    try:
        a = LLM_base.get_llm(model_name="m1", temperature=0.2, api_key="k")
        b = LLM_base.get_llm(model_name="m1", temperature=0.2, api_key="k")
        assert a is b, "相同参数应复用同一实例"
        assert len(calls) == 1, "应只调用一次 build_llm"
    finally:
        LLM_base._llm_cache.clear()


def test_get_llm_new_instance_on_temp_change(monkeypatch):
    seen = []
    monkeypatch.setattr(
        LLM_base,
        "build_llm",
        lambda **kw: (seen.append(kw), object()) and seen[-1],
    )
    LLM_base._llm_cache.clear()
    try:
        a = LLM_base.get_llm(model_name="m", temperature=0.0, api_key="k")
        b = LLM_base.get_llm(model_name="m", temperature=0.9, api_key="k")
        assert a is not b
        assert len(seen) == 2
    finally:
        LLM_base._llm_cache.clear()


def test_default_temperature_resolved_to_config(monkeypatch):
    kwargs = {}
    monkeypatch.setattr(LLM_base, "build_llm", lambda **kw: (kwargs.update(kw), object())[1])
    LLM_base._llm_cache.clear()
    try:
        LLM_base.get_llm(model_name="m", api_key="k")  # 不传 temperature
        # get_llm 会把默认温度显式解析并传给 build_llm(config 默认 0.7)
        assert kwargs["temperature"] == 0.7
        assert kwargs["model_name"] == "m"
    finally:
        LLM_base._llm_cache.clear()


class _SpyLLM:
    """记录 with_structured_output 的入参,用于锁定结构化输出的调用契约。"""

    def __init__(self):
        self.seen = {}

    def with_structured_output(self, schema, **kwargs):
        self.seen = {"schema": schema, **kwargs}
        return "wrapped"


def test_structured_output_uses_tool_calling():
    """结构化输出走工具调用并要求服务端自选工具。

    json_schema 只有少数服务实现;具名 tool_choice 在部分服务的思考模式下被拒绝,
    两者任一都会让记忆固化/隐私判定/复核整条链路失效。
    """
    spy = _SpyLLM()
    out = LLM_base.structured_llm(spy, dict)
    assert out == "wrapped"
    assert spy.seen["schema"] is dict
    assert spy.seen["method"] == "function_calling"
    assert spy.seen["tool_choice"] == "auto"
    # 请求体不带 parallel_tool_calls 等可选字段,保持最小标准集
    assert spy.seen["parallel_tool_calls"] is None
