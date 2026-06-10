# twitter-cli MCP 改造报告

## 中文

### 改造目标

本次改造的目标是把 `twitter-cli` 增加为一个可部署在 VPS 上的远程只读 MCP
server，使不同位置的 agent 可以通过 MCP 调用同一个 Twitter/X 访问层。

目标形态：

```text
Agent
  -> HTTPS /mcp
  -> Cloudflare / reverse proxy
  -> VPS 上的 twitter-mcp
  -> 使用 VPS 环境变量中的 TWITTER_AUTH_TOKEN + TWITTER_CT0
  -> 访问 Twitter/X
```

MCP 访问密钥和 Twitter/X cookie 是两套凭据：

- `TWITTER_MCP_API_KEY`：保护 MCP server，agent 调用时携带。
- `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`：由 VPS 上的 MCP server 使用，用于访问
  Twitter/X。

### 改造方案

采用“保留 CLI，新增 MCP 入口”的方案，而不是把原有 CLI 改写成 MCP。

核心设计：

- 新增 `twitter_cli/mcp_server.py`，作为远程 MCP server 实现。
- 复用现有 `TwitterClient`、搜索构造器和序列化层，避免从 MCP 中调用 Click CLI
  或解析 stdout。
- 使用 MCP Python SDK 的 FastMCP + Streamable HTTP transport。
- 默认监听 `127.0.0.1:8000`，通过 Caddy/Nginx/Cloudflare 对外暴露 HTTPS。
- MCP endpoint 默认是 `/mcp`。
- MCP server 必须配置 `TWITTER_MCP_API_KEY` 才能启动。
- 支持两种认证 header：
  - `Authorization: Bearer <key>`
  - `X-API-Key: <key>`
- 支持 `TWITTER_MCP_ALLOWED_ORIGINS` 做 Origin 校验。
- 支持 `TWITTER_MCP_ALLOWED_HOSTS`，适配 MCP SDK 的 DNS rebinding Host 校验。
- 只暴露读工具，写操作完全不暴露。
- 默认 `count=20`，单次调用上限 `50`。

### 改造结果

已新增远程 MCP server 入口：

```bash
twitter-mcp
```

新增依赖和入口：

- `mcp`
- `uvicorn`
- `twitter-mcp = "twitter_cli.mcp_server:main"`

已暴露的只读 MCP tools：

- `health`
- `whoami`
- `search_tweets`
- `get_tweet_detail`
- `get_article`
- `get_user_profile`
- `get_user_tweets`
- `get_home_timeline`
- `get_following_timeline`
- `get_bookmarks`
- `get_list_timeline`
- `get_followers`
- `get_following`

新增部署材料：

- `docs/mcp-systemd.md`：systemd + reverse proxy 部署说明。
- `deploy/twitter-mcp.service.example`：systemd unit 示例。
- `deploy/Caddyfile.twitter-mcp.example`：Caddy 反向代理示例。

新增测试：

- `tests/test_mcp_server.py`
  - API key 校验。
  - Origin 校验。
  - Host allowlist 构造。
  - count 上限。
  - tweet id / handle / search 参数归一化。

已完成验证：

```bash
uv run --extra dev ruff check .
uv run --extra dev pytest -q
uv run --extra dev mypy twitter_cli
```

验证结果：

- Ruff：通过。
- Pytest：`261 passed, 6 deselected`。
- Mypy：通过。

### 已知问题

- Twitter/X cookie 可能过期。过期后需要更新 VPS 上的 `TWITTER_AUTH_TOKEN` 和
  `TWITTER_CT0`，然后重启服务。
- 所有 agent 共用 VPS 上同一个 Twitter/X 身份。当前没有按 agent 区分 Twitter/X
  账号。
- 当前只支持通过 `TWITTER_AUTH_TOKEN` + `TWITTER_CT0` 配置 Twitter/X 身份；还没有
  增加 `TWITTER_COOKIE_STRING` 环境变量来传完整 cookie string。
- 不同 MCP client 对远程 HTTP MCP 和自定义 header 的支持可能不同。客户端必须能配置
  MCP URL 和 API key header。
- Cloudflare 橙云不替代应用层认证。仍然需要 `TWITTER_MCP_API_KEY`，并建议配置
  `TWITTER_MCP_ALLOWED_ORIGINS` 和 `TWITTER_MCP_ALLOWED_HOSTS`。
- MCP SDK 自带 Host 校验。如果反向代理把公网域名作为 upstream `Host` 转发，必须把
  该域名加入 `TWITTER_MCP_ALLOWED_HOSTS`。
- 当前未实现复杂限流、并发控制和 per-key 权限分级。只保留了单次 `count` 上限。
- 当前不暴露任何写操作，包括发推、回复、点赞、转推、收藏、关注和删除。

### 待处理事项

- 部署时确定真实域名，并配置：
  - `TWITTER_MCP_ALLOWED_ORIGINS=https://your-domain`
  - `TWITTER_MCP_ALLOWED_HOSTS=your-domain`
- 在 VPS 上创建 `/etc/twitter-mcp.env`，配置：
  - `TWITTER_AUTH_TOKEN`
  - `TWITTER_CT0`
  - `TWITTER_MCP_API_KEY`
  - 可选 `TWITTER_PROXY`
- 部署 systemd unit，并通过 Caddy/Nginx/Cloudflare 暴露 HTTPS。
- 使用真实 agent 做一次端到端 MCP 调用测试。
- 后续可选：增加 `TWITTER_COOKIE_STRING` 环境变量支持。
- 后续可选：增加简单全局限流和并发限制。
- 后续可选：增加多 key 轮换文档或 per-key 权限模型。
- 后续可选：增加 Docker/Compose 部署方式。
- 后续可选：如果确实需要写操作，再设计显式 opt-in 的写工具开关。

