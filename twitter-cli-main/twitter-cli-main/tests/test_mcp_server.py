from __future__ import annotations

import asyncio

import pytest

from twitter_cli import mcp_server
from twitter_cli.mcp_server import McpSettings


class TestMcpSettings:
    def test_loads_single_and_multiple_api_keys(self, monkeypatch) -> None:
        monkeypatch.setenv("TWITTER_MCP_API_KEY", "primary")
        monkeypatch.setenv("TWITTER_MCP_API_KEYS", "secondary, primary ,third")
        monkeypatch.setenv("TWITTER_MCP_ALLOWED_ORIGINS", "https://x.example.com/")
        monkeypatch.setenv("TWITTER_MCP_PATH", "mcp")
        monkeypatch.setenv("TWITTER_MCP_PORT", "9000")

        settings = mcp_server.load_settings_from_env()

        assert settings.api_keys == ("primary", "secondary", "third")
        assert settings.allowed_origins == ("https://x.example.com",)
        assert "x.example.com" in settings.allowed_hosts
        assert "x.example.com:*" in settings.allowed_hosts
        assert settings.path == "/mcp"
        assert settings.port == 9000

    def test_rejects_invalid_port(self, monkeypatch) -> None:
        monkeypatch.setenv("TWITTER_MCP_PORT", "70000")

        with pytest.raises(RuntimeError, match="between 1 and 65535"):
            mcp_server.load_settings_from_env()

    def test_explicit_allowed_hosts_are_expanded(self) -> None:
        hosts = mcp_server._build_allowed_hosts(
            ["mcp.example.com"],
            ["https://origin.example.com"],
        )

        assert "mcp.example.com" in hosts
        assert "mcp.example.com:*" in hosts
        assert "origin.example.com" in hosts
        assert "origin.example.com:*" in hosts
        assert "127.0.0.1:*" in hosts


class TestApiKeyAuth:
    def test_authorization_bearer_allows_matching_key(self) -> None:
        headers = {"authorization": "Bearer secret"}

        assert mcp_server.is_request_authorized(headers, ("secret",))

    def test_x_api_key_allows_matching_key(self) -> None:
        headers = {"x-api-key": "secret"}

        assert mcp_server.is_request_authorized(headers, ("secret",))

    def test_api_key_allows_matching_key(self) -> None:
        headers = {"api-key": "secret"}

        assert mcp_server.is_request_authorized(headers, ("secret",))

    def test_api_key_with_underscore_allows_matching_key(self) -> None:
        headers = {"api_key": "secret"}

        assert mcp_server.is_request_authorized(headers, ("secret",))

    def test_wrong_key_is_rejected(self) -> None:
        headers = {"authorization": "Bearer wrong"}

        assert not mcp_server.is_request_authorized(headers, ("secret",))

    def test_missing_configured_keys_rejects_all_requests(self) -> None:
        headers = {"authorization": "Bearer secret"}

        assert not mcp_server.is_request_authorized(headers, ())


class TestOriginChecks:
    def test_missing_origin_is_allowed_for_server_clients(self) -> None:
        settings = McpSettings(allowed_origins=("https://mcp.example.com",))

        assert mcp_server.is_origin_allowed(None, settings)

    def test_configured_origin_is_allowed(self) -> None:
        settings = McpSettings(allowed_origins=("https://mcp.example.com",))

        assert mcp_server.is_origin_allowed("https://mcp.example.com", settings)

    def test_unconfigured_origin_is_rejected(self) -> None:
        settings = McpSettings(allowed_origins=("https://mcp.example.com",))

        assert not mcp_server.is_origin_allowed("https://attacker.example.com", settings)

    def test_any_origin_flag_allows_browser_origins(self) -> None:
        settings = McpSettings(allow_any_origin=True)

        assert mcp_server.is_origin_allowed("https://anything.example.com", settings)


class TestHealthEndpoint:
    def test_create_asgi_app_uses_registered_mcp_server(self, monkeypatch) -> None:
        calls = []

        class FakeServer:
            def streamable_http_app(self):
                return "inner-app"

        def fake_create_mcp_server(*, settings):
            calls.append(settings)
            return FakeServer()

        monkeypatch.setattr(mcp_server, "create_mcp_server", fake_create_mcp_server)
        settings = McpSettings(api_keys=("secret",), path="/mcp")

        app = mcp_server.create_asgi_app(settings)

        assert isinstance(app, mcp_server.ApiKeyOriginMiddleware)
        assert calls == [settings]

    def test_authenticated_health_path_returns_json(self) -> None:
        async def inner_app(scope, receive, send):  # pragma: no cover - should not be reached
            raise AssertionError("health should not call inner MCP app")

        app = mcp_server.ApiKeyOriginMiddleware(inner_app, McpSettings(api_keys=("secret",)))
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(
            app(
                {
                    "type": "http",
                    "path": "/health",
                    "headers": [(b"x-api-key", b"secret")],
                },
                receive,
                send,
            )
        )

        assert messages[0]["status"] == 200
        assert b'"server":"twitter-cli"' in messages[1]["body"]


class TestToolInputNormalization:
    def test_count_defaults_and_caps(self) -> None:
        assert mcp_server._resolve_count(None) == 20
        assert mcp_server._resolve_count(10) == 10
        assert mcp_server._resolve_count(500) == 50

    def test_count_rejects_non_positive_values(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            mcp_server._resolve_count(0)

    def test_handle_normalization(self) -> None:
        assert mcp_server._normalize_handle("@jack") == "jack"

    def test_tweet_id_accepts_status_url(self) -> None:
        assert (
            mcp_server._normalize_tweet_id("https://x.com/user/status/12345?foo=bar")
            == "12345"
        )

    def test_tweet_id_rejects_non_numeric_value(self) -> None:
        with pytest.raises(ValueError, match="numeric"):
            mcp_server._normalize_tweet_id("not-a-tweet")

    def test_search_product_normalization(self) -> None:
        assert mcp_server._normalize_search_product("latest") == "Latest"

    def test_choice_validation(self) -> None:
        assert mcp_server._validate_choices(["links", "MEDIA"], {"links", "media"}, "has") == [
            "links",
            "media",
        ]

    def test_choice_validation_rejects_invalid_values(self) -> None:
        with pytest.raises(ValueError, match="invalid"):
            mcp_server._validate_choices(["links", "bad"], {"links"}, "has")
