"""Configuration via environment variables.

Supports two modes:
  - "local" (default): All agents use LM Studio at localhost. No API keys needed.
  - "remote": Agents routed to Groq / NVIDIA NIM / Cerebras free tiers.

Set LLM_MODE=local or LLM_MODE=remote in .env or environment.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

from code_review.models import Severity

# Load .env file explicitly from project root
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


class ProviderConfig(BaseSettings):
    """Config for a single LLM provider."""

    base_url: str
    api_key: str = ""
    model: str


class Settings(BaseSettings):
    """Top-level application settings, loaded from environment."""

    model_config = {
        "env_prefix": "",
        "env_file": str(_env_path),
        "extra": "ignore",
    }

    # Mode: "local" (LM Studio) or "remote" (cloud APIs)
    llm_mode: str = Field(default="local", alias="LLM_MODE")

    # LM Studio settings (local mode) — LM Studio serves an OpenAI-compatible API on port 1234
    lmstudio_base_url: str = Field(default="http://localhost:1234/v1", alias="LMSTUDIO_BASE_URL")
    lmstudio_heavy_model: str = Field(default="local-model", alias="LMSTUDIO_HEAVY_MODEL")
    lmstudio_light_model: str = Field(default="local-model", alias="LMSTUDIO_LIGHT_MODEL")
    # Context window size of the loaded model (in tokens). Used to size prompt budgets.
    # Set this to match your actual model — most modern models support 8K-32K+.
    lmstudio_context_size: int = Field(default=8192, alias="LMSTUDIO_CONTEXT_SIZE")

    # Remote API keys (only needed in remote mode)
    nvidia_api_key: str = Field(default="", alias="NVIDIA_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    cerebras_api_key: str = Field(default="", alias="CEREBRAS_API_KEY")

    # Severity threshold — findings below this are filtered out
    severity_threshold: Severity = Severity.MEDIUM

    # LSP settings
    lsp_enabled: bool = Field(default=True, alias="LSP_ENABLED")
    lsp_timeout_seconds: int = Field(default=10, alias="LSP_TIMEOUT_SECONDS")
    lsp_max_files: int = Field(default=50, alias="LSP_MAX_FILES")

    # Remote model assignments (used when LLM_MODE=remote)
    syntax_model: str = "llama-3.3-70b-versatile"
    logic_model: str = "mistralai/devstral-2-123b-instruct-2512"
    security_model: str = "llama3.1-8b"
    git_history_model: str = "llama-3.1-8b-instant"
    orchestrator_model: str = "nvidia/nemotron-mini-4b-instruct"

    def get_provider(self, agent: str) -> ProviderConfig:
        """Return the provider config for a given agent name."""
        if self.llm_mode == "local":
            return self._local_provider(agent)
        return self._remote_provider(agent)

    def _local_provider(self, agent: str) -> ProviderConfig:
        """All agents route to local LM Studio. Heavy model for reasoning, light for the rest."""
        heavy_agents = {"logic", "security", "orchestrator", "master"}
        model = self.lmstudio_heavy_model if agent in heavy_agents else self.lmstudio_light_model
        return ProviderConfig(
            base_url=self.lmstudio_base_url,
            api_key="lm-studio",
            model=model,
        )

    def _remote_provider(self, agent: str) -> ProviderConfig:
        """Route agents to cloud free-tier providers. Master agent always uses local."""
        # Master agent always uses local LM Studio - no cloud API
        if agent == "master":
            return ProviderConfig(
                base_url=self.lmstudio_base_url,
                api_key="lm-studio",
                model=self.lmstudio_heavy_model,
            )

        providers = {
            "syntax": ProviderConfig(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.groq_api_key,
                model=self.syntax_model,
            ),
            "logic": ProviderConfig(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=self.nvidia_api_key,
                model=self.logic_model,
            ),
            "security": ProviderConfig(
                base_url="https://api.cerebras.ai/v1",
                api_key=self.cerebras_api_key,
                model=self.security_model,
            ),
            "git_history": ProviderConfig(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.groq_api_key,
                model=self.git_history_model,
            ),
            "orchestrator": ProviderConfig(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=self.nvidia_api_key,
                model=self.orchestrator_model,
            ),
        }
        return providers[agent]


# Singleton — import this from anywhere
settings = Settings()
