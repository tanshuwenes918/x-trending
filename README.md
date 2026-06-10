# X Trending

Daily X trend pipeline for AI video and AI music growth operations.

## Flow

1. Open X Global Trending with Playwright.
2. Inject `X_COOKIES` to keep the X login session.
3. Click each configured category card.
4. Scroll the Popular today area to collect tweet candidates.
5. Keep tweets from the past 24 hours.
6. Merge source categories into 6 operating groups.
7. Keep the hottest 7 tweets in each group.
8. Optionally use an OpenAI-compatible LLM for summaries and traffic actions.
9. Send the report to Feishu and save a JSON copy.

## Feishu Output

```text
X 趋势日报 | AI 视频 & AI 音乐

数据范围：过去 24 小时
国家：Global
每类最多：7 条

一、AI / Tech / Creator Tools
1. 摘要：...
   链接：原推文

二、Music / Dance / Entertainment
1. 摘要：...
   链接：原推文

三、Viral Culture / Meme / Social Buzz
...

四、Gaming / Sports / Youth Culture
...

五、Lifestyle / Outdoor / Travel
...

六、News / Society / Sensitive Topics
...

七、最值得跟进的 5 个引流动作
1. 标题：...
   引流动作：...
   链接：原推文
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

Fill `.env`:

```dotenv
X_COUNTRIES=Global
X_EXPLORE_URL=https://x.com/i/jf/global-trending/home
X_COOKIES=auth_token=...; ct0=...; guest_id=...

OUTPUT_FORMAT=json
DRY_RUN=true
LLM_ENABLED=false
```

Dry run:

```bash
python main.py --output-format json --dry-run
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
- `FEISHU_WEBHOOK_URL` if exporting to Feishu

Optional repository secrets:

- `FEISHU_SECRET`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

Do not put API keys or X cookies in source files, README, `.env.example`, or normal GitHub variables.

## Notes

GitHub Actions can run this automatically. The runner installs Python dependencies and Playwright browsers each run; browser files are cached with `actions/cache` to reduce repeated download time.

X cookies expire or may be invalidated after logout/password changes. If Actions starts redirecting to login, update the `X_COOKIES` secret.
