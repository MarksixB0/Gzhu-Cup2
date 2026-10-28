# -*- coding: utf-8 -*-
"""runtime_connection + /api/connection* 路由测试(不触真实网络)。

要点:
  - 把 runtime_connection._CONN_FILE 重定向到 test/ 下的临时文件,避免污染仓库内 config 目录 JSON;
  - /api/connection/test 用 monkeypatch 顶掉 server.probe_models,不发起真实 OpenAI 请求;
  - 验证:保存后打码读取不回明文 Key、URL 校验、测试返回模型、chat 503 判定认可连接 Key。
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from gzhu_cup2.agent.config import runtime_connection  # noqa: E402
from gzhu_cup2.agent.config import config as cfg  # noqa: E402


@pytest.fixture
def c(api_client):
    """短别名(复用 conftest 的 api_client,含 fake_llm)。"""
    return api_client


@pytest.fixture
def conn_file():
    """连接 JSON 的隔离路径:用 test/ 下的临时文件(自建自清)。

    刻意不用 pytest 的 tmp_path:本机 Python 临时目录不可用(见仓库根 conftest.py
    的环境自愈);test/ 下的固定文件名同样干净,且失败时更容易直接查看。
    """
    path = Path(__file__).resolve().parent / "runtime_connection_test.json"
    if path.exists():
        path.unlink()
    try:
        yield path
    finally:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


@pytest.fixture
def conn_env(monkeypatch, conn_file):
    """把连接 JSON 与 env 隔离(清掉 env 与 config 出厂值),并返回连接文件路径。

    MODEL_NAME / MODEL_BASE_URL 一并清空:出厂值预置千问端点,这里模拟
    「用户把三项都清掉」的最坏状态,用于验证缺项判定。
    """
    monkeypatch.setattr(runtime_connection, "_CONN_FILE", conn_file)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(cfg, "MODEL_NAME", "")
    monkeypatch.setattr(cfg, "MODEL_BASE_URL", "")
    return conn_file


@pytest.fixture
def probe_models_fake(monkeypatch):
    """把路由层的 probe_models 换成假实现,避免触网。"""
    from gzhu_cup2.agent.web import routes_admin
    monkeypatch.setattr(
        routes_admin, "probe_models",
        lambda base_url, api_key: ["gpt-4o-mini", "gpt-4o"],
    )


class TestRuntimeConnectionUnit:
    def test_default_is_empty_and_not_ready(self, conn_env):
        assert runtime_connection.get_active() is None
        assert not runtime_connection.is_ready()

    def test_config_ships_usable_defaults(self):
        """出厂配置预置千问端点、模型与思考策略:安装后补 Key 即可对话。

        读源文件而非 cfg 属性:_fake_key 夹具会覆盖聚合命名空间,只有源文件能反映真实出厂值。
        """
        src = (Path(cfg.__file__).parent / "base.py").read_text(encoding="utf-8")
        assert 'MODEL_NAME = "qwen3.8-flash"' in src
        assert "dashscope.aliyuncs.com/compatible-mode/v1" in src

    def test_missing_config_lists_every_absent_field(self, conn_env):
        """三项全缺时逐项列出;补齐一项即从清单中消失。"""
        assert runtime_connection.missing_config() == ["API Key", "Base URL", "模型 ID"]

        runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-abc",
        })
        assert runtime_connection.missing_config() == ["模型 ID"]
        assert runtime_connection.is_ready() is False  # 有 url+key 但无模型 → 仍不可用

        runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-abc",
            "default_model": "gpt-4o-mini",
        })
        assert runtime_connection.missing_config() == []
        assert runtime_connection.is_ready() is True

    def test_save_then_active_and_read_back(self, conn_env, monkeypatch):
        state = runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1/",
            "api_key": "sk-secret-abc",
            "default_model": "gpt-4o-mini",
        })
        # 尾斜杠被去掉、key 保留明文供调用
        assert state["base_url"].endswith("/") is False
        assert runtime_connection.get_active()["api_key"] == "sk-secret-abc"
        assert runtime_connection.is_ready() is True
        # 打码只用于展示,不泄露完整 key
        assert "secret" not in runtime_connection.mask_key("sk-secret-abc")
        # resolve 优先级:关闭单点后 连接 > env
        monkeypatch.setattr(cfg, "MODEL_SINGLE_SOURCE", False)
        assert runtime_connection.resolve_base_url() == "https://api.example.com/v1"
        assert runtime_connection.resolve_model() == "gpt-4o-mini"

    def test_connection_model_takes_effect_by_default(self, conn_env):
        """默认非单点:连接的 default_model 生效,任意服务商可经设置面板切换模型。"""
        runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-abc",
            "default_model": "gpt-4o-mini",
        })
        assert cfg.MODEL_SINGLE_SOURCE is False
        assert runtime_connection.resolve_model() == "gpt-4o-mini"

    def test_single_source_ignores_connection_default_model(self, conn_env, monkeypatch):
        """显式开启单点模式:模型一律 config.MODEL_NAME,连接里的 default_model 不生效。"""
        monkeypatch.setattr(cfg, "MODEL_SINGLE_SOURCE", True)
        runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-abc",
            "default_model": "gpt-4o-mini",
        })
        assert runtime_connection.resolve_model() == cfg.MODEL_NAME

    def test_env_fallback_when_no_connection(self, monkeypatch, conn_env):
        """环境变量可独立提供三项配置;只给 Key 时不视为就绪。"""
        monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
        assert runtime_connection.get_active() is None
        assert runtime_connection.resolve_api_key() == "sk-from-env"
        assert runtime_connection.is_ready() is False          # 仍缺 Base URL 与模型
        assert runtime_connection.missing_config() == ["Base URL", "模型 ID"]

        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        assert runtime_connection.is_ready() is True
        assert runtime_connection.resolve_base_url() == "https://api.example.com/v1"
        assert runtime_connection.resolve_model() == "gpt-4o-mini"

    def test_connection_outranks_env_model(self, conn_env, monkeypatch):
        """连接里的模型优先于 LLM_MODEL:面板选择即最终生效值。"""
        monkeypatch.setenv("LLM_MODEL", "env-model")
        assert runtime_connection.resolve_model() == "env-model"
        runtime_connection.save_state({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-abc",
            "default_model": "panel-model",
        })
        assert runtime_connection.resolve_model() == "panel-model"


class TestThinkingAlwaysOff:
    """思考恒定关闭:请求体永远声明 enable_thinking=false。

    千问默认开启思考,推理 token 与正文共享输出上限。实测开启后首字延迟与总耗时
    接近翻倍,窄预算通道(主动消息、每日叙事)会被推理占满而返回空正文;该字段属
    DashScope 扩展,作为可选项存在时误设为开启或省略都会退化,故不提供配置面。
    """

    def _llm(self, **kw):
        from gzhu_cup2.agent.core.models.LLM_base import build_llm
        return build_llm(model_name="m", api_key="sk-x",
                         base_url="https://api.example.com/v1", **kw)

    def test_sends_explicit_false(self, conn_env):
        """不声明关闭时服务端按自身默认(千问为开启),必须显式写 false。"""
        assert self._llm().extra_body == {"enable_thinking": False}

    def test_stored_legacy_mode_is_ignored(self, conn_env):
        """旧配置里残留的 thinking 字段不再影响请求体。"""
        conn_env.write_text(json.dumps({
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-x",
            "default_model": "m",
            "models": [],
            "thinking": "on",
        }), encoding="utf-8")
        assert self._llm().extra_body == {"enable_thinking": False}

    def test_env_cannot_reenable(self, conn_env, monkeypatch):
        monkeypatch.setenv("LLM_THINKING", "on")
        assert self._llm().extra_body == {"enable_thinking": False}

    def test_max_tokens_passed_through(self, conn_env):
        """思考关闭后无需为推理预留额度,输出上限原样下发。"""
        assert self._llm(max_tokens=200).max_tokens == 200

    def test_explicit_extra_body_wins(self, conn_env):
        """调用方显式给出该字段时不被默认值覆盖(单通道实验入口)。"""
        llm = self._llm(extra_body={"enable_thinking": True})
        assert llm.extra_body == {"enable_thinking": True}


class TestConnectionRoutes:
    def test_get_defaults(self, c, conn_env):
        r = c.get("/api/connection")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "env_or_default"
        assert body["api_key_masked"] == ""
        assert not body["active"]
        # 无内置默认:未保存连接时 url/model 均为空串,由前端提示补全
        assert body["base_url"] == ""
        assert body["default_model"] == ""

    def test_save_no_url_422(self, c, conn_env):
        r = c.put("/api/connection", json={"enabled": True, "base_url": "ftp://bad", "api_key": "sk-x"})
        assert r.status_code == 422

    def test_save_and_read_masked(self, c, conn_env):
        r = c.put("/api/connection", json={
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-verylong-secret-key",
            "models": ["gpt-4o-mini"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is True
        assert body["source"] == "connection"
        assert body["api_key_set"] is True
        # 后端永不明文回显
        assert "verylong" not in json.dumps(body)
        # 空白 api_key 提交=沿用已存(明文不回)
        body2 = c.put("/api/connection", json={**body, "api_key": "", "base_url": body["base_url"]}).json()
        assert body2["active"] is True

    def test_test_connection_probe(self, c, conn_env, probe_models_fake):
        r = c.post("/api/connection/test", json={
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-probe",
        })
        assert r.status_code == 200, r.text
        assert r.json()["models"] == ["gpt-4o-mini", "gpt-4o"]

    def test_enabled_connection_unlocks_chat(self, c, conn_env, probe_models_fake):
        # 无连接&无 env → 503
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 503
        # 保存一条激活连接后 → 仍由 fake LLM 正常出结果(而非 503)
        c.put("/api/connection", json={
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-from-panel",
            "default_model": "gpt-4o-mini",
        })
        r = c.post("/api/chat", json={"message": "你好"})
        assert r.status_code == 200, r.text
        # analysis 只暴露对话基调(内部风险标签不进响应)
        assert r.json()["analysis"]["tone"] in ("serious", "gentle", "normal", "playful")

    def test_chat_503_names_the_missing_field(self, c, conn_env):
        """未配置模型时 503 文案点名缺项,而非笼统提示 Key。"""
        c.put("/api/connection", json={
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-from-panel",
        })
        r = c.post("/api/chat", json={"message": "你好"})
        assert r.status_code == 503
        assert "模型 ID" in r.json()["detail"]
        assert "API Key" not in r.json()["detail"]   # 已配置项不误报


class TestLegacyConnectionMigration:
    """重构迁移:旧落点的连接 JSON 应在首次读取前搬到当前 _CONN_FILE。

    旧落点 = <repo>/app/Agent/config/runtime_connection.json(见 config 常量);
    语义与 long_memory.paths 一致:仅当源存在且目标不存在时搬;失败只告警,
    读取回落默认,绝不抛异常。
    """

    @staticmethod
    def _write(path: Path, api_key: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "enabled": True,
            "base_url": "https://example.invalid/v1",
            "api_key": api_key,
            "default_model": "m",
            "models": [],
        }), encoding="utf-8")

    def test_migrates_when_target_absent(self, conn_env, monkeypatch, workdir):
        legacy = Path(workdir) / "legacy" / "runtime_connection.json"
        self._write(legacy, "sk-legacy")
        monkeypatch.setattr(cfg, "LEGACY_RUNTIME_CONNECTION_PATH", str(legacy))
        monkeypatch.setattr(runtime_connection, "_MIGRATION_DONE", False)

        state = runtime_connection.load_state()

        assert state["api_key"] == "sk-legacy"
        assert runtime_connection.get_active()["api_key"] == "sk-legacy"
        assert not legacy.exists()          # 源被搬走(移动,不是复制)
        assert conn_env.exists()            # 目标就是当前 _CONN_FILE

    def test_target_present_is_left_alone(self, conn_env, monkeypatch, workdir):
        legacy = Path(workdir) / "legacy" / "runtime_connection.json"
        self._write(legacy, "sk-legacy")
        self._write(conn_env, "sk-current")
        monkeypatch.setattr(cfg, "LEGACY_RUNTIME_CONNECTION_PATH", str(legacy))
        monkeypatch.setattr(runtime_connection, "_MIGRATION_DONE", False)

        state = runtime_connection.load_state()

        assert state["api_key"] == "sk-current"   # 当前连接优先,不被旧文件覆盖
        assert legacy.exists()                    # 旧文件原样保留(不搬不删)

    def test_second_read_does_not_rerun_migration(self, conn_env, monkeypatch, workdir):
        """进程内只迁移一次:目标在首次读取后被删,也不会再搬旧文件。"""
        legacy = Path(workdir) / "legacy" / "runtime_connection.json"
        self._write(legacy, "sk-legacy")
        monkeypatch.setattr(cfg, "LEGACY_RUNTIME_CONNECTION_PATH", str(legacy))
        monkeypatch.setattr(runtime_connection, "_MIGRATION_DONE", False)

        assert runtime_connection.load_state()["api_key"] == "sk-legacy"
        conn_env.unlink()                       # 目标消失(如被外部清理)
        self._write(legacy, "sk-late")          # 旧位置又出现一份

        assert runtime_connection.load_state()["api_key"] == ""   # 不再迁移 → 默认
        assert legacy.exists()

    def test_failure_never_raises(self, conn_env, monkeypatch, workdir):
        legacy = Path(workdir) / "legacy" / "runtime_connection.json"
        self._write(legacy, "sk-legacy")
        monkeypatch.setattr(cfg, "LEGACY_RUNTIME_CONNECTION_PATH", str(legacy))
        monkeypatch.setattr(runtime_connection, "_MIGRATION_DONE", False)

        def denied(*_a, **_k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(runtime_connection.os, "replace", denied)
        monkeypatch.setattr(runtime_connection.shutil, "copy2", denied)

        state = runtime_connection.load_state()   # 不得抛异常

        assert state["enabled"] is False          # 回落默认状态
        assert legacy.exists()                    # 源未受损
