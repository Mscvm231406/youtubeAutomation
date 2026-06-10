"""Orchestrator — runs the full Reddit-card → YouTube Shorts pipeline.

Usage:
  python main.py --subreddit AskReddit --limit 1 --no-upload
  python main.py --limit 3 --privacy private

Each video: scrape a trending post + comments → render Reddit cards →
narrate per segment → compose over the background → (optionally) upload.

A usage history (state/history.json) records every post turned into a video.
Posts used in the last `run.cooldown` videos are skipped, so the same post is
never aired back-to-back, but may recur once it falls out of the cooldown.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from src import channels as ch
from src import moderation
from src import pipeline as pl
from src import reddit_fetch as rf
from src import youtube_upload as yt
from src.utils import (ensure_dirs, load_config, log, log_upload, record_used,
                       recent_ids, slugify)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reddit → YouTube Shorts automation")
    parser.add_argument("--subreddit", help="override config subreddit list")
    parser.add_argument("--limit", type=int, help="max videos to produce this run")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"])
    parser.add_argument("--no-upload", action="store_true", help="build only, skip YouTube")
    parser.add_argument("--channel", default="main",
                        help="channel name from config youtube.channels, or 'all' "
                             "to post each built video to every channel (default: main)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.privacy:
        cfg["youtube"]["privacy"] = args.privacy
    run = cfg["run"]
    limit = args.limit if args.limit is not None else run["daily_quota"]
    do_upload = not args.no_upload

    # Resolve target channel(s) from the registry (channels.json / config).
    channels = ch.load_channels(cfg)
    targets = ch.resolve_targets(channels, args.channel)
    if targets is None:
        return 1

    # Only post to channels whose token is healthy NOW (refreshes good ones,
    # skips expired/revoked/Google-restricted accounts) — an un-authorized or
    # dead channel would otherwise hang on a browser flow or fail mid-run.
    if do_upload:
        healthy, skipped = [], []
        for t in targets:
            status, detail = ch.token_health(channels, t)
            if status == "ok":
                healthy.append(t)
            else:
                skipped.append((t, status, detail))
        for name, status, detail in skipped:
            log.warning("Skipping channel %s [%s]: %s", name, status, detail)
        targets = healthy
        if not targets:
            log.error("No usable channels to upload to (see warnings above). "
                      "Re-authorize with scripts/channels.py login <name>, or "
                      "build only with --no-upload.")
            return 1
        log.info("Will upload each Short to %d channel(s): %s",
                 len(targets), ", ".join(targets))

    ensure_dirs(run["output_dir"], run["temp_dir"], "state", "assets/backgrounds")

    # Posts used in the last `cooldown` videos are off-limits this run too, and we
    # extend that set as we go so a single run never repeats a post back-to-back.
    skip = set(recent_ids(run["history_file"], run.get("cooldown", 5)))
    if skip:
        log.info("Cooldown: skipping %d recently-used post(s)", len(skip))

    made = 0
    while made < limit:
        subreddit = rf.pick_subreddit(cfg["reddit"], args.subreddit)
        post, comments = rf.fetch_post_with_comments(subreddit, cfg["reddit"], exclude=skip)
        if not post or len(comments) < cfg["reddit"].get("min_comments", 3):
            log.warning("No fresh post with enough comments for r/%s (cooldown=%d).",
                        subreddit, run.get("cooldown", 5))
            break

        log.info("── [%s] r/%s: %s", post.id, subreddit, post.title[:70])
        try:
            base = slugify(f"{subreddit}_{post.id}")
            out_path = f"{run['output_dir']}/{base}.mp4"
            pl.build_reddit_short(post, comments, cfg, out_path)

            allow_nsfw = bool(cfg["reddit"].get("allow_nsfw", False))
            nsfw_blocked = (not allow_nsfw and moderation.post_is_nsfw(
                post.title, getattr(post, "body", ""),
                getattr(post, "nsfw", False)))
            if do_upload and nsfw_blocked:
                log.error("BLOCKED NSFW — not uploading %s: '%s'",
                          out_path, post.title[:60])
            elif do_upload:
                meta = {"title": post.title, "subreddit": subreddit,
                        "source_url": post.url}
                for channel in targets:
                    token_file, secrets_file, yt_cfg = ch.channel_cfg(
                        channels, channel, cfg["youtube"])
                    try:
                        url = yt.upload(out_path, meta, yt_cfg,
                                        token_file=token_file, secrets_file=secrets_file)
                        log.info("✓ Uploaded to %s: %s", channel, url)
                        log_upload(channel, out_path, url, meta,
                                   yt_cfg.get("privacy", "public"))
                    except Exception as e:  # noqa: BLE001 - one channel failing
                        # (e.g. quota) shouldn't stop the others or lose the video.
                        log.error("✗ Upload to %s failed: %s", channel, e)
            else:
                log.info("✓ Built: %s", out_path)

            record_used(run["history_file"], post.id)
            skip.add(post.id)  # don't reuse within this same run
            made += 1
        except Exception as e:  # noqa: BLE001 - keep the batch alive
            log.error("✗ Failed on %s: %s", post.id, e)
            log.debug(traceback.format_exc())
            skip.add(post.id)  # avoid retrying the same broken post in a loop

    log.info("Finished: %d/%d videos produced.", made, limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
