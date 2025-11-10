thonimport json
import logging
import os
from datetime import datetime
from typing import List

from extractors.instagram_parser import Comment

logger = logging.getLogger(__name__)

def export_comments_to_file(
    comments: List[Comment],
    output_dir: str,
    file_prefix: str = "comments",
) -> str:
    """
    Serialize a sequence of Comment objects to a JSON file
    in the specified directory.

    Returns the absolute path of the written file.
    """
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"{file_prefix}-{timestamp}.json"
    output_path = os.path.join(output_dir, filename)

    payload = [comment.to_dict() for comment in comments]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info("Wrote %d comments to %s", len(comments), output_path)
    return os.path.abspath(output_path)