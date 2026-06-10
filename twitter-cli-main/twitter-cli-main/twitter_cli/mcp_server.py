"""Remote MCP server for twitter-cli.

This module exposes read-only Twitter/X tools over Streamable HTTP.  It does
not import or call the Click CLI, so stdout stays reserved for the MCP protocol
when other transports are added later.
"""

from __future__ import annotations

import hmac
import logging
import os
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    MutableMapping,
    Optional,
    Tuple,
)

if TYPE_CHECKING:
    from .client import TwitterClient

logger = logging.getLogger(__name__)

DEFAULT_MCP_PATH = "/mcp"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8000
DEFAULT_TOOL_COUNT = 20
MAX_TOOL_COUNT = 50
SEARCH_PRODUCTS = {"Top", "Latest", "Photos", "Videos"}
SEARCH_HAS_VALUES = {"links", "images", "videos", "media"}
SEARCH_EXCLUDE_VALUES = {"retweets", "replies", "links"}

AsgiMessage = MutableMapping[str, Any]
AsgiScope = MutableMapping[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
AsgiApp = Callable[[AsgiScope, AsgiReceive, AsgiSend], Awaitable[None]]


@dataclass(frozen=True)
class McpSettings:
    """Runtime settings for the remote MCP server."""

    host: str = DEFAULT_MCP_HOST
    port: int = DEFAULT_MCP_PORT
    path: str = DEFAULT_MCP_PATH
    api_keys: Tuple[str, ...] = ()
    allowed_hosts: Tuple[str, ...] = ()
    allowed_origins: Tuple[str, ...] = ()
    allow_any_origin: bool = False
    log_level: str = "info"


class ApiKeyOriginMiddleware:
    """ASGI middleware that enforces API key and Origin checks."""

    def __init__(self, app: AsgiApp, settings: McpSettings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = _headers_to_dict(scope.get("headers", []))
        origin = headers.get("origin")
        if not is_origin_allowed(origin, self._settings):
            await _send_json_error(send, 403, "forbidden_origin")
            return

        if not is_request_authorized(headers, self._settings.api_keys):
            await _send_json_error(send, 401, "unauthorized")
            return

        if scope.get("path") == "/health":
            await _send_json_response(send, 200, _health_payload())
            return

        await self._app(scope, receive, send)


def load_settings_from_env() -> McpSettings:
    """Load MCP server settings from environment variables."""
    allowed_origins = _split_csv_env("TWITTER_MCP_ALLOWED_ORIGINS")
    return McpSettings(
        host=os.environ.get("TWITTER_MCP_HOST", DEFAULT_MCP_HOST).strip() or DEFAULT_MCP_HOST,
        port=_parse_port(os.environ.get("TWITTER_MCP_PORT"), DEFAULT_MCP_PORT),
        path=_normalize_path(os.environ.get("TWITTER_MCP_PATH", DEFAULT_MCP_PATH)),
        api_keys=_load_api_keys_from_env(),
        allowed_hosts=_build_allowed_hosts(
            _split_csv_env("TWITTER_MCP_ALLOWED_HOSTS"),
            allowed_origins,
        ),
        allowed_origins=allowed_origins,
        allow_any_origin=_parse_bool(os.environ.get("TWITTER_MCP_ALLOW_ANY_ORIGIN")),
        log_level=os.environ.get("TWITTER_MCP_LOG_LEVEL", "info").strip().lower() or "info",
    )


def is_request_authorized(headers: Dict[str, str], api_keys: Iterable[str]) -> bool:
    """Return True when headers contain a configured API key."""
    keys = tuple(key for key in api_keys if key)
    if not keys:
        return False

    candidates = []
    auth_header = headers.get("authorization", "")
    prefix = "bearer "
    if auth_header.lower().startswith(prefix):
        candidates.append(auth_header[len(prefix):].strip())

    for header_name in ("x-api-key", "api-key", "api_key"):
        api_key_header = headers.get(header_name, "")
        if api_key_header:
            candidates.append(api_key_header.strip())

    return any(
        hmac.compare_digest(candidate, key)
        for candidate in candidates
        for key in keys
    )


def is_origin_allowed(origin: Optional[str], settings: McpSettings) -> bool:
    """Return True when the Origin header is acceptable for this server."""
    if not origin:
        return True
    if settings.allow_any_origin:
        return True
    return origin.strip().rstrip("/") in settings.allowed_origins


def create_mcp_server(path: str = DEFAULT_MCP_PATH, settings: Optional[McpSettings] = None) -> Any:
    """Create and register the read-only FastMCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise RuntimeError(
            "MCP support requires the 'mcp' package. Run `uv sync` or install twitter-cli "
            "with its runtime dependencies."
        ) from exc

    settings = settings or McpSettings(
        path=_normalize_path(path),
        allowed_hosts=_default_allowed_hosts(),
    )
    server = _new_fastmcp(FastMCP, settings)

    @server.tool()
    def health() -> Dict[str, Any]:
        """Check whether the MCP server can authenticate to Twitter/X."""
        return _tool_response(
            lambda client: {
                "authenticated": True,
                "user": _user_profile_to_dict(client.fetch_me()),
            }
        )

    @server.tool()
    def whoami() -> Dict[str, Any]:
        """Return the currently authenticated Twitter/X user."""
        return _tool_response(lambda client: {"user": _user_profile_to_dict(client.fetch_me())})

    @server.tool()
    def search_tweets(
        query: str = "",
        product: str = "Top",
        count: int = DEFAULT_TOOL_COUNT,
        from_user: Optional[str] = None,
        to_user: Optional[str] = None,
        lang: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        has: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        min_likes: Optional[int] = None,
        min_retweets: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search Twitter/X tweets with optional advanced search filters."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            product_name = _normalize_search_product(product)
            from .search import build_search_query

            query_text = build_search_query(
                query,
                from_user=from_user,
                to_user=to_user,
                lang=lang,
                since=since,
                until=until,
                has=_validate_choices(has, SEARCH_HAS_VALUES, "has"),
                exclude=_validate_choices(exclude, SEARCH_EXCLUDE_VALUES, "exclude"),
                min_likes=min_likes,
                min_retweets=min_retweets,
            )
            if not query_text:
                raise ValueError("Provide a query or at least one advanced search filter.")
            tweets = client.fetch_search(query_text, _resolve_count(count), product_name)
            return {"query": query_text, "product": product_name, "tweets": _tweets_to_data(tweets)}

        return _tool_response(run)

    @server.tool()
    def get_tweet_detail(tweet_id: str, count: int = DEFAULT_TOOL_COUNT) -> Dict[str, Any]:
        """Fetch a tweet and its conversation replies."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            tweets = client.fetch_tweet_detail(_normalize_tweet_id(tweet_id), _resolve_count(count))
            return {"tweets": _tweets_to_data(tweets)}

        return _tool_response(run)

    @server.tool()
    def get_article(tweet_id: str) -> Dict[str, Any]:
        """Fetch a Twitter/X Article attached to a tweet."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            article_tweet = client.fetch_article(_normalize_tweet_id(tweet_id))
            return {"article": _tweet_to_dict(article_tweet)}

        return _tool_response(run)

    @server.tool()
    def get_user_profile(screen_name: str) -> Dict[str, Any]:
        """Fetch a Twitter/X user profile by screen name."""
        return _tool_response(
            lambda client: {
                "user": _user_profile_to_dict(client.fetch_user(_normalize_handle(screen_name)))
            }
        )

    @server.tool()
    def get_user_tweets(screen_name: str, count: int = DEFAULT_TOOL_COUNT) -> Dict[str, Any]:
        """Fetch tweets posted by a Twitter/X user."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            profile = client.fetch_user(_normalize_handle(screen_name))
            tweets = client.fetch_user_tweets(profile.id, _resolve_count(count))
            return {"user": _user_profile_to_dict(profile), "tweets": _tweets_to_data(tweets)}

        return _tool_response(run)

    @server.tool()
    def get_home_timeline(
        count: int = DEFAULT_TOOL_COUNT,
        cursor: Optional[str] = None,
        include_promoted: bool = False,
    ) -> Dict[str, Any]:
        """Fetch the authenticated account's For You timeline."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            tweets, next_cursor = client.fetch_home_timeline(
                _resolve_count(count),
                include_promoted=include_promoted,
                cursor=cursor,
                return_cursor=True,
            )
            return _timeline_data(tweets, next_cursor)

        return _tool_response(run)

    @server.tool()
    def get_following_timeline(
        count: int = DEFAULT_TOOL_COUNT,
        cursor: Optional[str] = None,
        include_promoted: bool = False,
    ) -> Dict[str, Any]:
        """Fetch the authenticated account's chronological Following timeline."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            tweets, next_cursor = client.fetch_following_feed(
                _resolve_count(count),
                include_promoted=include_promoted,
                cursor=cursor,
                return_cursor=True,
            )
            return _timeline_data(tweets, next_cursor)

        return _tool_response(run)

    @server.tool()
    def get_bookmarks(count: int = DEFAULT_TOOL_COUNT) -> Dict[str, Any]:
        """Fetch bookmarked tweets for the authenticated account."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            tweets = client.fetch_bookmarks(_resolve_count(count))
            return {"tweets": _tweets_to_data(tweets)}

        return _tool_response(run)

    @server.tool()
    def get_list_timeline(
        list_id: str,
        count: int = DEFAULT_TOOL_COUNT,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch tweets from a Twitter/X list."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            tweets, next_cursor = client.fetch_list_timeline(
                list_id.strip(),
                _resolve_count(count),
                cursor=cursor,
                return_cursor=True,
            )
            return _timeline_data(tweets, next_cursor)

        return _tool_response(run)

    @server.tool()
    def get_followers(screen_name: str, count: int = DEFAULT_TOOL_COUNT) -> Dict[str, Any]:
        """Fetch followers for a Twitter/X user."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            profile = client.fetch_user(_normalize_handle(screen_name))
            users = client.fetch_followers(profile.id, _resolve_count(count))
            return {"user": _user_profile_to_dict(profile), "followers": _users_to_data(users)}

        return _tool_response(run)

    @server.tool()
    def get_following(screen_name: str, count: int = DEFAULT_TOOL_COUNT) -> Dict[str, Any]:
        """Fetch accounts followed by a Twitter/X user."""
        def run(client: TwitterClient) -> Dict[str, Any]:
            profile = client.fetch_user(_normalize_handle(screen_name))
            users = client.fetch_following(profile.id, _resolve_count(count))
            return {"user": _user_profile_to_dict(profile), "following": _users_to_data(users)}

        return _tool_response(run)

    return server


def create_asgi_app(settings: Optional[McpSettings] = None) -> Any:
    """Create the authenticated ASGI app mounted at the configured MCP path."""
    settings = settings or load_settings_from_env()
    _require_api_key(settings)

    server = create_mcp_server(settings=settings)
    inner_app = server.streamable_http_app()
    return ApiKeyOriginMiddleware(inner_app, settings)


def main() -> None:
    """Run the remote HTTP MCP server."""
    settings = load_settings_from_env()
    _require_api_key(settings)

    import uvicorn

    app = create_asgi_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)


