"""
Instagram Content Extractor - Core Module

Extracts content from Instagram posts (single & carousel):
- Caption, hashtags, mentions
- Media URLs with optional local download
- Post metadata (date, likes, comments, owner)
- OCR text extraction from image slides
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from difflib import SequenceMatcher
from io import BytesIO
from urllib.parse import urlparse

import instaloader
import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pytesseract import Output

DEFAULT_OCR_LANG = "eng"
DEFAULT_OCR_PSM = 6
DEFAULT_OCR_MIN_CONFIDENCE = 30.0
DEFAULT_FETCH_ATTEMPTS = 3
DEFAULT_VIDEO_FRAME_INTERVAL_SECONDS = 1.0
SHORT_VIDEO_FRAME_INTERVAL_SECONDS = 0.5
SHORT_VIDEO_DURATION_THRESHOLD_SECONDS = 15.0
SCENE_SIMILARITY_THRESHOLD = 0.9
MIN_SCENE_CONFIDENCE = 60.0
MIN_SCENE_MEANINGFUL_TOKENS = 4
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}


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


def extract_shortcode(url: str) -> str:
    """Extract the shortcode from an Instagram post URL."""
    _, shortcode = _extract_instagram_url_parts(url)
    return shortcode


def _build_canonical_instagram_url(kind: str, shortcode: str) -> str:
    """Return a canonical Instagram URL for the detected media type."""
    return f"https://www.instagram.com/{kind}/{shortcode}/"


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


def extract_post(
    url: str,
    download_media: bool = True,
    output_dir: str = "downloads",
    ocr: bool = False,
    save_json: bool = False,
    ocr_provider: str = "tesseract",
    ocr_lang: str = DEFAULT_OCR_LANG,
    ocr_psm: int = DEFAULT_OCR_PSM,
    ocr_min_confidence: float = DEFAULT_OCR_MIN_CONFIDENCE,
    sarvam_model: str = "auto",
    sarvam_language: str = "en-IN",
) -> dict:
    """Extract all content from a public Instagram post."""
    url_kind, shortcode = _extract_instagram_url_parts(url)
    post_output_dir = _build_post_output_dir(output_dir, shortcode)
    media_output_dir = _build_media_output_dir(post_output_dir)
    content_output_dir = _build_content_output_dir(post_output_dir)
    loader = _create_loader()
    post = _fetch_post(loader, shortcode)

    media_items = _collect_media(post)
    download_map: dict[int, str] = {}
    if download_media:
        download_map = _download_media(loader, media_items, shortcode, media_output_dir)

    slides = _build_slides(media_items, download_map)
    ocr_results: list[dict] = []
    resolved_sarvam_model: str | None = None
    if ocr:
        if ocr_provider == "sarvam":
            _ensure_tesseract_available()
            if any(slide["type"] == "video" for slide in slides):
                _ensure_ffmpeg_available()
            ocr_results, resolved_sarvam_model = _ocr_images_with_sarvam(
                slides=slides,
                requested_chat_model=sarvam_model,
                ocr_lang=ocr_lang,
                ocr_psm=ocr_psm,
                ocr_min_confidence=ocr_min_confidence,
            )
        elif ocr_provider == "sarvam_vision":
            if any(slide["type"] == "video" for slide in slides):
                _ensure_ffmpeg_available()
            ocr_results, resolved_sarvam_model = _ocr_images_with_sarvam_vision(
                slides=slides,
                output_dir=content_output_dir,
                requested_chat_model=sarvam_model,
                sarvam_language=sarvam_language,
            )
        else:
            _ensure_tesseract_available()
            if any(slide["type"] == "video" for slide in slides):
                _ensure_ffmpeg_available()
            ocr_results = _ocr_images(
                slides=slides,
                ocr_lang=ocr_lang,
                ocr_psm=ocr_psm,
                ocr_min_confidence=ocr_min_confidence,
            )
        _attach_ocr_results(slides, ocr_results)

    post_data = {
        "shortcode": shortcode,
        "url": _build_canonical_instagram_url(url_kind, shortcode),
        "post_type": _get_post_type(post),
        "owner": {
            "username": post.owner_username,
            "user_id": post.owner_id,
        },
        "caption": post.caption or "",
        "accessibility_caption": getattr(post, "accessibility_caption", None),
        "hashtags": list(post.caption_hashtags) if post.caption_hashtags else [],
        "mentions": list(post.caption_mentions) if post.caption_mentions else [],
        "date": post.date_utc.isoformat(),
        "date_local": post.date_local.isoformat() if post.date_local else None,
        "likes": post.likes,
        "comments_count": post.comments,
        "media_count": len(media_items),
        "media": media_items,
        "slides": slides,
        "downloaded_files": [download_map[idx] for idx in sorted(download_map)],
        "ocr_text": ocr_results,
        "ocr_combined_text": _combine_ocr_text(ocr_results),
        "ocr_provider": ocr_provider if ocr else None,
    }
    if resolved_sarvam_model:
        post_data["ocr_cleanup_model"] = resolved_sarvam_model

    os.makedirs(post_output_dir, exist_ok=True)
    artifact_stem = _build_output_artifact_stem(shortcode, post_data["ocr_provider"])

    if ocr:
        os.makedirs(content_output_dir, exist_ok=True)
        txt_path = os.path.join(content_output_dir, f"{artifact_stem}.ocr.txt")
        with open(txt_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(post_data["ocr_combined_text"])
        post_data["ocr_text_file"] = txt_path

    if save_json:
        os.makedirs(content_output_dir, exist_ok=True)
        json_path = os.path.join(content_output_dir, f"{artifact_stem}.json")
        with open(json_path, "w", encoding="utf-8") as file_obj:
            json.dump(post_data, file_obj, indent=2, ensure_ascii=False)
        post_data["json_file"] = json_path

    return post_data


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


def _get_post_type(post) -> str:
    """Return a human-readable post type string."""
    if post.typename == "GraphSidecar":
        return "carousel"
    return "video" if post.is_video else "image"


def _build_post_output_dir(base_output_dir: str, shortcode: str) -> str:
    """Return the dedicated output directory for one Instagram post."""
    return os.path.join(base_output_dir, shortcode)


def _build_media_output_dir(post_output_dir: str) -> str:
    """Return the media subdirectory for one Instagram post."""
    return os.path.join(post_output_dir, "media")


def _build_content_output_dir(post_output_dir: str) -> str:
    """Return the content subdirectory for one Instagram post."""
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


def _ensure_tesseract_available() -> None:
    """Raise a clear error if the local Tesseract binary is missing."""
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract is not installed or not available in PATH. "
            "Install it first, for example with `brew install tesseract`."
        ) from exc


def _ensure_ffmpeg_available() -> None:
    """Raise a clear error if ffmpeg is not available for reel OCR."""
    if shutil.which("ffmpeg"):
        return
    raise RuntimeError(
        "ffmpeg is required for reel OCR but was not found in PATH. "
        "Install it first, for example with `brew install ffmpeg`."
    )


def _create_sarvam_client():
    """Create a Sarvam client from the local environment."""
    api_key = _get_env_value("SARVAM_API_KEY")
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is required for Sarvam OCR")

    try:
        from sarvamai import SarvamAI
    except ImportError as exc:
        raise RuntimeError(
            "Sarvam SDK is not installed. Install it first with `pip install sarvamai`."
        ) from exc

    return SarvamAI(api_subscription_key=api_key)


def _resolve_sarvam_chat_model(requested_model: str, slides: list[dict]) -> str:
    """Choose the Sarvam cleanup model based on the OCR use case."""
    if requested_model in {"sarvam-30b", "sarvam-105b"}:
        return requested_model
    return "sarvam-30b"


def _ocr_images_with_sarvam(
    slides: list[dict],
    requested_chat_model: str,
    ocr_lang: str,
    ocr_psm: int,
    ocr_min_confidence: float,
) -> tuple[list[dict], str]:
    """Run local OCR first, then clean the result with a Sarvam chat model."""
    client = _create_sarvam_client()
    cleanup_model = _resolve_sarvam_chat_model(requested_chat_model, slides)
    results: list[dict] = []

    for slide in slides:
        if slide["type"] == "video":
            results.extend(
                _ocr_video_slide_with_sarvam(
                    slide=slide,
                    client=client,
                    cleanup_model=cleanup_model,
                    ocr_lang=ocr_lang,
                    ocr_psm=ocr_psm,
                    ocr_min_confidence=ocr_min_confidence,
                )
            )
            continue

        try:
            image, media_source = _load_ocr_image(slide)
            local_result = _run_best_ocr(
                image=image,
                lang=ocr_lang,
                psm=ocr_psm,
                min_confidence=ocr_min_confidence,
            )
            cleaned_text = _clean_single_ocr_text_with_sarvam(
                client=client,
                cleanup_model=cleanup_model,
                slide=slide["index"],
                text=local_result["text"],
            )
            lines = _split_ocr_lines(cleaned_text)
            results.append(
                {
                    "slide": slide["index"],
                    "text": cleaned_text,
                    "lines": lines,
                    "confidence": local_result["confidence"],
                    "word_count": sum(len(line.split()) for line in lines),
                    "line_count": len(lines),
                    "variant": f"{local_result['variant']}_sarvam_cleanup",
                    "media_type": slide["type"],
                    "timestamp": None,
                    "ocr_source": f"{media_source}+{cleanup_model}",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "slide": slide["index"],
                    "text": f"[OCR failed: {exc}]",
                    "lines": [],
                    "confidence": 0.0,
                    "word_count": 0,
                    "line_count": 0,
                    "variant": "failed",
                    "media_type": slide["type"],
                    "timestamp": None,
                    "ocr_source": "failed",
                }
            )

    return results, cleanup_model


def _ocr_images_with_sarvam_vision(
    slides: list[dict],
    output_dir: str,
    requested_chat_model: str,
    sarvam_language: str,
) -> tuple[list[dict], str]:
    """Run Sarvam Vision OCR first, then clean the result with a Sarvam chat model."""
    client = _create_sarvam_client()
    cleanup_model = _resolve_sarvam_chat_model(requested_chat_model, slides)
    results: list[dict] = []

    os.makedirs(output_dir, exist_ok=True)

    for slide in slides:
        if slide["type"] == "video":
            results.extend(
                _ocr_video_slide_with_sarvam_vision(
                    slide=slide,
                    output_dir=output_dir,
                    client=client,
                    sarvam_language=sarvam_language,
                    cleanup_model=cleanup_model,
                )
            )
            continue

        try:
            image_path, cleanup_path = _ensure_local_image_path(slide)
            try:
                raw_text = _run_sarvam_vision_on_file(
                    client=client,
                    file_path=image_path,
                    language=sarvam_language,
                    output_dir=output_dir,
                )
            finally:
                if cleanup_path:
                    _remove_invalid_cached_file(cleanup_path)

            cleaned_text = _clean_single_ocr_text_with_sarvam(
                client=client,
                cleanup_model=cleanup_model,
                slide=slide["index"],
                text=raw_text,
            )
            lines = _split_ocr_lines(cleaned_text)
            results.append(
                {
                    "slide": slide["index"],
                    "text": cleaned_text,
                    "lines": lines,
                    "confidence": 0.0,
                    "word_count": sum(len(line.split()) for line in lines),
                    "line_count": len(lines),
                    "variant": "sarvam_vision_cleanup",
                    "media_type": slide["type"],
                    "timestamp": None,
                    "ocr_source": f"sarvam_vision+{cleanup_model}",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "slide": slide["index"],
                    "text": f"[OCR failed: {exc}]",
                    "lines": [],
                    "confidence": 0.0,
                    "word_count": 0,
                    "line_count": 0,
                    "variant": "failed",
                    "media_type": slide["type"],
                    "timestamp": None,
                    "ocr_source": "failed",
                }
            )

    return results, cleanup_model


def _ensure_local_image_path(slide: dict) -> tuple[str, str | None]:
    """Return a local image path for OCR, downloading a temp copy if necessary."""
    file_path = slide.get("file_path")
    if file_path and os.path.exists(file_path):
        return file_path, None

    image_url = _get_ocr_image_url(slide)
    response = requests.get(image_url, headers=REQUEST_HEADERS, timeout=20)
    response.raise_for_status()

    suffix = ".jpg"
    temp_file = tempfile.NamedTemporaryFile(prefix="sarvam_image_", suffix=suffix, delete=False)
    try:
        temp_file.write(response.content)
    finally:
        temp_file.close()
    return temp_file.name, temp_file.name


def _run_sarvam_vision_on_file(client, file_path: str, language: str, output_dir: str) -> str:
    """Extract OCR text from one file with Sarvam Vision Document Intelligence."""
    job = client.document_intelligence.create_job(
        language=language,
        output_format="md",
    )
    job.upload_file(file_path)
    job.start()
    status = job.wait_until_complete()

    job_state = getattr(status, "job_state", None)
    if job_state and str(job_state).lower() not in {"completed", "succeeded", "success"}:
        raise RuntimeError(f"Sarvam Vision job failed with state: {job_state}")

    with tempfile.NamedTemporaryFile(
        prefix="sarvam_output_",
        suffix=".zip",
        dir=output_dir,
        delete=False,
    ) as temp_zip:
        zip_path = temp_zip.name

    try:
        job.download_output(zip_path)
        return _read_first_text_file_from_zip(zip_path)
    finally:
        _remove_invalid_cached_file(zip_path)


def _run_sarvam_vision_on_pil_image(client, image: Image.Image, language: str, output_dir: str) -> str:
    """Persist a PIL image temporarily so Sarvam Vision can OCR it."""
    with tempfile.NamedTemporaryFile(
        prefix="sarvam_frame_",
        suffix=".jpg",
        dir=output_dir,
        delete=False,
    ) as temp_image:
        image_path = temp_image.name

    try:
        image.convert("RGB").save(image_path, "JPEG", quality=95)
        return _run_sarvam_vision_on_file(
            client=client,
            file_path=image_path,
            language=language,
            output_dir=output_dir,
        )
    finally:
        _remove_invalid_cached_file(image_path)


def _read_first_text_file_from_zip(zip_path: str) -> str:
    """Read the first markdown/html payload from a Sarvam output archive."""
    with zipfile.ZipFile(zip_path) as zip_file:
        text_files = [
            name
            for name in zip_file.namelist()
            if not name.endswith("/") and name.lower().endswith((".md", ".html", ".txt"))
        ]
        for name in sorted(text_files):
            text = zip_file.read(name).decode("utf-8", errors="replace").strip()
            if text:
                return text
    raise RuntimeError("Sarvam Vision output archive did not contain OCR text")


def _clean_single_ocr_text_with_sarvam(client, cleanup_model: str, slide: int, text: str) -> str:
    """Clean one OCR block without inventing new text."""
    stripped = _normalize_scene_text_for_output(text)
    if not stripped:
        return ""

    response = client.chat.completions(
        model=cleanup_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You clean OCR text from educational slides. Preserve meaning and order. "
                    "Do not invent missing text. Remove only obvious OCR garbage and normalize formatting. "
                    "Return only the cleaned slide text as plain text. "
                    "Do not explain your work. Do not return JSON. Do not include markdown fences."
                ),
            },
            {
                "role": "user",
                "content": stripped,
            },
        ],
        temperature=0.0,
        top_p=1,
        max_tokens=800,
    )
    cleaned = _get_sarvam_message_content(response).strip()
    cleaned = _strip_markdown_fences(cleaned)
    if not cleaned or _looks_like_model_reasoning(cleaned):
        return stripped
    return _normalize_scene_text_for_output(cleaned)


def _ocr_video_slide_with_sarvam(
    slide: dict,
    client,
    cleanup_model: str,
    ocr_lang: str,
    ocr_psm: int,
    ocr_min_confidence: float,
) -> list[dict]:
    """Extract reel scenes locally, then clean them with a Sarvam chat model."""
    video_path = slide.get("file_path")
    if not video_path or not os.path.exists(video_path):
        fallback_result = _run_thumbnail_ocr(
            slide=slide,
            ocr_lang=ocr_lang,
            ocr_psm=ocr_psm,
            ocr_min_confidence=ocr_min_confidence,
        )
        fallback_text = _clean_single_ocr_text_with_sarvam(
            client=client,
            cleanup_model=cleanup_model,
            slide=slide["index"],
            text=fallback_result["text"],
        )
        lines = _split_ocr_lines(fallback_text)
        return [
            {
                "slide": slide["index"],
                "text": fallback_text,
                "lines": lines,
                "confidence": fallback_result["confidence"],
                "word_count": sum(len(line.split()) for line in lines),
                "line_count": len(lines),
                "variant": f"{fallback_result['variant']}_sarvam_cleanup",
                "media_type": slide["type"],
                "timestamp": "00:00",
                "ocr_source": f"{fallback_result['ocr_source']}+{cleanup_model}",
            }
        ]

    local_scenes = _ocr_video_slide(
        slide=slide,
        ocr_lang=ocr_lang,
        ocr_psm=ocr_psm,
        ocr_min_confidence=ocr_min_confidence,
    )
    scene_candidates = [
        {
            "slide": scene["slide"],
            "media_type": "video",
            "timestamp": scene.get("timestamp") or "00:00",
            "timestamp_seconds": _timestamp_to_seconds(scene.get("timestamp") or "00:00"),
            "text": scene.get("text", ""),
        }
        for scene in local_scenes
        if scene.get("text") and not str(scene.get("text", "")).startswith("[OCR failed")
    ]

    if not scene_candidates:
        return local_scenes

    try:
        cleaned_scenes = _clean_video_scene_records_with_sarvam(
            client=client,
            cleanup_model=cleanup_model,
            scene_candidates=scene_candidates,
        )
    except Exception as exc:
        print(
            f"  Warning: Sarvam chat cleanup failed for reel slide #{slide['index']}: {exc}. "
            "Falling back to per-scene cleanup."
        )
        cleaned_scenes = _clean_video_scene_records_individually_with_sarvam(
            client=client,
            cleanup_model=cleanup_model,
            scene_candidates=scene_candidates,
        )

    collapsed = _collapse_scene_candidates_by_second(cleaned_scenes)
    return _deduplicate_scene_records(collapsed)


def _ocr_video_slide_with_sarvam_vision(
    slide: dict,
    output_dir: str,
    client,
    sarvam_language: str,
    cleanup_model: str,
) -> list[dict]:
    """Extract reel scenes with Sarvam Vision on sampled frames, then clean them."""
    video_path = slide.get("file_path")
    if not video_path or not os.path.exists(video_path):
        return _ocr_video_thumbnail_with_sarvam_vision(
            slide=slide,
            output_dir=output_dir,
            client=client,
            sarvam_language=sarvam_language,
            cleanup_model=cleanup_model,
        )

    try:
        frame_records = _extract_video_frames_for_ocr(video_path)
    except Exception as exc:
        print(f"  Warning: Sarvam Vision frame extraction failed on slide #{slide['index']}: {exc}")
        return _ocr_video_thumbnail_with_sarvam_vision(
            slide=slide,
            output_dir=output_dir,
            client=client,
            sarvam_language=sarvam_language,
            cleanup_model=cleanup_model,
        )

    scene_candidates = []
    for frame_record in frame_records:
        try:
            raw_text = _run_sarvam_vision_on_pil_image(
                client=client,
                image=frame_record["image"],
                language=sarvam_language,
                output_dir=output_dir,
            )
        except Exception:
            continue

        normalized = _normalize_scene_text_for_output(raw_text)
        if not normalized:
            continue

        letter_tokens = re.findall(r"[A-Za-z]{3,}", normalized)
        if len(letter_tokens) < 2 and len(normalized) < 24:
            continue

        scene_candidates.append(
            {
                "slide": slide["index"],
                "media_type": "video",
                "timestamp": _format_seconds_timestamp(frame_record["timestamp_seconds"]),
                "timestamp_seconds": frame_record["timestamp_seconds"],
                "text": normalized,
            }
        )

    if not scene_candidates:
        return _ocr_video_thumbnail_with_sarvam_vision(
            slide=slide,
            output_dir=output_dir,
            client=client,
            sarvam_language=sarvam_language,
            cleanup_model=cleanup_model,
        )

    try:
        cleaned_scenes = _clean_video_scene_records_with_sarvam(
            client=client,
            cleanup_model=cleanup_model,
            scene_candidates=scene_candidates,
        )
    except Exception as exc:
        print(
            f"  Warning: Sarvam chat cleanup failed for reel slide #{slide['index']}: {exc}. "
            "Falling back to per-scene cleanup."
        )
        cleaned_scenes = _clean_video_scene_records_individually_with_sarvam(
            client=client,
            cleanup_model=cleanup_model,
            scene_candidates=scene_candidates,
        )

    collapsed = _collapse_scene_candidates_by_second(cleaned_scenes)
    return _deduplicate_scene_records(collapsed)


def _ocr_video_thumbnail_with_sarvam_vision(
    slide: dict,
    output_dir: str,
    client,
    sarvam_language: str,
    cleanup_model: str,
) -> list[dict]:
    """Fallback to thumbnail OCR when frame-based Sarvam Vision OCR is unavailable."""
    image_path, cleanup_path = _ensure_local_image_path(slide)
    try:
        raw_text = _run_sarvam_vision_on_file(
            client=client,
            file_path=image_path,
            language=sarvam_language,
            output_dir=output_dir,
        )
    finally:
        if cleanup_path:
            _remove_invalid_cached_file(cleanup_path)

    fallback_text = _clean_single_ocr_text_with_sarvam(
        client=client,
        cleanup_model=cleanup_model,
        slide=slide["index"],
        text=raw_text,
    )
    lines = _split_ocr_lines(fallback_text)
    return [
        {
            "slide": slide["index"],
            "text": fallback_text,
            "lines": lines,
            "confidence": 0.0,
            "word_count": sum(len(line.split()) for line in lines),
            "line_count": len(lines),
            "variant": "sarvam_vision_thumbnail_cleanup",
            "media_type": slide["type"],
            "timestamp": "00:00",
            "ocr_source": f"sarvam_vision_thumbnail+{cleanup_model}",
        }
    ]


def _clean_video_scene_records_with_sarvam(client, cleanup_model: str, scene_candidates: list[dict]) -> list[dict]:
    """Use per-scene Sarvam cleanup for reel OCR records."""
    return _clean_video_scene_records_individually_with_sarvam(
        client=client,
        cleanup_model=cleanup_model,
        scene_candidates=scene_candidates,
    )


def _build_raw_sarvam_scene_records(scene_candidates: list[dict], cleanup_model: str) -> list[dict]:
    """Convert raw Sarvam Vision scene text into the standard OCR record shape."""
    cleaned_scenes = []
    for scene in scene_candidates:
        text = _normalize_scene_text_for_output(str(scene.get("text", "")))
        if not text:
            continue
        lines = _split_ocr_lines(text)
        if not lines:
            continue
        cleaned_scenes.append(
            {
                "slide": scene["slide"],
                "media_type": "video",
                "timestamp": scene["timestamp"],
                "timestamp_seconds": scene["timestamp_seconds"],
                "text": "\n".join(lines),
                "lines": lines,
                "confidence": 0.0,
                "word_count": sum(len(line.split()) for line in lines),
                "line_count": len(lines),
                "variant": "sarvam_vision_raw",
                "ocr_source": f"sarvam_vision+{cleanup_model}",
            }
        )
    return cleaned_scenes


def _clean_video_scene_records_individually_with_sarvam(
    client,
    cleanup_model: str,
    scene_candidates: list[dict],
) -> list[dict]:
    """Fallback cleanup path when the model does not return valid JSON."""
    cleaned_scenes = []
    for scene in scene_candidates:
        try:
            cleaned_text = _clean_single_ocr_text_with_sarvam(
                client=client,
                cleanup_model=cleanup_model,
                slide=scene["slide"],
                text=scene["text"],
            )
        except Exception:
            cleaned_text = scene["text"]

        cleaned_text = _normalize_scene_text_for_output(cleaned_text)
        if not cleaned_text:
            continue

        if _looks_like_marketing_endcard(cleaned_text):
            continue

        lines = _split_ocr_lines(cleaned_text)
        if not lines:
            continue

        cleaned_scenes.append(
            {
                "slide": scene["slide"],
                "media_type": "video",
                "timestamp": scene["timestamp"],
                "timestamp_seconds": scene["timestamp_seconds"],
                "text": "\n".join(lines),
                "lines": lines,
                "confidence": 0.0,
                "word_count": sum(len(line.split()) for line in lines),
                "line_count": len(lines),
                "variant": "sarvam_scene_cleanup",
                "ocr_source": f"sarvam_scene_cleanup+{cleanup_model}",
            }
        )
    return cleaned_scenes


def _timestamp_to_seconds(timestamp: str) -> float:
    """Convert an MM:SS timestamp string into seconds."""
    try:
        minutes_str, seconds_str = timestamp.split(":", 1)
        return (int(minutes_str) * 60) + int(seconds_str)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _split_ocr_lines(text: str) -> list[str]:
    """Split OCR text into normalized non-empty lines."""
    return [_normalize_ocr_line(line) for line in text.splitlines() if _normalize_ocr_line(line)]


def _strip_markdown_fences(text: str) -> str:
    """Remove a surrounding markdown code fence if the model wraps its JSON/text."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
    stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def _get_sarvam_message_content(response) -> str:
    """Extract the assistant's final content text from a Sarvam chat response."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content

    return ""


def _looks_like_model_reasoning(text: str) -> bool:
    """Detect prompt reflection or chain-of-thought style content from a cleanup model."""
    lowered = text.lower()
    suspicious_markers = [
        "analyze the user's request",
        "core task:",
        "constraints:",
        "return only the cleaned text",
        "input text:",
        "line-by-line breakdown",
        "synthesize",
        "final polish",
        "```",
    ]
    return any(marker in lowered for marker in suspicious_markers)


def _normalize_scene_text_for_output(text: str) -> str:
    """Clean up common OCR artifacts while preserving slide structure."""
    normalized_lines = []
    for raw_line in text.splitlines():
        line = _normalize_ocr_line(_strip_accents(raw_line))
        if not line:
            continue
        if _looks_like_embedded_image_markdown(line):
            continue
        if _looks_like_image_description_line(line):
            continue

        line = re.sub(r"^[=©¢°>*@/|;,\-\[\]{}_]+", "", line).strip()
        line = re.sub(r"^(?:e¢|e|o|¢|©|°o|°)\s+(?=[A-Za-z(])", "", line)
        line = re.sub(r"\s+[°@»|/¢]+$", "", line).strip()
        line = re.sub(r"^[A-Za-z]?[—_]{2,}$", "", line).strip()
        line = re.sub(r"^[—@&]+\s*", "", line).strip()
        line = re.sub(r"(?<=\S)/\s+(?=\S)", "/", line)

        if line in {"", "/ | |", "@ »", "ao No", "e °", "o____", "____"}:
            continue
        if re.fullmatch(r"[\W_]{1,8}", line):
            continue

        normalized_lines.append(line)

    return "\n".join(normalized_lines)


def _looks_like_embedded_image_markdown(line: str) -> bool:
    """Detect markdown image embeds or inline data URLs leaked by OCR providers."""
    lowered = line.lower()
    return "data:image/" in lowered or lowered.startswith("![") and "](" in lowered


def _looks_like_image_description_line(line: str) -> bool:
    """Filter generic image-caption prose that is not OCR text from the slide itself."""
    lowered = line.lower()
    prefixes = [
        "the image is ",
        "this image ",
        "the figure shows ",
        "this figure ",
        "the diagram shows ",
        "this diagram ",
        "the screenshot shows ",
        "this screenshot ",
        "the illustration shows ",
        "this illustration ",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True

    description_markers = [
        "background is ",
        "axes, grid lines",
        "legend, or numerical data",
        "uniform dark gray",
    ]
    return any(marker in lowered for marker in description_markers)


def _strip_accents(text: str) -> str:
    """Normalize accented OCR characters into their ASCII equivalents."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _looks_like_marketing_endcard(text: str) -> bool:
    """Drop noisy promotional end-card OCR that is usually not the main content."""
    lowered = text.lower()
    endcard_markers = [
        "complete tutorial",
        "full course",
        "docker compose",
        "github actions",
        "eslint",
        "prettier",
    ]
    marker_count = sum(marker in lowered for marker in endcard_markers)
    return marker_count >= 3 and len(lowered.splitlines()) > 8


