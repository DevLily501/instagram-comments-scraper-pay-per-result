thonimport logging
import re
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_POST_PATH_PATTERNS = (
    re.compile(r"^/p/([^/]+)/?"),
    re.compile(r"^/reel/([^/]+)/?"),
    re.compile(r"^/tv/([^/]+)/?"),
)

def normalize_post_identifier(value: str) -> Optional[str]:
    """
    Accepts either:
      - a full Instagram URL (e.g., https://www.instagram.com/p/SHORTCODE/)
      - or a bare shortcode / post ID.

    Returns a normalized shortcode string or None if unrecognized.
    """
    value = value.strip()
    if not value:
        return None

    # If it's a URL, try to extract the shortcode from the path.
    if value.startswith("http://") or value.startswith("https://"):
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            logger.warning("Failed to parse URL '%s': %s", value, exc)
            return None

        path = parsed.path or ""
        for pattern in _POST_PATH_PATTERNS:
            match = pattern.match(path)
            if match:
                shortcode = match.group(1)
                logger.debug(
                    "Extracted shortcode '%s' from URL '%s'", shortcode, value
                )
                return shortcode

        logger.warning("Could not recognize Instagram post pattern in path '%s'", path)
        return None

    # Otherwise, assume it's a shortcode-like token if it looks sane.
    if "/" in value or " " in value:
        logger.warning("Invalid post identifier (contains separator or spaces): '%s'", value)
        return None

    # Shortcodes are typically alphanumeric plus a few safe chars.
    if not re.match(r"^[A-Za-z0-9_\-]+$", value):
        logger.warning("Post identifier contains unexpected characters: '%s'", value)
        return None

    return value

def validate_non_empty_input_list(items: List[str]) -> bool:
    """
    Ensure that there is at least one non-empty entry in the input list.
    """
    for item in items:
        if item.strip():
            return True
    logger.error("Input list is empty or contains only blank/whitespace entries.")
    return False