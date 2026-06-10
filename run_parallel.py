"""Parallel runner — build N Shorts at once, then fan every Short out to all
channels in parallel, with a live terminal dashboard you can watch.

Pipeline (staged barriers so the display reads cleanly):
  1. SELECT   — pick N distinct trending posts (sequential; cheap network work).
  2. BUILD    — render all N videos concurrently (one worker per video).
  3. UPLOAD   — upload every (video × channel) pair concurrently.

Why this shape: post SELECTION is serialized so two parallel builds never grab
the same post (same post → same temp-file basename → corruption). Builds run in
threads because the heavy step (FFmpeg) is a subprocess that runs truly in
parallel. Uploads run in threads because they're network-I/O bound.

Usage:
    python run_parallel.py                  # daily_quota Shorts → all channels
    python run_parallel.py --limit 3        # 3 Shorts → all 4 channels (12 uploads)
    python run_parallel.py --no-upload      # build only (safe smoke test)
    python run_parallel.py --privacy private
    python run_parallel.py --channel rg1    # restrict to one channel
    python run_parallel.py --upload-workers 6   # cap simultaneous uploads

Each channel must be authorized once first:  python scripts/channels.py login <name>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (BarColumn, DownloadColumn, Progress, SpinnerColumn,
                           TextColumn, TimeElapsedColumn, TransferSpeedColumn)
from rich.live import Live
from rich.table import Table

from src import channels as ch
from src import moderation
from src import pipeline as pl
from src import reddit_fetch as rf
from src import youtube_upload as yt
from src.utils import (ROOT, ensure_dirs, load_config, log, log_upload,
                       record_used, recent_ids, slugify)

# The dashboard uses box-drawing/emoji glyphs; force UTF-8 so it renders on a
# default Windows console (cp1252) or a redirected pipe instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - older/odd streams: best effort
        pass

# log_upload does a read-modify-write of one JSON file; serialize the parallel
# upload workers so concurrent finishes don't clobber the dashboard's feed.
_LOG_LOCK = threading.Lock()


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


class Dashboard:
    """A single live renderable: a header banner + the build and upload tables.

    Worker threads mutate the two Progress objects (their updates are
    thread-safe); Live re-renders this object every tick, so __rich__ always
    reflects the current state.
    """

    def __init__(self, console: Console, n_videos: int, n_channels: int,
                 do_upload: bool):
        self.console = console
        self.n_videos = n_videos
        self.n_channels = n_channels
        self.do_upload = do_upload
        self.phase = "SELECT"
        self.start = time.monotonic()

        self.build = Progress(
            SpinnerColumn(finished_text="[green]✓"),
            TextColumn("[bold cyan]{task.fields[name]}", justify="left"),
            BarColumn(bar_width=20),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[stage]}"),
            console=console, expand=False,
        )
        self.upload = Progress(
            SpinnerColumn(finished_text="[green]✓"),
            TextColumn("[bold magenta]{task.fields[name]}", justify="left"),
            BarColumn(bar_width=16),
            DownloadColumn(),
            TransferSpeedColumn(),
            TextColumn("{task.fields[stage]}"),
            console=console, expand=False,
        )

    @staticmethod
    def _fmt_dur(secs: float | None) -> str:
        if secs is None:
            return "—"
        secs = int(secs)
        return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"

    def _header(self) -> Panel:
        elapsed = int(time.monotonic() - self.start)
        b_done = sum(1 for t in self.build.tasks if t.finished)
        u_done = sum(1 for t in self.upload.tasks if t.finished)
        line = (
            f"[bold]Phase:[/] [yellow]{self.phase:<7}[/]   "
            f"[bold]Build:[/] {b_done}/{self.n_videos}   "
        )
        if self.do_upload:
            line += f"[bold]Upload:[/] {u_done}/{self.n_videos * self.n_channels}   "
        line += f"[bold]Elapsed:[/] {elapsed // 60:d}m{elapsed % 60:02d}s"
        return Panel(line, title="🎬 Parallel Shorts Factory",
                     border_style="cyan", padding=(0, 1))

    def _telemetry(self) -> Panel | None:
        """Aggregate live upload telemetry: total throughput, concurrency, ETA."""
        tasks = list(self.upload.tasks)
        if not tasks:
            return None
        total = sum(t.total or 0 for t in tasks)
        done = sum(min(t.completed, t.total or t.completed) for t in tasks)
        finished = sum(1 for t in tasks if t.finished)
        active = [t for t in tasks if not t.finished and t.completed > 0]
        queued = sum(1 for t in tasks if not t.finished and t.completed <= 0)
        speed = sum((t.speed or 0) for t in active)  # bytes/sec, summed
        eta = (total - done) / speed if speed > 0 else None
        line = (
            f"📡 [bold green]{speed / 1e6:5.1f} MB/s[/] total   "
            f"[bold cyan]{len(active)}[/] uploading · "
            f"[bold]{queued}[/] queued · "
            f"[bold]{finished}/{len(tasks)}[/] done   "
            f"[dim]{done / 1e6:.0f}/{total / 1e6:.0f} MB[/]   "
            f"ETA [bold]{self._fmt_dur(eta)}[/]"
        )
        return Panel(line, title="Live telemetry", border_style="green",
                     padding=(0, 1))

    def __rich__(self) -> Group:
        items = [self._header(),
                 Panel(self.build, title="① Generating videos",
                       border_style="cyan", padding=(0, 1))]
        if self.do_upload:
            items.append(Panel(self.upload, title="② Uploading to channels",
                               border_style="magenta", padding=(0, 1)))
            tel = self._telemetry()
            if tel is not None:
                items.append(tel)
        return Group(*items)


def _select_posts(cfg: dict, limit: int, subreddit_override: str | None,
                  console: Console) -> list[tuple]:
    """Pick `limit` DISTINCT posts (each with enough comments). Serialized on
    purpose so parallel builds never collide on the same post/basename."""
    run = cfg["run"]
    rcfg = cfg["reddit"]
    min_comments = rcfg.get("min_comments", 3)
    skip = set(recent_ids(run["history_file"], run.get("cooldown", 5)))

    selected: list[tuple] = []
    attempts, max_attempts = 0, limit * 12 + 12
    # A ticking elapsed timer + attempt/found counters make it obvious the
    # (often-slow) pullpush scrape is alive and working, not frozen.
    with Progress(SpinnerColumn(),
                  TextColumn("[bold]{task.description}"),
                  TextColumn("· [cyan]{task.completed:.0f}/{task.total:.0f} found"),
                  TextColumn("· attempt [yellow]{task.fields[attempt]}"),
                  TimeElapsedColumn(),
                  console=console, transient=True) as sp:
        t = sp.add_task("Scouting Reddit…", total=limit, attempt=0)
        while len(selected) < limit and attempts < max_attempts:
            attempts += 1
            subreddit = rf.pick_subreddit(rcfg, subreddit_override)
            sp.update(t, description=f"Scouting r/{subreddit}", attempt=attempts)
            try:
                post, comments = rf.fetch_post_with_comments(
                    subreddit, rcfg, exclude=skip)
            except Exception as e:  # noqa: BLE001
                log.warning("scout error on r/%s: %s", subreddit, e)
                sp.update(t, description=f"r/{subreddit}: error — retrying")
                continue
            if not post:
                sp.update(t, description=f"r/{subreddit}: no usable post — retrying")
                continue
            if len(comments) < min_comments:
                sp.update(t, description=f"r/{subreddit}: too few comments — retrying")
                continue
            selected.append((subreddit, post, comments))
            skip.add(post.id)
            sp.update(t, advance=1, description=f"r/{subreddit}: got one!")
            console.print(f"  [green]✓[/] r/{subreddit}: "
                          f"[white]{_short(post.title, 60)}[/] "
                          f"[dim]({len(comments)} comments)[/]")
    return selected


def _build_one(post, comments, cfg, out_path, progress: Progress, tid) -> str:
    def cb(frac: float, label: str) -> None:
        progress.update(tid, completed=frac * 100, stage=f"[dim]{label}")
    progress.update(tid, stage="[dim]starting")
    pl.build_reddit_short(post, comments, cfg, out_path, on_progress=cb)
    return out_path


def _upload_one(out_path, meta, yt_cfg, token_file, secrets_file, channel,
                progress: Progress, tid) -> str:
    def cb(done: int, total: int) -> None:
        progress.update(tid, completed=done, total=max(total, 1), stage="[dim]uploading")
    url = yt.upload(out_path, meta, yt_cfg, token_file=token_file,
                    secrets_file=secrets_file, on_progress=cb)
    with _LOG_LOCK:
        log_upload(channel, out_path, url, meta, yt_cfg.get("privacy", "public"))
    return url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parallel Reddit → YouTube Shorts (build N, upload to all)")
    parser.add_argument("--subreddit", help="force a single subreddit")
    parser.add_argument("--limit", type=int,
                        help="how many Shorts to build (default: run.daily_quota)")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"])
    parser.add_argument("--no-upload", action="store_true",
                        help="build only; skip all uploads (safe smoke test)")
    parser.add_argument("--channel", default="all",
                        help="channel name or 'all' (default: all)")
    parser.add_argument("--upload-workers", type=int,
                        help="max simultaneous uploads (default: all at once)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    console = Console()
    cfg = load_config(args.config)
    if args.privacy:
        cfg["youtube"]["privacy"] = args.privacy
    run = cfg["run"]
    limit = args.limit if args.limit is not None else run["daily_quota"]
    do_upload = not args.no_upload

    channels = ch.load_channels(cfg)
    targets = ch.resolve_targets(channels, args.channel)
    if targets is None:
        return 1

    if do_upload:
        # Verify each channel's token can actually refresh NOW (catches expired /
        # revoked / Google-restricted accounts) before we waste time building.
        # Healthy tokens get refreshed here so the upload phase won't stall.
        console.print("[dim]Checking channel authorization…[/]")
        healthy, skipped = [], []
        for t in targets:
            status, detail = ch.token_health(channels, t)
            if status == "ok":
                healthy.append(t)
            else:
                skipped.append((t, status, detail))
        for name, status, detail in skipped:
            console.print(f"[yellow]⚠ Skipping {name} [[{status}]][/] — {detail}")
        targets = healthy
        if not targets:
            console.print("[yellow]No usable channels — building only "
                          "(use --no-upload to silence this).[/]")
            do_upload = False
        else:
            console.print(f"[green]Will upload to {len(targets)} channel(s): "
                          f"{', '.join(targets)}[/]")

    ensure_dirs(run["output_dir"], run["temp_dir"], "state", "assets/backgrounds")

    console.rule("[bold cyan]① SELECT[/] — choosing distinct posts")
    selected = _select_posts(cfg, limit, args.subreddit, console)
    if not selected:
        console.print("[red]No suitable posts found. Aborting.[/]")
        return 1
    console.print(f"[bold green]Selected {len(selected)} post(s).[/] "
                  f"Building in parallel"
                  + (f", then uploading to {len(targets)} channel(s) "
                     f"({len(selected) * len(targets)} uploads)…"
                     if do_upload else " (no upload)…"))

    dash = Dashboard(console, len(selected), len(targets) if do_upload else 0,
                     do_upload)

    # Live redirects stdout/stderr above the bars, but the shared logger grabbed
    # the real stderr at import — quiet routine INFO lines so they don't glitch
    # the display; failures still surface in the final summary.
    prev_level = log.level
    log.setLevel(logging.ERROR)

    built: list[tuple] = []          # (subreddit, post, out_path)
    upload_results: list[dict] = []  # {video, channel, ok, url|error}

    try:
        with Live(dash, console=console, refresh_per_second=12,
                  redirect_stdout=False, redirect_stderr=False):
            # ---- PHASE 2: build all videos concurrently ----
            dash.phase = "BUILD"
            build_meta = {}
            with ThreadPoolExecutor(max_workers=max(len(selected), 1),
                                    thread_name_prefix="build") as ex:
                futs = {}
                for subreddit, post, comments in selected:
                    base = slugify(f"{subreddit}_{post.id}")
                    out_path = f"{run['output_dir']}/{base}.mp4"
                    tid = dash.build.add_task("", name=_short(base, 22),
                                              total=100, stage="[dim]queued")
                    build_meta[tid] = (subreddit, post, out_path)
                    futs[ex.submit(_build_one, post, comments, cfg, out_path,
                                   dash.build, tid)] = tid
                for fut in as_completed(futs):
                    tid = futs[fut]
                    subreddit, post, out_path = build_meta[tid]
                    try:
                        fut.result()
                        dash.build.update(tid, completed=100, stage="[green]✓ built")
                        built.append((subreddit, post, out_path))
                        record_used(run["history_file"], post.id)
                    except Exception as e:  # noqa: BLE001
                        dash.build.update(tid, completed=100,
                                          stage=f"[red]✗ {type(e).__name__}")
                        log.error("build failed for %s: %s\n%s", post.id, e,
                                  traceback.format_exc())

            # ---- PHASE 3: upload every (video × channel) concurrently ----
            if do_upload and built:
                dash.phase = "UPLOAD"
                allow_nsfw = bool(cfg["reddit"].get("allow_nsfw", False))
                jobs = []
                for subreddit, post, out_path in built:
                    # Final NSFW guard: never upload adult content (defense in
                    # depth — selection already filters, this is the last line).
                    if not allow_nsfw and moderation.post_is_nsfw(
                            post.title, getattr(post, "body", ""),
                            getattr(post, "nsfw", False)):
                        log.error("BLOCKED NSFW — not uploading %s: '%s'",
                                  Path(out_path).name, post.title[:60])
                        upload_results.append({"video": Path(out_path).name,
                                               "channel": "(all)", "ok": False,
                                               "error": "blocked NSFW"})
                        continue
                    meta = {"title": post.title, "subreddit": subreddit,
                            "source_url": post.url}
                    for channel in targets:
                        jobs.append((channel, subreddit, post, out_path, meta))
                workers = args.upload_workers or len(jobs)
                with ThreadPoolExecutor(max_workers=max(workers, 1),
                                        thread_name_prefix="upload") as ex:
                    futs = {}
                    up_meta = {}
                    for channel, subreddit, post, out_path, meta in jobs:
                        token_file, secrets_file, yt_cfg = ch.channel_cfg(
                            channels, channel, cfg["youtube"])
                        size = os.path.getsize(out_path)
                        name = f"{_short(Path(out_path).stem, 14)}→{channel}"
                        tid = dash.upload.add_task("", name=name, total=max(size, 1),
                                                   stage="[dim]queued")
                        up_meta[tid] = (channel, Path(out_path).name)
                        futs[ex.submit(_upload_one, out_path, meta, yt_cfg,
                                       token_file, secrets_file, channel,
                                       dash.upload, tid)] = tid
                    for fut in as_completed(futs):
                        tid = futs[fut]
                        channel, vname = up_meta[tid]
                        try:
                            url = fut.result()
                            dash.upload.update(tid, stage=f"[green]✓ {_short(url, 34)}")
                            upload_results.append({"video": vname, "channel": channel,
                                                   "ok": True, "url": url})
                        except Exception as e:  # noqa: BLE001
                            dash.upload.update(tid, stage=f"[red]✗ {type(e).__name__}")
                            upload_results.append({"video": vname, "channel": channel,
                                                   "ok": False, "error": str(e)})
    finally:
        log.setLevel(prev_level or logging.INFO)

    # ---- summary ----
    console.rule("[bold]Summary[/]")
    console.print(f"[bold]Built:[/] {len(built)}/{len(selected)} videos")
    if do_upload:
        ok = sum(1 for r in upload_results if r["ok"])
        table = Table(show_header=True, header_style="bold")
        table.add_column("Video"); table.add_column("Channel")
        table.add_column("Result")
        for r in sorted(upload_results, key=lambda x: (x["video"], x["channel"])):
            res = (f"[green]{r['url']}[/]" if r["ok"]
                   else f"[red]FAILED: {r.get('error', '')[:50]}[/]")
            table.add_row(_short(r["video"], 30), r["channel"], res)
        console.print(table)
        console.print(f"[bold]Uploads:[/] [green]{ok} ok[/] / "
                      f"[red]{len(upload_results) - ok} failed[/]")
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
