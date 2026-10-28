# -*- coding: utf-8 -*-
"""仓库根 conftest:修复本机/沙箱下两个会让 pytest 无法运行的临时目录问题。

问题一:系统临时目录不可写
--------------------------
本机 ``%TEMP%`` 指向 ``C:\\Users\\<user>\\AppData\\Local\\Temp``,该目录不可写,
于是 ``tempfile.gettempdir()`` 抛 ``FileNotFoundError: No usable temporary
directory found``。pytest 的全局输出采集(``start_global_capturing`` 在
``pytest_load_initial_conftests`` 里就要建临时文件)会在任何用例执行之前崩掉,
表现为一条与测试本身毫无关系的环境错误。

问题二:mode 0o700 的目录立即不可访问
-------------------------------------
``tempfile.mkdtemp()`` 内部是 ``os.mkdir(path, 0o700)``。实测在本环境下:

    os.mkdir(p, None)   -> 可写
    os.mkdir(p, 0o755)  -> 可写
    os.mkdir(p, 0o777)  -> 可写
    os.mkdir(p, 0o700)  -> **建完就 PermissionError,连删都删不掉**

即 0o700 只给 owner 权限,而沙箱进程的有效身份与目录 owner 不一致,只能靠
group/other 位取权 —— 于是 0o700 目录变成死目录。仓库根目录里那一堆
``pytest-cache-files-*`` / ``.ptmp*`` / ``.pytest-run*`` 就是这样一批只读死目录。

修复方式
--------
根 conftest 在 pytest 采集开始**之前**被加载(``_preparse`` 里先
``_set_initial_conftests``,之后才轮到 ``pytest_load_initial_conftests`` 钩子),
因此可以在这里先动环境:

  1. 写探针文件判断系统临时目录是否真的可写;不可写才重定向到仓库内
     ``.pytest-tmp/``(可写则原样不动,不打扰健康机器);
  2. 探测 0o700 目录是否可访问;不可访问才给 ``tempfile.mkdtemp`` 打补丁,
     改用默认权限建目录(默认模式可用则完全不动 stdlib)。

这样 pytest 采集、``tmp_path`` 夹具、cacheprovider,以及被测代码里任何
``tempfile`` 调用都能正常工作,既不依赖系统临时目录,也不污染版本库
(``.gitignore`` 已排除这些目录)。

本文件只改环境变量、``tempfile`` 缓存与一个函数,不导入任何被测代码,
对导入顺序零影响。
"""
import os
import random
import string
import tempfile

_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_TMP = os.path.join(_ROOT, ".pytest-tmp")


# ============================ 问题一:临时目录重定向 ============================

def _writable(path: str) -> bool:
    """真实写一个探针文件来验证可写性(os.access 在 Windows 上不可靠)。"""
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _pick_temp_dir() -> tuple:
    """返回 (生效的临时目录, 是否发生了重定向)。"""
    tempfile.tempdir = None          # 清缓存,拿系统真实判定
    try:
        system_tmp = tempfile.gettempdir()
    except Exception:                # noqa: BLE001 - 系统目录不可用正是本函数要处理的场景
        system_tmp = ""

    if _writable(system_tmp):
        return system_tmp, False

    if not _writable(REPO_TMP):
        # 两处都写不了:不强行改环境,让 pytest 抛原始错误,便于排查
        return "", False

    for var in ("TEMP", "TMP", "TMPDIR"):
        os.environ[var] = REPO_TMP
    tempfile.tempdir = None
    return REPO_TMP, True


# ============================ 问题二:mkdtemp 权限位 ============================

def _mode_0700_broken(base: str) -> bool:
    """探测:用 0o700 建的目录是否**建完就不可写**。"""
    if not base:
        return False
    probe = os.path.join(base, ".mode0700_probe")
    try:
        os.mkdir(probe, 0o700)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        with open(os.path.join(probe, "w"), "w", encoding="utf-8") as f:
            f.write("ok")
        broken = False
    except OSError:
        broken = True
    try:
        os.rmdir(probe)
    except OSError:
        pass
    return broken


def _random_token(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _install_mkdtemp_fix() -> bool:
    """把 tempfile.mkdtemp 换成"默认权限建目录"版本(保持原语义)。"""
    if getattr(tempfile, "_dsh_mkdtemp_fixed", False):
        return True
    original = tempfile.mkdtemp

    def mkdtemp(suffix=None, prefix=None, dir=None):
        # bytes 参数与冷门用法一律交回原实现,降低行为偏差
        if isinstance(prefix, bytes) or isinstance(suffix, bytes) or isinstance(dir, bytes):
            return original(suffix, prefix, dir)
        s = "" if suffix is None else str(suffix)
        p = tempfile.gettempprefix() if prefix is None else str(prefix)
        d = tempfile.gettempdir() if dir is None else str(dir)
        for _ in range(10000):
            path = os.path.join(d, p + _random_token() + s)
            try:
                os.makedirs(path)        # 关键:不带 0o700
            except FileExistsError:
                continue
            return path
        raise FileExistsError("mkdtemp: 无法生成唯一目录名")

    tempfile.mkdtemp = mkdtemp
    tempfile._dsh_mkdtemp_fixed = True
    return True


# ============================ 执行 ============================
TMP_DIR, REDIRECTED = _pick_temp_dir()
MODE_0700_BROKEN = _mode_0700_broken(TMP_DIR)
MKDtemp_PATCHED = _install_mkdtemp_fix() if MODE_0700_BROKEN else False

if REDIRECTED or MKDtemp_PATCHED:
    import sys
    print(
        "[conftest] 临时目录环境已自愈: "
        f"TMP_DIR={TMP_DIR!r} 重定向={REDIRECTED} 0o700异常={MODE_0700_BROKEN} "
        f"mkdtemp已打补丁={MKDtemp_PATCHED}",
        file=sys.stderr,
    )
