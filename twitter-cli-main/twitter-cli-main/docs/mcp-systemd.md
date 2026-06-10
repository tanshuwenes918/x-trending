# Remote MCP Deployment

This guide runs the read-only twitter-cli MCP server on a VPS with systemd and
exposes it through a reverse proxy such as Caddy behind Cloudflare.

## Server Shape

```text
Agent clients
  -> https://twitter-mcp.example.com/mcp
  -> Cloudflare
  -> Caddy or Nginx on the VPS
  -> 127.0.0.1:8000
  -> twitter-mcp
  -> x.com with TWITTER_AUTH_TOKEN and TWITTER_CT0
```

The MCP API key protects the MCP server. It is separate from Twitter/X cookies.

## Environment

Create `/etc/twitter-mcp.env`:

```dotenv
TWITTER_AUTH_TOKEN=replace-with-twitter-auth-token
TWITTER_CT0=replace-with-twitter-ct0
TWITTER_MCP_API_KEY=replace-with-a-random-long-secret

TWITTER_MCP_HOST=127.0.0.1
TWITTER_MCP_PORT=8000
TWITTER_MCP_PATH=/mcp

# Set this after the domain is known. Requests without Origin are still allowed.
TWITTER_MCP_ALLOWED_ORIGINS=https://twitter-mcp.example.com

# Host header allowlist for MCP SDK DNS rebinding protection.
# This is also derived from TWITTER_MCP_ALLOWED_ORIGINS, but keeping it explicit
# makes reverse proxy behavior easier to audit.
TWITTER_MCP_ALLOWED_HOSTS=twitter-mcp.example.com
```

Optional settings:

```dotenv
# Comma-separated extra keys. Useful when rotating keys or separating agents.
TWITTER_MCP_API_KEYS=another-secret,third-secret

# Only use this for local debugging.
TWITTER_MCP_ALLOW_ANY_ORIGIN=false

# Proxy used by twitter-cli when calling x.com.
TWITTER_PROXY=socks5://127.0.0.1:1080
```

Protect the env file:

```bash
sudo chown root:root /etc/twitter-mcp.env
sudo chmod 600 /etc/twitter-mcp.env
```

## Install

Example layout:

```bash
sudo useradd --system --home /opt/twitter-cli --shell /usr/sbin/nologin twitter-mcp
sudo mkdir -p /opt/twitter-cli
sudo chown twitter-mcp:twitter-mcp /opt/twitter-cli

sudo -u twitter-mcp git clone https://github.com/jackwener/twitter-cli.git /opt/twitter-cli
cd /opt/twitter-cli
sudo -u twitter-mcp uv sync
```

Install the systemd unit:

```bash
sudo cp deploy/twitter-mcp.service.example /etc/systemd/system/twitter-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now twitter-mcp
sudo journalctl -u twitter-mcp -f
```

## Reverse Proxy

Caddy example:

```caddyfile
twitter-mcp.example.com {
	reverse_proxy 127.0.0.1:8000
}
```

If Cloudflare orange cloud is enabled, keep the service bound to
`127.0.0.1:8000`. For stronger origin protection, also restrict direct inbound
traffic to the VPS or use Cloudflare Tunnel.

If your reverse proxy rewrites the upstream `Host` header to `127.0.0.1:8000`,
the default local Host allowlist is enough. If it forwards the public domain as
the Host header, set `TWITTER_MCP_ALLOWED_HOSTS` to that domain.

## Client Configuration

Use the Streamable HTTP endpoint:

```text
https://twitter-mcp.example.com/mcp
```

Send one of these headers:

```http
Authorization: Bearer replace-with-a-random-long-secret
```

or:

```http
X-API-Key: replace-with-a-random-long-secret
```

The server also accepts `Api-Key` and `API_KEY` headers for clients with limited
header naming options.

## Exposed Tools

The server is read-only. It does not expose post, reply, like, retweet,
bookmark, follow, unfollow, or delete operations.

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

Default `count` is 20. A single tool call is capped at 50 results.

## Updating Cookies

If `health` or `whoami` reports an authentication error, update
`TWITTER_AUTH_TOKEN` and `TWITTER_CT0` in `/etc/twitter-mcp.env`, then restart:

```bash
sudo systemctl restart twitter-mcp
```
