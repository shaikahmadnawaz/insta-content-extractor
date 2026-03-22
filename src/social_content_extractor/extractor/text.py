"""Text, URL, and content-selection helpers for the extractor."""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse


def _read_env_file(env_path: str = ".env") -> dict[str, str]:
    """Read simple KEY=VALUE pairs from a local .env file."""
    if not os.path.exists(env_path):
        return {}

    values: dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def _get_env_value(name: str, env_path: str = ".env") -> str | None:
    """Get an environment variable, falling back to the project's .env file."""
    value = os.environ.get(name)
    if value:
        return value
    return _read_env_file(env_path).get(name)


def _extract_instagram_url_parts(url: str) -> tuple[str, str]:
    """Extract the Instagram media kind and shortcode from a supported URL."""
    path = urlparse(url).path.strip("/")
    match = re.match(r"(?P<kind>p|reel|tv)/(?P<shortcode>[A-Za-z0-9_-]+)", path)
    if match:
        return match.group("kind"), match.group("shortcode")
    raise ValueError(
        f"Could not extract shortcode from URL: {url}\n"
        "Expected: https://www.instagram.com/p/SHORTCODE/ "
        "or /reel/SHORTCODE/ or /tv/SHORTCODE/"
    )


def _extract_youtube_url_parts(url: str) -> tuple[str, str]:
    """Extract the YouTube media kind and video ID from a supported URL."""
    parsed = urlparse(url)
    hostname = (parsed.netloc or "").lower()
    path = parsed.path.strip("/")

    if hostname in {"youtu.be", "www.youtu.be"}:
        video_id = path.split("/", 1)[0]
        if video_id:
            return "shorts", video_id

    if "youtube.com" in hostname:
        if path.startswith("shorts/"):
            video_id = path.split("/", 1)[1].split("/", 1)[0]
            if video_id:
                return "shorts", video_id

        if path == "watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return "shorts", video_id

    raise ValueError(
        f"Could not extract YouTube video ID from URL: {url}\n"
        "Expected: https://www.youtube.com/shorts/VIDEO_ID "
        "or https://youtu.be/VIDEO_ID"
    )


def _extract_supported_url_parts(url: str) -> tuple[str, str, str]:
    """Return the platform, media kind, and content ID for a supported URL."""
    hostname = (urlparse(url).netloc or "").lower()
    if "instagram.com" in hostname:
        kind, shortcode = _extract_instagram_url_parts(url)
        return "instagram", kind, shortcode
    if "youtube.com" in hostname or "youtu.be" in hostname:
        kind, video_id = _extract_youtube_url_parts(url)
        return "youtube", kind, video_id
    raise ValueError(
        f"Unsupported URL: {url}\n"
        "Supported sources: Instagram posts/reels and YouTube Shorts"
    )


def extract_shortcode(url: str) -> str:
    """Extract the platform-specific content ID from a supported URL."""
    _, _, content_id = _extract_supported_url_parts(url)
    return content_id


def _build_canonical_instagram_url(kind: str, shortcode: str) -> str:
    """Return a canonical Instagram URL for the detected media type."""
    return f"https://www.instagram.com/{kind}/{shortcode}/"


def _build_canonical_youtube_url(video_id: str) -> str:
    """Return a canonical YouTube Shorts URL."""
    return f"https://www.youtube.com/shorts/{video_id}"


def _build_output_artifact_stem(shortcode: str, ocr_provider: str | None) -> str:
    """Build a mode-aware artifact stem so OCR runs can coexist side by side."""
    if not ocr_provider:
        return shortcode

    suffix_map = {
        "tesseract": "local",
        "sarvam": "sarvam",
        "sarvam_vision": "sarvam-vision",
    }
    suffix = suffix_map.get(ocr_provider)
    if not suffix:
        return shortcode
    return f"{shortcode}.{suffix}"


def _output_collection_for_kind(kind: str) -> str:
    """Map Instagram URL kinds to top-level output buckets."""
    return "reels" if kind == "reel" else "posts"


