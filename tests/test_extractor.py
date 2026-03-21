import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from extractor import (
    _build_canonical_instagram_url,
    _deduplicate_scene_records,
    _ensure_ffmpeg_available,
    _extract_instagram_url_parts,
    _format_seconds_timestamp,
    _build_post_output_dir,
    _combine_ocr_text,
    _get_ocr_image_url,
    _is_valid_cached_media,
    _normalize_ocr_line,
    _ocr_video_slide,
    _should_keep_video_scene,
    extract_post,
    extract_shortcode,
)


class ExtractorTests(unittest.TestCase):
    def test_extract_shortcode_accepts_querystring(self) -> None:
        url = "https://www.instagram.com/p/DVVXez5Ctc3/?igsh=ZWVkeGUweHI4bWI0"
        self.assertEqual(extract_shortcode(url), "DVVXez5Ctc3")

    def test_extract_shortcode_rejects_non_post_url(self) -> None:
        with self.assertRaises(ValueError):
            extract_shortcode("https://www.instagram.com/coding.sight/")

    def test_extract_instagram_url_parts_accepts_reel_url(self) -> None:
        self.assertEqual(
            _extract_instagram_url_parts("https://www.instagram.com/reel/DTTBJSgE6pP/"),
            ("reel", "DTTBJSgE6pP"),
        )

    def test_build_canonical_instagram_url_uses_media_kind(self) -> None:
        self.assertEqual(
            _build_canonical_instagram_url("tv", "ABC123"),
            "https://www.instagram.com/tv/ABC123/",
        )

    def test_normalize_ocr_line_collapses_whitespace(self) -> None:
        self.assertEqual(
            _normalize_ocr_line("Assignment    1   Terraform   "),
            "Assignment 1 Terraform",
        )

    def test_combine_ocr_text_skips_failed_or_empty_slides(self) -> None:
        combined = _combine_ocr_text(
            [
                {"slide": 1, "text": "Alpha"},
                {"slide": 2, "text": ""},
                {"slide": 3, "text": "[OCR failed: test]"},
                {"slide": 4, "text": "Beta"},
            ]
        )
        self.assertEqual(combined, "Slide 1\nAlpha\n\nSlide 4\nBeta")

    def test_combine_ocr_text_uses_timestamp_headers_for_video_scenes(self) -> None:
        combined = _combine_ocr_text(
            [
                {
                    "slide": 1,
                    "media_type": "video",
                    "timestamp": "00:00",
                    "text": "Intro",
                },
                {
                    "slide": 1,
                    "media_type": "video",
                    "timestamp": "00:03",
                    "text": "Main point",
                },
            ]
        )
        self.assertEqual(combined, "00:00\nIntro\n\n00:03\nMain point")

    def test_build_post_output_dir_nests_shortcode_under_base_dir(self) -> None:
        self.assertEqual(
            _build_post_output_dir("downloads", "DVVXez5Ctc3"),
            "downloads/DVVXez5Ctc3",
        )

    def test_get_ocr_image_url_uses_video_thumbnail(self) -> None:
        self.assertEqual(
            _get_ocr_image_url(
                {
                    "type": "video",
                    "url": "https://cdn.example/video.mp4",
                    "thumbnail_url": "https://cdn.example/thumb.jpg",
                }
            ),
            "https://cdn.example/thumb.jpg",
        )

    def test_is_valid_cached_media_accepts_well_formed_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "sample.jpg")
            Image.new("RGB", (8, 8), color="white").save(image_path, "JPEG")
            self.assertTrue(_is_valid_cached_media({"type": "image"}, image_path))

    def test_is_valid_cached_media_rejects_invalid_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "broken.jpg")
            with open(image_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("not-an-image")
            self.assertFalse(_is_valid_cached_media({"type": "image"}, image_path))

    def test_is_valid_cached_media_rejects_tiny_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "clip.mp4")
            with open(video_path, "wb") as file_obj:
                file_obj.write(b"tiny")
            self.assertFalse(_is_valid_cached_media({"type": "video"}, video_path))

    @patch("extractor.shutil.which", return_value=None)
    def test_is_valid_cached_media_rejects_non_mp4_video_cache(self, _) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "clip.mp4")
            with open(video_path, "wb") as file_obj:
                file_obj.write(b"<html>rate limited</html>" * 80)
            self.assertFalse(_is_valid_cached_media({"type": "video"}, video_path))

    @patch("extractor.shutil.which", return_value=None)
    def test_is_valid_cached_media_accepts_mp4_signature_when_ffprobe_unavailable(self, _) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "clip.mp4")
            with open(video_path, "wb") as file_obj:
                file_obj.write(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 2048))
            self.assertTrue(_is_valid_cached_media({"type": "video"}, video_path))

    @patch("extractor.subprocess.run")
    @patch("extractor.shutil.which", return_value="/usr/bin/ffprobe")
    def test_is_valid_cached_media_uses_ffprobe_for_video_validation(self, _, mock_run) -> None:
        mock_run.return_value = SimpleNamespace(
            stdout='{"format":{"duration":"9.7"},"streams":[{"codec_type":"video"}]}'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "clip.mp4")
            with open(video_path, "wb") as file_obj:
                file_obj.write(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 2048))
            self.assertTrue(_is_valid_cached_media({"type": "video"}, video_path))

    def test_format_seconds_timestamp_rounds_and_zero_pads(self) -> None:
        self.assertEqual(_format_seconds_timestamp(0), "00:00")
        self.assertEqual(_format_seconds_timestamp(3.2), "00:03")
        self.assertEqual(_format_seconds_timestamp(3.8), "00:03")
        self.assertEqual(_format_seconds_timestamp(65.4), "01:05")

    def test_deduplicate_scene_records_keeps_first_timestamp_in_order(self) -> None:
        deduped = _deduplicate_scene_records(
            [
                {
                    "slide": 1,
                    "media_type": "video",
                    "timestamp": "00:00",
                    "timestamp_seconds": 0.0,
                    "text": "HELLO, WORLD!",
                },
                {
                    "slide": 1,
                    "media_type": "video",
                    "timestamp": "00:01",
                    "timestamp_seconds": 1.0,
                    "text": "hello world",
                },
                {
                    "slide": 1,
                    "media_type": "video",
                    "timestamp": "00:04",
                    "timestamp_seconds": 4.0,
                    "text": "different scene",
                },
            ]
        )
        self.assertEqual([scene["timestamp"] for scene in deduped], ["00:00", "00:04"])

    @patch("extractor.shutil.which", return_value=None)
    def test_ensure_ffmpeg_available_raises_clear_error_when_missing(self, _) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            _ensure_ffmpeg_available()
        self.assertIn("ffmpeg is required for reel OCR", str(ctx.exception))

    def test_ocr_video_slide_falls_back_to_thumbnail_when_frame_extraction_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "clip.mp4")
            with open(video_path, "wb") as file_obj:
                file_obj.write(b"placeholder")

            slide = {
                "index": 1,
                "type": "video",
                "file_path": video_path,
                "thumbnail_url": "https://cdn.example/thumb.jpg",
            }

            with patch("extractor._extract_video_frames_for_ocr", side_effect=RuntimeError("boom")):
                with patch(
                    "extractor._run_thumbnail_ocr",
                    return_value={
                        "slide": 1,
                        "media_type": "video",
                        "timestamp": "00:00",
                        "text": "thumbnail text",
                        "lines": ["thumbnail text"],
                        "confidence": 75.0,
                        "word_count": 2,
                        "line_count": 1,
                        "variant": "enhanced",
                        "ocr_source": "thumbnail_fallback:remote_thumbnail",
                    },
                ) as thumbnail_ocr:
                    result = _ocr_video_slide(slide, "eng", 6, 30.0)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "00:00")
        self.assertIn("thumbnail_fallback", result[0]["ocr_source"])
        thumbnail_ocr.assert_called_once()

    def test_should_keep_video_scene_rejects_noisy_text(self) -> None:
        self.assertFalse(
            _should_keep_video_scene(
                {
                    "text": "| _ ial ay 4A f\\nI; {\\n4 — = — j\\n=== «CS",
                    "confidence": 58.85,
                }
            )
        )

    def test_should_keep_video_scene_accepts_meaningful_slide_text(self) -> None:
        self.assertTrue(
            _should_keep_video_scene(
                {
                    "text": (
                        "DevOps Roadmap\\nDevOps Foundations\\n"
                        "Linux Networking Scripting Version Control Build Tools"
                    ),
                    "confidence": 87.9,
                }
            )
        )

    def test_should_keep_video_scene_rejects_short_fragment_even_if_confident(self) -> None:
        self.assertFalse(
            _should_keep_video_scene(
                {
                    "text": "This |",
                    "confidence": 90.0,
                }
            )
        )

    @patch("extractor._fetch_post")
    @patch("extractor._create_loader")
    def test_extract_post_preserves_reel_url_kind(self, _, mock_fetch_post) -> None:
        mock_fetch_post.return_value = SimpleNamespace(
            typename="GraphVideo",
            is_video=True,
            owner_username="creator",
            owner_id="123",
            caption="caption",
            accessibility_caption=None,
            caption_hashtags=(),
            caption_mentions=(),
            date_utc=datetime(2026, 1, 9, 16, 48, 26, tzinfo=timezone.utc),
            date_local=datetime(2026, 1, 9, 22, 18, 26, tzinfo=timezone.utc),
            likes=42,
            comments=5,
            video_url="https://cdn.example/reel.mp4",
            url="https://cdn.example/thumb.jpg",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            data = extract_post(
                "https://www.instagram.com/reel/DTTBJSgE6pP/",
                download_media=False,
                output_dir=temp_dir,
            )

        self.assertEqual(data["url"], "https://www.instagram.com/reel/DTTBJSgE6pP/")


if __name__ == "__main__":
    unittest.main()
