# -*- coding: utf-8 -*-
"""后台记忆 worker:单例 daemon 线程,驱动「记忆图」固化到期段并周期执行遗忘。

可靠性设计:
  - daemon 线程:进程退出不阻塞;真正不丢数据的根基是「捕获已落盘 + 启动补跑」;
  - 启动补跑:worker 每次启动都会扫描遗留 pending,进程中途被杀后下次启动自动续跑;
  - 幂等:固化用确定性键 + 事实合并(见 store.commit_segment),重复执行不产生重复记忆;
  - 不依赖退出 flush:固化失败时 pending 原样保留,并按指数退避自动重试,不狂打日志。
"""
import threading
import time
import logging

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import forget

logger = logging.getLogger(__name__)

_state = {
    "thread": None,
    "stop": None,   # threading.Event
    "wake": None,   # threading.Event(每轮回复后唤醒,缩短跨会话固化延迟)
    "graph": None,
    "last_forget": 0.0,
    "failures": {},  # user -> 连续失败次数(退避用)
    "cooldown": {},  # user -> 下一次允许固化尝试的时间戳
}


def _get_graph():
    if _state["graph"] is None:
        from gzhu_cup2.agent.graphs.memory_graph import build_memory_graph
        _state["graph"] = build_memory_graph()
    return _state["graph"]


def _backoff_seconds(user: str) -> float:
    """连续失败按 base×2^n 指数退避,封顶 max;成功时清零。"""
    n = _state["failures"].get(user, 0)
    wait = cfg.MEMORY_CONSOLIDATE_RETRY_BASE_SECONDS * (2 ** max(0, n - 1))
    return min(wait, cfg.MEMORY_CONSOLIDATE_RETRY_MAX_SECONDS)


def _tick() -> None:
    """扫描一遍:固化所有到期段(带失败退避);按间隔执行遗忘。

    优先级:含 high/crisis 证据的段(urgent)立即固化,不等轮数/空闲/跨线程。
    """
    now = time.time()
    for user in mstore.list_memory_users():
        if now < _state["cooldown"].get(user, 0.0):
            continue  # 该用户近期固化失败过,等待退避期
        pending = mstore.get_pending(user)
        # 危机级证据(high/crisis)立即固化,不等 5 轮/空闲超时/跨线程
        if not mstore.pending_is_urgent(pending) and not mstore.pending_is_due(pending, now):
            continue
        try:
            _get_graph().invoke({"user_id": user, "pending": pending})
            _state["failures"].pop(user, None)
            _state["cooldown"].pop(user, None)
        except Exception as e:  # noqa: BLE001 - 单段失败不拖垮 worker,进入退避
            _state["failures"][user] = _state["failures"].get(user, 0) + 1
            wait = _backoff_seconds(user)
            _state["cooldown"][user] = time.time() + wait
            logger.warning(
                "记忆固化失败 user=%s,将在 %.0f 秒后重试(第 %d 次): %s",
                user, wait, _state["failures"][user], e,
            )
    if now - _state["last_forget"] >= cfg.MEMORY_FORGET_INTERVAL_SECONDS:
        try:
            forget.apply_forgetting_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("遗忘维护失败: %s", e)
        finally:
            _state["last_forget"] = now
    # 复核调查通道(⑦):排空边界信号触发的复核任务(后台执行,不阻塞回复)
    if getattr(cfg, "REVIEW_ENABLED", True):
        try:
            from gzhu_cup2.agent.long_memory import review_channel
            review_channel.drain_due(now)
        except Exception as e:  # noqa: BLE001 - worker 永不死
            logger.warning("复核通道排空失败: %s", e)


def _loop() -> None:
    poll = max(0.5, float(cfg.MEMORY_WORKER_POLL_SECONDS))
    _state["last_forget"] = time.time()
    while not _state["stop"].is_set():
        try:
            _tick()
        except Exception:  # noqa: BLE001 - worker 永不死
            logger.exception("memory worker tick error")
        # 等待唤醒或被轮询超时打断
        _state["wake"].wait(poll)
        _state["wake"].clear()


def ensure_worker_running() -> bool:
    """幂等启动 worker;已在运行返回 True(不做二次启动)。"""
    thread = _state["thread"]
    if thread is not None and thread.is_alive():
        return True
    _state["stop"] = threading.Event()
    _state["wake"] = threading.Event()
    t = threading.Thread(target=_loop, name="long-memory-worker", daemon=True)
    _state["thread"] = t
    t.start()
    return True


def stop_worker() -> None:
    """请求优雅停止(设置 stop 并唤醒;daemon 线程兜底,不强等)。"""
    stop = _state["stop"]
    if stop is not None:
        stop.set()
    wake = _state["wake"]
    if wake is not None:
        wake.set()
    thread = _state["thread"]
    if thread is not None:
        thread.join(timeout=3)
    _state["thread"] = None


def wake_worker() -> None:
    """每轮回复提交后调用,让 worker 尽快处理到期段(缩短固化延迟)。"""
    wake = _state["wake"]
    if wake is not None:
        wake.set()