def _tool_response(action: Callable[["TwitterClient"], Dict[str, Any]]) -> Dict[str, Any]:
    from .exceptions import TwitterError
    from .output import error_payload, success_payload

    try:
        return success_payload(action(_get_client()))
    except (TwitterError, RuntimeError, ValueError) as exc:
        return error_payload(_error_code_from_exc(exc), str(exc))


@lru_cache(maxsize=1)
def _get_client() -> "TwitterClient":
    from .auth import get_cookies
    from .client import TwitterClient
    from .config import load_config

    config = load_config()
    cookies = get_cookies()
    return TwitterClient(
        cookies["auth_token"],
        cookies["ct0"],
        config.get("rateLimit"),
        cookie_string=cookies.get("cookie_string"),
    )


def _new_fastmcp(fastmcp_cls: Any, settings: McpSettings) -> Any:
    kwargs = {
        "host": settings.host,
        "port": settings.port,
        "stateless_http": True,
        "json_response": True,
        "streamable_http_path": settings.path,
        "transport_security": _transport_security_settings(settings),
    }
    try:
        server = fastmcp_cls("twitter-cli", **kwargs)
    except TypeError:
        server = fastmcp_cls("twitter-cli")
        _configure_fastmcp_settings(server, kwargs)
    else:
        _configure_fastmcp_settings(server, kwargs)
    return server


