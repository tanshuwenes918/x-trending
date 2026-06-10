"""Data validation utilities."""

from typing import Any, Dict


def validate_tweet_url(url: str) -> bool:
    """Validate Twitter/X tweet URL.
    
    Args:
        url: URL to validate
    
    Returns:
        True if valid tweet URL
    """
    return isinstance(url, str) and ("twitter.com" in url or "x.com" in url)


def validate_email(email: str) -> bool:
    """Validate email address.
    
    Args:
        email: Email to validate
    
    Returns:
        True if valid email
    """
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None
