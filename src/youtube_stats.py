"""Fetch per-channel YouTube stats for the dashboard.

Pulls lifetime totals (subscribers / views / videos) from the Data API v3 and a
30-day daily time-series (views, subscribers gained, estimated revenue) from the
YouTube Analytics API v2. Degrades gracefully: each piece is independent, so a
missing scope, a disabled Analytics API, or a non-monetized channel just leaves
that field empty rather than failing the whole fetch.

Requires the broadened OAuth scopes in youtube_upload.SCOPES — channels
authorized before stats existed must be re-authorized (channels.py login
--relogin) to grant `youtube.readonly` + the analytics scopes.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import youtube_upload as yu
from .utils import ROOT, log

_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
# Whether a video is a Short never changes once published, so cache the verdict
# (keyed by video id) to avoid re-probing youtube.com on every dashboard launch.
_SHORT_CACHE_PATH = ROOT / "state" / "shorts_cache.json"


def _dur_seconds(iso: str) -> int:
    """Parse an ISO-8601 video duration (e.g. 'PT2M56S') to seconds."""
    m = _DUR_RE.fullmatch(iso or "")
    if not m:
        return 0
    h, mn, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mn * 60 + s


def _load_short_cache() -> dict:
    try:
        return json.loads(_SHORT_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_short_cache(cache: dict) -> None:
    try:
        _SHORT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SHORT_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def _is_short(video_id: str, duration_iso: str, cache: dict | None = None) -> bool:
    """Is this video a YouTube Short? Shorts are vertical and <= 3 min.

    Primary signal: requesting youtube.com/shorts/<id> returns HTTP 200 for a
    real Short but a 3xx redirect to /watch for a regular video. Falls back to a
    duration heuristic (<=60s) if the network probe is unavailable. Verdicts are
    memoised in `cache` (the immutable short/regular status) so we probe each
    video at most once across launches.
    """
    if cache is not None and video_id in cache:
        return bool(cache[video_id])

    secs = _dur_seconds(duration_iso)
    verdict = None
    if secs > 180:                      # too long to be a Short, skip the probe
        verdict = False
    else:
        try:
            import requests
            # stream=True so a 200 (Short) doesn't pull the whole page body.
            r = requests.get(f"https://www.youtube.com/shorts/{video_id}",
                             allow_redirects=False, timeout=6, stream=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            code = r.status_code
            r.close()
            if code == 200:
                verdict = True
            elif 300 <= code < 400:
                verdict = False
        except Exception:               # noqa: BLE001 — network unavailable
            pass
        if verdict is None:             # probe inconclusive — conservative fallback
            return 0 < secs <= 60       # (don't cache an unsure verdict)

    if cache is not None:
        cache[video_id] = verdict
    return verdict


def _err_kind(exc: Exception) -> str:
    """Classify a Google API error into a short, actionable code."""
    s = str(exc).lower()
    if "insufficient" in s or "scope" in s or "forbidden" in s and "youtube.readonly" in s:
        return "scope"
    if "has not been used" in s or "is disabled" in s or "accessnotconfigured" in s:
        return "api_disabled"
    if "monet" in s:
        return "not_monetized"
    return "error"


def fetch_channel_stats(name: str, token_file: str, secrets_file: str | None,
                        days: int = 30) -> dict:
    """Return a stats dict for one channel (never raises)."""
    from googleapiclient.discovery import build

    out: dict = {"name": name, "title": name, "subscribers": None,
                 "views_total": None, "videos": None, "monetized": False,
                 "daily": [], "revenue_total": 0.0, "errors": []}
    try:
        creds = yu._get_credentials(token_file, secrets_file)
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"part": "auth", "kind": _err_kind(e), "msg": str(e)[:200]})
        return out

    # --- lifetime totals via Data API v3 (already-enabled) ---
    try:
        yt = build("youtube", "v3", credentials=creds)
        item = yt.channels().list(part="snippet,statistics", mine=True).execute()
        items = item.get("items") or []
        if items:
            snip, st = items[0]["snippet"], items[0].get("statistics", {})
            out["title"] = snip.get("title", name)
            out["subscribers"] = int(st.get("subscriberCount", 0)) if not st.get("hiddenSubscriberCount") else None
            out["views_total"] = int(st.get("viewCount", 0))
            out["videos"] = int(st.get("videoCount", 0))
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"part": "totals", "kind": _err_kind(e), "msg": str(e)[:200]})

    # --- 30-day daily time-series via Analytics API v2 ---
    end = date.today()
    start = end - timedelta(days=days - 1)
    daily = {}
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        resp = ya.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics="views,subscribersGained,subscribersLost", dimensions="day", sort="day",
        ).execute()
        for row in resp.get("rows", []):
            d, views, gained, lost = row[0], int(row[1]), int(row[2]), int(row[3])
            daily[d] = {"date": d, "views": views, "subs": gained - lost, "revenue": 0.0}
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"part": "timeseries", "kind": _err_kind(e), "msg": str(e)[:200]})

    # --- estimated revenue (needs monetary scope + monetized channel) ---
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        resp = ya.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics="estimatedRevenue", dimensions="day", sort="day", currency="USD",
        ).execute()
        for row in resp.get("rows", []):
            d, rev = row[0], float(row[1])
            daily.setdefault(d, {"date": d, "views": 0, "subs": 0, "revenue": 0.0})
            daily[d]["revenue"] = rev
            out["revenue_total"] += rev
        out["monetized"] = out["revenue_total"] > 0 or bool(resp.get("rows"))
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"part": "revenue", "kind": _err_kind(e), "msg": str(e)[:200]})

    out["daily"] = [daily[d] for d in sorted(daily)]
    return out


def fetch_video_stats(token_file: str, secrets_file: str | None,
                      video_ids: list[str]) -> dict:
    """Per-video viewCount + thumbnail via Data API v3 (videos are public, so
    any authorized channel's creds can read them). Returns {id: {...}}.

    Used for the dashboard's top-videos leaderboard. Never raises.
    """
    from googleapiclient.discovery import build

    out: dict = {}
    if not video_ids:
        return out
    try:
        creds = yu._get_credentials(token_file, secrets_file)
        yt = build("youtube", "v3", credentials=creds)
        for i in range(0, len(video_ids), 50):  # API caps id list at 50
            batch = video_ids[i:i + 50]
            resp = yt.videos().list(part="statistics,snippet",
                                    id=",".join(batch)).execute()
            for it in resp.get("items", []):
                st, sn = it.get("statistics", {}), it.get("snippet", {})
                thumbs = sn.get("thumbnails", {})
                thumb = ((thumbs.get("medium") or thumbs.get("high")
                          or thumbs.get("default") or {}).get("url"))
                out[it["id"]] = {
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st["likeCount"]) if "likeCount" in st else None,
                    "comments": int(st["commentCount"]) if "commentCount" in st else None,
                    "title": sn.get("title", ""),
                    "thumb": thumb,
                }
    except Exception as e:  # noqa: BLE001
        log.warning("video stats fetch failed: %s", str(e)[:160])
    return out


def fetch_channel_videos(name: str, token_file: str, secrets_file: str | None,
                         limit: int = 50) -> list[dict]:
    """Return a channel's REAL **public Shorts** (not just automation-logged
    ones), each with viewCount/likes/thumbnail. Walks the channel's 'uploads'
    playlist via the Data API v3, then keeps only videos that are
    privacyStatus=='public' AND are actually Shorts — private/unlisted videos
    and regular long-form uploads are excluded. Never raises.

    Requires the channel's OWN creds (mine=True). Used for the leaderboard.
    """
    from googleapiclient.discovery import build

    vids: list[dict] = []
    try:
        creds = yu._get_credentials(token_file, secrets_file)
        yt = build("youtube", "v3", credentials=creds)
        chan = yt.channels().list(part="contentDetails", mine=True).execute()
        items = chan.get("items") or []
        if not items:
            return vids
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        ids: list[str] = []
        page = None
        while len(ids) < limit:
            pl = yt.playlistItems().list(
                part="contentDetails", playlistId=uploads,
                maxResults=50, pageToken=page).execute()
            ids += [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
            page = pl.get("nextPageToken")
            if not page:
                break
        ids = ids[:limit]

        short_cache = _load_short_cache()
        skipped_private = skipped_regular = 0
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            resp = yt.videos().list(part="statistics,snippet,status,contentDetails",
                                    id=",".join(batch)).execute()
            for it in resp.get("items", []):
                vid = it["id"]
                # 1) public only — drop private / unlisted
                if (it.get("status", {}).get("privacyStatus") or "").lower() != "public":
                    skipped_private += 1
                    continue
                # 2) Shorts only — drop regular long-form videos
                if not _is_short(vid, it.get("contentDetails", {}).get("duration", ""),
                                 short_cache):
                    skipped_regular += 1
                    continue
                st, sn = it.get("statistics", {}), it.get("snippet", {})
                thumbs = sn.get("thumbnails", {})
                thumb = ((thumbs.get("medium") or thumbs.get("high")
                          or thumbs.get("default") or {}).get("url"))
                vids.append({
                    "video_id": vid,
                    "channel": name,
                    "title": sn.get("title", ""),
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st["likeCount"]) if "likeCount" in st else None,
                    "thumb": thumb,
                    "published": sn.get("publishedAt"),
                })
        _save_short_cache(short_cache)
        log.info("  %s: %d public shorts (skipped %d private/unlisted, %d regular)",
                 name, len(vids), skipped_private, skipped_regular)
    except Exception as e:  # noqa: BLE001
        log.warning("channel-videos fetch failed for %s: %s", name, str(e)[:160])
    return vids


def fetch_all(channels: dict, only: list[str] | None = None, days: int = 30) -> dict:
    """Fetch stats for every (authorized) channel; returns the dashboard cache."""
    from . import channels as ch

    results = []
    for name, entry in channels.items():
        if only and name not in only:
            continue
        entry = entry or {}
        if not ch.is_authorized(channels, name):
            log.info("Skip %s (not authorized)", name)
            continue
        token_file = entry.get("token_file") or ch.default_token_file(name)
        log.info("Fetching stats for %s…", name)
        s = fetch_channel_stats(name, token_file, entry.get("client_secret"), days)
        results.append(s)
        if s["errors"]:
            for er in s["errors"]:
                log.warning("  %s/%s: %s", name, er["part"], er["kind"])

    return _aggregate(results, days)


def _aggregate(results: list[dict], days: int) -> dict:
    from datetime import datetime

    end = date.today()
    span = [(end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    series = {d: {"date": d, "views": 0, "subs": 0, "revenue": 0.0} for d in span}
    for c in results:
        for pt in c["daily"]:
            if pt["date"] in series:
                series[pt["date"]]["views"] += pt["views"]
                series[pt["date"]]["subs"] += pt["subs"]
                series[pt["date"]]["revenue"] += pt["revenue"]

    def _sum(key):
        vals = [c[key] for c in results if isinstance(c.get(key), (int, float))]
        return sum(vals)

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "channels": results,
        "daily": [series[d] for d in span],
        "totals": {
            "subscribers": _sum("subscribers"),
            "views": _sum("views_total"),
            "videos": _sum("videos"),
            "revenue": round(sum(c["revenue_total"] for c in results), 2),
            "monetized": any(c["monetized"] for c in results),
        },
    }
