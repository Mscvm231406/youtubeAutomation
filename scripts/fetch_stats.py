"""Fetch YouTube stats (subscribers / views / revenue) for all channels and
cache them to state/analytics.json for the dashboard.

Best-effort: channels that aren't authorized for stats, or projects where the
YouTube Analytics API isn't enabled, are reported but don't fail the run — the
dashboard renders whatever data is available.

    .venv\\Scripts\\python.exe scripts\\fetch_stats.py            # all channels
    .venv\\Scripts\\python.exe scripts\\fetch_stats.py rg1 rg2    # specific ones
    .venv\\Scripts\\python.exe scripts\\fetch_stats.py --days 90
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import channels as ch                    # noqa: E402
from src import quota_usage as qu                  # noqa: E402
from src import youtube_stats as ys               # noqa: E402
from src.utils import ROOT, load_config, log       # noqa: E402

CACHE = ROOT / "state" / "analytics.json"


def _account_label(secret: str | None) -> str:
    """Short project label from a client-secret filename (client_secret_cm.json
    -> 'cm'); mirrors dashboard._account_of so the keys line up."""
    stem = Path(secret or "").stem
    if stem.startswith("client_secret_"):
        return stem[len("client_secret_"):] or "default"
    return "default"


def _build_quota_live(registry: dict) -> dict:
    """Real per-Cloud-project YouTube Data API quota usage via Cloud Monitoring.

    Keyed by the short account label (cm/rg/…). One call per project (channels
    that share a client_secret share a quota), using the first authorized
    channel of that project. Each value is the quota_usage.fetch_quota_usage
    dict (used/limit/error) plus the channel it was read through.
    """
    out: dict = {}
    for name, entry in registry.items():
        entry = entry or {}
        if not ch.is_authorized(registry, name):
            continue
        acct = _account_label(entry.get("client_secret"))
        if acct in out:                 # one authorized channel per project suffices
            continue
        token = entry.get("token_file") or ch.default_token_file(name)
        res = qu.fetch_quota_usage(token, entry.get("client_secret"))
        res["via_channel"] = name
        out[acct] = res
        if res.get("used") is not None:
            log.info("  quota %s (%s): %s units used today",
                     acct, res.get("project_id"), res["used"])
        else:
            log.warning("  quota %s (%s): %s", acct, res.get("project_id"),
                        res.get("error"))
    return out


def _vid_id(url: str) -> str:
    """Extract the YouTube video id from a shorts/watch URL."""
    url = str(url or "")
    for sep in ("/shorts/", "watch?v=", "youtu.be/"):
        if sep in url:
            return url.split(sep, 1)[1].split("&")[0].split("?")[0].strip("/")
    return url.rstrip("/").rsplit("/", 1)[-1]


def _build_leaderboard(registry: dict, top: int = 15) -> list[dict]:
    """Rank every channel's REAL uploaded videos by view count, with thumbnails.

    Pulls each authorized channel's actual uploads from YouTube (not just the
    automation-logged ones), so manually-posted videos and older uploads show
    up too. Enriches each with the Reddit title/subreddit/source-URL from
    upload_log.json when the video id matches a logged upload.
    """
    log_path = ROOT / "state" / "upload_log.json"
    events = []
    try:
        events = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    except (json.JSONDecodeError, OSError):
        events = []

    # video_id -> the logged upload (gives us the Reddit title/subreddit/url + local mp4 link).
    logged = {}
    for e in events:
        vid = _vid_id(e.get("url"))
        if vid and vid not in logged:
            logged[vid] = e

    rows = []
    for name in registry:
        if not ch.is_authorized(registry, name):
            continue
        entry = registry[name] or {}
        token = entry.get("token_file") or ch.default_token_file(name)
        for v in ys.fetch_channel_videos(name, token, entry.get("client_secret"), limit=50):
            vid = v["video_id"]
            meta = logged.get(vid, {})
            rows.append({
                "video_id": vid,
                # keep the exact logged URL so the dashboard can map it to a local mp4;
                # otherwise link to the live short.
                "url": meta.get("url") or f"https://youtube.com/shorts/{vid}",
                "channel": name,
                # prefer the descriptive Reddit title we logged; fall back to YouTube's.
                "title": meta.get("title") or v.get("title") or vid,
                "subreddit": meta.get("subreddit", ""),
                "views": v.get("views", 0),
                "likes": v.get("likes"),
                "thumb": v.get("thumb") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

    rows.sort(key=lambda r: r["views"], reverse=True)
    return rows[:top]


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch YouTube stats for the dashboard")
    ap.add_argument("channels", nargs="*", help="specific channel names (default: all)")
    ap.add_argument("--days", type=int, default=30, help="time-series window (default 30)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    registry = ch.load_channels(load_config(args.config))
    data = ys.fetch_all(registry, only=args.channels or None, days=args.days)

    # --- top-videos leaderboard: real per-video views (Data API) + thumbnails ---
    data["leaderboard"] = _build_leaderboard(registry)

    # --- real API-quota usage per Cloud project (Cloud Monitoring) ---
    data["quota_live"] = _build_quota_live(registry)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    t = data["totals"]
    log.info("Stats cached → %s", CACHE)
    log.info("  %s subscribers · %s lifetime views · $%.2f revenue (%d channels)",
             t["subscribers"], t["views"], t["revenue"], len(data["channels"]))

    # Surface the most common actionable problem once, clearly.
    kinds = {er["kind"] for c in data["channels"] for er in c.get("errors", [])}
    if "scope" in kinds:
        log.warning("Some channels need re-auth for stats: "
                    "python scripts/channels.py login <name> --relogin")
    if "api_disabled" in kinds:
        log.warning("Enable the 'YouTube Analytics API' in each Cloud project's "
                    "console (APIs & Services → Library) for the time-series graphs.")

    # Quota (Cloud Monitoring) setup hints.
    qkinds = {q.get("error") for q in data["quota_live"].values() if q.get("error")}
    if "api_disabled" in qkinds:
        log.warning("Enable the 'Cloud Monitoring API' per project for real API-quota "
                    "usage: console.cloud.google.com/apis/library/monitoring.googleapis.com")
    if "scope" in qkinds:
        log.warning("Re-auth channels for the monitoring.read scope: "
                    "python scripts/channels.py login <name> --relogin")
    if "billing" in qkinds:
        log.warning("Cloud Monitoring reads require billing enabled on the project: "
                    "console.cloud.google.com/billing/enable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
