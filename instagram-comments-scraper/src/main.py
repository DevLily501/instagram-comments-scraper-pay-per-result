thonimport argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List

# Ensure local packages are importable when running as a script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)

from extractors.instagram_parser import (  # type: ignore  # noqa: E402
    InstagramCommentExtractor,
    Comment,
)
from extractors.utils_validation import (  # type: ignore  # noqa: E402
    normalize_post_identifier,
    validate_non_empty_input_list,
)
from outputs.export_json import export_comments_to_file  # type: ignore  # noqa: E402

def load_settings() -> Dict[str, Any]:
    """
    Load configuration from settings.json if present,
    otherwise fall back to settings.example.json.
    """
    config_dir = os.path.join(CURRENT_DIR, "config")
    primary_path = os.path.join(config_dir, "settings.json")
    fallback_path = os.path.join(config_dir, "settings.example.json")

    path_to_use = primary_path if os.path.exists(primary_path) else fallback_path

    if not os.path.exists(path_to_use):
        raise FileNotFoundError(
            f"No configuration file found. Expected {primary_path} or {fallback_path}"
        )

    with open(path_to_use, "r", encoding="utf-8") as f:
        settings = json.load(f)

    return settings

def configure_logging(settings: Dict[str, Any]) -> None:
    logging_config = settings.get("logging", {})
    level_name = logging_config.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

def read_input_urls(path: str) -> List[str]:
    if not os.path.exists(path):
        logging.warning("Input URLs file '%s' not found. No URLs to scrape.", path)
        return []

    urls: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped)

    return urls

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Instagram Comments Scraper (Pay Per Result demo implementation)"
    )
    parser.add_argument(
        "--input",
        default=os.path.join(os.path.dirname(CURRENT_DIR), "data", "input_urls.txt"),
        help="Path to input file with Instagram post URLs or IDs (one per line).",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(CURRENT_DIR), "data"),
        help="Directory where JSON output will be written.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Maximum number of comments to fetch per post. "
        "Overrides configuration if provided.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode (caps total results to a small number).",
    )
    return parser

def main() -> None:
    settings = load_settings()
    configure_logging(settings)
    logger = logging.getLogger("main")

    parser = build_arg_parser()
    args = parser.parse_args()

    # Merge CLI overrides into settings
    scraper_config = settings.setdefault("scraper", {})
    if args.max_items is not None:
        scraper_config["max_items_per_post"] = args.max_items
    if args.demo:
        scraper_config["demo_mode"] = True

    max_items_per_post = int(scraper_config.get("max_items_per_post", 100))
    demo_mode = bool(scraper_config.get("demo_mode", True))

    output_config = settings.setdefault("output", {})
    output_dir = args.output_dir or output_config.get("directory", "data")
    file_prefix = output_config.get("file_prefix", "comments")

    request_config = settings.setdefault("request", {})
    timeout_seconds = float(request_config.get("timeout_seconds", 10))
    max_retries = int(request_config.get("max_retries", 3))
    retry_backoff_seconds = float(request_config.get("retry_backoff_seconds", 2))

    logger.info("Starting Instagram Comments Scraper")
    logger.debug("Effective configuration: %s", settings)

    raw_inputs = read_input_urls(args.input)
    if not validate_non_empty_input_list(raw_inputs):
        logger.error("No valid input URLs or IDs found. Exiting.")
        sys.exit(1)

    normalized_ids: List[str] = []
    for value in raw_inputs:
        post_id = normalize_post_identifier(value)
        if post_id is None:
            logger.warning("Skipping unrecognized input: %s", value)
            continue
        normalized_ids.append(post_id)

    if not normalized_ids:
        logger.error("No valid post identifiers after normalization. Exiting.")
        sys.exit(1)

    extractor = InstagramCommentExtractor(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    all_comments: List[Comment] = []
    total_cap = 10 if demo_mode else None

    for post_id in normalized_ids:
        if total_cap is not None and len(all_comments) >= total_cap:
            logger.info(
                "Demo mode cap of %s comments reached. Stopping further requests.",
                total_cap,
            )
            break

        try:
            logger.info("Fetching comments for post: %s", post_id)
            comments = extractor.fetch_comments_for_post(
                post_id, max_items_per_post
            )
            logger.info(
                "Fetched %d comments for post %s", len(comments), post_id
            )
            all_comments.extend(comments)

            if total_cap is not None and len(all_comments) >= total_cap:
                # Trim if we went over the cap in the last request
                all_comments = all_comments[:total_cap]
                logger.info(
                    "Demo mode: trimming total comments to %s and stopping.",
                    total_cap,
                )
                break

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error fetching comments for post %s: %s", post_id, exc)

    if not all_comments:
        logger.warning("No comments were retrieved. Nothing to export.")
        return

    output_path = export_comments_to_file(
        comments=all_comments,
        output_dir=output_dir,
        file_prefix=file_prefix,
    )

    logger.info("Export complete. Output written to %s", output_path)

if __name__ == "__main__":
    main()