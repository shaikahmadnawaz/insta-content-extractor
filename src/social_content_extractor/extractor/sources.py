"""Platform and media helpers for the extractor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

import instaloader
from PIL import Image

from .constants import DEFAULT_FETCH_ATTEMPTS
from .text import _output_collection_for_kind


def _create_loader() -> instaloader.Instaloader:
    """Create an Instaloader instance with download disabled."""
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=3,
    )


def _fetch_post(
    loader: instaloader.Instaloader,
    shortcode: str,
    max_attempts: int = DEFAULT_FETCH_ATTEMPTS,
) -> instaloader.Post:
    """Fetch a post with small backoff to smooth over transient Instagram failures."""
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return instaloader.Post.from_shortcode(loader.context, shortcode)
        except Exception as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            time.sleep(attempt)

    assert last_error is not None
    raise last_error


def _create_youtube_downloader(options: dict):
    """Create a yt-dlp client lazily so Instagram-only usage stays lightweight."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is required for YouTube Shorts support. "
            "Install it first with `pip install yt-dlp`."
        ) from exc
    return yt_dlp.YoutubeDL(options)


def _fetch_youtube_video_info(url: str) -> dict:
    """Fetch YouTube video metadata without downloading media."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
    }
    downloader = _create_youtube_downloader(options)
    return downloader.extract_info(url, download=False)


def _build_youtube_caption(info: dict) -> str:
    """Combine title and description into one caption-like text block."""
    title = (info.get("title") or "").strip()
    description = (info.get("description") or "").strip()
    if title and description:
        return f"{title}\n\n{description}"
    return title or description


def _get_youtube_owner_username(info: dict) -> str:
    """Choose the most recognizable YouTube creator name available."""
    candidates = [
        info.get("uploader_id"),
        info.get("channel_handle"),
        info.get("channel"),
        info.get("uploader"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).lstrip("@")
    return "unknown"


def _get_youtube_upload_iso_datetime(info: dict) -> str | None:
    """Normalize YouTube upload dates into ISO 8601."""
    timestamp = info.get("timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            pass

    upload_date = str(info.get("upload_date") or "").strip()
    if len(upload_date) == 8 and upload_date.isdigit():
        try:
            parsed = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            return None

    return None


def _collect_youtube_media(info: dict) -> list[dict]:
    """Build the shared media record structure for one YouTube Short."""
    entry = {
        "index": 1,
        "type": "video",
        "url": info.get("webpage_url") or info.get("original_url") or "",
        "thumbnail_url": info.get("thumbnail"),
    }
    return [entry]


def _get_post_type(post) -> str:
    """Return a human-readable post type string."""
    if post.typename == "GraphSidecar":
        return "carousel"
    return "video" if post.is_video else "image"


def _build_post_output_dir(
    base_output_dir: str,
    kind: str,
    shortcode: str,
    source: str = "instagram",
) -> str:
    """Return the dedicated output directory for one supported post."""
    if source == "youtube":
        return os.path.join(base_output_dir, "youtube", "shorts", shortcode)
    return os.path.join(base_output_dir, source, _output_collection_for_kind(kind), shortcode)


def _build_media_output_dir(post_output_dir: str) -> str:
    """Return the media subdirectory for one post."""
    return os.path.join(post_output_dir, "media")


def _build_content_output_dir(post_output_dir: str) -> str:
    """Return the content subdirectory for one post."""
    return os.path.join(post_output_dir, "content")


def _collect_media(post) -> list[dict]:
    """Collect all media items from a post."""
    items = []

    if post.typename == "GraphSidecar":
        for idx, node in enumerate(post.get_sidecar_nodes(), start=1):
            entry = {
                "index": idx,
                "type": "video" if node.is_video else "image",
                "url": node.video_url if node.is_video else node.display_url,
            }
            if node.is_video:
                entry["thumbnail_url"] = node.display_url
            items.append(entry)
        return items

    entry = {
        "index": 1,
        "type": "video" if post.is_video else "image",
        "url": post.video_url if post.is_video else post.url,
    }
    if post.is_video:
        entry["thumbnail_url"] = post.url
    items.append(entry)
    return items


def _build_slides(media_items: list[dict], download_map: dict[int, str]) -> list[dict]:
    """Build per-slide records to make downstream consumption easier."""
    slides = []
    for item in media_items:
        slide = dict(item)
        slide["file_path"] = download_map.get(item["index"])
        slides.append(slide)
    return slides


def _download_media(
    loader: instaloader.Instaloader,
    media_items: list[dict],
    shortcode: str,
    output_dir: str,
) -> dict[int, str]:
    """Download all media files and return a map of slide index -> file path."""
    os.makedirs(output_dir, exist_ok=True)
    downloaded: dict[int, str] = {}

    for item in media_items:
        ext = "mp4" if item["type"] == "video" else "jpg"
        filename = f"{shortcode}_{item['index']}.{ext}"
        filepath = os.path.join(output_dir, filename)

        if _is_valid_cached_media(item, filepath):
            downloaded[item["index"]] = filepath
            continue

        _remove_invalid_cached_file(filepath)

        try:
            loader.context.get_and_write_raw(item["url"], filepath)
            if _is_valid_cached_media(item, filepath):
                downloaded[item["index"]] = filepath
            else:
                _remove_invalid_cached_file(filepath)
                print(f"  Warning: Downloaded media #{item['index']} was invalid and was discarded")
        except Exception as exc:
            print(f"  Warning: Failed to download media #{item['index']}: {exc}")

    return downloaded


def _download_youtube_media(url: str, video_id: str, output_dir: str) -> dict[int, str]:
    """Download one YouTube Short video and return the shared index->path map."""
    os.makedirs(output_dir, exist_ok=True)

    cached_candidates = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith(f"{video_id}_1.")
    )
    for filepath in cached_candidates:
        if _is_valid_video_file(filepath):
            return {1: filepath}

    template = os.path.join(output_dir, f"{video_id}_1.%(ext)s")

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bv*+ba/b",
        "outtmpl": template,
        "merge_output_format": "mp4",
    }
    downloader = _create_youtube_downloader(options)
    downloader.download([url])

    candidates = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith(f"{video_id}_1.")
    )
    for filepath in candidates:
        if _is_valid_video_file(filepath):
            return {1: filepath}

    raise RuntimeError("YouTube Short download completed but no valid video file was found")


def _is_valid_cached_media(item: dict, filepath: str) -> bool:
    """Check whether an existing cached media file is safe to reuse."""
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) == 0:
        return False

    if item["type"] == "image":
        try:
            with Image.open(filepath) as image:
                image.verify()
            return True
        except Exception:
            return False

    return _is_valid_video_file(filepath)


def _is_valid_video_file(filepath: str) -> bool:
    """Validate a cached video using MP4 signature checks and ffprobe when available."""
    if os.path.getsize(filepath) < 1024:
        return False

    try:
        with open(filepath, "rb") as file_obj:
            header = file_obj.read(64)
    except OSError:
        return False

    if b"ftyp" not in header:
        return False

    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return True

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=codec_type",
        filepath,
    ]
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
        probe_data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return False

    duration = probe_data.get("format", {}).get("duration")
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        return False

    streams = probe_data.get("streams", [])
    has_video_stream = any(stream.get("codec_type") == "video" for stream in streams)
    return duration_seconds > 0 and has_video_stream


def _remove_invalid_cached_file(filepath: str) -> None:
    """Delete a known-bad cache file if it exists."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError:
        pass
