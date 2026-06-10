# X Trending

Daily pipeline for X trending operations:

1. Scrape X trending pages by country and category.
2. Normalize, deduplicate, and summarize Popular today tweets.
3. Send the result to a Feishu custom bot webhook.
4. Save a JSON copy for audit and troubleshooting.

## Current Scope

Configured countries:

- Global
- United States

Configured categories can be overridden with `X_CATEGORIES`. Defaults include Technology, News, Business & Finance, Science, Travel, Gaming, Sports, Health&Fitness, cryptocurrency, cars, music, dance, celebrity, relationship, movies&tv, nature&outdoors, Entertainment, food, meme, beauty, Pets, fashion, religion, and Home & Garden.

Extracted tweet fields:

- Trending term
- Author
- Published time
- Content
- Likes, reposts, replies, views
- Media URLs
- Tweet URL

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

Optional repository secret:

- `FEISHU_SECRET`

Useful workflow inputs:

- `countries`: comma-separated country names.
- `categories`: comma-separated category names.
- `output_format`: `feishu`, `json`, or `both`.
- `dry_run`: skip Feishu export.

## X Page Calibration

The scraper has a generic parser for X tweet articles, but country/category switching often depends on the exact page URL, account state, or frontend controls.

If the country/category page has a stable URL, set:

```dotenv
X_CATEGORY_URL_TEMPLATE=https://x.com/your/path?country={country_slug}&category={category_slug}
```

Available template variables:

- `{country}`
- `{category}`
- `{country_slug}`
- `{category_slug}`

If switching requires clicking controls in the browser, update `TrendingScraper._fetch_page()` or add a small browser interaction step before extracting trend terms and tweets.

## Project Structure

```text
scrapers/      Web scraping modules
processors/    Data cleaning and transformation
exporters/     Feishu and other output handlers
config/        Runtime configuration
utils/         Shared utilities
tests/         Unit tests
main.py        Pipeline entry point
```
