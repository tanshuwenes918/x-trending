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

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
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

Check category clicking:

```bash
python scripts\check_x_categories.py --headed --use-chrome
```

If GitHub Actions is redirected to X login even after setting `X_COOKIES`, export a full Playwright login state locally:

```bash
python scripts\export_x_storage_state.py --persistent
```

After the browser opens, log in to X and press Enter in the terminal. Then copy the full content of `outputs/x_storage_state.b64.txt` into the GitHub Secret `X_STORAGE_STATE_B64`.

## GitHub Actions

The workflow is in `.github/workflows/daily-trending.yml`.

It runs every day at `01:00 UTC`, which is `09:00 Asia/Shanghai`, and can also be started manually.

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

X cookies can expire or be invalidated after logout/password changes. If Actions starts redirecting to login, update `X_STORAGE_STATE_B64` or `X_COOKIES`.
