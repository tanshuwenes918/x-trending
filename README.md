# X Trending

Daily X Global Trending pipeline for AI music and AI video growth operations.

## Flow

1. Open X Global Trending with Playwright.
2. Reuse `X_STORAGE_STATE_B64` or inject `X_COOKIES` for the X login session.
3. Open or click each configured category.
4. Scroll the category feed to collect tweet candidates.
5. Keep tweets from the configured lookback window.
6. Merge source categories into 6 operating groups.
7. Keep the hottest tweets in each group.
8. Use an OpenAI-compatible LLM to write Chinese summaries and traffic actions for AI music/video acquisition.
9. Send the report to Feishu and save a JSON copy.

## Scraped Fields

Each tweet candidate includes:

- `author`
- `created_at`
- `content`
- `replies`
- `retweets`
- `likes`
- `views`
- `media_urls`
- `tweet_url`

The processor also adds `country`, `source_category`, `trending_term`, and `score`.

## Feishu Output

```text
X 趋势日报 | AI 视频 & AI 音乐 2026-06-10T08:30:00+08:00

一、AI / Tech / Creator Tools
1. AI 音乐（工具热议）：创作者在讨论新工作流，适合拆成产品演示、教程短视频或生成模板。（1.2K赞 + 34转 + 56K浏览；https://x.com/...）

二、Music / Dance / Entertainment
暂无符合条件的推文。

七、最值得跟进的 5 个引流动作
1. AI 音乐工作流：发起“30 秒生成同款 BGM + 口播视频”挑战，提供 3 个模板，评论区关键词领取并跳转生成页。（34转 + 56K浏览；https://x.com/...）
```

When a metric is not collected from X, it is omitted instead of displayed as `0赞`.

## Category Groups

```text
AI / Tech / Creator Tools
Technology, Science, Business & Finance, cryptocurrency

Music / Dance / Entertainment
music, dance, celebrity, Movies & TV, anime

Viral Culture / Meme / Social Buzz
meme, relationship, fashion, beauty, food, Pets

Gaming / Sports / Youth Culture
Gaming, Sports, cars

Lifestyle / Outdoor / Travel
Travel, Nature & Outdoors, Health & Fitness, Home & Garden

News / Society / Sensitive Topics
News, religion
```

## Local Setup

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Fill `.env` locally:

```dotenv
X_COUNTRIES=Global
X_EXPLORE_URL=https://x.com/i/jf/global-trending/home
X_COOKIES=auth_token=...; ct0=...; guest_id=...
X_STORAGE_STATE_B64=

LLM_ENABLED=true
LLM_REQUIRED=true
LLM_API_KEY=...
LLM_BASE_URL=https://your-openai-compatible-endpoint
LLM_MODEL=...
LLM_REQUEST_TIMEOUT=300

OUTPUT_FORMAT=preview
DRY_RUN=true
```

The LLM client tries Responses first. If that endpoint returns HTTP 404, it uses Chat Completions with the same model, source data, and summary instructions. Both APIs use streaming and also accept complete JSON responses from compatible gateways. Authentication errors, rate limits, overloads, and incomplete output still fail the run and preserve a retry input. An explicit `model_not_found` error does not trigger a protocol switch.

Set `LLM_BASE_URL` to the service's base URL, such as `https://api.example.com` or `https://api.example.com/v1`, rather than a full `/responses` or `/chat/completions` endpoint. Switching back to a service that supports Responses requires no code changes.

Preview without sending Feishu:

```bash
python main.py --output-format preview --dry-run
```

Preview from the latest local JSON without scraping X again:

```bash
python main.py --input-json latest --output-format preview --dry-run
```

Send the latest local JSON to Feishu without scraping X again:

```bash
python main.py --input-json latest --output-format feishu
```

If LLM enrichment fails, the run exits unsuccessfully and saves the nonempty input as `outputs/retry_input_*.json`. Retry that file without scraping X again:

```bash
python main.py --input-json outputs/retry_input_<timestamp>.json --output-format preview --dry-run
```