def _ocr_images(
    slides: list[dict],
    ocr_lang: str,
    ocr_psm: int,
    ocr_min_confidence: float,
) -> list[dict]:
    """Run OCR on each image slide and return structured OCR results."""
    results = []

    for slide in slides:
        index = slide["index"]
        try:
            if slide["type"] == "video":
                results.extend(
                    _ocr_video_slide(
                        slide=slide,
                        ocr_lang=ocr_lang,
                        ocr_psm=ocr_psm,
                        ocr_min_confidence=ocr_min_confidence,
                    )
                )
            else:
                image, media_source = _load_ocr_image(slide)
                result = _run_best_ocr(
                    image=image,
                    lang=ocr_lang,
                    psm=ocr_psm,
                    min_confidence=ocr_min_confidence,
                )
                results.append(
                    {
                        "slide": index,
                        "text": result["text"],
                        "lines": result["lines"],
                        "confidence": result["confidence"],
                        "word_count": result["word_count"],
                        "line_count": result["line_count"],
                        "variant": result["variant"],
                        "media_type": slide["type"],
                        "timestamp": None,
                        "ocr_source": media_source,
                    }
                )
        except Exception as exc:
            results.append(
                {
                    "slide": index,
                    "text": f"[OCR failed: {exc}]",
                    "lines": [],
                    "confidence": 0.0,
                    "word_count": 0,
                    "line_count": 0,
                    "variant": "failed",
                    "media_type": slide["type"],
                    "timestamp": None,
                    "ocr_source": "failed",
                }
            )

    return results


