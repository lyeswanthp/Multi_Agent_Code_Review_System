"""Configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from code_review.models import Severity


class ProviderConfig(BaseSettings):
    """Config for a single LLM provider."""

    base_url: str
    api_key: str = ""
    model: str


class Settings(BaseSettings):
    """Top-level application settings, loaded from environment."""

    model_config = {
        "env_prefix": "",
        "env_file": str(Path(__file__).resolve().parent.parent.parent.parent / ".env"),
        "extra": "ignore",
    }

    # API keys
    nvidia_api_key: str = Field(default="", alias="NVIDIA_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    cerebras_api_key: str = Field(default="", alias="CEREBRAS_API_KEY")

    # Severity threshold — findings below this are filtered out
    severity_threshold: Severity = Severity.MEDIUM

    # Agent model assignments
    syntax_model: str = "llama-3.3-70b-versatile"
    logic_model: str = "nvidia/nemotron-mini-4b-instruct"
    security_model: str = "llama3.1-8b"
    git_history_model: str = "llama-3.1-8b-instant"
    orchestrator_model: str = "nvidia/nemotron-mini-4b-instruct"

    def get_provider(self, agent: str) -> ProviderConfig:
        """Return the provider config for a given agent name."""
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
