# Instagram Content Extractor

A Python CLI tool to extract content from Instagram posts, including carousel slides with OCR-ready text extraction.

## What it extracts

- Post metadata: owner, timestamps, likes, comments, post type
- Caption content: full caption, hashtags, mentions
- Media assets: all image/video URLs plus optional local downloads
- OCR content: text detected on each image slide and timestamped text scenes from reels/videos
- Accessibility caption: Instagram's own alt/accessibility text when available

## Setup

```bash
pip install -r requirements.txt
```

If you want OCR, install Tesseract and ffmpeg:

```bash
brew install tesseract
brew install ffmpeg
```

## Usage

```bash
# Extract metadata + media
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/"

# Extract a reel
python main.py "https://www.instagram.com/reel/DTTBJSgE6pP/"

# Extract metadata + OCR from all image slides
# This creates one OCR text file
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --ocr

# OCR with custom tuning
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --ocr --ocr-psm 6 --ocr-min-confidence 35

# Save JSON too
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --ocr --json
```

## Output

The tool creates a folder per Instagram post inside the base output directory:

```text
downloads/
  DVVXez5Ctc3/
    DVVXez5Ctc3_1.jpg
    DVVXez5Ctc3_2.jpg
    DVVXez5Ctc3.ocr.txt
    DVVXez5Ctc3.json
```

Output rules:

- Default run: downloads media into the post folder
- `--ocr`: downloads media plus one combined OCR `.txt` file
- `--json`: saves one `.json` file
- `--ocr --json`: saves both the OCR `.txt` file and the `.json` file

For reels/videos, OCR output is grouped by timestamp in playback order:

```text
00:00
Extracted text

00:03
Next scene text
```

The JSON now includes:

- `media`: raw media items from Instagram
- `slides`: per-slide objects with `file_path` and OCR results
- `ocr_text`: OCR result objects with `slide`, `media_type`, `timestamp`, `text`, `lines`, `confidence`, `variant`, `ocr_source`
- `ocr_combined_text`: one merged text blob for downstream use

URL behavior:

- The saved `url` field preserves the original Instagram media type: `/p/`, `/reel/`, or `/tv/`
- Reel and TV links are not rewritten to `/p/` in JSON output

Cached media behavior:

- Image cache entries are verified as real images before reuse
- Video cache entries are checked for a valid MP4 signature before reuse
- If `ffprobe` is available, cached videos are also validated for a real video stream and positive duration

## Testing

Run the test suite with either of these commands:

```bash
python3 -m unittest -v
./.venv/bin/python -m unittest discover -s tests -v
```

## Notes

- Works best for public posts
- Supports `/p/`, `/reel/`, and `/tv/` URLs
- Instagram may intermittently return `403` or rate-limit anonymous requests
- Reel/video OCR samples frames from downloaded video (default every `1s`, and `0.5s` for short reels), then deduplicates repeated scenes
- If frame OCR fails for a video slide, OCR falls back to the thumbnail image
- OCR is strongest on text-heavy slides like educational carousels and weaker on stylized or low-contrast imagery