def _attach_ocr_results(slides: list[dict], ocr_results: list[dict]) -> None:
    """Attach OCR output directly onto slide objects."""
    by_slide: dict[int, list[dict]] = {}
    for item in ocr_results:
        by_slide.setdefault(item["slide"], []).append(item)

    for slide in slides:
        if slide["type"] not in {"image", "video"}:
            continue

        entries = by_slide.get(slide["index"], [])
        if slide["type"] == "video":
            slide["ocr_scenes"] = entries
            slide["ocr"] = entries[0] if entries else None
            continue

        slide["ocr"] = entries[0] if entries else None


def _combine_ocr_text(ocr_results: list[dict]) -> str:
    """Build one plain-text OCR artifact for easy downstream usage."""
    sections = []
    for item in ocr_results:
        text = item["text"].strip()
        if not text or text.startswith("[OCR failed"):
            continue
        if item.get("media_type") == "video" and item.get("timestamp"):
            sections.append(f"{item['timestamp']}\n{text}")
            continue
        sections.append(f"Slide {item['slide']}\n{text}")
    return "\n\n".join(sections)


def _ocr_video_slide(
    slide: dict,
    ocr_lang: str,
    ocr_psm: int,
    ocr_min_confidence: float,
) -> list[dict]:
    """Extract OCR scenes from video frames with thumbnail fallback."""
    video_path = slide.get("file_path")
    if video_path and os.path.exists(video_path):
        try:
            frame_records = _extract_video_frames_for_ocr(video_path)
            scene_records = _ocr_video_frames(
                slide_index=slide["index"],
                frame_records=frame_records,
                lang=ocr_lang,
                psm=ocr_psm,
                min_confidence=ocr_min_confidence,
            )
            if scene_records:
                return scene_records
        except Exception as exc:
            print(f"  Warning: Video frame OCR failed on slide #{slide['index']}: {exc}")

    return [_run_thumbnail_ocr(slide, ocr_lang, ocr_psm, ocr_min_confidence)]


