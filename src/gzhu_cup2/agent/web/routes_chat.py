# -*- coding: utf-8 -*-
"""对话与会话路由:/api/chat、/api/chat/stream、/api/threads*。

流式采用 A 方案:线程内跑完整 langgraph 管线,SSE 分阶段推送
「准备 → 思考心跳 → 逐 token → 结果」。真正的逐 token 需改动 agent 节点,
本阶段有意不做(见 README 边界说明)。
"""
import asyncio
import json
import time
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.config import runtime_connection
from gzhu_cup2.agent.long_memory.worker import wake_worker
from gzhu_cup2.agent.web import engine, sessions

router = APIRouter()

def _need_config_detail() -> str:
    """503 文案:列出当前缺失的连接项,避免只提示 Key 而漏配端点或模型。"""
    missing = runtime_connection.missing_config()
    return (f"模型连接未配置(缺少 {'、'.join(missing)}):"
            "请在 设置 → 模型与连接 中填写并保存,"
            "或设置环境变量 LLM_API_KEY / OPENAI_BASE_URL 后重启服务")

# 用户标识请求头:长期记忆按它分区(见 web/sessions.resolve_user)。
# 缺省 / 非法 → 默认用户,故既有客户端行为完全不变。
_USER_HEADER = Header(
    default=None, alias="X-User-Id",
    description="长期记忆归属的用户键(可选;非法值回落默认用户)")


# ============================ 请求/响应模型 ============================
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户本轮输入")
    thread_id: Optional[str] = Field(default=None, description="会话 id,不传则新建")
    # 可选的模型覆盖参数(与 llm_params 同名,进 graph state);缺省用 config 默认
    model: Optional[str] = Field(default=None, description="模型 id 覆盖")
    temperature: Optional[float] = Field(default=None, description="温度覆盖(0-2)")


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60, description="新会话标题")


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    analysis: dict
    message: str = ""


class ThreadMeta(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    last_preview: str


class ThreadDetail(BaseModel):
    thread_id: str
    title: str
    turns: list
    created_at: str
    updated_at: str


def _llm_params_of(req) -> dict:
    """从请求提取 llm 覆盖参数(仅含显式提供的 key,便于 graph 兜底 config)。

    单点模式(MODEL_SINGLE_SOURCE=True)下忽略请求携带的 model ——
    模型一律以 config 的 MODEL_NAME 为准,避免被网页/浏览器残留覆盖。
    """
    p = {}
    if not cfg.MODEL_SINGLE_SOURCE and req.model:
        p["model"] = req.model
    if req.temperature is not None:
        p["temperature"] = req.temperature
    return p


def _touch_title(thread, message: str) -> None:
    """首条消息用作会话标题。"""
    if not thread.turns:
        thread.title = sessions.title_from(message)


# ============================ 非流式对话 ============================
@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, x_user_id: Optional[str] = _USER_HEADER):
    if not runtime_connection.is_ready():
        raise HTTPException(status_code=503, detail=_need_config_detail())
    user = sessions.resolve_user(x_user_id)
    thread_id = sessions.get_or_create(req.thread_id)
    thread = sessions.get(thread_id)
    _touch_title(thread, req.message)
    try:
        reply, state = engine.run_turn(thread_id, req.message,
                                       llm_params=_llm_params_of(req),
                                       user=user)
    except Exception as e:  # noqa: BLE001 - 转成 502 回给前端
        raise HTTPException(status_code=502, detail=f"本轮对话处理失败: {e}")

    analysis = engine.extract_analysis(state)
    sessions.commit_turn(thread, req.message, reply, analysis, user=user)
    wake_worker()  # 唤醒后台记忆 worker,尽早固化到期段
    return ChatResponse(thread_id=thread_id, reply=reply, analysis=analysis)


