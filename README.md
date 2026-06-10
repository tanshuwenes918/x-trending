# X Trending

Daily X trend pipeline for AI video and AI music growth operations.

The report is designed for off-platform acquisition, not media commentary:

1. Scrape X trending tweets for `Global`.
2. Keep tweets published in the past 24 hours.
3. Merge source categories into 6 operating groups.
4. Pick the hottest 7 tweets in each group.
5. Use an optional OpenAI-compatible LLM to write short Chinese summaries.
6. Put the 5 best traffic-driving actions at the end.
7. Send the report to Feishu and save a JSON copy.

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
music, dance, Entertainment, celebrity, movies&tv, anime

Viral Culture / Meme / Social Buzz
meme, relationship, fashion, beauty, food, Pets

Gaming / Sports / Youth Culture
Gaming, Sports, cars

Lifestyle / Outdoor / Travel
Travel, nature&outdoors, Health&Fitness, Home & Garden

News / Society / Sensitive Topics
News, religion
```

## Ranking

Each group keeps at most 7 tweets from the past 24 hours.

Score:

```text
score = views * 0.4 + likes * 2 + retweets * 4 + replies * 3
```

If views are missing:

```text
score = likes * 2 + retweets * 4 + replies * 3
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
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
FEISHU_SECRET=

LLM_ENABLED=true
LLM_API_KEY=
LLM_BASE_URL=https://your-openai-compatible-endpoint
LLM_MODEL=

X_COUNTRIES=Global
OUTPUT_FORMAT=both
DRY_RUN=false
```

Run:

```bash
python main.py
```

Dry run without sending to Feishu:

```bash
python main.py --output-format json --dry-run
```

## GitHub Actions

The workflow is in `.github/workflows/daily-trending.yml`.

It runs every day at `00:30 UTC`, which is `08:30 Asia/Shanghai`, and can also be started manually from the GitHub Actions page.

Required repository secret:

- `FEISHU_WEBHOOK_URL`

Optional repository secrets:

- `FEISHU_SECRET`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

Do not put API keys in source files, README, `.env.example`, or normal GitHub variables.

## X Page Calibration

The scraper has a generic parser for X tweet articles, but country/category switching can depend on the exact page URL, account state, or frontend controls.

If the category page has a stable URL, set:

```dotenv
X_CATEGORY_URL_TEMPLATE=https://x.com/your/path?country={country_slug}&category={category_slug}
```

Available template variables:

- `{country}`
- `{category}`
- `{country_slug}`
- `{category_slug}`

If switching requires clicking controls in the browser, update `TrendingScraper._fetch_page()` or add a browser interaction step before extracting trend terms and tweets.
