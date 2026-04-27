"""Tests for skillctl.config — centralized configuration module."""

from __future__ import annotations


from skillctl.config import (
    SkillctlConfig,
    load_config,
    save_config,
    _parse_config,
    run_configure_wizard,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_config_has_local_backend(self):
        cfg = SkillctlConfig()
        assert cfg.registry.backend == "local"

    def test_default_optimizer_model(self):
        cfg = SkillctlConfig()
        assert cfg.optimize.model == "bedrock/us.anthropic.claude-opus-4-6-v1"

    def test_default_budget(self):
        cfg = SkillctlConfig()
        assert cfg.optimize.budget_usd == 10.0

    def test_default_max_tokens(self):
        cfg = SkillctlConfig()
        assert cfg.optimize.max_tokens == 4096

    def test_default_github_is_none(self):
        cfg = SkillctlConfig()
        assert cfg.github.token is None
        assert cfg.github.client_id is None


# ---------------------------------------------------------------------------
# to_dict / round-trip
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict_omits_none(self):
        cfg = SkillctlConfig()
        d = cfg.to_dict()
        assert "token" not in d.get("github", {})
        assert "token" not in d.get("registry", {}).get("local", {})

    def test_to_dict_includes_set_values(self):
        cfg = SkillctlConfig()
        cfg.registry.backend = "agent-registry"
        cfg.registry.agent_registry.registry_id = "arn:aws:test"
        d = cfg.to_dict()
        assert d["registry"]["backend"] == "agent-registry"
        assert d["registry"]["agent_registry"]["registry_id"] == "arn:aws:test"


# ---------------------------------------------------------------------------
# _parse_config
# ---------------------------------------------------------------------------


class TestParseConfig:
    def test_parse_empty_dict(self):
        cfg = _parse_config({})
        assert cfg.registry.backend == "local"
        assert cfg.optimize.model == "bedrock/us.anthropic.claude-opus-4-6-v1"

    def test_parse_agent_registry_config(self):
        raw = {
            "registry": {
                "backend": "agent-registry",
                "agent_registry": {
                    "registry_id": "arn:aws:bedrock-agentcore:us-east-1:123:registry/test",
                    "region": "us-west-2",
                },
            },
        }
        cfg = _parse_config(raw)
        assert cfg.registry.backend == "agent-registry"
        assert cfg.registry.agent_registry.registry_id == "arn:aws:bedrock-agentcore:us-east-1:123:registry/test"
        assert cfg.registry.agent_registry.region == "us-west-2"

    def test_parse_local_config(self):
        raw = {
            "registry": {
                "backend": "local",
                "local": {
                    "url": "https://my-registry:8080",
                    "token": "sk-test-123",
                },
            },
        }
        cfg = _parse_config(raw)
        assert cfg.registry.backend == "local"
        assert cfg.registry.local.url == "https://my-registry:8080"
        assert cfg.registry.local.token == "sk-test-123"

    def test_parse_optimizer_config(self):
        raw = {
            "optimize": {
                "model": "openai/gpt-4o",
                "budget_usd": 5.0,
                "max_tokens": 8192,
            },
        }
        cfg = _parse_config(raw)
        assert cfg.optimize.model == "openai/gpt-4o"
        assert cfg.optimize.budget_usd == 5.0
        assert cfg.optimize.max_tokens == 8192

    def test_backward_compat_old_registry_url_format(self):
        raw = {
            "registry": {
                "url": "https://old-format:8080",
                "token": "old-token",
            },
        }
        cfg = _parse_config(raw)
        assert cfg.registry.local.url == "https://old-format:8080"
        assert cfg.registry.local.token == "old-token"

    def test_parse_github_config(self):
        raw = {"github": {"token": "ghp_test", "client_id": "abc123"}}
        cfg = _parse_config(raw)
        assert cfg.github.token == "ghp_test"
        assert cfg.github.client_id == "abc123"


# ---------------------------------------------------------------------------
# load_config / save_config (filesystem)
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_load_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", tmp_path / "nonexistent.yaml")
        cfg = load_config()
        assert cfg.registry.backend == "local"

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("skillctl.config.CONFIG_DIR", tmp_path)

        cfg = SkillctlConfig()
        cfg.registry.backend = "agent-registry"
        cfg.registry.agent_registry.registry_id = "arn:test"
        cfg.optimize.model = "openai/gpt-4o"
        cfg.optimize.budget_usd = 3.0

        save_config(cfg)

        assert config_path.exists()
        assert oct(config_path.stat().st_mode)[-3:] == "600"

        loaded = load_config()
        assert loaded.registry.backend == "agent-registry"
        assert loaded.registry.agent_registry.registry_id == "arn:test"
        assert loaded.optimize.model == "openai/gpt-4o"
        assert loaded.optimize.budget_usd == 3.0

    def test_save_creates_parent_directory(self, tmp_path, monkeypatch):
        config_path = tmp_path / "subdir" / "config.yaml"
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("skillctl.config.CONFIG_DIR", tmp_path / "subdir")

        save_config(SkillctlConfig())
        assert config_path.exists()

    def test_load_corrupt_yaml_returns_defaults(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{unclosed: [bracket")
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", config_path)

        cfg = load_config()
        assert cfg.registry.backend == "local"
        assert cfg.optimize.model == "bedrock/us.anthropic.claude-opus-4-6-v1"

    def test_load_non_dict_yaml_returns_defaults(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("just a string")
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", config_path)

        cfg = load_config()
        assert cfg.registry.backend == "local"

    def test_parse_none_sections(self):
        raw = {"registry": None, "optimize": None, "github": None}
        cfg = _parse_config(raw)
        assert cfg.registry.backend == "local"
        assert cfg.optimize.model == "bedrock/us.anthropic.claude-opus-4-6-v1"
        assert cfg.github.token is None

    def test_load_with_partial_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("optimize:\n  model: ollama/llama3\n")
        monkeypatch.setattr("skillctl.config.CONFIG_PATH", config_path)

        cfg = load_config()
        assert cfg.optimize.model == "ollama/llama3"
        assert cfg.registry.backend == "local"
        assert cfg.optimize.budget_usd == 10.0


# ---------------------------------------------------------------------------
# run_configure_wizard
# ---------------------------------------------------------------------------


class TestConfigureWizard:
    def test_wizard_local_backend(self, monkeypatch):
        inputs = iter(
            [
                "local",  # backend
                "https://my-server:8080",  # url
                "",  # token (blank)
                "",  # model (default)
                "5.0",  # budget
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        cfg = run_configure_wizard(SkillctlConfig())
        assert cfg.registry.backend == "local"
        assert cfg.registry.local.url == "https://my-server:8080"
        assert cfg.registry.local.token is None
        assert cfg.optimize.budget_usd == 5.0

    def test_wizard_agent_registry_backend(self, monkeypatch):
        inputs = iter(
            [
                "agent-registry",  # backend
                "arn:aws:test:registry/my-reg",  # registry_id
                "us-west-2",  # region
                "openai/gpt-4o",  # model
                "20.0",  # budget
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        cfg = run_configure_wizard(SkillctlConfig())
        assert cfg.registry.backend == "agent-registry"
        assert cfg.registry.agent_registry.registry_id == "arn:aws:test:registry/my-reg"
        assert cfg.registry.agent_registry.region == "us-west-2"
        assert cfg.optimize.model == "openai/gpt-4o"
        assert cfg.optimize.budget_usd == 20.0

    def test_wizard_accepts_defaults(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")

        cfg = run_configure_wizard(SkillctlConfig())
        assert cfg.registry.backend == "local"
        assert cfg.optimize.model == "bedrock/us.anthropic.claude-opus-4-6-v1"
        assert cfg.optimize.budget_usd == 10.0

    def test_wizard_preserves_existing_config(self, monkeypatch):
        existing = SkillctlConfig()
        existing.registry.backend = "agent-registry"
        existing.registry.agent_registry.registry_id = "arn:existing"

        inputs = iter(
            [
                "",  # backend (keep agent-registry)
                "",  # registry_id (keep existing)
                "",  # region (keep default)
                "anthropic/claude-sonnet-4-6",  # new model
                "",  # budget (keep default)
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        cfg = run_configure_wizard(existing)
        assert cfg.registry.backend == "agent-registry"
        assert cfg.registry.agent_registry.registry_id == "arn:existing"
        assert cfg.optimize.model == "anthropic/claude-sonnet-4-6"

    def test_wizard_invalid_budget_keeps_default(self, monkeypatch):
        inputs = iter(
            [
                "",  # backend
                "",  # url
                "",  # token
                "",  # model
                "not-a-number",  # invalid budget
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        cfg = run_configure_wizard(SkillctlConfig())
        assert cfg.optimize.budget_usd == 10.0

    def test_wizard_handles_eof(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError))

        cfg = run_configure_wizard(SkillctlConfig())
        assert cfg.registry.backend == "local"
