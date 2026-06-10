"""Build ONE complete sample Reddit-card Short end-to-end.

Scrapes a trending r/AskReddit post + top comments (live, via pullpush.io — no
Reddit credentials needed), renders Reddit-style cards, narrates each with
edge-tts, and composes them over the Minecraft background.

Run: .venv\\Scripts\\python.exe scripts\\build_sample.py
Output: output/sample.mp4
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import (ensure_dirs, ffmpeg_bin, load_config, log,
                       recent_ids, record_used)
from src import reddit_fetch as rf
from src import pipeline as pl
from src.reddit_fetch import Comment, Post

SUBREDDIT = "AskReddit"

# Fallback used only if the live fetch returns nothing (network/API hiccup),
# so the demo always produces a video.
FALLBACK_POST = Post(
    id="_fallback", subreddit="AskReddit",
    title="What's a small habit that completely changed your life?",
    body="", score=48700, url="", nsfw=False,
)
FALLBACK_COMMENTS = [
    Comment("quiethabit", "Making my bed every morning. It sounds dumb but starting "
            "the day with one finished task sets the tone for everything else.", 15200),
    Comment("twowater", "Drinking a full glass of water before coffee. My headaches "
            "basically disappeared within a week.", 9800),
    Comment("walk_it_off", "A 20 minute walk with no phone. It's the only time my brain "
            "actually gets quiet and I solve half my problems out there.", 7400),
    Comment("twominute", "The two-minute rule. If something takes less than two minutes, "
            "I do it immediately instead of letting it pile up.", 5100),
]


def ensure_background(cfg) -> None:
    bg_dir = ROOT / cfg["video"]["backgrounds_dir"]
    bg_dir.mkdir(parents=True, exist_ok=True)
    if any(bg_dir.glob("*.mp4")) or any(bg_dir.glob("*.mov")):
        return
    placeholder = bg_dir / "_placeholder.mp4"
    log.info("No background found — generating placeholder %s", placeholder.name)
    subprocess.run([
        ffmpeg_bin(), "-y", "-f", "lavfi", "-i",
        "gradients=s=1080x1920:d=15:speed=0.05:c0=0x1a1a2e:c1=0x16213e:c2=0x0f3460",
        "-t", "15", "-pix_fmt", "yuv420p", str(placeholder),
    ], capture_output=True, text=True, check=True)


def main() -> int:
    cfg = load_config()
    ensure_dirs("temp", "output", "state")
    ensure_background(cfg)

    # Skip posts used in the last `cooldown` videos (repeats OK, just not back-to-back).
    hist_file = cfg["run"]["history_file"]
    cooldown = cfg["run"].get("cooldown", 5)
    skip = recent_ids(hist_file, cooldown)
    if skip:
        log.info("Cooldown active — skipping %d recently-used post(s): %s",
                 len(skip), ", ".join(sorted(skip)))

    log.info("Scraping a trending r/%s post + comments…", SUBREDDIT)
    post, comments = rf.fetch_post_with_comments(SUBREDDIT, cfg["reddit"], exclude=skip)
    if not post or len(comments) < 2:
        log.warning("Live fetch unavailable — using built-in fallback content.")
        post, comments = FALLBACK_POST, FALLBACK_COMMENTS

    log.info("POST: %s (score %s)", post.title, post.score)
    for i, c in enumerate(comments, 1):
        log.info("  comment %d (u/%s, %s): %s", i, c.author, c.score, c.body[:60])

    out = pl.build_reddit_short(post, comments, cfg, "output/sample.mp4")
    record_used(hist_file, post.id)
    log.info("Recorded post %s to history (%s)", post.id, hist_file)
    print(f"\nSample Reddit Short built: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
