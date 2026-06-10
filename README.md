# X Trending

Daily X Global Trending pipeline for AI video and AI music growth operations.

## Flow

1. Open X Global Trending with Playwright.
2. Inject `X_COOKIES` to keep the X login session.
3. Click each configured category.
4. Scroll the Popular today area to collect tweet candidates.
5. Keep tweets from the past 24 hours.
6. Merge source categories into 6 operating groups.
7. Keep the hottest 7 tweets in each group.
8. Use an OpenAI-compatible LLM to write Chinese summaries and traffic actions.
9. Send the report to Feishu and save a JSON copy.

## Scraped Fields

Each tweet candidate includes:

- author
- created_at
- content
- replies
- retweets
- likes
- views
- media_urls
- tweet_url

The processor also adds `country`, `source_category`, `trending_term`, and `score`.

## Feishu Output

```text
X 趋势日报 | AI 视频 & AI 音乐

数据范围：过去 24 小时
国家：Global
每类最多：7 条 | 入选推文：42 | LLM：已启用

一、AI / Tech / Creator Tools
1. 科技趋势（Claude Fable 5新消息）：Claude 发布新模型相关消息，适合观察创作者对 AI 工具升级的反应。（89.6K赞 + 20.9K转 + 30.1M浏览；https://x.com/...）

二、Music / Dance / Entertainment
...

七、最值得跟进的 5 个引流动作
1. AI MV模板挑战：把该热点改成短视频模板挑战，引导用户生成同款 AI 视频或 AI 音乐。（12.3K赞 + 2.1K转 + 1.8M浏览；https://x.com/...）
```

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
scrapling install
copy .env.example .env
```

Fill `.env` locally:

```dotenv
X_COUNTRIES=Global
X_EXPLORE_URL=https://x.com/i/jf/global-trending/home
X_COOKIES=auth_token=...; ct0=...; guest_id=...

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

Send to Feishu after configuring `FEISHU_WEBHOOK_URL`:

```bash
python main.py --output-format feishu
```

Check category clicking:

```bash
python scripts\check_x_categories.py --headed --use-chrome
```

## GitHub Actions

The workflow is in `.github/workflows/daily-trending.yml`.

It runs every day at `00:30 UTC`, which is `08:30 Asia/Shanghai`, and can also be started manually.

Required repository secrets:

- `X_COOKIES`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `FEISHU_WEBHOOK_URL`

Optional repository secrets:

- `FEISHU_SECRET`

Do not put API keys, X cookies, or Feishu webhook URLs in source files, README, `.env.example`, or normal GitHub variables.

## Notes

GitHub Actions runs on a fresh Linux runner each time. The workflow installs Python dependencies and Playwright browsers, with browser files cached by `actions/cache` to reduce repeated downloads.

X cookies can expire or be invalidated after logout/password changes. If Actions starts redirecting to login, update the `X_COOKIES` secret.
