# -*- coding: utf-8 -*-
"""pytest 公共夹具与策略。

约定:
  - 不对真实模型服务 / 真实 embedding 网络发起任何调用。
  - “图内节点”的 LLM 一律用 FakeLLM(monkeypatch 三个导入 get_llm 的节点模块,
    使其 `_resolve_llm()` 返回假引擎),故可无 Key 稳定跑出完整图/API 流程断言。
  - 会话存 server 进程内 `_threads`/`InMemorySaver`,每个用例前清空隔离。

本目录即仓库根下的 tests/;后端已按 src 布局安装为 gzhu_cup2 包,
故测试直接用 `from gzhu_cup2.agent.nodes import …` 一类绝对导入。
"""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

# ---- 让 Agent 目录可像“顶层包”被导入(与 server/main 相同假定) ----
_ROOT = Path(__file__).resolve().parent.parent

# 单元级纯逻辑测试常会 import core/nodes,提前把 embedding 镜像环境变量固定,防误下模型
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


# ============================ 工作区临时目录(全项目共用一份) ============================
# 为什么不用 pytest 的 tmp_path:本机 ``os.mkdir(p, 0o700)`` 建完即不可访问,而 pytest 的
# tmpdir 插件正是用 ``mode=0o700`` 建 basetemp(见仓库根 conftest.py 的说明),
# 于是 setup 阶段就 PermissionError。
# ⚠ 此前 test_memory_paths / test_care_llm_content / test_session_persistence 各写了一份
#   几乎相同的"自建自清临时目录"—— 同一件事多份实现正是本项目反复出现的毛病,
#   且三份里只有一份会在 Windows 上因句柄未释放而残留。统一到这里。
@pytest.fixture
def workdir():
    """工作区内的独立临时目录(自建自清,不依赖系统临时目录)。"""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "_test_artifacts", uuid.uuid4().hex)
    os.makedirs(d, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
        parent = os.path.dirname(d)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)


# ============================ 假 LLM 引擎 ============================
class FakeLLM:
    """模拟 ChatOpenAI:支持节点用到的普通文本调用 invoke(messages).content -> str。"""

    def __init__(self, model_name="fake-model", temperature=0.5, reply="(假回复) 我很关心你。"):
        self.model_name = model_name
        self.temperature = temperature
        self.reply = reply
        # 可被测试观察调用次数
        self.invoke_count = 0

    # ---- 普通文本调用(generate/crisis 混用) ----
    def invoke(self, messages, **kwargs):
        self.invoke_count += 1
        from langchain_core.messages import AIMessage
        return AIMessage(content=self.reply)


