"""Upload videos that are ALREADY rendered in output/ — no re-rendering.

Use this when you have .mp4s sitting in output/ (built earlier with
`--no-upload`, or by build_sample.py) and just want them on YouTube.

It reads the metadata sidecar `<video>.json` written at render time for the
real Reddit title/source; if none exists it falls back to a title derived from
the filename. Every successful upload is recorded in state/uploaded.json so
re-running never double-posts the same file.

Examples
--------
  # Upload every not-yet-uploaded video in output/ as PRIVATE (safe default):
  .venv\\Scripts\\python.exe scripts\\upload_existing.py

  # Make them unlisted, only the first 2:
  .venv\\Scripts\\python.exe scripts\\upload_existing.py --privacy unlisted --limit 2

  # Upload one specific file:
  .venv\\Scripts\\python.exe scripts\\upload_existing.py output\\AskReddit_draola.mp4

  # Re-upload something already in the log:
  .venv\\Scripts\\python.exe scripts\\upload_existing.py output\\sample.mp4 --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import channels as ch                 # noqa: E402
from src import reddit_fetch as rf             # noqa: E402
from src import youtube_upload as yt           # noqa: E402
from src.utils import ROOT, load_config, log, log_upload    # noqa: E402

UPLOADED_LOG = ROOT / "state" / "uploaded.json"


def _load_log() -> dict:
    if UPLOADED_LOG.exists():
        try:
            return json.loads(UPLOADED_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_log(data: dict) -> None:
    UPLOADED_LOG.parent.mkdir(parents=True, exist_ok=True)
    UPLOADED_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _meta_for(video: Path) -> dict:
    """Resolve real metadata for a video, best title source first:

    1. the `<video>.json` sidecar written at render time (authoritative);
    2. a live lookup of the Reddit post title by its id (for legacy videos
       with no sidecar) — cached back to a sidecar so we only fetch once;
    3. a last-resort title derived from the filename.
    """
    sidecar = video.with_suffix(".json")
    if sidecar.exists():
        try:
            m = json.loads(sidecar.read_text(encoding="utf-8"))
            if m.get("title"):
                return m
        except (json.JSONDecodeError, OSError):
            pass

    # Parse "AskReddit_draola" -> subreddit "AskReddit", post_id "draola".
    stem = video.stem.lstrip("_")
    if "_" in stem:
        subreddit, post_id = stem.split("_", 1)
    else:
        subreddit, post_id = "reddit", stem

    # Recover the real post title from Reddit so the Short isn't titled with a code.
    fetched = rf.fetch_title_by_id(post_id)
    if fetched:
        title, sub = fetched
        meta = {
            "title": title,
            "subreddit": sub or subreddit,
            "source_url": f"https://reddit.com/comments/{post_id}",
            "post_id": post_id,
        }
        try:  # cache so we never have to look it up again
            sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        except OSError:
            pass
        log.info("Recovered title for %s: %s", video.name, title)
        return meta

    log.warning("No sidecar and no Reddit title for %s — using filename.", video.name)
    return {
        "title": stem.replace("_", " ").title(),
        "subreddit": subreddit,
        "source_url": "",
        "post_id": post_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload already-rendered videos to YouTube")
    ap.add_argument("files", nargs="*", help="specific .mp4s (default: all in output/)")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"])
    ap.add_argument("--limit", type=int, help="upload at most N videos")
    ap.add_argument("--include-samples", action="store_true",
                    help="also upload sample.mp4 / _sample.mp4 (skipped by default)")
    ap.add_argument("--force", action="store_true",
                    help="upload even if already in state/uploaded.json")
    ap.add_argument("--channel", default="main",
                    help="channel name from config youtube.channels, or 'all' "
                         "to post to every configured channel (default: main)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.privacy:
        cfg["youtube"]["privacy"] = args.privacy

    # Resolve which channel(s) to post to from the registry (channels.json / config).
    channels = ch.load_channels(cfg)
    targets = ch.resolve_targets(channels, args.channel)
    if targets is None:
        return 1

    out_dir = ROOT / cfg["run"]["output_dir"]
    if args.files:
        videos = [Path(f) if Path(f).is_absolute() else ROOT / f for f in args.files]
    else:
        videos = sorted(out_dir.glob("*.mp4"))
        if not args.include_samples:
            videos = [v for v in videos if v.stem.lstrip("_").lower() != "sample"]

    uploaded = _load_log()
    done = 0
    for channel in targets:
        # Per-channel keys (e.g. privacy) override the shared youtube settings.
        token_file, secrets_file, yt_cfg = ch.channel_cfg(
            channels, channel, cfg["youtube"])
        log.info("=== Channel: %s (token=%s) ===", channel, token_file)

        for video in videos:
            if not video.exists():
                log.warning("Skip (missing): %s", video)
                continue
            # Log key is channel-scoped. Legacy bare-name entries count as 'main'.
            key = f"{channel}:{video.name}"
            legacy_hit = channel == "main" and video.name in uploaded
            if not args.force and (key in uploaded or legacy_hit):
                rec = uploaded.get(key) or uploaded[video.name]
                log.info("Skip (already on %s → %s): %s",
                         channel, rec["url"], video.name)
                continue
            if args.limit is not None and done >= args.limit:
                break

            meta = _meta_for(video)
            try:
                url = yt.upload(video, meta, yt_cfg,
                                token_file=token_file, secrets_file=secrets_file)
            except Exception as e:  # noqa: BLE001
                log.error("Upload failed for %s on %s: %s", video.name, channel, e)
                # The very first failure is almost always missing client_secret.json
                # or OAuth — stop so the user can fix it rather than spamming errors.
                if "client" in str(e).lower() or "secret" in str(e).lower():
                    log.error("→ Set up YouTube OAuth first (see HANDOFF.md step 2).")
                    return 1
                continue

            uploaded[key] = {"url": url, "privacy": yt_cfg.get("privacy"),
                             "title": meta["title"], "channel": channel}
            _save_log(uploaded)
            log_upload(channel, video, url, meta, yt_cfg.get("privacy", "public"))
            log.info("✓ %s → %s (%s)", video.name, url, channel)
            done += 1

    log.info("Done. Uploaded %d new video(s); %d total in log.",
             done, len(uploaded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
