import os
import tempfile
import unittest

from PIL import Image

from extractor import (
    _build_post_output_dir,
    _combine_ocr_text,
    _get_ocr_image_url,
    _is_valid_cached_media,
    _normalize_ocr_line,
    extract_shortcode,
)


class ExtractorTests(unittest.TestCase):
    def test_extract_shortcode_accepts_querystring(self) -> None:
        url = "https://www.instagram.com/p/DVVXez5Ctc3/?igsh=ZWVkeGUweHI4bWI0"
        self.assertEqual(extract_shortcode(url), "DVVXez5Ctc3")

    def test_extract_shortcode_rejects_non_post_url(self) -> None:
        with self.assertRaises(ValueError):
            extract_shortcode("https://www.instagram.com/coding.sight/")

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


if __name__ == "__main__":
    unittest.main()