@pytest.fixture
def fake_llm(monkeypatch):
    """把图里两个消费节点的 LLM 切换成 FakeLLM。

    原理:节点模块内 `from core.models.LLM_base import get_llm` 是值绑定,
    替换 `nodes.<x>.get_llm` 即可让 `_resolve_llm()` 返回假引擎(无需触网)。
    """
    llm = FakeLLM()

    def _install():
        from gzhu_cup2.agent.nodes import generate_response, handle_crisis
        for mod in (generate_response, handle_crisis):
            monkeypatch.setattr(mod, "get_llm", lambda *a, **k: llm)
        return llm

    _install()
    return llm


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    """始终给 server/LLM 一套假连接参数,避免 503 / KeyError。

    模型名与 Base URL 无内置默认(配置层留空),故在此一并提供,
    使不关心连接来源的用例仍能走到图与路由逻辑。
    """
    from gzhu_cup2.agent.config import config as cfg

    monkeypatch.setenv("LLM_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(cfg, "MODEL_NAME", "test-model")


@pytest.fixture(autouse=True)
def _fresh_memory_store(monkeypatch):
    """长期记忆测试隔离:强制内存后端 + 每用例重置单例。

    注意:后台记忆 worker 只在 server lifespan(TestClient 上下文)中启动,
    本套测试以裸 TestClient 运行,不会启动 worker 线程。
    """
    monkeypatch.setenv("LONG_MEMORY_BACKEND", "memory")
    from gzhu_cup2.agent.long_memory import store as mem_store
    mem_store.reset_store()
    yield
    mem_store.reset_store()


# ============================ 「API 连接」持久化隔离 ============================
# 该 JSON 在仓库里可能真实存在(开发者从设置面板保存过连接)。
_CONN_ISOLATION_FILE = (
    Path(__file__).resolve().parent / "_conn_isolation" / "runtime_connection.json"
)


@pytest.fixture(autouse=True)
def _isolated_connection(monkeypatch):
    """把「API 连接」持久化 JSON 指向一个不存在的测试文件。

    必要性:`config/runtime_connection.json` 是开发者本地文件,若测试读到它,
    `test_chat_without_key_returns_503` 会因为"连接里仍存着 Key"而拿到 200 ——
    测试结果将依赖本地机器状态(实测就是如此)。统一隔离后结果才确定。
    需要真实读写的用例(test_connection.py 的 conn_env)可自行 monkeypatch 覆盖。
    """
    from gzhu_cup2.agent.config import runtime_connection
    from gzhu_cup2.agent.config import config as cfg
    try:
        if _CONN_ISOLATION_FILE.exists():
            _CONN_ISOLATION_FILE.unlink()
    except OSError:
        pass
    monkeypatch.setattr(runtime_connection, "_CONN_FILE", _CONN_ISOLATION_FILE)
    # 迁移源也必须隔离:否则首次 load_state() 会把开发者真实的旧连接文件(明文 Key)
    # 搬进这个测试文件,夹具清理时连同真实数据一起删掉。
    monkeypatch.setattr(
        cfg, "LEGACY_RUNTIME_CONNECTION_PATH",
        str(_CONN_ISOLATION_FILE.parent / "legacy_runtime_connection.json"),
    )
    monkeypatch.setattr(runtime_connection, "_MIGRATION_DONE", False)
    yield
    try:
        if _CONN_ISOLATION_FILE.exists():
            _CONN_ISOLATION_FILE.unlink()
        parent = _CONN_ISOLATION_FILE.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


# ============================ 「记忆隐私」状态隔离 ============================
# 与上面的「API 连接」**同一类坑**:``memory_privacy.json`` 在仓库里可能真实存在
# (开发者在界面点过「暂停记录」,或在对话里说过"别记下来"被 consent 落地),
# 而 ``nodes/capture_memory.py`` 的写入门控**直接读它** —— 于是测试结果依赖本机状态。
#
# 实测:真实文件里 ``local_user`` 被暂停后,``test_identity`` / ``test_memory`` 中
# 3 条断言 pending 的用例全部失败(get_pending 返回 None)。
_PRIVACY_ISOLATION_FILE = (
    Path(__file__).resolve().parent / "_privacy_isolation" / "memory_privacy.json"
)


def _drop_privacy_file():
    try:
        if _PRIVACY_ISOLATION_FILE.exists():
            _PRIVACY_ISOLATION_FILE.unlink()
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolated_privacy():
    """把「记忆隐私」状态指向测试文件(等价于"从未暂停记录")。

    默认隔离即可,不需要 monkeypatch:``privacy`` 的公开 ``set_state_path()``
    正是为"测试/部署"准备的(空串 = 回到 config 默认)。
    需要真实读写开关的用例(test_privacy*.py / test_settings_api.py)照旧用
    ``privacy_state_file`` 夹具覆盖,两者叠加时以更晚建立的为准。
    """
    from gzhu_cup2.agent.long_memory import privacy as mprivacy

    _drop_privacy_file()
    mprivacy.set_state_path(str(_PRIVACY_ISOLATION_FILE))
    mprivacy.reset()
    yield
    mprivacy.set_state_path("")     # 空串 = 回到 config 默认路径
    _drop_privacy_file()
    try:
        parent = _PRIVACY_ISOLATION_FILE.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


# ============================ 「用户自述资料」状态隔离 ============================
# 与上面两处**同一类坑**:``user_profile.json`` 在开发机上可能真实存在(用户填过称呼),
# 而它会被 ``pet.character.renderer.resolve_user_name`` 与生成提示词直接读取 ——
# 不隔离的话,"提示词里的 {{user}} 应当是默认值"这类断言会依赖本机状态。
_USER_PROFILE_ISOLATION_FILE = (
    Path(__file__).resolve().parent / "_profile_isolation" / "user_profile.json"
)


def _drop_user_profile_file():
    try:
        if _USER_PROFILE_ISOLATION_FILE.exists():
            _USER_PROFILE_ISOLATION_FILE.unlink()
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolated_user_profile():
    """把用户自述资料指向测试文件(等价于"从未填写过")。

    公开 ``set_state_path()`` 就是为"测试/部署"准备的(空串 = 回到 config 默认)。
    需要真实读写资料的用例(set_state_path 到自己的临时文件)照旧覆盖即可,
    两者叠加时以更晚建立的为准。
    """
    from gzhu_cup2.agent.core import user_profile as uprofile

    _drop_user_profile_file()
    uprofile.set_state_path(str(_USER_PROFILE_ISOLATION_FILE))
    uprofile.reset()
    yield
    uprofile.set_state_path("")     # 空串 = 回到 config 默认路径
    _drop_user_profile_file()
    try:
        parent = _USER_PROFILE_ISOLATION_FILE.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _no_privacy_llm(monkeypatch):
    """默认关掉「记忆隐私意愿」的**模型兜底层**,让套件保持离线与确定。

    ``long_memory/consent.py`` 是两级的:确定性规则(离线) + 模型兜底(长尾写法)。
    后者在规则没命中、且文本沾了话题边时会调一次小模型 —— 测试里必须替换成
    确定实现,否则会真的联网、且结果不可复现。

    需要验证该路径的用例自行 monkeypatch ``consent._classify_llm`` 覆盖即可。
    """
    from gzhu_cup2.agent.long_memory import consent
    monkeypatch.setattr(consent, "_classify_llm", lambda text: {})
    yield


@pytest.fixture
def api_client(fake_llm, monkeypatch):
    """FastAPI TestClient,连假 LLM(重),并对线程全局做每用例重置。

    fake_llm 已在调用本 fixture 时把两个节点模块的 get_llm 换成假引擎;
    图是惰性单例(web/engine.py),此处重置强制首次请求时用假引擎重建。
    该 import 会拉起较重栈(torch/agent),但按 pytest 会话只做一次。
    """
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.web import engine, sessions

    sessions.reset()   # 清空会话记录(进程内,跨用例会污染)
    engine.reset()     # 丢弃图单例 → 下次请求用已 patch 的假 get_llm 重建
    return TestClient(server_mod.app)


# ============================ 主动关怀参数:测试期固定为「生产保守值」 ============================
# 背景:config.py 末尾有 DEV_FAST_MODE 开关(把主动回复提速到秒级,便于开发联调)。
# 若用例直接读 config,行为就会随这个开关漂移 —— 实测开着它会让 3 个用例失败
# (probe 窗口被放宽、petlife 36h 间隔失效、checkin 48h 门槛失效)。
#
# ⚠ 下面这张表**只是补充**:被 DEV_FAST_MODE 改写过的键由 `cfg.PROD_DEFAULTS`
#   (config 自己在应用预设前抓的快照)统一覆盖,不再手抄 —— 见夹具的说明。
#   这里保留的是预设碰不到、但同样值得钉死的键。
_CARE_PROD_DEFAULTS = {
    "CARE_ENABLED": True,
    "CARE_DEFAULT_ENABLED": True,
    "CARE_SOOTHE_NEG_THRESHOLD": 0.55,
    "CARE_PROBE_MAX_CANDIDATES": 2,
    "CARE_MIN_EVAL_INTERVAL_SECONDS": 5,
    "CARE_MESSAGE_MAX_TOKENS": 200,
    "CARE_MESSAGE_MAX_LEN": 240,
    "CARE_PENDING_MAX_AGE_HOURS": 168,
    "CARE_QUIET_ASK_WHEN_UNANSWERED": True,
}


@pytest.fixture(autouse=True)
def _stable_care_thresholds(monkeypatch):
    """把主动关怀阈值固定在「生产保守值」,让测试结果与 DEV_FAST_MODE 无关。

    ⚠ 生产值**不在这里手抄**:``cfg.PROD_DEFAULTS`` 是 config 在"应用 dev 预设之前"
      抓的快照。此前这里手抄了一份,与 config 各写一份 —— 新增一个 dev 开关就要记得
      改两处,漏一处用例就会静默依赖于开发开关(这正是本夹具当初存在的原因)。
    下面的显式表只保留 config 快照**没覆盖**的那些键(dev 预设不碰、但值得钉死),
    两者合并时以 config 快照为准。
    """
    from gzhu_cup2.agent.config import config as cfg
    pinned = {**_CARE_PROD_DEFAULTS, **(getattr(cfg, "PROD_DEFAULTS", None) or {})}
    for key, value in pinned.items():
        monkeypatch.setattr(cfg, key, value)
    yield


# ============================ 运行时状态文件隔离(两域共用一套写法) ============================
# 此前 test_pet_pkg / test_pet_api / test_care_pkg / test_petlife_e2e 各自复制了
# 一份几乎相同的夹具,且都 monkeypatch 私有 _STATE_PATH。这里统一收拢,并改用
# **公开的 set_state_path()** —— 那正是该函数存在的意义("测试/部署用")。


def _redirect_state(module, filename):
    """把某个 runtime 模块的状态文件指到 test/ 下的临时文件。

    返回 (path, 还原函数);还原函数会把路径交回该域的 config 默认值。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if os.path.exists(path):
        os.remove(path)
    module.set_state_path(path)
    module.reset()

    def _restore():
        module.set_state_path("")  # 空串 = 回到该域 config 默认路径
        try:
            os.remove(path)
        except OSError:
            pass

    return path, _restore


@pytest.fixture
def care_state_file():
    """关怀运行时状态隔离(自建自清,用完回到默认路径)。"""
    from gzhu_cup2.agent.care import runtime as cruntime
    path, restore = _redirect_state(cruntime, "care_runtime_test.json")
    yield path
    restore()


@pytest.fixture
def pet_state_file():
    """桌宠运行时状态隔离(自建自清,用完回到默认路径)。"""
    from gzhu_cup2.agent.pet import runtime as pruntime
    path, restore = _redirect_state(pruntime, "pet_runtime_test.json")
    yield path
    restore()


@pytest.fixture
def privacy_state_file():
    """记忆隐私状态隔离(暂停记录开关;自建自清,用完回到默认路径)。"""
    from gzhu_cup2.agent.long_memory import privacy as mprivacy
    path, restore = _redirect_state(mprivacy, "memory_privacy_test.json")
    yield path
    restore()


# ============================ 角色卡隔离 ============================
# 与上面「API 连接」「记忆隐私」同一类坑:PET_CARD_DIR 默认落在数据目录里,开发者本机
# 可能真的有卡、甚至启用了某张 —— 那样用例结果会依赖本机状态。
#
# 还有方向相反、更要紧的一条:仓库里**不再附带任何预设角色卡**(人格由用户自己创建),
# 所以这里不只隔离,还要保证"默认一张卡都没有" —— 「没有卡就完整回落」正是本项目
# 最需要守住的一条语义(见 loader / renderer 的 docstring)。
_CARD_ISOLATION_DIR = Path(__file__).resolve().parent / "_card_isolation"


def _drop_card_isolation():
    shutil.rmtree(_CARD_ISOLATION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_cards(monkeypatch):
    """把角色卡目录与「启用哪张卡」的状态都指到测试临时目录(默认空)。"""
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.pet.character import loader as cloader

    _drop_card_isolation()
    monkeypatch.setattr(cfg, "PET_CARD_DIR", str(_CARD_ISOLATION_DIR / "cards"))
    monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", "")
    cloader.set_state_path(str(_CARD_ISOLATION_DIR / "active_card.json"))
    cloader.clear_cache()
    yield
    cloader.set_state_path("")     # 空串 = 回到 config 默认路径
    cloader.clear_cache()
    _drop_card_isolation()


# ---- 测试卡:仓库里没有预设人格,用例需要的卡一律在这里现造 ----
TEST_CARD_NAME = "测试角色"
TEST_USER_NAME = "小测"

# 这些章节名必须全部出现在渲染产物里(TestRenderer 会逐条核对)。
# 刻意用中性措辞:它只是"一张结构齐全的卡",不是任何预设人格。
TEST_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": TEST_CARD_NAME,
        "description": (
            "【身份】你是测试角色,用来验证角色卡链路。\n"
            "【核心人格】稳定、可预期、不即兴发挥。\n"
            "【核心价值】准确 ＞ 简短 ＞ 好看。\n"
            f"【与 {TEST_USER_NAME}】长期协作的搭档。\n"
            "【行为模式】每次都用同一套口径作答。\n"
            "【兴趣】测试本身。\n"
            "【知识边界】只讲有依据的内容。\n"
            "【长期关系】可靠且一致。\n"
            "【AI 行为】不替使用者做决定。\n"
        ),
        "scenario": "你正与 {{user}} 一起跑一轮测试。",
        "post_history_instructions": (
            "【交流风格】平实直白。\n"
            "【表达方式】先给结论。\n"
            "【标点】少用感叹号。\n"
            "【回复规则】严格按测试要求输出。\n"
            "【输出规则】不额外解释。"
        ),
        # mes_example 刻意留宏,用来验证 {{char}} / {{user}} 会被替换
        "mes_example": "{{user}}:在吗？\n{{char}}:在的,随时可以开始。",
        "first_mes": "初次见面,我们从哪里开始？",
        "alternate_greetings": ["第二个开场白。", "第三个开场白。"],
        "extensions": {"companion": {"user_default_name": TEST_USER_NAME}},
    },
}


def write_test_card(name: str = TEST_CARD_NAME, raw: dict | None = None) -> str:
    """把测试卡写进当前(已隔离的)卡目录,返回卡名。

    供需要"磁盘上真的有一张卡"的用例调用 —— 它们要么走 loader 的读盘路径,
    要么走 /api/pet/* 的端到端流程,只给内存里的卡对象不够。
    """
    from gzhu_cup2.agent.pet.character import loader as cloader

    d = cloader.card_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw if raw is not None else TEST_CARD, f, ensure_ascii=False)
    cloader.clear_cache()
    return name


@pytest.fixture
def test_card(monkeypatch):
    """铺一张测试卡并**启用**它,返回卡名。

    需要"有卡但不启用"的用例自行 monkeypatch ``cfg.PET_ACTIVE_CARD`` 置空即可。
    """
    from gzhu_cup2.agent.config import config as cfg

    name = write_test_card()
    monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", name)
    return name