def _configure_fastmcp_settings(server: Any, settings: Dict[str, Any]) -> None:
    fastmcp_settings = getattr(server, "settings", None)
    if fastmcp_settings is None:
        return
    for name, value in settings.items():
        if hasattr(fastmcp_settings, name):
            try:
                setattr(fastmcp_settings, name, value)
            except Exception:
                logger.debug("Unable to set FastMCP setting %s", name, exc_info=True)


def _transport_security_settings(settings: McpSettings) -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=not settings.allow_any_origin,
        allowed_hosts=list(settings.allowed_hosts or _default_allowed_hosts()),
        allowed_origins=list(settings.allowed_origins),
    )


def _timeline_data(tweets: Iterable[Any], next_cursor: Optional[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"tweets": _tweets_to_data(tweets)}
    if next_cursor:
        data["pagination"] = {"nextCursor": next_cursor}
    return data


def _resolve_count(count: Optional[int]) -> int:
    if count is None:
        return DEFAULT_TOOL_COUNT
    try:
        value = int(count)
    except (TypeError, ValueError) as exc:
        raise ValueError("count must be an integer") from exc
    if value <= 0:
        raise ValueError("count must be greater than 0")
    return min(value, MAX_TOOL_COUNT)


def _normalize_handle(screen_name: str) -> str:
    handle = screen_name.strip().lstrip("@")
    if not handle:
        raise ValueError("screen_name is required")
    return handle


def _normalize_tweet_id(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        raise ValueError("tweet_id is required")
    candidate = raw.split("/")[-1].split("?", 1)[0].split("#", 1)[0]
    if not candidate.isdigit():
        raise ValueError("tweet_id must be a numeric tweet id or Twitter/X status URL")
    return candidate


def _normalize_search_product(product: str) -> str:
    product_name = product.strip().title() if product else "Top"
    if product_name not in SEARCH_PRODUCTS:
        raise ValueError("product must be one of: %s" % ", ".join(sorted(SEARCH_PRODUCTS)))
    return product_name


def _validate_choices(
    values: Optional[Iterable[str]],
    allowed: Iterable[str],
    field_name: str,
) -> Optional[List[str]]:
    if not values:
        return None
    allowed_values = set(allowed)
    normalized = [str(value).strip().lower() for value in values if str(value).strip()]
    invalid = [value for value in normalized if value not in allowed_values]
    if invalid:
        raise ValueError(
            "%s contains invalid values: %s" % (field_name, ", ".join(sorted(set(invalid))))
        )
    return normalized or None


def _error_code_from_exc(exc: Exception) -> str:
    return str(getattr(exc, "error_code", "api_error"))


def _tweet_to_dict(tweet: Any) -> Dict[str, Any]:
    from .serialization import tweet_to_dict

    return tweet_to_dict(tweet)


def _tweets_to_data(tweets: Iterable[Any]) -> List[Dict[str, Any]]:
    from .serialization import tweets_to_data

    return tweets_to_data(tweets)


def _user_profile_to_dict(profile: Any) -> Dict[str, Any]:
    from .serialization import user_profile_to_dict

    return user_profile_to_dict(profile)


def _users_to_data(users: Iterable[Any]) -> List[Dict[str, Any]]:
    from .serialization import users_to_data

    return users_to_data(users)


def _headers_to_dict(headers: Iterable[Tuple[bytes, bytes]]) -> Dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in headers
    }


async def _send_json_error(send: AsgiSend, status: int, code: str) -> None:
    await _send_json_response(send, status, {"error": code})


async def _send_json_response(send: AsgiSend, status: int, payload: Dict[str, Any]) -> None:
    import json

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _health_payload() -> Dict[str, Any]:
    from . import __version__

    return {
        "status": "ok",
        "server": "twitter-cli",
        "version": __version__,
        "transport": "streamable-http",
    }


def _load_api_keys_from_env() -> Tuple[str, ...]:
    values = []
    single_key = os.environ.get("TWITTER_MCP_API_KEY", "").strip()
    if single_key:
        values.append(single_key)
    values.extend(_split_csv_env("TWITTER_MCP_API_KEYS"))
    return tuple(dict.fromkeys(value for value in values if value))


def _build_allowed_hosts(
    explicit_hosts: Iterable[str],
    allowed_origins: Iterable[str],
) -> Tuple[str, ...]:
    values = list(_default_allowed_hosts())
    for host in explicit_hosts:
        _append_host_patterns(values, host)
    for origin in allowed_origins:
        parsed = urllib.parse.urlparse(origin)
        if parsed.netloc:
            _append_host_patterns(values, parsed.netloc)
    return tuple(dict.fromkeys(value for value in values if value))


def _default_allowed_hosts() -> Tuple[str, ...]:
    return ("127.0.0.1:*", "localhost:*", "[::1]:*")


def _append_host_patterns(values: List[str], raw_host: str) -> None:
    host = raw_host.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    if not host:
        return
    values.append(host)
    if not host.endswith(":*") and ":" not in host:
        values.append("%s:*" % host)


def _split_csv_env(name: str) -> Tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


def _parse_port(raw: Optional[str], default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("TWITTER_MCP_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise RuntimeError("TWITTER_MCP_PORT must be between 1 and 65535")
    return port


def _parse_bool(raw: Optional[str]) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_path(raw: Optional[str]) -> str:
    path = (raw or DEFAULT_MCP_PATH).strip() or DEFAULT_MCP_PATH
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _require_api_key(settings: McpSettings) -> None:
    if not settings.api_keys:
        raise RuntimeError("Set TWITTER_MCP_API_KEY before starting the remote MCP server.")


if __name__ == "__main__":
    main()
