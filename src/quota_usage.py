"""Real YouTube Data API quota consumption via Google Cloud Monitoring.

Reads the consumer-quota usage metric
(`serviceruntime.googleapis.com/quota/rate/net_usage`) for the YouTube Data API
in a Cloud project and returns the units consumed so far in the current quota
day (which resets at midnight US/Pacific). This is the same number the Cloud
Console shows under APIs & Services → Quotas, so the dashboard can display the
authoritative usage instead of a local guess.

One-time setup, per Cloud project, before this returns real numbers:
  * Enable the **Cloud Monitoring API** (monitoring.googleapis.com) in the
    project (console.cloud.google.com/apis/library/monitoring.googleapis.com).
  * Re-authorize each channel so its OAuth token carries the `monitoring.read`
    scope (now part of youtube_upload.SCOPES):
        python scripts/channels.py login <name> --relogin

Never raises: every failure path returns an `error` code (api_disabled / scope
/ no_project / error) so the dashboard can fall back to its local estimate and
tell the user exactly what to fix.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import youtube_upload as yu
from .utils import ROOT, log

# YouTube Data API default daily quota (units). An upload costs ~1600 units, a
# list call 1–5, so the real ceiling is ~6 uploads/project/day.
DAILY_UNIT_LIMIT = 10_000

_YT_SERVICE = "youtube.googleapis.com"
_USAGE_METRIC = "serviceruntime.googleapis.com/quota/rate/net_usage"


def project_id_of(secrets_file: str | Path | None) -> str | None:
    """Read the Cloud project id from an OAuth client-secret JSON (the
    `installed.project_id` / `web.project_id` field)."""
    if not secrets_file:
        return None
    try:
        data = json.loads((ROOT / secrets_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    block = data.get("installed") or data.get("web") or {}
    return block.get("project_id")


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pacific_day_start_utc() -> datetime:
    """Start of the current quota day (midnight US/Pacific) as UTC. The YouTube
    Data API daily quota resets at midnight PT, so summing usage from that
    instant matches what the Console reports."""
    try:
        from zoneinfo import ZoneInfo
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        midnight = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001 — zoneinfo / tzdata unavailable
        # Trailing-24h fallback: slightly off vs the PT reset but still useful.
        return datetime.now(timezone.utc) - timedelta(hours=24)


def _err_kind(exc: Exception) -> str:
    s = str(exc).lower()
    if "billing" in s:
        return "billing"
    if "has not been used" in s or "is disabled" in s or "accessnotconfigured" in s:
        return "api_disabled"
    if "insufficient" in s or "scope" in s or "permission" in s or "forbidden" in s:
        return "scope"
    return "error"


def fetch_quota_usage(token_file: str, secrets_file: str | None,
                      project_id: str | None = None,
                      limit: int = DAILY_UNIT_LIMIT) -> dict:
    """YouTube Data API quota units consumed today for one Cloud project.

    Returns {"project_id", "used", "limit", "error"}; `used` is None on any
    failure (with `error` set), otherwise an int (0 when nothing's been used).
    """
    from googleapiclient.discovery import build

    pid = project_id or project_id_of(secrets_file)
    out: dict = {"project_id": pid, "used": None, "limit": limit, "error": None}
    if not pid:
        out["error"] = "no_project"
        return out
    try:
        creds = yu._get_credentials(token_file, secrets_file)
        svc = build("monitoring", "v3", credentials=creds, cache_discovery=False)

        start = _pacific_day_start_utc()
        end = datetime.now(timezone.utc) + timedelta(minutes=1)
        window = max(60, int((end - start).total_seconds()))

        # net_usage is a DELTA metric: ALIGN_SUM over the whole window collapses
        # each series to its total, then REDUCE_SUM merges any per-quota-metric
        # series into one grand total of units consumed today.
        resp = svc.projects().timeSeries().list(
            name=f"projects/{pid}",
            filter=(f'metric.type="{_USAGE_METRIC}" '
                    f'AND resource.label.service="{_YT_SERVICE}"'),
            interval_startTime=_rfc3339(start),
            interval_endTime=_rfc3339(end),
            aggregation_alignmentPeriod=f"{window}s",
            aggregation_perSeriesAligner="ALIGN_SUM",
            aggregation_crossSeriesReducer="REDUCE_SUM",
            view="FULL",
        ).execute()

        total = 0.0
        for ts in resp.get("timeSeries", []):
            for pt in ts.get("points", []):
                v = pt.get("value", {})
                total += float(v.get("int64Value") or v.get("doubleValue") or 0)
        out["used"] = int(round(total))
    except Exception as e:  # noqa: BLE001 — degrade gracefully
        out["error"] = _err_kind(e)
        log.warning("quota usage fetch failed (%s): %s", pid, str(e)[:160])
    return out
