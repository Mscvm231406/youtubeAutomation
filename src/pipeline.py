"""Reddit-card 'story' pipeline.

Given a Post + Comments, narrates each segment, renders a Reddit card per
segment, and composes a vertical Short where each card is on screen exactly
while it is being read.

Segment order: [post title] → [comment 1] → [comment 2] → ...
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from . import reddit_card as rc
from . import script_gen as sg
from . import tts as tts_mod
from . import video as vid
from .reddit_fetch import Comment, Post
from .utils import ROOT, log, slugify

# A progress callback reports build advancement as (fraction 0..1, stage label).
# Used by the parallel orchestrator to drive a live progress bar per video; the
# default no-op keeps the normal single-threaded callers unaffected.
ProgressFn = Callable[[float, str], None]


def _ssml_clean(text: str) -> str:
    """Light cleanup for narration without stripping sentence flow."""
    return sg.clean_text(text)


def build_reddit_short(post: Post, comments: list[Comment], cfg: dict,
                       out_path: str | Path,
                       on_progress: ProgressFn | None = None) -> Path:
    def _p(frac: float, label: str) -> None:
        if on_progress:
            try:
                on_progress(frac, label)
            except Exception:  # noqa: BLE001 - progress must never break a build
                pass

    run, vcfg, tcfg = cfg["run"], cfg["video"], cfg["tts"]
    rcfg = cfg.get("reddit_card", {})
    temp = Path(ROOT / run["temp_dir"])
    temp.mkdir(parents=True, exist_ok=True)
    base = slugify(f"{post.subreddit}_{post.id}")

    max_total = cfg["script"]["max_speech_seconds"]
    wps = cfg["script"]["words_per_second"]

    _p(0.02, "building segments")
    # --- build the ordered segment list (title + as many comments as fit) ---
    segments = []  # each: {"text", "card", "audio"}

    # post card
    title_text = _ssml_clean(post.title)
    post_card = rc.render_post_card(
        post.subreddit, rcfg.get("post_author", "redditor"),
        post.title, post.score, max(post.score // 3, len(comments)),
    )
    segments.append({"text": title_text, "card": post_card, "kind": "post"})

    # comment cards, accruing until we approach the speech budget
    est_words = len(title_text.split())
    for i, c in enumerate(comments):
        body = _ssml_clean(c.body)
        words = len(body.split())
        if est_words + words > max_total * wps and len(segments) > 1:
            break
        card = rc.render_comment_card(c.author, c.body, c.score)
        segments.append({"text": body, "card": card, "kind": "comment"})
        est_words += words

    # --- synth audio per segment, save cards, compute timeline ---
    # TTS is the bulk of pre-render work; spread it across 0.05 → 0.55 so the
    # bar climbs steadily as each segment is narrated.
    n_seg = len(segments)
    audio_files, windows, t = [], [], 0.0
    for i, seg in enumerate(segments):
        _p(0.05 + 0.50 * (i / max(n_seg, 1)), f"narrating {i + 1}/{n_seg}")
        apath = temp / f"{base}_seg{i}.mp3"
        _, dur = tts_mod.synthesize(seg["text"], apath, tcfg)
        png = temp / f"{base}_card{i}.png"
        rc.save_card(seg["card"], png)
        audio_files.append(apath)
        windows.append({"png": png, "start": t, "end": t + dur})
        t += dur

    # tile windows so a card is always visible (no blank gaps between segments)
    for i in range(len(windows) - 1):
        windows[i]["end"] = windows[i + 1]["start"]
    windows[-1]["end"] = t  # last card holds to the end

    log.info("Reddit Short: %d segments, ~%.1fs total", len(segments), t)
    _p(0.58, "composing video")
    result = vid.compose_reddit(windows, audio_files, out_path, vcfg)
    _p(0.96, "writing metadata")

    # Drop a metadata sidecar next to the video so it can be uploaded later
    # (e.g. by scripts/upload_existing.py) without re-running the whole pipeline.
    sidecar = Path(out_path).with_suffix(".json")
    sidecar.write_text(json.dumps({
        "title": post.title,
        "subreddit": post.subreddit,
        "source_url": post.url,
        "post_id": post.id,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    _p(1.0, "done")
    return result
