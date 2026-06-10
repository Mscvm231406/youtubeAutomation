"""Build a POOL of distinct background clips by downloading one longer parkour
video and splitting it into several non-overlapping segments.

This sidesteps the fact that YouTube search returns the same #1 video for most
"minecraft parkour" queries — different *time ranges* of one long video give
genuinely different footage.

Run: .venv\\Scripts\\python.exe scripts\\make_backgrounds.py --parts 4 --seg 180
Output: assets/backgrounds/parkour_01.mp4 ... parkour_NN.mp4
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252 and choke on non-ASCII; force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "backgrounds"


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
    ap.add_argument("--url", help="explicit long video URL (preferred for control)")
    ap.add_argument("--query", default="minecraft parkour gameplay no copyright 10 minutes")
    ap.add_argument("--parts", type=int, default=4, help="how many clips to produce")
    ap.add_argument("--seg", type=int, default=180, help="seconds per clip")
    ap.add_argument("--prefix", default="parkour")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    ytdlp, ffmpeg = _find("yt-dlp"), _find("ffmpeg")
    src = OUT / "_source_long.mp4"

    # Require a LONG source so the segments don't overlap.
    need = args.parts * args.seg
    target = args.url or f"ytsearch10:{args.query}"
    if src.exists() and src.stat().st_size > 0:
        print(f"Reusing existing source: {src.name}")
    else:
        cmd = [
            ytdlp, target,
            "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
            "--merge-output-format", "mp4", "--ffmpeg-location", str(Path(ffmpeg).parent),
            "-o", str(src), "--no-playlist", "--newline",
            "--socket-timeout", "30", "--retries", "5", "--fragment-retries", "5",
            "--match-filter", f"duration >= {need} & duration < 3600 & !is_live",
            "--playlist-items", "1",
        ]
        print("Downloading long source...")
        if subprocess.run(cmd).returncode != 0 or not src.exists():
            print("Failed to fetch a long-enough source. Try --url of a 10+ min clip.",
                  file=sys.stderr)
            return 1

    print(f"Splitting into {args.parts} x {args.seg}s clips…")
    made = []
    for i in range(args.parts):
        start = i * args.seg
        out = OUT / f"{args.prefix}_{i+1:02d}.mp4"
        rc = subprocess.run([
            ffmpeg, "-y", "-ss", str(start), "-i", str(src), "-t", str(args.seg),
            "-c:v", "libx264", "-crf", "22", "-an", "-pix_fmt", "yuv420p", str(out),
        ], capture_output=True, text=True)
        if rc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            made.append(out.name)
            print(f"  OK {out.name}")
    src.unlink(missing_ok=True)
    print(f"\nCreated {len(made)} distinct backgrounds: {', '.join(made)}")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
