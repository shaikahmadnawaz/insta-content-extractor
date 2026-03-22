"""Social Content Extractor CLI."""

import argparse
import json
import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .extractor import extract_post, extract_shortcode

console = Console()


def display_results(
    data: dict,
    show_json: bool = False,
    show_accessibility: bool = False,
) -> None:
    """Display extracted content data in the terminal."""
    if show_json:
        console.print_json(json.dumps(data, default=str))
        return

    platform = data.get("platform", "instagram")
    header_title = "Social Content Extractor"

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold cyan]{header_title}[/bold cyan]\n"
            f"[dim]{data['url']}[/dim]",
            box=box.DOUBLE,
            border_style="cyan",
        )
    )

    # Post Info
    info = Table(
        box=box.ROUNDED,
        border_style="blue",
        title="Post Info",
        title_style="bold blue",
        show_header=False,
        padding=(0, 2),
    )
    info.add_column("Field", style="bold white", min_width=15)
    info.add_column("Value", style="white")
    info.add_row("Platform", platform.capitalize())
    info.add_row("Owner", f"@{data['owner']['username']}")
    if data.get("title"):
        info.add_row("Title", data["title"])
    info.add_row("Type", data["post_type"].upper())
    info.add_row("Date (UTC)", data["date"])
    if data.get("date_local"):
        info.add_row("Date (Local)", data["date_local"])
    info.add_row("Likes", str(data["likes"]))
    info.add_row("Comments", str(data["comments_count"]))
    info.add_row("Media Count", str(data["media_count"]))
    console.print(info)

    # Caption / description
    if data["caption"]:
        caption = data["caption"]
        if len(caption) > 2000:
            caption = caption[:2000] + "\n[dim](truncated, see JSON for full text)[/dim]"
        console.print(
            Panel(caption, title="Caption", title_align="left",
                  border_style="green", box=box.ROUNDED)
        )

    # Hashtags
    if data["hashtags"]:
        tags = " ".join(f"[bold cyan]#{t}[/bold cyan]" for t in data["hashtags"])
        console.print(
            Panel(tags, title="Hashtags", title_align="left",
                  border_style="yellow", box=box.ROUNDED)
        )

    # Accessibility caption
    if show_accessibility and data.get("accessibility_caption"):
        console.print(
            Panel(
                data["accessibility_caption"],
                title="Accessibility Caption",
                title_align="left",
                border_style="bright_blue",
                box=box.ROUNDED,
            )
        )

    # Mentions
    if data["mentions"]:
        mentions = " ".join(f"[bold magenta]@{m}[/bold magenta]" for m in data["mentions"])
        console.print(
            Panel(mentions, title="Mentions", title_align="left",
                  border_style="magenta", box=box.ROUNDED)
        )

    # Media Summary
    media = Table(
        box=box.ROUNDED, border_style="cyan",
        title="Media Summary", title_style="bold cyan",
    )
    media.add_column("#", style="bold", justify="center", width=4)
    media.add_column("Type", style="bold", justify="center", width=8)
    media.add_column("Saved As", style="white")
    for item in data["media"]:
        badge = "[bold red]VIDEO[/bold red]" if item["type"] == "video" else "[bold green]IMAGE[/bold green]"
        file_path = next(
            (
                slide.get("file_path")
                for slide in data.get("slides", [])
                if slide.get("index") == item["index"]
            ),
            None,
        )
        display_path = os.path.basename(file_path) if file_path else "-"
        media.add_row(str(item["index"]), badge, display_path)
    console.print(media)

    # OCR Results
    if data.get("ocr_text"):
        console.print()
        video_blocks: dict[int, list[dict]] = {}
        for ocr_item in data["ocr_text"]:
            if ocr_item.get("media_type") == "video":
                video_blocks.setdefault(ocr_item["slide"], []).append(ocr_item)
                continue

            slide = ocr_item["slide"]
            text = ocr_item["text"]
            confidence = ocr_item.get("confidence", 0.0)
            if text and not text.startswith("[OCR failed"):
                console.print(
                    Panel(
                        text,
                        title=f"Slide {slide} - OCR Text ({confidence:.1f}%)",
                        title_align="left",
                        border_style="bright_yellow",
                        box=box.ROUNDED,
                    )
                )
            elif text.startswith("[OCR failed"):
                console.print(f"  [dim]Slide {slide}: {text}[/dim]")
            else:
                console.print(f"  [dim]Slide {slide}: (no text detected)[/dim]")

        for slide in sorted(video_blocks):
            scenes = sorted(video_blocks[slide], key=lambda item: item.get("timestamp") or "")
            readable_scenes = []
            for scene in scenes:
                text = scene.get("text", "")
                if not text or text.startswith("[OCR failed"):
                    continue
                timestamp = scene.get("timestamp") or "00:00"
                readable_scenes.append(f"{timestamp}\n{text}")

            if readable_scenes:
                body = "\n\n".join(readable_scenes)
                console.print(
                    Panel(
                        body,
                        title=f"Slide {slide} - Video OCR Scenes",
                        title_align="left",
                        border_style="bright_yellow",
                        box=box.ROUNDED,
                    )
                )
            else:
                console.print(f"  [dim]Slide {slide}: (no text detected in video scenes)[/dim]")

    # Output Summary
    console.print()
    if data.get("downloaded_files"):
        console.print(f"[bold green]Downloaded media:[/bold green] {len(data['downloaded_files'])} file(s)")
    elif not data.get("ocr_text"):
        console.print("[dim]Media download skipped (use without --no-download to save files)[/dim]")

    # JSON path
    if data.get("json_file"):
        console.print(f"[bold green]JSON saved to:[/bold green] {data['json_file']}")
    if data.get("ocr_text_file"):
        console.print(f"[bold green]OCR text saved to:[/bold green] {data['ocr_text_file']}")

    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract content from Instagram posts/reels and YouTube Shorts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/"\n'
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/" --local\n'
            '  social-content-extractor "https://www.instagram.com/reel/SHORTCODE/" --sarvam\n'
            '  social-content-extractor "https://www.youtube.com/shorts/VIDEO_ID" --sarvam\n'
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/" --sarvam\n'
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/" --sarvam-vision\n'
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/" --no-download\n'
            '  social-content-extractor "https://www.instagram.com/p/SHORTCODE/" --json\n'
        ),
    )
    parser.add_argument("url", help="Instagram post/reel URL or YouTube Shorts URL")
    parser.add_argument("--no-download", action="store_true",
                        help="Skip downloading media files")
    parser.add_argument("-o", "--output-dir", default="downloads",
                        help="Base output directory; each post gets its own folder (default: downloads)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Save extracted data as a JSON file")
    parser.add_argument("--show-accessibility", action="store_true",
                        help="Show Instagram's autogenerated accessibility caption")
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument("--local", action="store_const", const="local", dest="ocr_mode",
                           help="Use local Tesseract OCR only")
    ocr_group.add_argument("--ocr", action="store_const", const="local", dest="ocr_mode",
                           help=argparse.SUPPRESS)
    ocr_group.add_argument("--sarvam", action="store_const", const="sarvam", dest="ocr_mode",
                           help="Use local Tesseract OCR + Sarvam 30b cleanup")
    ocr_group.add_argument("--sarvam-vision", action="store_const", const="sarvam_vision", dest="ocr_mode",
                           help="Use Sarvam Vision OCR + Sarvam 30b cleanup")
    parser.add_argument("--sarvam-model", default="auto",
                        choices=["auto", "sarvam-30b", "sarvam-105b"],
                        help="Sarvam chat model for OCR cleanup (default: auto)")
    parser.add_argument("--sarvam-language", default="en-IN",
                        help="Sarvam Vision language code for --sarvam-vision (default: en-IN)")
    parser.add_argument("--ocr-lang", default="eng",
                        help="Tesseract language code (default: eng)")
    parser.add_argument("--ocr-psm", type=int, default=6,
                        help="Tesseract page segmentation mode (default: 6)")
    parser.add_argument("--ocr-min-confidence", type=float, default=30.0,
                        help="Minimum OCR word confidence from 0-100 (default: 30)")

    args = parser.parse_args()

    # Validate URL
    try:
        extract_shortcode(args.url)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

    # OCR requires downloaded images
    download = not args.no_download
    ocr_enabled = bool(args.ocr_mode)
    if ocr_enabled:
        download = True

    if not args.json_output:
        console.print()
        msg = "Extracting content"
        if args.ocr_mode == "local":
            msg += " + running local OCR"
        elif args.ocr_mode == "sarvam":
            msg += " + running local OCR + Sarvam cleanup"
        elif args.ocr_mode == "sarvam_vision":
            msg += " + running Sarvam Vision OCR + Sarvam cleanup"
        console.print(f"[bold cyan]{msg}...[/bold cyan]")

    try:
        data = extract_post(
            url=args.url,
            download_media=download,
            output_dir=args.output_dir,
            ocr=ocr_enabled,
            save_json=args.json_output,
            ocr_provider=(
                "sarvam"
                if args.ocr_mode == "sarvam"
                else "sarvam_vision"
                if args.ocr_mode == "sarvam_vision"
                else "tesseract"
            ),
            ocr_lang=args.ocr_lang,
            ocr_psm=args.ocr_psm,
            ocr_min_confidence=args.ocr_min_confidence,
            sarvam_model=args.sarvam_model,
            sarvam_language=args.sarvam_language,
        )
    except Exception as e:
        console.print(f"\n[bold red]Extraction failed:[/bold red] {e}")
        console.print("[dim]If the media is rate-limited, private, or temporarily unavailable, wait a bit and retry.[/dim]")
        sys.exit(1)

    display_results(
        data,
        show_json=args.json_output,
        show_accessibility=args.show_accessibility,
    )


if __name__ == "__main__":
    main()
