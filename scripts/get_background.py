"""Download a Minecraft-parkour (or any) background clip for use behind narration.

Uses yt-dlp. By default it pulls a short segment of a search result for
"no copyright minecraft parkour gameplay" so you get usable footage immediately,
but you SHOULD prefer passing an explicit URL you have the right to use.

Examples:
  python scripts/get_background.py                       # default search, first 3 min
  python scripts/get_background.py --url https://youtu.be/XXXX
  python scripts/get_background.py --query "satisfying soap cutting" --minutes 5

Output: assets/backgrounds/<name>.mp4  (the composer auto-crops to 9:16 & loops)

NOTE on rights: gameplay footage is still owned by whoever recorded it. Use clips
explicitly published as "no copyright / free to use", your own recordings, or
licensed packs — especially before monetizing.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "backgrounds"


def _find(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    pkgs = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if pkgs.exists():
        for exe in pkgs.rglob(f"{name}.exe"):
            return str(exe)
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="explicit video URL (preferred)")
    ap.add_argument("--query", default="no copyright minecraft parkour gameplay 1080p",
                    help="search query if no --url given")
    ap.add_argument("--minutes", type=int, default=3,
                    help="how many minutes to grab from the start")
    ap.add_argument("--name", default="minecraft_parkour", help="output filename stem")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ytdlp = _find("yt-dlp")
    ffmpeg = _find("ffmpeg")

    # ytsearchN lets us skip dead/throttled results by trying the next match.
    target = args.url or f"ytsearch5:{args.query}"
    out_tmpl = str(OUT_DIR / f"{args.name}.%(ext)s")

    # Full download at <=720p is far more reliable than ffmpeg section-extraction
    # (which stalls on throttled YouTube ranges). 720p is plenty for a looping bg.
    cmd = [
        ytdlp, target,
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--ffmpeg-location", str(Path(ffmpeg).parent),
        "-o", out_tmpl,
        "--no-playlist", "--newline",
        "--socket-timeout", "30",
        "--retries", "5", "--fragment-retries", "5",
        "--match-filter", "duration < 900 & !is_live",  # avoid hours-long VODs/livestreams
        "--playlist-items", "1",  # for ytsearch: take the first that passes the filter
    ]
    print("Running:", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print("yt-dlp failed. Try a specific --url, or check your connection.", file=sys.stderr)
        return rc

    # If we grabbed a long clip, trim to the first N minutes to keep files small.
    raw = next(iter(OUT_DIR.glob(f"{args.name}.mp4")), None)
    if raw and args.minutes:
        trimmed = OUT_DIR / f"{args.name}_trim.mp4"
        subprocess.run([
            ffmpeg, "-y", "-i", str(raw), "-t", str(args.minutes * 60),
            "-c", "copy", str(trimmed),
        ], capture_output=True)
        if trimmed.exists() and trimmed.stat().st_size > 0:
            raw.unlink(missing_ok=True)
            trimmed.rename(raw)

    mp4s = list(OUT_DIR.glob(f"{args.name}.mp4"))
    if mp4s:
        print(f"\nSaved background -> {mp4s[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
