# X Trending

抓推文➡️分类汇总➡️写入飞书

A Python application that scrapes X (Twitter) Global Trending data across multiple countries and categories, organizes the data, and sends summaries to Feishu (飞书).

## Features

- **Multi-Country Support**: Global, United States, and more
- **Multi-Category Support**: Technology, News, Business & Finance, Science, Travel, Gaming, Sports, Health&Fitness, cryptocurrency, cars, music, dance, celebrity, relationship, movies&tv, nature&outdoors, Entertainment, food, meme, beauty, Pets, fashion, religion, Home & Garden, etc.
- **Data Extraction**: 
  - Trending terms
  - Popular today tweets
  - Author information
  - Publication time
  - Tweet content
  - Engagement metrics (likes, retweets, replies)
  - View count
  - Image/video URLs
  - Tweet URLs
- **Feishu Integration**: Automatic summarization and organization of trending data

## Architecture

```
├── scrapers/          # Web scraping modules
├── processors/        # Data processing and transformation
├── exporters/         # Output handlers (Feishu, file, etc.)
├── config/            # Configuration files
├── utils/             # Utility functions
├── tests/             # Unit and integration tests
└── main.py           # Entry point
```

## Installation

```bash
git clone https://github.com/tanshuwenes918/x-trending.git
cd x-trending
pip install -r requirements.txt
```

## Configuration

Create a `.env` file with the following variables:

```
FEISHU_WEBHOOK_URL=your_feishu_webhook_url
SCRAPE_INTERVAL=3600  # Seconds between scrapes
DEBUG=false
```

## Usage

```bash
python main.py
```

## Technologies

- **Scraping**: [Scrapling](https://github.com/D4Vinci/Scrapling)
- **Language**: Python 3.8+
- **Data Processing**: Pandas, BeautifulSoup
- **API Integration**: Feishu (Lark)

## Project Structure

- **Scrapers**: Handles data collection from X Global Trending
- **Processors**: Cleans, categorizes, and organizes trending data
- **Exporters**: Sends organized data to Feishu
- **Config**: Manages countries, categories, and settings
- **Utils**: Helper functions for logging, data validation, etc.

## License

MIT

## Author

tanshuwenes918
