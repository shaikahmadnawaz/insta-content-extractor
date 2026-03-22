# Instagram Content Extractor

A Python CLI tool for extracting content from public Instagram posts and reels.

It supports:
- metadata extraction
- caption, hashtags, and mentions
- media download
- OCR from carousel slides and reels
- multiple OCR pipelines for local and Sarvam-based processing

## Current Status

The project currently supports:
- Instagram `/p/` posts and carousels
- Instagram `/reel/` reels
- canonical URL preservation in output
- cached media reuse
- segregated output folders for posts vs reels
- separate `media/` and `content/` folders per shortcode
- mode-aware OCR artifact filenames
- local OCR with Tesseract
- local OCR plus Sarvam cleanup
- Sarvam Vision OCR plus Sarvam cleanup

The item identifier used in folders is the Instagram `shortcode`, for example:
- post: `DVVXez5Ctc3`
- reel: `DTTBJSgE6pP`

## How It Works

The extraction flow is:

1. Parse the Instagram URL and detect whether it is a post or a reel.
2. Extract the shortcode from the URL.
3. Fetch Instagram metadata with Instaloader.
4. Preserve the original media kind in the saved URL, such as `/p/` or `/reel/`.
5. Download media into the post-specific or reel-specific `media/` folder.
6. Reuse cached media if a valid copy already exists locally.
7. Optionally run OCR depending on the selected mode.
8. Save OCR and JSON artifacts into the `content/` folder.

## What It Extracts

- Post metadata: owner, timestamps, likes, comments, type
- Caption text
- Hashtags
- Mentions
- Downloaded image/video files
- OCR text for slides and reel scenes
- Accessibility caption from Instagram when explicitly requested

## Content Source Handling

Instagram content is not always text-on-image first.

In practice:
- sometimes the media is only representational and the real message is in the caption
- sometimes the caption is promotional and the real message is inside the slides or reel frames
- sometimes both sources matter

To handle that, the extractor does not merge caption and OCR blindly.

Instead it keeps them separate and adds a lightweight content decision layer:
- `content_strategy`
- `primary_source`
- `primary_text`

Current strategy values:
- `caption_only`
- `ocr_only`
- `caption_plus_ocr`
- `media_representational`
- `none`

How it behaves:
- if caption is substantial and OCR is weak, it prefers the caption
- if OCR is substantial and caption is weak or mostly promotional, it prefers OCR
- if both are meaningful, it keeps both and marks the result as `caption_plus_ocr`

This keeps the raw data intact while still giving downstream users a practical default.

## Supported OCR Modes

### `--local`

Pure local OCR.

Behavior:
- uses `Tesseract` only
- does not use Sarvam anywhere
- best when you want a simple offline-ish OCR path after media download

### `--sarvam`

Local OCR plus Sarvam cleanup.

Behavior:
- OCR is done locally with `Tesseract`
- text cleanup and sentence formatting are done with Sarvam chat
- this is currently the safest Sarvam-backed mode for reels

### `--sarvam-vision`

Sarvam Vision OCR plus Sarvam cleanup.

Behavior:
- OCR is done by Sarvam Vision
- cleanup and sentence formatting are done with Sarvam chat
- useful for some posts/carousels
- currently less reliable for reels because provider-generated image-description text can still leak through

## Recommended Usage

### For posts and carousels

Recommended order to try:
- `--local`
- `--sarvam`
- `--sarvam-vision` when you specifically want to compare Vision OCR quality

### For reels

Recommended order to try:
- `--sarvam`
- `--local`

`--sarvam-vision` for reels is still experimental and may produce noisy scene text.

## Setup

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Install local OCR dependencies:

```bash
brew install tesseract
brew install ffmpeg
```

Install Sarvam SDK if you want Sarvam-backed modes:

```bash
uv pip install --python .venv/bin/python sarvamai
```

Set your API key:

```bash
export SARVAM_API_KEY="your-key-here"
```

The extractor also reads `SARVAM_API_KEY` from a local `.env` file.

Example `.env`:

```env
SARVAM_API_KEY=your-key-here
```

## CLI Usage

### Basic extraction

```bash
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/"
python main.py "https://www.instagram.com/reel/DTTBJSgE6pP/"
```

### OCR modes

```bash
# Pure local OCR
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --local

# Local OCR + Sarvam cleanup
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --sarvam
python main.py "https://www.instagram.com/reel/DTTBJSgE6pP/" --sarvam

# Sarvam Vision OCR + Sarvam cleanup
# Best used for posts/carousels when you want to compare Vision OCR quality
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --sarvam-vision
```

For reels, prefer:

```bash
python main.py "https://www.instagram.com/reel/DTTBJSgE6pP/" --sarvam
```

`--sarvam-vision` on reels is still experimental and may produce noisy scene text.

### JSON output

```bash
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --local --json
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --sarvam --json
```

### OCR tuning

```bash
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --local --ocr-psm 6 --ocr-min-confidence 35
```

### Sarvam cleanup model override

```bash
python main.py "https://www.instagram.com/reel/DTTBJSgE6pP/" --sarvam --sarvam-model sarvam-30b
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --sarvam-vision --sarvam-model sarvam-105b
```

### Accessibility caption

Instagram accessibility captions are hidden by default because they are often noisy autogenerated summaries.

