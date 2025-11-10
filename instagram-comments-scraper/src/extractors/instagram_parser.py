thonimport json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

@dataclass
class User:
    id: str
    username: str
    full_name: str
    is_verified: bool
    is_private: bool
    profile_pic_url: str

@dataclass
class Comment:
    post_id: str
    id: str
    user_id: str
    message: str
    created_at: datetime
    like_count: int
    reply_count: int
    user: User
    is_ranked: bool = False
    type: str = field(default="comment", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the Comment object into a JSON-serializable dict
        matching the README's documented output schema.
        """
        return {
            "postId": self.post_id,
            "type": self.type,
            "id": self.id,
            "userId": self.user_id,
            "message": self.message,
            "createdAt": self.created_at.replace(tzinfo=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "likeCount": self.like_count,
            "replyCount": self.reply_count,
            "user": {
                "id": self.user.id,
                "username": self.user.username,
                "fullName": self.user.full_name,
                "isVerified": self.user.is_verified,
                "isPrivate": self.user.is_private,
                "profilePicUrl": self.user.profile_pic_url,
            },
            "isRanked": self.is_ranked,
        }

class InstagramCommentExtractor:
    """
    Responsible for retrieving and parsing comment data from
    Instagram's public web JSON endpoints.

    This implementation targets the commonly used JSON endpoint:
      https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis

    Note:
    - Instagram may change their internal APIs at any time.
    - This scraper is best-effort; it includes robust error handling
      and graceful fallbacks so it will not crash if the format changes.
    """

    BASE_URL_TEMPLATE = "https://www.instagram.com/p/{shortcode}/"

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

        self.session = requests.Session()
        # A realistic, desktop-like User-Agent often helps avoid trivial blocking.
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

    def _request_with_retries(self, url: str) -> Optional[str]:
        """
        Perform an HTTP GET request with retry logic and exponential backoff.
        Returns the response text on success, or None on failure.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug("Requesting URL (attempt %d/%d): %s", attempt, self.max_retries, url)
                response = self.session.get(url, timeout=self.timeout_seconds)
                if response.status_code == 429:
                    # Rate limited; log and back off harder.
                    logger.warning("Rate limit encountered (HTTP 429) for URL %s", url)
                elif not response.ok:
                    logger.warning(
                        "Non-success status code %s for URL %s", response.status_code, url
                    )

                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                logger.error("Request error for %s: %s", url, exc)
                if attempt == self.max_retries:
                    logger.error("Max retries reached for %s. Giving up.", url)
                    break
                sleep_for = self.retry_backoff_seconds * attempt
                logger.info("Retrying in %.1f seconds...", sleep_for)
                time.sleep(sleep_for)
        return None

    def _extract_json_from_html(self, html: str) -> Optional[Dict[str, Any]]:
        """
        Instagram's HTML typically includes a JSON blob in a <script> tag.
        We attempt to locate it and parse it.

        This is intentionally tolerant: if the structure changes, we fail
        gracefully and return None.
        """
        marker = "window._sharedData = "
        idx = html.find(marker)
        if idx == -1:
            # Newer layouts might embed JSON in ".__a" responses or within other scripts.
            # Try to detect an inline JSON object by a simple heuristic.
            logger.debug("SharedData marker not found; attempting to parse entire response as JSON.")
            try:
                return json.loads(html)
            except json.JSONDecodeError:
                return None

        start_idx = idx + len(marker)
        end_idx = html.find(";</script>", start_idx)
        if end_idx == -1:
            end_idx = html.find(";</", start_idx)

        if end_idx == -1:
            logger.debug("Unable to find end of JSON blob in HTML.")
            return None

        json_str = html[start_idx:end_idx].strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("Failed to decode embedded JSON: %s", exc)
            return None

    def _locate_comment_nodes(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Try multiple known layouts of Instagram's public JSON
        to find and return raw comment 'node' dicts.
        """
        # Classic structure: graphql.shortcode_media.edge_media_to_parent_comment.edges
        paths = [
            [
                "graphql",
                "shortcode_media",
                "edge_media_to_parent_comment",
                "edges",
            ],
            # Alternative / older structures can be added here if necessary.
        ]

        for path in paths:
            current: Any = payload
            try:
                for key in path:
                    current = current[key]
                if isinstance(current, list):
                    # Expect list of edges: {"node": {...}}
                    nodes = []
                    for edge in current:
                        node = edge.get("node") if isinstance(edge, dict) else None
                        if isinstance(node, dict):
                            nodes.append(node)
                    if nodes:
                        return nodes
            except (KeyError, TypeError):
                continue

        logger.debug("No known comment structure found in payload.")
        return []

    def _parse_user(self, node: Dict[str, Any]) -> User:
        owner = node.get("owner", {}) or {}
        return User(
            id=str(owner.get("id", "")),
            username=str(owner.get("username", "")),
            full_name=str(owner.get("full_name", "")),
            is_verified=bool(owner.get("is_verified", False)),
            is_private=bool(owner.get("is_private", False)),
            profile_pic_url=str(owner.get("profile_pic_url", "")),
        )

    def _parse_comment(self, post_id: str, node: Dict[str, Any]) -> Comment:
        created_at_ts = node.get("created_at")
        if isinstance(created_at_ts, (int, float)):
            created_at = datetime.fromtimestamp(created_at_ts, tz=timezone.utc)
        else:
            created_at = datetime.now(tz=timezone.utc)

        like_count = 0
        if isinstance(node.get("edge_liked_by"), dict):
            like_count = int(node["edge_liked_by"].get("count", 0))
        elif isinstance(node.get("like_count"), int):
            like_count = int(node.get("like_count", 0))

        reply_count = 0
        if isinstance(node.get("edge_threaded_comments"), dict):
            reply_count = int(node["edge_threaded_comments"].get("count", 0))
        elif isinstance(node.get("reply_count"), int):
            reply_count = int(node.get("reply_count", 0))

        is_ranked = bool(node.get("is_ranked", False))

        user = self._parse_user(node)

        return Comment(
            post_id=str(post_id),
            id=str(node.get("id", "")),
            user_id=user.id,
            message=str(node.get("text", "")),
            created_at=created_at,
            like_count=like_count,
            reply_count=reply_count,
            user=user,
            is_ranked=is_ranked,
        )

    def fetch_comments_for_post(self, post_id: str, max_items: int) -> List[Comment]:
        """
        Fetch up to `max_items` comments for a given Instagram post.

        This implementation fetches the initial set of comments from the main
        HTML page. It does not attempt pagination for simplicity and stability.
        """
        url = self.BASE_URL_TEMPLATE.format(shortcode=post_id) + "?__a=1&__d=dis"
        html_or_json = self._request_with_retries(url)
        if html_or_json is None:
            logger.error("Failed to retrieve any data for post %s", post_id)
            return []

        payload = self._extract_json_from_html(html_or_json)
        if payload is None:
            logger.error("Unable to extract JSON payload for post %s", post_id)
            return []

        nodes = self._locate_comment_nodes(payload)
        logger.debug("Located %d comment nodes for post %s", len(nodes), post_id)

        comments: List[Comment] = []
        for node in nodes[: max_items or None]:
            try:
                comment = self._parse_comment(post_id, node)
                comments.append(comment)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error parsing comment node for post %s: %s", post_id, exc)

        return comments