def _run_thumbnail_ocr(
    slide: dict,
    ocr_lang: str,
    ocr_psm: int,
    ocr_min_confidence: float,
) -> dict:
    """Run OCR against video thumbnail as fallback when frame OCR is unavailable."""
    image, media_source = _load_ocr_image(slide)
    result = _run_best_ocr(
        image=image,
        lang=ocr_lang,
        psm=ocr_psm,
        min_confidence=ocr_min_confidence,
    )
    return {
        "slide": slide["index"],
        "text": result["text"],
        "lines": result["lines"],
        "confidence": result["confidence"],
        "word_count": result["word_count"],
        "line_count": result["line_count"],
        "variant": result["variant"],
        "media_type": slide["type"],
        "timestamp": "00:00",
        "ocr_source": f"thumbnail_fallback:{media_source}",
    }


def _extract_video_frames_for_ocr(video_path: str) -> list[dict]:
    """Extract sampled frame files and timestamps from a local video using ffmpeg."""
    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")

    interval_seconds = _select_video_frame_interval(video_path)
    with tempfile.TemporaryDirectory(prefix="ocr_frames_") as temp_dir:
        frame_pattern = os.path.join(temp_dir, "frame_%06d.jpg")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            video_path,
            "-vf",
            f"fps=1/{interval_seconds}",
            "-q:v",
            "2",
            frame_pattern,
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

        frame_paths = sorted(
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.lower().endswith(".jpg")
        )
        if not frame_paths:
            raise RuntimeError("No frames were extracted from the video")

        records = []
        for idx, frame_path in enumerate(frame_paths):
            with Image.open(frame_path) as frame_image:
                records.append(
                    {
                        "timestamp_seconds": idx * interval_seconds,
                        "image": frame_image.copy(),
                    }
                )
        return records


