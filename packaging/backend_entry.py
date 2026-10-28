# -*- coding: utf-8 -*-
"""PyInstaller 打包入口:以编程方式启动 FastAPI 后端(供桌面安装包内置)。

为什么需要这个文件
------------------
开发态跑的是 ``uv run uvicorn gzhu_cup2.entrypoints.api:app``:uv 提供解释器、
cwd 是仓库根,uvicorn 用**字符串导入**。打包成 exe 后既没有 uv 也没有仓库根,
而且 PyInstaller 不会静态分析字符串导入 —— 所以这里改成**显式 import** 拿到
app 对象,再交给 ``uvicorn.run`` 启动。

路径约定(与 ``desktop/electron/main.cjs`` 的打包分支一一对应)
------------------------------------------------------------
主进程按 onedir 布局调用 ``<安装目录>/resources/backend/gzhu-backend.exe``,
并把 cwd 设为该目录。角色卡等随包资源位于冻结根(``_internal/``),由
``config.py`` 的 ``_BASE_DIR`` 自然解析到同一位置 —— 后端代码**不需要**判断
"是否冻结"。

用户数据不写安装目录:长期记忆 / 关怀状态 / API Key 配置若落在 Program Files
下会因权限失败。主进程通过 ``LONG_MEMORY_DIR`` 环境变量把数据目录指向
Electron 的 userData(``config.MEMORY_DIR`` 原生支持该变量),``runtime_connection``
的落点同理由主进程用 ``GZHU_CONNECTION_PATH`` 覆盖(见 config/runtime_connection.py)。
"""
import os
import sys

# 冻结态兜底:把可执行文件所在目录补进 sys.path,防止 cwd 变化导致的解析失败。
# 包本身(gzhu_cup2)由 PyInstaller 打进归档,经 frozen importer 直接导入,
# 不依赖 sys.path。
if getattr(sys, "frozen", False):
    _HERE = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isdir(_HERE) and _HERE not in sys.path:
        sys.path.insert(0, _HERE)

import uvicorn  # noqa: E402

from gzhu_cup2.entrypoints.api import app  # noqa: E402


def _parse_args(argv: list[str]) -> tuple[str, int]:
    """解析 ``--host/--port``(与 main.cjs 的传参保持一致)。"""
    host = "127.0.0.1"
    port = 8000
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
        elif arg == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass
    return host, port


def main() -> None:
    host, port = _parse_args(sys.argv[1:])
    # 传 app 对象而非 "server:app" 字符串:冻结态下字符串导入会失败
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
