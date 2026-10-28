# -*- coding: utf-8 -*-
"""域无关的 JSON 状态文件(线程锁 + 原子替换 + 默认路径钩子)。

各域只声明:
  - 默认路径;
  - 空状态形状(``empty_factory``);
  - 可选:使用默认路径时的前置钩子(确保目录存在等)。

约定:
  - 一个域一份文件,互不干扰;
  - 写入先写 ``.tmp`` 再 ``os.replace``;
  - 全流程持同一把 ``RLock``;
  - 读出的 dict 用 ``empty_factory()`` 补齐缺失的顶层键;
  - ``on_default_path`` 仅在使用默认路径时触发。

只依赖标准库,不 import 业务域。
"""
import json
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional


class JsonStateFile:
    """一份带锁的 JSON 状态文件。"""

    def __init__(self, default_path: str,
                 empty_factory: Callable[[], dict],
                 on_default_path: Optional[Callable[[], Any]] = None) -> None:
        """
        default_path     默认文件路径(``set_path("")`` 时回到它);
        empty_factory    空状态工厂,同时用于"补齐缺失的顶层键";
        on_default_path  使用默认路径时执行的前置钩子(可为 None)。
        """
        self._default_path = str(default_path)
        self._empty_factory = empty_factory
        self._on_default_path = on_default_path
        self._path = self._default_path
        self._lock = threading.RLock()

    # ---------------- 路径 ----------------

    @property
    def path(self) -> str:
        """当前生效的文件路径。"""
        return self._path

    def is_default_path(self) -> bool:
        return self._path == self._default_path

    def set_path(self, path: Optional[str]) -> None:
        """重定向状态文件(测试/部署用);空串/None 回到默认路径。"""
        self._path = str(path) if path else self._default_path

    # ---------------- 内部读写(调用方须已持锁) ----------------

    def _prepare_dir(self) -> None:
        if self.is_default_path() and self._on_default_path is not None:
            self._on_default_path()

    def _load_unlocked(self) -> dict:
        self._prepare_dir()
        loaded: Any = {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            loaded = {}
        if not isinstance(loaded, dict):
            loaded = {}
        # 与原先各域的 setdefault 等价:缺哪个顶层键就补哪个
        return {**self._empty_factory(), **loaded}

    def _save_unlocked(self, data: dict) -> None:
        self._prepare_dir()
        path = self._path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    # ---------------- 对外 ----------------

    def reset(self) -> None:
        """清空为"空状态"(测试隔离用)。"""
        with self._lock:
            self._save_unlocked(self._empty_factory())

    @contextmanager
    def view(self) -> Iterator[dict]:
        """只读:锁内取当前状态,退出时**不落盘**。

        调用方在块内的修改不会被保存(与原先"只 load 不 save"的读函数一致)。
        """
        with self._lock:
            yield self._load_unlocked()

    @contextmanager
    def locked(self) -> Iterator[dict]:
        """读改写:锁内取当前状态,**正常退出自动落盘**(异常则不落盘)。"""
        with self._lock:
            data = self._load_unlocked()
            yield data
            self._save_unlocked(data)

    def read(self) -> dict:
        """取一份状态快照(只读场景的便捷写法)。"""
        with self.view() as data:
            return json.loads(json.dumps(data, ensure_ascii=False))