def _select_video_frame_interval(video_path: str) -> float:
    """Use denser sampling for short videos when duration can be determined."""
    duration_seconds = _probe_video_duration_seconds(video_path)
    if duration_seconds and duration_seconds <= SHORT_VIDEO_DURATION_THRESHOLD_SECONDS:
        return SHORT_VIDEO_FRAME_INTERVAL_SECONDS
    return DEFAULT_VIDEO_FRAME_INTERVAL_SECONDS


def _probe_video_duration_seconds(video_path: str) -> float | None:
    """Read video duration via ffprobe when available."""
    if not shutil.which("ffprobe"):
        return None

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
        return float(proc.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def _ocr_video_frames(
    slide_index: int,
    frame_records: list[dict],
    lang: str,
    psm: int,
    min_confidence: float,
) -> list[dict]:
    """Run OCR over sampled video frames and return deduplicated scene records."""
    scene_candidates = []
    for frame_record in frame_records:
        result = _run_best_ocr(
            image=frame_record["image"],
            lang=lang,
            psm=psm,
            min_confidence=min_confidence,
        )
        text = result["text"].strip()
        if not text:
            continue
        if not _should_keep_video_scene(result):
            continue
        scene_candidates.append(
            {
                "slide": slide_index,
                "media_type": "video",
                "timestamp": _format_seconds_timestamp(frame_record["timestamp_seconds"]),
                "timestamp_seconds": frame_record["timestamp_seconds"],
                "text": text,
                "lines": result["lines"],
                "confidence": result["confidence"],
                "word_count": result["word_count"],
                "line_count": result["line_count"],
                "variant": result["variant"],
                "ocr_source": "video_frame",
            }
        )

    collapsed = _collapse_scene_candidates_by_second(scene_candidates)
    return _deduplicate_scene_records(collapsed)


def _should_keep_video_scene(result: dict) -> bool:
    """Filter out low-quality OCR scenes that are mostly noise."""
    text = result.get("text", "")
    if not text.strip():
        return False

    confidence = float(result.get("confidence", 0.0))
    words = re.findall(r"[A-Za-z]{3,}", text)
    meaningful_tokens = [token for token in words if re.search(r"[aeiou]", token.lower())]
    average_token_length = (
        sum(len(token) for token in meaningful_tokens) / len(meaningful_tokens)
        if meaningful_tokens
        else 0.0
    )

    alnum_chars = sum(1 for char in text if char.isalnum())
    letter_chars = sum(1 for char in text if char.isalpha())
    alpha_ratio = (letter_chars / alnum_chars) if alnum_chars else 0.0

    if confidence < MIN_SCENE_CONFIDENCE and len(meaningful_tokens) < MIN_SCENE_MEANINGFUL_TOKENS:
        return False
    if len(meaningful_tokens) < 2 and len(text.strip()) < 24:
        return False
    if confidence < 82.0 and len(meaningful_tokens) < 3:
        return False
    if confidence < 82.0 and average_token_length < 4.0:
        return False
    if alpha_ratio < 0.4 and confidence < 75.0:
        return False
    return True


def _collapse_scene_candidates_by_second(scene_candidates: list[dict]) -> list[dict]:
    """Keep one strongest OCR scene per second to reduce rapid-frame duplicates."""
    best_by_second: dict[int, dict] = {}

    for scene in scene_candidates:
        second_key = int(scene.get("timestamp_seconds", 0.0))
        existing = best_by_second.get(second_key)
        if existing is None or _scene_quality_score(scene) > _scene_quality_score(existing):
            best_by_second[second_key] = scene

    return [best_by_second[second] for second in sorted(best_by_second)]


def _scene_quality_score(scene: dict) -> float:
    """Prefer scenes with confident OCR and richer text content."""
    confidence = float(scene.get("confidence", 0.0))
    word_count = int(scene.get("word_count", 0))
    return confidence * math.log(word_count + 2, 2)


def _deduplicate_scene_records(scene_records: list[dict]) -> list[dict]:
    """Deduplicate repeated OCR scenes and keep first appearance timestamp."""
    kept: list[dict] = []
    normalized_history: list[str] = []

    for scene in sorted(scene_records, key=lambda item: item.get("timestamp_seconds", 0.0)):
        normalized = _normalize_scene_text(scene.get("text", ""))
        if not normalized:
            continue

        if any(_texts_are_similar(normalized, seen) for seen in normalized_history):
            continue

        normalized_history.append(normalized)
        cleaned = dict(scene)
        cleaned.pop("timestamp_seconds", None)
        kept.append(cleaned)

    return kept


def _normalize_scene_text(text: str) -> str:
    """Normalize OCR scene text for reliable deduplication."""
    compact = re.sub(r"\s+", " ", text).strip().lower()
    compact = re.sub(r"[^\w\s]", "", compact)
    return compact


def _texts_are_similar(text_a: str, text_b: str) -> bool:
    """Compare normalized texts with a high similarity threshold."""
    if text_a == text_b:
        return True
    if not text_a or not text_b:
        return False
    return SequenceMatcher(None, text_a, text_b).ratio() >= SCENE_SIMILARITY_THRESHOLD


def _format_seconds_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS for OCR scene headers."""
    total_seconds = max(0, int(seconds))
    minutes = total_seconds // 60
    remainder = total_seconds % 60
    return f"{minutes:02d}:{remainder:02d}"


def _load_ocr_image(slide: dict) -> tuple[Image.Image, str]:
    """Load the best available OCR image for a slide.

    For videos, OCR runs against the cover/thumbnail image.
    """
    file_path = slide.get("file_path")
    if slide["type"] == "image" and file_path and os.path.exists(file_path):
        with Image.open(file_path) as image:
            return image.copy(), "downloaded_image"

    image_url = _get_ocr_image_url(slide)
    response = requests.get(image_url, headers=REQUEST_HEADERS, timeout=20)
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as image:
        return image.copy(), "remote_thumbnail" if slide["type"] == "video" else "remote_image"


def _get_ocr_image_url(slide: dict) -> str:
    """Return the OCR-friendly image URL for a slide."""
    if slide["type"] == "video":
        thumbnail_url = slide.get("thumbnail_url")
        if not thumbnail_url:
            raise RuntimeError("Video slide has no thumbnail URL for OCR")
        return thumbnail_url
    return slide["url"]


def _run_best_ocr(
    image: Image.Image,
    lang: str,
    psm: int,
    min_confidence: float,
) -> dict:
    """Try a few OCR-oriented image variants and keep the best result."""
    candidates = []
    for variant_name, variant_image in _build_ocr_variants(image):
        candidate = _extract_text_from_variant(
            image=variant_image,
            lang=lang,
            psm=psm,
            min_confidence=min_confidence,
        )
        candidate["variant"] = variant_name
        candidates.append(candidate)

    return max(candidates, key=_ocr_score)


def _build_ocr_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """Create multiple preprocessed versions to handle different post designs."""
    normalized = image.convert("RGB")
    width, height = normalized.size
    if width < 2200:
        scale = 2200 / width
        normalized = normalized.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    grayscale = ImageOps.grayscale(normalized)
    grayscale = ImageOps.autocontrast(grayscale)

    enhanced = ImageEnhance.Contrast(grayscale).enhance(1.8)
    sharpened = enhanced.filter(ImageFilter.SHARPEN)
    thresholded = sharpened.point(lambda px: 255 if px > 180 else 0).convert("L")
    softened = sharpened.filter(ImageFilter.MedianFilter(size=3))

    return [
        ("enhanced", sharpened),
        ("thresholded", thresholded),
        ("softened", softened),
    ]


def _extract_text_from_variant(
    image: Image.Image,
    lang: str,
    psm: int,
    min_confidence: float,
) -> dict:
    """Extract OCR lines plus confidence from one preprocessed image."""
    config = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
    data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=Output.DICT)

    grouped_lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []

    for idx, raw_text in enumerate(data["text"]):
        text = _normalize_ocr_fragment(raw_text)
        conf = _parse_confidence(data["conf"][idx])
        if not text or conf < min_confidence:
            continue

        key = (
            data["block_num"][idx],
            data["par_num"][idx],
            data["line_num"][idx],
        )
        grouped_lines.setdefault(key, []).append(text)
        confidences.append(conf)

    lines = [
        _normalize_ocr_line(" ".join(words))
        for _, words in sorted(grouped_lines.items())
        if words
    ]
    lines = [line for line in lines if line]

    if not lines:
        fallback = pytesseract.image_to_string(image, lang=lang, config=config)
        lines = [
            _normalize_ocr_line(line)
            for line in fallback.splitlines()
            if _normalize_ocr_line(line)
        ]

    text = "\n".join(lines)
    word_count = sum(len(line.split()) for line in lines)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    return {
        "text": text,
        "lines": lines,
        "confidence": confidence,
        "word_count": word_count,
        "line_count": len(lines),
    }


def _ocr_score(result: dict) -> float:
    """Score OCR candidates by balancing confidence and amount of text recovered."""
    if not result["text"]:
        return 0.0
    return result["confidence"] * math.log(result["word_count"] + 2, 2)


def _normalize_ocr_fragment(text: str) -> str:
    """Normalize an OCR fragment while preserving useful punctuation."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_ocr_line(text: str) -> str:
    """Normalize OCR output lines while preserving readable formatting."""
    return re.sub(r"\s+", " ", text).strip()


def _parse_confidence(raw_value: str) -> float:
    """Parse Tesseract confidence values safely."""
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return -1.0
