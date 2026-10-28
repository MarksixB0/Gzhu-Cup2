# -*- coding: utf-8 -*-
#加载并使用LLM
from typing import Optional
import threading

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.config.runtime_connection import (
    resolve_api_key,
    resolve_base_url,
    resolve_model,
)
from langchain_openai import ChatOpenAI


def build_llm(
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
        streaming: Optional[bool] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
) -> ChatOpenAI:
    """构建 ChatOpenAI 实例。连接三要素缺失时抛 ValueError。

    未显式传入的 model/api_key/base_url 统一走运行时连接解析
    (设置面板「模型与连接」> 环境变量 LLM_API_KEY / OPENAI_BASE_URL > config 出厂默认),
    支持热更新;出厂默认指向千问 DashScope 兼容端点,可被任一层覆盖。
    """
    _model_name = (model_name or resolve_model() or "").strip()
    _api_key = api_key or resolve_api_key()
    base_url = (base_url or resolve_base_url() or "").strip()
    temperature = temperature if temperature is not None else cfg.MODEL_TEMPERATURE
    streaming = streaming if streaming is not None else cfg.MODEL_IS_STREAMING

    missing = [
        label for label, value in (
            ("API Key", _api_key), ("Base URL", base_url), ("模型 ID", _model_name),
        ) if not (value or "").strip()
    ]
    if missing:
        raise ValueError(
            f"模型连接未配置(缺少 {'、'.join(missing)}):请在界面 设置 → 模型与连接 "
            "中填写并保存,或设置环境变量 LLM_API_KEY / OPENAI_BASE_URL 后重启服务。"
        )

    # 恒定声明关闭思考。千问系列默认开启,推理 token 与正文共享输出上限:
    # 不声明时首字延迟与总耗时接近翻倍,窄预算通道(主动消息、每日叙事)还会被
    # 推理占满而返回空正文。调用方在 extra_body 里显式给出该字段时不覆盖。
    _extra = dict(extra_body or {})
    if "enable_thinking" not in _extra:
        _extra["enable_thinking"] = False
    return ChatOpenAI(
        model=_model_name,
        api_key=_api_key,
        base_url=base_url,
        temperature=temperature,
        streaming=streaming,
        max_tokens=max_tokens,
        extra_body=_extra or None,
    )


# 按 (model_name, temperature, api_key, base_url) 键控的进程内 LLM 缓存。
# 说明:graph 各节点不再使用单一模块级单例,而是依据 state 中的
# llm_params(可选)选择模型/温度;同参复用实例,换参自动新建。
# 线程安全:关怀后台线程与主图请求并发调用 get_llm,用双检锁防止同参并发双建。
_llm_cache: dict = {}
_llm_cache_lock = threading.Lock()


def get_llm(
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        streaming: Optional[bool] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
) -> ChatOpenAI:
    """返回(按参数键控共享的)ChatOpenAI 实例。调用方把 llm_params 传入即可。"""
    _model = model_name or resolve_model()
    _temp = temperature if temperature is not None else cfg.MODEL_TEMPERATURE
    _key = api_key or resolve_api_key()
    _base = base_url or resolve_base_url()
    _stream = streaming if streaming is not None else cfg.MODEL_IS_STREAMING
    _extra = tuple(sorted((extra_body or {}).items()))
    _cache_key = (_model, _temp, _key, _base, _stream, max_tokens, _extra)
    cached = _llm_cache.get(_cache_key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(_cache_key)
        if cached is None:
            cached = build_llm(
                model_name=_model, temperature=_temp,
                api_key=api_key, base_url=base_url, streaming=streaming,
                max_tokens=max_tokens, extra_body=extra_body,
            )
            _llm_cache[_cache_key] = cached
    return cached


def clear_llm_cache() -> None:
    """清空 LLM 实例缓存(连接参数变化后调用)。

    持锁清空,与 get_llm 的双检锁一致。
    """
    with _llm_cache_lock:
        _llm_cache.clear()


def structured_llm(llm: ChatOpenAI, schema):
    """把 LLM 包装为结构化输出调用:走工具调用,并要求服务端按默认策略选工具。

    OpenAI 兼容服务对结构化输出的支持面差异很大:json_schema 仅少数服务实现,
    工具调用(带 JSON Schema 参数)是普遍能力,故统一走 function_calling。
    tool_choice 取 auto 而非具名工具:部分服务在思考模式下拒绝强制指定工具名,
    遇此限制会直接 400,导致抽取链路整体失效。
    """
    return llm.with_structured_output(
        schema, method="function_calling", tool_choice="auto",
        parallel_tool_calls=None)
