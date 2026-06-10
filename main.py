#!/usr/bin/env python3
"""Main entry point for X Trending scraper."""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    # Load environment variables
    load_dotenv()
    logger.info("Starting X Trending scraper...")
    
    try:
        # Import and run the scraper
        from scrapers.trending_scraper import TrendingScraper
        from exporters.feishu_exporter import FeishuExporter
        from config.settings import COUNTRIES, CATEGORIES
        
        logger.info(f"Configured countries: {COUNTRIES}")
        logger.info(f"Configured categories: {CATEGORIES}")
        
        # Initialize scraper and exporter
        scraper = TrendingScraper()
        exporter = FeishuExporter()
        
        # Scrape and export data
        trending_data = scraper.scrape_all()
        exporter.export(trending_data)
        
        logger.info("X Trending scraper completed successfully")
        
    except Exception as e:
        logger.error(f"Error running scraper: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
