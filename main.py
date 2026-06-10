#!/usr/bin/env python3
"""Main entry point for X Trending scraper."""

import logging
import argparse
import json
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


def parse_args():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the X Trending scraper.")
    parser.add_argument(
        "--output-format",
        choices=["feishu", "json", "both"],
        default=None,
        help="Override OUTPUT_FORMAT for this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and write JSON without sending to Feishu.",
    )
    return parser.parse_args()


def write_json_output(data: dict) -> Path:
    """Write processed data to an output JSON file."""
    from config.settings import OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_timestamp = data.get("timestamp", "run").replace(":", "-")
    output_path = OUTPUT_DIR / f"x_trending_{safe_timestamp}.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def main():
    """Main entry point."""
    # Load environment variables
    load_dotenv()
    args = parse_args()
    logger.info("Starting X Trending scraper...")
    
    try:
        # Import and run the scraper
        from scrapers.trending_scraper import TrendingScraper
        from processors.data_processor import DataProcessor
        from processors.llm_processor import LLMProcessor
        from exporters.feishu_exporter import FeishuExporter
        from config.settings import CATEGORIES, COUNTRIES, DRY_RUN, OUTPUT_FORMAT
        
        logger.info(f"Configured countries: {COUNTRIES}")
        logger.info(f"Configured categories: {CATEGORIES}")
        
        output_format = args.output_format or OUTPUT_FORMAT
        dry_run = args.dry_run or DRY_RUN

        # Initialize pipeline
        scraper = TrendingScraper()
        processor = DataProcessor()
        llm_processor = LLMProcessor()
        exporter = FeishuExporter()
        
        # Scrape, process, persist, and export data
        raw_data = scraper.scrape_all()
        trending_data = processor.process(raw_data)
        trending_data = llm_processor.enrich(trending_data)
        output_path = write_json_output(trending_data)
        logger.info("Wrote JSON output to %s", output_path)

        if output_format in {"feishu", "both"} and not dry_run:
            if not exporter.export(trending_data):
                raise RuntimeError("Failed to export data to Feishu")
        else:
            logger.info("Skipping Feishu export. output_format=%s dry_run=%s", output_format, dry_run)
        
        logger.info("X Trending scraper completed successfully")
        
    except Exception as e:
        logger.error(f"Error running scraper: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
