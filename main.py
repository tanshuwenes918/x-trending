#!/usr/bin/env python3
"""Main entry point for X Trending scraper."""

import logging
import argparse
import json
import shlex
import sys
from copy import deepcopy
from datetime import datetime, timezone
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
        choices=["feishu", "json", "both", "preview"],
        default=None,
        help="Override OUTPUT_FORMAT for this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and write JSON without sending to Feishu.",
    )
    parser.add_argument(
        "--input-json",
        default="",
        help="Load an existing processed JSON file instead of scraping. Use 'latest' for the newest outputs/x_trending_*.json.",
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


def write_retry_checkpoint(data: dict) -> Path:
    """Preserve a nonempty LLM input separately from completed daily reports."""
    from config.settings import OUTPUT_DIR

    validate_report_data(data)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_time = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = OUTPUT_DIR / f"retry_input_{checkpoint_time}.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path.resolve()


def resolve_input_json(value: str) -> Path:
    """Resolve an input JSON path, with 'latest' as a convenience alias."""
    from config.settings import OUTPUT_DIR

    if value.lower() == "latest":
        candidates = sorted(
            OUTPUT_DIR.glob("x_trending_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(f"No x_trending_*.json files found in {OUTPUT_DIR}")
        return candidates[0]

    return Path(value)


def read_json_input(input_path: Path) -> dict:
    """Read a processed JSON file."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")
    return json.loads(input_path.read_text(encoding="utf-8"))


def validate_report_data(data: dict) -> None:
    """Do not enrich or publish a report with no usable source items."""
    if not any(group.get("items") for group in data.get("groups", [])):
        raw_count = data.get("summary", {}).get("raw_tweet_count", 0)
        raise RuntimeError(
            f"No eligible tweets for the daily report (raw_tweet_count={raw_count}). "
            "Check X access, category extraction, and tweet timestamps/lookback settings. "
            "No daily report will be written or sent."
        )


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
        
        # Scrape or load, enrich, persist, and export data
        if args.input_json:
            input_path = resolve_input_json(args.input_json)
            logger.info("Loading existing JSON input from %s", input_path)
            trending_data = read_json_input(input_path)
        else:
            raw_data = scraper.scrape_all()
            trending_data = processor.process(raw_data)

        validate_report_data(trending_data)
        retry_input = deepcopy(trending_data)
        try:
            trending_data = llm_processor.enrich(trending_data)
        except Exception:
            try:
                retry_path = write_retry_checkpoint(retry_input)
            except Exception:
                logger.error("Failed to save LLM retry input", exc_info=True)
            else:
                logger.error(
                    "LLM failed; saved nonempty retry input to %s. "
                    "Replay with: python main.py --input-json %s",
                    retry_path,
                    shlex.quote(str(retry_path)),
                )
            raise
        validate_report_data(trending_data)
        output_path = write_json_output(trending_data)
        logger.info("Wrote JSON output to %s", output_path)

        if output_format == "preview":
            print(exporter.format_plain_text(trending_data))
        elif output_format in {"feishu", "both"} and not dry_run:
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
