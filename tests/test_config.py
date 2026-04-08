"""Tests for config — provider routing, local vs remote mode, model assignments."""

import os
from unittest.mock import patch

import pytest

from code_review.config import ProviderConfig, Settings


# Map constructor kwarg names to env var aliases used by pydantic-settings
_ALIAS_MAP = {
    "llm_mode": "LLM_MODE",
    "lmstudio_base_url": "LMSTUDIO_BASE_URL",
    "lmstudio_heavy_model": "LMSTUDIO_HEAVY_MODEL",
    "lmstudio_light_model": "LMSTUDIO_LIGHT_MODEL",
    "nvidia_api_key": "NVIDIA_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
    "cerebras_api_key": "CEREBRAS_API_KEY",
}

# Env vars that must be cleared to avoid test pollution
_ALL_ENV_KEYS = set(_ALIAS_MAP.values())


def _make_settings(**kwargs):
    """Create Settings via env vars to work with pydantic-settings aliases."""
    # Build a clean env: remove all config-related vars, then set what we need
    env_overrides = {}
    for kwarg_name, value in kwargs.items():
        env_key = _ALIAS_MAP.get(kwarg_name, kwarg_name.upper())
        env_overrides[env_key] = str(value)

    # Start from current env, remove all our config keys, then add overrides
    clean_env = {k: v for k, v in os.environ.items() if k not in _ALL_ENV_KEYS}
    clean_env.update(env_overrides)

    with patch.dict(os.environ, clean_env, clear=True):
        class TestSettings(Settings):
            model_config = {**Settings.model_config, "env_file": None}
        return TestSettings()


class TestLocalProvider:
    def test_heavy_agents_get_heavy_model(self):
        s = _make_settings(llm_mode="local", lmstudio_heavy_model="heavy-7b", lmstudio_light_model="light-1b")
        for agent in ("logic", "security", "orchestrator"):
            p = s.get_provider(agent)
            assert p.model == "heavy-7b", f"{agent} should use heavy model"

    def test_light_agents_get_light_model(self):
        s = _make_settings(llm_mode="local", lmstudio_heavy_model="heavy-7b", lmstudio_light_model="light-1b")
        for agent in ("syntax", "git_history"):
            p = s.get_provider(agent)
            assert p.model == "light-1b", f"{agent} should use light model"

    def test_local_always_has_api_key(self):
        s = _make_settings(llm_mode="local")
        p = s.get_provider("syntax")
        assert p.api_key == "lm-studio"

    def test_local_uses_lmstudio_base_url(self):
        s = _make_settings(llm_mode="local", lmstudio_base_url="http://custom:9999/v1")
        p = s.get_provider("syntax")
        assert p.base_url == "http://custom:9999/v1"


class TestRemoteProvider:
    def test_syntax_routes_to_groq(self):
        s = _make_settings(llm_mode="remote", groq_api_key="gk")
        p = s.get_provider("syntax")
        assert "groq" in p.base_url
        assert p.api_key == "gk"

    def test_logic_routes_to_nvidia(self):
        s = _make_settings(llm_mode="remote", nvidia_api_key="nk")
        p = s.get_provider("logic")
        assert "nvidia" in p.base_url
        assert p.api_key == "nk"

    def test_security_routes_to_cerebras(self):
        s = _make_settings(llm_mode="remote", cerebras_api_key="ck")
        p = s.get_provider("security")
        assert "cerebras" in p.base_url
        assert p.api_key == "ck"

    def test_git_history_routes_to_groq(self):
        s = _make_settings(llm_mode="remote", groq_api_key="gk")
        p = s.get_provider("git_history")
        assert "groq" in p.base_url

    def test_orchestrator_routes_to_nvidia(self):
        s = _make_settings(llm_mode="remote", nvidia_api_key="nk")
        p = s.get_provider("orchestrator")
        assert "nvidia" in p.base_url

    def test_remote_missing_key_returns_empty(self):
        s = _make_settings(llm_mode="remote")
        p = s.get_provider("syntax")
        assert p.api_key == ""

    def test_unknown_agent_raises(self):
        s = _make_settings(llm_mode="remote")
        with pytest.raises(KeyError):
            s.get_provider("nonexistent_agent")


class TestProviderConfig:
    def test_basic_creation(self):
        p = ProviderConfig(base_url="http://x", api_key="k", model="m")
        assert p.base_url == "http://x"
        assert p.api_key == "k"
        assert p.model == "m"

    def test_default_api_key(self):
        p = ProviderConfig(base_url="http://x", model="m")
        assert p.api_key == ""


class TestSettingsDefaults:
    def test_default_mode_is_local(self):
        s = _make_settings()
        assert s.llm_mode == "local"

    def test_default_base_url(self):
        s = _make_settings()
        assert "localhost" in s.lmstudio_base_url
        assert "1234" in s.lmstudio_base_url
