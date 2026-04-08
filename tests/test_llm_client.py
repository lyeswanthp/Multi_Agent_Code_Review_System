"""Tests for llm_client — extract_json adversarial, truncate_content boundary,
call_agent error handling, client caching."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_review.llm_client import (
    _USER_MSG_CHAR_BUDGET,
    call_agent,
    extract_json,
    get_client,
    truncate_content,
)
from code_review.models import AgentName


# ---------------------------------------------------------------------------
# truncate_content
# ---------------------------------------------------------------------------

class TestTruncateContent:
    def test_short_content_unchanged(self):
        assert truncate_content("hello", 100) == "hello"

    def test_exact_boundary_unchanged(self):
        content = "x" * 100
        assert truncate_content(content, 100) == content

    def test_truncates_on_newline_boundary(self):
        content = "line1\nline2\nline3\nline4\n"
        result = truncate_content(content, 15)
        assert "truncated" in result
        assert len(result.split("\n")[0]) <= 15

    def test_truncation_includes_char_count(self):
        content = "x" * 200
        result = truncate_content(content, 50)
        assert "150" in result  # ~150 chars omitted

    def test_very_long_first_line(self):
        """When no newline in first half, falls back to hard cutoff."""
        content = "x" * 200
        result = truncate_content(content, 100)
        assert "truncated" in result

    def test_default_budget(self):
        """Default budget matches the module constant."""
        short = "x" * 100
        assert truncate_content(short) == short
        long = "x" * (_USER_MSG_CHAR_BUDGET + 1000)
        result = truncate_content(long)
        assert "truncated" in result

    def test_empty_string(self):
        assert truncate_content("") == ""

    def test_unicode_content(self):
        content = "变量" * 10000
        result = truncate_content(content, 100)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# extract_json (additional adversarial — the main battery is in test_agents.py)
# ---------------------------------------------------------------------------

class TestExtractJsonEdgeCases:
    def test_object_before_array(self):
        """When object appears before array, should pick object."""
        text = '{"key": "val"} and then [1, 2, 3]'
        result = extract_json(text)
        assert isinstance(result, dict)

    def test_array_before_object(self):
        text = '[1, 2] and {"key": "val"}'
        result = extract_json(text)
        assert isinstance(result, list)

    def test_nested_braces_in_string(self):
        text = '{"msg": "use {} for formatting"}'
        result = extract_json(text)
        assert result["msg"] == "use {} for formatting"

    def test_escaped_quotes(self):
        text = '{"msg": "he said \\"hello\\""}'
        result = extract_json(text)
        assert "hello" in result["msg"]

    def test_newlines_in_json(self):
        text = '{\n  "findings": [],\n  "summary": "clean"\n}'
        result = extract_json(text)
        assert result["summary"] == "clean"

    def test_backtick_fence_without_json_tag(self):
        text = "```\n[{\"a\": 1}]\n```"
        result = extract_json(text)
        assert result == [{"a": 1}]


# ---------------------------------------------------------------------------
# get_client caching
# ---------------------------------------------------------------------------

class TestGetClient:
    def test_same_url_returns_same_client(self):
        from code_review.llm_client import _clients
        _clients.clear()
        c1 = get_client("http://localhost:1234/v1", "key")
        c2 = get_client("http://localhost:1234/v1", "key")
        assert c1 is c2
        _clients.clear()

    def test_different_url_returns_different_client(self):
        from code_review.llm_client import _clients
        _clients.clear()
        c1 = get_client("http://localhost:1234/v1", "key")
        c2 = get_client("http://other:5678/v1", "key")
        assert c1 is not c2
        _clients.clear()


# ---------------------------------------------------------------------------
# call_agent
# ---------------------------------------------------------------------------

class TestCallAgent:
    @pytest.mark.asyncio
    async def test_no_api_key_raises_value_error(self):
        with patch("code_review.llm_client.settings") as mock_settings:
            mock_settings.get_provider.return_value = MagicMock(api_key="", base_url="http://x", model="m")
            with pytest.raises(ValueError, match="No API key"):
                await call_agent(AgentName.SYNTAX, [{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_auth_error_re_raises(self):
        """401/auth errors should propagate, not be swallowed."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("401 Unauthorized - invalid api key")

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            with pytest.raises(Exception, match="401"):
                await call_agent(AgentName.SYNTAX, [{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_transient_error_returns_empty(self):
        """Non-auth errors should return empty string."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("Connection timeout")

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            result = await call_agent(AgentName.LOGIC, [{"role": "user", "content": "test"}])
        assert result == ""

    @pytest.mark.asyncio
    async def test_successful_call_returns_content(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response text"

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            result = await call_agent(AgentName.SYNTAX, [{"role": "user", "content": "test"}])
        assert result == "response text"

    @pytest.mark.asyncio
    async def test_null_content_returns_empty(self):
        """LLM returns None content — should return empty string."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            result = await call_agent(AgentName.SYNTAX, [{"role": "user", "content": "test"}])
        assert result == ""

    @pytest.mark.asyncio
    async def test_string_agent_name(self):
        """Agent name passed as string instead of enum should work."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            result = await call_agent("syntax", [{"role": "user", "content": "test"}])
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_rate_limit_error_returns_empty(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("Rate limit exceeded, retry after 30s")

        with patch("code_review.llm_client.settings") as mock_settings, \
             patch("code_review.llm_client.get_client", return_value=mock_client):
            mock_settings.get_provider.return_value = MagicMock(api_key="key", base_url="http://x", model="m")
            result = await call_agent(AgentName.SECURITY, [{"role": "user", "content": "test"}])
        assert result == ""