def _extract_hashtags(text: str) -> list[str]:
    """Extract hashtags without the leading # while keeping insertion order."""
    seen = set()
    hashtags = []
    for tag in re.findall(r"(?<!\w)#([A-Za-z0-9_]+)", text or ""):
        normalized = tag.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        hashtags.append(normalized)
    return hashtags


def _extract_mentions(text: str) -> list[str]:
    """Extract @mentions while avoiding duplicates."""
    seen = set()
    mentions = []
    for mention in re.findall(r"(?<![\w.])@([A-Za-z0-9_.]+)", text or ""):
        normalized = mention.strip("._")
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        mentions.append(normalized)
    return mentions


def _select_primary_content(caption: str, ocr_text: str) -> dict[str, str]:
    """Choose the most useful primary text while keeping caption and OCR separate."""
    caption_clean = caption.strip()
    ocr_clean = ocr_text.strip()

    if not caption_clean and not ocr_clean:
        return {
            "content_strategy": "none",
            "primary_source": "none",
            "primary_text": "",
        }
    if caption_clean and not ocr_clean:
        return {
            "content_strategy": "caption_only",
            "primary_source": "caption",
            "primary_text": caption_clean,
        }
    if ocr_clean and not caption_clean:
        return {
            "content_strategy": "ocr_only",
            "primary_source": "ocr",
            "primary_text": ocr_clean,
        }

    caption_word_count = _count_meaningful_words(caption_clean)
    ocr_word_count = _count_meaningful_words(ocr_clean)
    caption_substantive = caption_word_count >= 12
    ocr_substantive = ocr_word_count >= 12
    caption_promo_markers = _promotional_marker_count(caption_clean)

    if caption_substantive and not ocr_substantive:
        if caption_promo_markers >= 2 and ocr_word_count >= 6:
            return {
                "content_strategy": "caption_plus_ocr",
                "primary_source": "ocr",
                "primary_text": ocr_clean,
            }
        return {
            "content_strategy": "media_representational",
            "primary_source": "caption",
            "primary_text": caption_clean,
        }
    if ocr_substantive and not caption_substantive:
        return {
            "content_strategy": "ocr_only",
            "primary_source": "ocr",
            "primary_text": ocr_clean,
        }

    if caption_substantive and ocr_substantive:
        primary_source = _choose_primary_source(caption_clean, ocr_clean)
        return {
            "content_strategy": "caption_plus_ocr",
            "primary_source": primary_source,
            "primary_text": caption_clean if primary_source == "caption" else ocr_clean,
        }

    primary_source = "caption" if caption_word_count >= ocr_word_count else "ocr"
    return {
        "content_strategy": "caption_plus_ocr",
        "primary_source": primary_source,
        "primary_text": caption_clean if primary_source == "caption" else ocr_clean,
    }


def _count_meaningful_words(text: str) -> int:
    """Count readable words for simple content-source heuristics."""
    return len(re.findall(r"[A-Za-z0-9]{2,}", text))


def _choose_primary_source(caption: str, ocr_text: str) -> str:
    """Prefer the richer, less promotional source when both caption and OCR matter."""
    caption_score = _content_source_score(caption)
    ocr_score = _content_source_score(ocr_text)
    return "caption" if caption_score >= ocr_score else "ocr"


def _content_source_score(text: str) -> int:
    """Score a text source by substance minus obvious promotional language."""
    word_count = _count_meaningful_words(text)
    promo_penalty = 8 * _promotional_marker_count(text)
    return word_count - promo_penalty


def _promotional_marker_count(text: str) -> int:
    """Detect generic CTA/promotional language often found in Instagram captions."""
    lowered = text.lower()
    markers = [
        "follow",
        "like",
        "share",
        "save this",
        "save this post",
        "comment",
        "dm ",
        "link in bio",
        "for more",
        "course",
        "subscribe",
    ]
    return sum(marker in lowered for marker in markers)