## English

### Goal

The goal of this change is to add a remotely deployable, read-only MCP server
to `twitter-cli`, so agents in different locations can access Twitter/X through
one MCP endpoint running on a VPS.

Target shape:

```text
Agent
  -> HTTPS /mcp
  -> Cloudflare / reverse proxy
  -> twitter-mcp on the VPS
  -> TWITTER_AUTH_TOKEN + TWITTER_CT0 from VPS environment
  -> Twitter/X
```

The MCP access key and Twitter/X cookies are separate credentials:

- `TWITTER_MCP_API_KEY`: protects the MCP server and is sent by agent clients.
- `TWITTER_AUTH_TOKEN` + `TWITTER_CT0`: used only by the MCP server on the VPS
  to access Twitter/X.

### Plan

The implementation keeps the existing CLI and adds a new MCP entry point,
instead of rewriting the Click CLI into MCP.

Core design:

- Add `twitter_cli/mcp_server.py` as the remote MCP server implementation.
- Reuse `TwitterClient`, the search query builder, and existing serialization
  helpers. MCP tools do not invoke Click commands or parse CLI stdout.
- Use the MCP Python SDK with FastMCP and Streamable HTTP transport.
- Bind to `127.0.0.1:8000` by default and expose HTTPS through
  Caddy/Nginx/Cloudflare.
- Use `/mcp` as the default MCP endpoint.
- Require `TWITTER_MCP_API_KEY` before the MCP server can start.
- Support two authentication headers:
  - `Authorization: Bearer <key>`
  - `X-API-Key: <key>`
- Support Origin validation through `TWITTER_MCP_ALLOWED_ORIGINS`.
- Support `TWITTER_MCP_ALLOWED_HOSTS` for the MCP SDK DNS rebinding Host check.
- Expose read-only tools only. Write operations are not exposed.
- Use `count=20` by default and cap each call at `50`.

### Result

The remote MCP server entry point has been added:

```bash
twitter-mcp
```

Added runtime dependencies and script entry:

- `mcp`
- `uvicorn`
- `twitter-mcp = "twitter_cli.mcp_server:main"`

Exposed read-only MCP tools:

- `health`
- `whoami`
- `search_tweets`
- `get_tweet_detail`
- `get_article`
- `get_user_profile`
- `get_user_tweets`
- `get_home_timeline`
- `get_following_timeline`
- `get_bookmarks`
- `get_list_timeline`
- `get_followers`
- `get_following`

Added deployment materials:

- `docs/mcp-systemd.md`: systemd + reverse proxy deployment guide.
- `deploy/twitter-mcp.service.example`: example systemd unit.
- `deploy/Caddyfile.twitter-mcp.example`: example Caddy reverse proxy config.

Added tests:

- `tests/test_mcp_server.py`
  - API key validation.
  - Origin validation.
  - Host allowlist construction.
  - Count cap.
  - Tweet id / handle / search argument normalization.

Completed verification:

```bash
uv run --extra dev ruff check .
uv run --extra dev pytest -q
uv run --extra dev mypy twitter_cli
```

Verification result:

- Ruff: passed.
- Pytest: `261 passed, 6 deselected`.
- Mypy: passed.

### Known Issues

- Twitter/X cookies may expire. When they do, update `TWITTER_AUTH_TOKEN` and
  `TWITTER_CT0` on the VPS, then restart the service.
- All agents share the same Twitter/X identity configured on the VPS. There is
  no per-agent Twitter/X account separation.
- Twitter/X identity is currently configured with `TWITTER_AUTH_TOKEN` +
  `TWITTER_CT0`; `TWITTER_COOKIE_STRING` is not implemented yet.
- Remote HTTP MCP and custom header support varies across MCP clients. The
  client must be able to configure the MCP URL and API key header.
- Cloudflare orange cloud does not replace application-layer authentication.
  `TWITTER_MCP_API_KEY` is still required, and `TWITTER_MCP_ALLOWED_ORIGINS`
  plus `TWITTER_MCP_ALLOWED_HOSTS` are recommended.
- The MCP SDK has its own Host validation. If the reverse proxy forwards the
  public domain as the upstream `Host`, that domain must be listed in
  `TWITTER_MCP_ALLOWED_HOSTS`.
- Complex rate limiting, concurrency control, and per-key permissions are not
  implemented yet. The current lightweight guard is the per-call `count` cap.
- No write operations are exposed, including post, reply, like, retweet,
  bookmark, follow, unfollow, or delete.

### TODO

- Choose the real deployment domain and configure:
  - `TWITTER_MCP_ALLOWED_ORIGINS=https://your-domain`
  - `TWITTER_MCP_ALLOWED_HOSTS=your-domain`
- Create `/etc/twitter-mcp.env` on the VPS with:
  - `TWITTER_AUTH_TOKEN`
  - `TWITTER_CT0`
  - `TWITTER_MCP_API_KEY`
  - Optional `TWITTER_PROXY`
- Deploy the systemd unit and expose HTTPS through Caddy/Nginx/Cloudflare.
- Run an end-to-end MCP call from a real agent client.
- Optional: add `TWITTER_COOKIE_STRING` environment variable support.
- Optional: add simple global rate limiting and concurrency limits.
- Optional: document multi-key rotation or add a per-key permission model.
- Optional: add Docker/Compose deployment.
- Optional: if write operations are ever needed, design an explicit opt-in
  switch for write tools.