# ============================ 流式对话 ============================
def _pair_msg(payload: dict, event: str = "message") -> str:
    """把事件序列化为一条 SSE 数据消息。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, x_user_id: Optional[str] = _USER_HEADER):
    if not runtime_connection.is_ready():
        raise HTTPException(status_code=503, detail=_need_config_detail())
    user = sessions.resolve_user(x_user_id)
    thread_id = sessions.get_or_create(req.thread_id)
    thread = sessions.get(thread_id)
    _touch_title(thread, req.message)
    llm_params = _llm_params_of(req)

    async def event_gen():
        # 阶段 1:准备 + 首次 thinking(前端据此展示"正在想…")
        yield _pair_msg({"phase": "prepare", "thread_id": thread_id}, "phase")
        yield _pair_msg({"phase": "thinking"}, "phase")

        graph = engine.graph()
        config = {"configurable": {"thread_id": thread_id}}
        chunks: list = []
        got_token = False
        queue: asyncio.Queue = asyncio.Queue()

        async def run():
            """把图执行放线程里跑(锁在引擎层),事件经 queue 回主协程。"""
            try:
                await asyncio.to_thread(engine.lock.acquire)
                try:
                    async for ev in graph.astream_events(
                        {"messages": [HumanMessage(content=req.message,
                                                   additional_kwargs={"ts": time.time()})],
                         # 记忆按"用户"划分(见 web/sessions.resolve_user);thread 仅作会话记忆键
                         "user_id": user,
                         "llm_params": llm_params},
                        config=config,
                        version="v2",
                    ):
                        await queue.put(("ev", ev))
                finally:
                    engine.lock.release()
            except Exception as e:  # noqa: BLE001 - 作为 SSE error 事件回给前端
                await queue.put(("err", e))
            finally:
                await queue.put(("done", None))

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    if not got_token:
                        yield _pair_msg({"phase": "thinking"}, "phase")
                    continue
                if kind == "err":
                    yield _pair_msg({"phase": "error", "detail": str(payload)}, "error")
                    return
                if kind == "done":
                    break
                ev = payload
                if ev["event"] == "on_chat_model_stream":
                    node = (ev.get("metadata") or {}).get("langgraph_node")
                    if node in ("generate_response", "handle_crisis"):
                        text = getattr(ev["data"].get("chunk"), "content", "")
                        if isinstance(text, str) and text:
                            got_token = True
                            chunks.append(text)
                            yield _pair_msg({"content": text}, "token")
        finally:
            if not task.done():
                task.cancel()

        # 阶段 3:汇总(流空则回退 state)+ 落会话 + 完整结果
        snap = await graph.aget_state(config)
        values = snap.values if snap else {}
        reply = "".join(chunks)
        if not reply and values.get("messages"):
            reply = values["messages"][-1].content or ""
        analysis = engine.extract_analysis(values)
        sessions.commit_turn(thread, req.message, reply, analysis, user=user)
        wake_worker()
        yield _pair_msg({
            "phase": "result",
            "thread_id": thread_id,
            "reply": reply,
            "analysis": analysis,
        }, "result")
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ============================ 会话管理 ============================
@router.get("/api/threads", response_model=List[ThreadMeta])
def list_threads():
    threads = sorted(sessions.snapshot(), key=lambda t: t.updated_at, reverse=True)
    return [
        ThreadMeta(
            id=t.id, title=t.title,
            created_at=t.created_at, updated_at=t.updated_at,
            message_count=len(t.turns),
            last_preview=t.turns[-1]["ai"][:50] if t.turns else "",
        )
        for t in threads
    ]


@router.post("/api/threads")
def new_thread():
    return {"thread_id": sessions.get_or_create(None)}


@router.get("/api/threads/{thread_id}", response_model=ThreadDetail)
def get_thread(thread_id: str):
    t = sessions.get(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ThreadDetail(
        thread_id=t.id, title=t.title, turns=t.turns,
        created_at=t.created_at, updated_at=t.updated_at,
    )


@router.patch("/api/threads/{thread_id}")
def rename_thread(thread_id: str, req: RenameRequest):
    t = sessions.rename(thread_id, req.title.strip())
    if t is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "id": thread_id, "title": t.title}


@router.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str):
    sessions.delete(thread_id)
    return {"ok": True}


@router.delete("/api/threads")
def delete_all_threads(confirm: int = 0):
    """清空**全部会话历史**(不可逆)。

    单条删除只在侧边栏一条条做;本接口给「一次清空」的出口。
    保护同 /api/memory/all:① 必须显式带 ``confirm=1``;
    ② 返回**实际删了多少**条会话。
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="该操作不可逆,请带 confirm=1 显式确认")
    removed = len(sessions.snapshot())
    sessions.reset()
    return {"ok": True, "removed": removed}