Retry inputs are separate from completed daily reports and are never sent to Feishu by the failed run.

Only failures after valid input has been collected can produce a retry input. A login or scraping failure without usable data cannot be replayed. GitHub Actions artifacts are kept for the repository's configured artifact retention period; download the JSON for longer storage.

Check category clicking:

```bash
python scripts/check_x_categories.py --headed --use-chrome
```

### Restore an X session

An empty report does not prove that X has no matching tweets. A redirect to `/account/access`, a login page, or Cloudflare content means the trending feed may not have been accessible. The exact reason for `/account/access` must be checked in the browser; the URL alone does not establish whether the account needs verification or the runner was challenged.

First open `https://x.com/i/jf/global-trending/home` on your own computer. Complete any login or account verification manually and confirm that the trending feed and category buttons load. Then, in the project directory with the virtual environment activated, export a full Playwright login state:

```bash
python scripts/export_x_storage_state.py --persistent
```

This command works on macOS and Linux as well as Windows, and uses installed Google Chrome with a dedicated profile in `outputs/x_playwright_profile`. The profile allows you to complete login manually and reuse it for later exports. If Chrome is unavailable, omit `--persistent` to use the Playwright Chromium installed during setup.

When the browser opens, finish login/verification and press Enter in the terminal. The script reopens the configured trending URL and checks that it is ready. Login pages, account-access challenges, Cloudflare blocks, and timeouts fail the export; neither login state file is replaced. Only a successful check produces `outputs/x_storage_state.json` and `outputs/x_storage_state.b64.txt`.

Update the repository's **Settings → Secrets and variables → Actions → Secrets → X_STORAGE_STATE_B64** with the complete `.b64.txt` content. If GitHub CLI is installed and authenticated, macOS / Linux users can update it directly without printing the secret:

```bash
gh secret set X_STORAGE_STATE_B64 --repo tanshuwenes918/x-trending < outputs/x_storage_state.b64.txt
```

`X_STORAGE_STATE_B64` takes precedence over `X_COOKIES`; refresh or remove an old storage-state secret when changing the session. The JSON, base64 file, and persistent browser profile contain login credentials. Keep them local; `outputs/` is ignored by Git.

Run the workflow manually after updating the secret and check the scraping logs. A successful local export does not guarantee that X will accept the session from GitHub-hosted runners. If the same session works locally but Actions still reaches a verification/block page, use a trusted self-hosted runner with a supported network environment, or a supported X data source. Refreshing cookies alone may not resolve a runner/network challenge.

## GitHub Actions

The workflow is in `.github/workflows/daily-trending.yml`.

It runs every day at `01:00 UTC`, which is `09:00 Asia/Shanghai`, and can also be started manually.

Scheduled runs always scrape fresh X data. Normal manual runs also scrape fresh data when `retry_from_run` is left blank; a failed run never automatically replaces a later day's input.

To recover an LLM failure, open **Run workflow**, explicitly set `retry_from_run` to the failed run's numeric ID, and choose JSON output with `dry_run=true` for verification. The workflow downloads that run's `x-trending-output` artifact and reuses its `retry_input_*.json`, skipping X scraping. This mode generates a report for the original collection time. **Re-run jobs** preserves a run's original inputs; use a new normal run with `retry_from_run` blank to collect fresh data.

Required repository secrets:

- `X_STORAGE_STATE_B64` or `X_COOKIES`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `FEISHU_WEBHOOK_URL`

Optional repository secrets:

- `FEISHU_SECRET`

Do not put API keys, X cookies, or Feishu webhook URLs in source files, README, `.env.example`, or normal GitHub variables.

## Notes

GitHub Actions runs on a fresh Linux runner each time. The workflow installs Python dependencies and Playwright Chromium with system dependencies.

X sessions can expire or be invalidated after logout/password changes. If Actions starts redirecting to login, verify access locally and refresh `X_STORAGE_STATE_B64` using the steps above.
