# -*- coding: utf-8 -*-
"""AI心理健康陪伴助手 - 命令行交互入口

用法:
    python main.py                      # 默认 thread_id=demo，连续对话
    python main.py --thread chat-1      # 指定会话 id（多开不同会话）
输入:
    - 直接打字 = 继续对话（同一会话自动累积历史）
    - quit / exit / 退出 = 结束
"""
import argparse
import os
import sys


from langchain_core.messages import HumanMessage
from gzhu_cup2.agent.graphs.main_graph import build_graph
from gzhu_cup2.agent.long_memory.worker import ensure_worker_running


def show_status(r):
    """打印一轮的状态摘要。"""
    risk = r.get("risk_level", "?")
    emo = r.get("emotional_analysis")
    emo_txt = emo.emotion if emo else "?"
    print(f"[风险={risk} | 情绪={emo_txt}]")


def main():
    parser = argparse.ArgumentParser(description="AI心理健康陪伴命令行")
    parser.add_argument("--thread", default="demo", help="会话 id（默认 demo）")
    args = parser.parse_args()

    app = build_graph()
    cfg = {"configurable": {"thread_id": args.thread}}
    ensure_worker_running()  # CLI 下也启动后台记忆固化 worker(与 Web 行为一致)

    print("=" * 50)
    print("AI心理健康陪伴助手（输入内容对话，quit 退出）")
    print("=" * 50)

    while True:
        try:
            text = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见，照顾好自己。")
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "退出"):
            print("再见，照顾好自己。")
            break

        try:
            r = app.invoke(
                {"messages": [HumanMessage(content=text)], "user_id": args.thread},
                config=cfg,
            )
        except Exception as e:
            print(f"[出错] {e}")
            continue

        show_status(r)
        reply = r.get("messages", [])[-1].content if r.get("messages") else ""
        print(f"\nAI: {reply}")


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()   # Windows 控制台默认 GBK,中文输出会崩
    main()