Show them only when needed:

```bash
python main.py "https://www.instagram.com/p/DVVXez5Ctc3/" --show-accessibility
```

## Important Flags

- `--local`: use pure Tesseract OCR
- `--sarvam`: use Tesseract OCR plus Sarvam cleanup
- `--sarvam-vision`: use Sarvam Vision OCR plus Sarvam cleanup
- `--sarvam-model`: choose `sarvam-30b` or `sarvam-105b` for cleanup
- `--ocr-lang`: Tesseract language code
- `--ocr-psm`: Tesseract page segmentation mode
- `--ocr-min-confidence`: minimum Tesseract word confidence
- `--json`: save JSON output
- `--show-accessibility`: show Instagram accessibility caption
- `--no-download`: skip media download when OCR is not requested

Note:
- `--ocr` is kept as a hidden compatibility alias for `--local`

## Output Layout

The tool now segregates posts and reels and separates raw assets from extracted content.

```text
downloads/
  posts/
    DVVXez5Ctc3/
      media/
        DVVXez5Ctc3_1.jpg
        DVVXez5Ctc3_2.jpg
      content/
        DVVXez5Ctc3.local.ocr.txt
        DVVXez5Ctc3.local.json
  reels/
    DTTBJSgE6pP/
      media/
        DTTBJSgE6pP_1.mp4
      content/
        DTTBJSgE6pP.sarvam.ocr.txt
```

Folder rules:
- posts go under `downloads/posts/<shortcode>/`
- reels go under `downloads/reels/<shortcode>/`
- media files go under `<shortcode>/media/`
- OCR text and JSON files go under `<shortcode>/content/`

Mode-aware artifact naming:
- local OCR: `<shortcode>.local.ocr.txt`
- local OCR + Sarvam cleanup: `<shortcode>.sarvam.ocr.txt`
- Sarvam Vision + cleanup: `<shortcode>.sarvam-vision.ocr.txt`
- JSON-only without OCR: `<shortcode>.json`

## Caching and Download Reuse

Downloaded media is not fetched again if a valid local copy already exists.

Current cache behavior:
- images are reused only if they can be opened and verified as real images
- videos are reused only if they look like valid MP4 files
- if `ffprobe` is available, cached videos are also validated for:
  - positive duration
  - a real video stream

If a cached file is broken or invalid:
- it is deleted
- the extractor downloads a fresh copy

## OCR Behavior

### Posts and carousels

- image slides are OCRed one by one
- results are stored per slide
- combined text is also saved into one `.ocr.txt` file

### Reels

- reels are processed as sampled video scenes
- timestamps are formatted in playback order
- repeated scenes are deduplicated
- if local frame OCR fails, thumbnail OCR is used as fallback

## Sarvam Cleanup Behavior

Sarvam cleanup is designed to:
- preserve meaning and order
- remove obvious OCR junk
- normalize formatting
- avoid inventing missing text

Additional safety behavior:
- reasoning-style responses are ignored
- markdown fences are stripped
- generic provider artifacts like embedded `data:image/...` payloads are filtered
- generic image-description prose such as `The image is ...` is filtered

What we intentionally avoid:
- content-specific hardcoded rewrites
- reel-specific phrase hacks
- post-specific text replacements

## JSON Structure

The saved JSON includes:
- `shortcode`
- `url`
- `post_type`
- `owner`
- `caption`
- `accessibility_caption`
- `hashtags`
- `mentions`
- `date`
- `date_local`
- `likes`
- `comments_count`
- `media_count`
- `media`
- `slides`
- `downloaded_files`
- `ocr_text`
- `ocr_combined_text`
- `ocr_provider`
- `ocr_cleanup_model` when applicable
- `content_strategy`
- `primary_source`
- `primary_text`
- `ocr_text_file` when OCR is enabled
- `json_file` when JSON output is enabled

## URL Behavior

The saved `url` preserves the original Instagram media type:
- `/p/` stays `/p/`
- `/reel/` stays `/reel/`

This matters because posts and reels are saved into different output buckets.

## Current Tradeoffs and Practical Guidance

What is working well:
- posts and carousels with local OCR
- reels with local OCR plus Sarvam cleanup
- cached media reuse
- clean output folder segregation

What is still imperfect:
- Sarvam Vision on reels can still be noisy
- stylized or low-contrast visuals remain challenging for any OCR provider
- anonymous Instagram requests may still occasionally hit `403` or rate limits

Practical recommendation:
- use `--local` when you want pure Tesseract
- use `--sarvam` as the main upgraded mode
- use `--sarvam-vision` mainly for comparison on posts/carousels

## Testing

Run tests with either:

```bash
python3 -m unittest -v
./.venv/bin/python -m unittest discover -s tests -v
```

The test suite currently covers:
- URL parsing
- canonical URL preservation
- cache validation
- OCR mode selection
- Sarvam cleanup behavior
- output layout
- mode-aware filenames
- post vs reel folder segregation
- content vs media folder segregation

## Notes

- works best for public Instagram content
- supports `/p/` and `/reel/` workflows in practice
- `/tv/` parsing still exists in code, but the main organization/documentation now focuses on posts and reels
- accessibility caption is Instagram metadata, not our OCR result
- OCR quality is best on educational slides with large readable text
