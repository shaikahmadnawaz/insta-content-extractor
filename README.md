# Instagram Content Extractor

A Python CLI tool to extract content from Instagram posts, including carousel slides with OCR-ready text extraction.

## What it extracts

- Post metadata: owner, timestamps, likes, comments, post type
- Caption content: full caption, hashtags, mentions
- Media assets: all image/video URLs plus optional local downloads
- OCR content: text detected on each image slide, with confidence and a combined text file
- Accessibility caption: Instagram's own alt/accessibility text when available

## Setup

```bash
pip install -r requirements.txt
```

If you want OCR, install Tesseract too:

```bash
brew install tesseract
```

## Usage

```bash
# Extract metadata + media
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/"

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

The JSON now includes:

- `media`: raw media items from Instagram
- `slides`: per-slide objects with `file_path` and OCR results
- `ocr_text`: OCR result objects with `text`, `lines`, `confidence`, `variant`, `media_type`, `ocr_source`
- `ocr_combined_text`: one merged text blob for downstream use

## Notes

- Works best for public posts
- Supports `/p/`, `/reel/`, and `/tv/` URLs
- Instagram may intermittently return `403` or rate-limit anonymous requests
- Video OCR uses the post's cover/thumbnail image
- OCR is strongest on text-heavy slides like educational carousels and weaker on stylized or low-contrast imagery
