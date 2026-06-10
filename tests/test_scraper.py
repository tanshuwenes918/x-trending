"""Tests for the trending scraper."""

import pytest
from scrapers.trending_scraper import TrendingScraper


class TestTrendingScraper:
    """Test cases for TrendingScraper."""
    
    def setup_method(self):
        """Setup test fixtures."""
        self.scraper = TrendingScraper()
    
    def test_scraper_initialization(self):
        """Test scraper initializes without errors."""
        assert self.scraper is not None
    
    def test_scrape_all_returns_dict(self):
        """Test scrape_all returns a dictionary."""
        result = self.scraper.scrape_all()
        assert isinstance(result, dict)
