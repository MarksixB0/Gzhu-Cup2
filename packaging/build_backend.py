# -*- coding: utf-8 -*-
"""把 Python 后端打成可分发的可执行文件(桌面安装包内置,目标机器无需 Python)。

用法(在仓库根):
    uv run python packaging/build_backend.py

产出:
    apps/desktop/backend-dist/gzhu-backend/gzhu-backend.exe   ← PyInstaller onedir
    同级的 _internal/ 由 electron-builder 一并拷进 <安装目录>/resources/backend/

为什么要脚本而不是一行命令
--------------------------
1) PyInstaller 的 ``--add-data`` 源路径是**相对 spec 目录**解析的:写成
   ``src/gzhu_cup2/...`` 而 spec 落在 ``packaging/`` 时,它会去找
   ``packaging/src/gzhu_cup2/...`` 然后报 "Unable to find"。这里用 pathlib 算绝对路径,
   既绕开该坑、又不会把某台机器的路径写死。
2) 依赖收集参数(langchain/langgraph/pydantic/uvicorn 全量收集 + sqlite 驱动
   hiddenimport)是打包能否跑起来的关键,集中在这里便于复核。

资源与路径约定(与 apps/desktop/electron/main.cjs 一一对应)
----------------------------------------------------
- exe → ``apps/desktop/backend-dist/gzhu-backend/``,electron-builder 拷成
  ``resources/backend/``,主进程按 ``resources/backend/gzhu-backend.exe`` 调用;
- **角色卡不再随包分发**(2026-09 起):它是用户自己创建的内容,落点由
  GZHU_CARD_DIR 重定向到数据目录(见 config.PET_CARD_DIR)。早期版本把 sayori.json
  打进 ``_internal/pet/character/``,而那在只读的安装目录里 —— 用户既加不了新卡、
  也改不动旧卡,等于"预设了人格且无法构建"。
- 用户数据一律不写安装目录:主进程把数据根经 GZHU_DATA_DIR 注入,再派生
  LONG_MEMORY_DIR / GZHU_CONNECTION_PATH / GZHU_CARD_DIR(见 main.cjs 的 backendEnv)。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "packaging" / "backend_entry.py"
SPEC_DIR = ROOT / "packaging"
WORK_DIR = ROOT / "packaging" / "build"
DIST_DIR = ROOT / "apps" / "desktop" / "backend-dist"

# 需要整包收集的第三方库:它们的子模块多为动态导入,PyInstaller 静态分析看不全。
COLLECT_ALL = [
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "pydantic",
    "uvicorn",
    "fastapi",
]
# 显式补的隐藏导入:
#   aiosqlite —— langgraph 的 sqlite checkpointer 在运行期才导入;
#   langgraph_checkpoint_sqlite / langchain_text_splitters —— 单模块包,
#   --collect-all 对它们无效(会警告 "not a package"),只能用 hidden-import。
HIDDEN_IMPORTS = [
    "aiosqlite",
    "langgraph_checkpoint_sqlite",
    "langchain_text_splitters",
]


def build_argv() -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "gzhu-backend",
        # src/ 是包根(gzhu_cup2 在其中);仓库根仍留着供相对资源解析
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "src"),
    ]
    for pkg in COLLECT_ALL:
        argv += ["--collect-all", pkg]
    for mod in HIDDEN_IMPORTS:
        argv += ["--hidden-import", mod]
    argv += [
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--distpath",
        str(DIST_DIR),
        str(ENTRY),
    ]
    return argv


def strip_py_sources(bundle: Path) -> tuple[int, int]:
    """删除 ``_internal`` 下随包拷进来的 ``.py`` 源码,返回 ``(个数, 字节数)``。

    为什么要删:``--collect-all`` 会把被收集包的**源码**当数据文件一并拷进来
    (实测 510 个 / 6.94MB)。运行期真正加载的是 PYZ 里编译好的模块,这些源码
    对运行毫无贡献,却会**1:1 变成安装包体积**(源码与 PNG 一样压不动)。

    安全性:已做对照实验 —— 剥掉后后端启动正常、``/api/chat`` 走完整条 LLM
    调用链(含 langchain_openai → tiktoken),响应与未剥版本逐字一致。
    """
    internal = bundle / "_internal"
    if not internal.is_dir():
        return 0, 0
    files = [p for p in internal.rglob("*.py") if p.is_file()]
    freed = sum(p.stat().st_size for p in files)
    for p in files:
        p.unlink()
    return len(files), freed


def main() -> int:
    missing = [p for p in (ENTRY,) if not p.exists()]
    if missing:
        for p in missing:
            print(f"[backend] 缺少必需路径: {p}", file=sys.stderr)
        return 1

    # 每次全量重建:PyInstaller 的增量缓存最容易在改过依赖后产出半陈旧产物,
    # 而这里一次构建才一两分钟,不值得为省这点时间留下"改了没生效"的坑。
    shutil.rmtree(DIST_DIR / "gzhu-backend", ignore_errors=True)

    argv = build_argv()
    print("[backend] 开始打包 →", DIST_DIR)
    print("[backend] 条目:", ENTRY.relative_to(ROOT))
    result = subprocess.run(argv, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[backend] 打包失败(退出码 {result.returncode})", file=sys.stderr)
        return result.returncode

    exe = DIST_DIR / "gzhu-backend" / ("gzhu-backend.exe" if sys.platform == "win32" else "gzhu-backend")
    if not exe.exists():
        print(f"[backend] 打包结束但找不到可执行文件: {exe}", file=sys.stderr)
        return 1

    stripped_n, stripped_bytes = strip_py_sources(DIST_DIR / "gzhu-backend")
    if stripped_n:
        print(f"[backend] 已剥离随包 .py 源码: {stripped_n} 个 / {stripped_bytes / 1e6:.1f} MB")

    size_mb = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"[backend] 完成: {exe}（目录合计 {size_mb:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
