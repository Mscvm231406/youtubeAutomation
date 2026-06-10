"""Stage 6 — upload to YouTube via the Data API v3.

First run opens a browser for OAuth consent and caches a refresh token, so
subsequent runs are fully unattended. Returns the new video's URL.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .utils import ROOT, env, log

# Reports upload advancement as (bytes_uploaded, bytes_total). Used by the
# parallel orchestrator to drive a live per-channel progress bar.
ProgressFn = Callable[[int, int], None]

# Resumable-upload chunk size when progress reporting is requested. 4 MiB is a
# multiple of 256 KiB (a Google requirement) and gives smooth bar movement
# without excessive request overhead.
_PROGRESS_CHUNK = 4 * 1024 * 1024

# Upload + read-only stats + analytics (views/subs over time) + revenue +
# Cloud Monitoring read (real API-quota usage for the dashboard). Tokens
# authorized before a scope was added lack it and must be re-authorized
# (channels.py login --relogin) to grant the rest.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    "https://www.googleapis.com/auth/monitoring.read",
]


def _get_credentials(token_file: str | Path | None = None,
                     secrets_file: str | Path | None = None):
    """Load (or interactively obtain) OAuth credentials for one channel.

    token_file selects the CHANNEL (each has its own cached token, chosen at the
    consent-screen picker); secrets_file selects the Cloud PROJECT / quota.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Google sometimes returns scopes in a different order / adds openid; relax
    # so re-consent with the broadened scope set doesn't raise on a mismatch.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

    if token_file is None:
        token_file = env("YOUTUBE_TOKEN_FILE", "state/youtube_token.json")
    token_file = ROOT / token_file
    if secrets_file is None:
        secrets_file = env("YOUTUBE_CLIENT_SECRETS", "client_secret.json")
    secrets_file = ROOT / secrets_file

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets_file.exists():
                raise FileNotFoundError(
                    f"Missing OAuth client secrets at {secrets_file}. "
                    f"Download from Google Cloud Console (Desktop app)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _get_service(token_file: str | Path | None = None,
                 secrets_file: str | Path | None = None):
    from googleapiclient.discovery import build

    creds = _get_credentials(token_file, secrets_file)

    return build("youtube", "v3", credentials=creds)


def token_status(token_file: str | Path | None = None,
                 secrets_file: str | Path | None = None) -> tuple[str, str]:
    """Check a channel's cached OAuth token WITHOUT any interactive browser flow.

    Returns (status, detail):
      'ok'           — token valid (refreshed & re-saved here if it was expired)
      'missing'      — no token file yet (never authorized)
      'restricted'   — Google has restricted the account; refresh is denied
      'needs_reauth' — token expired/revoked and cannot be refreshed
      'error'        — unreadable token or an unexpected failure

    Safe to call for every channel at startup: it never blocks on a browser.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if token_file is None:
        token_file = env("YOUTUBE_TOKEN_FILE", "state/youtube_token.json")
    tf = ROOT / token_file
    if not tf.exists():
        return "missing", "no token — run: channels.py login <name>"
    try:
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
    except Exception as e:  # noqa: BLE001
        return "error", f"unreadable token ({type(e).__name__})"
    if creds.valid:
        return "ok", ""
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tf.write_text(creds.to_json(), encoding="utf-8")  # cache the refresh
            return "ok", ""
        except RefreshError as e:
            msg = " ".join(str(a) for a in e.args).lower()
            if "account restricted" in msg or "servicerestricted" in msg:
                return ("restricted",
                        "Google account restricted — clear the hold on that "
                        "account, then: channels.py login <name> --relogin")
            if any(k in msg for k in ("invalid_grant", "revoked", "expired")):
                return ("needs_reauth",
                        "token expired/revoked — channels.py login <name> --relogin")
            return "needs_reauth", str(e)[:140]
        except Exception as e:  # noqa: BLE001
            return "error", f"{type(e).__name__}: {str(e)[:120]}"
    return "needs_reauth", "no refresh token — channels.py login <name> --relogin"


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _title_template(cfg: dict, subreddit: str) -> str:
    """Pick a subreddit-specific title template, else default, else legacy."""
    tmap = cfg.get("title_templates")
    if isinstance(tmap, dict):
        return tmap.get(subreddit) or tmap.get("default") or "{title} #Shorts"
    return cfg.get("title_template", "{title} #Shorts")


def _compose_title(cfg: dict, subreddit: str, raw_title: str) -> str:
    """Build the final title, trimming only the post-title text so the template's
    suffix (e.g. ' 🤔 #Shorts') always survives the length cap."""
    template = _title_template(cfg, subreddit)
    n = cfg.get("title_max_len", 90)
    full = template.format(title=raw_title, subreddit=subreddit)
    if len(full) <= n:
        return full
    # Length cap hit: keep the fixed parts around {title}, shrink the title.
    prefix, suffix = template.split("{title}", 1)
    prefix = prefix.format(subreddit=subreddit)
    suffix = suffix.format(subreddit=subreddit)
    room = n - len(prefix) - len(suffix)
    if room < 1:  # suffix alone already too long — fall back to a plain trim
        return full[: n - 1].rstrip() + "…"
    trimmed = raw_title[: room - 1].rstrip() + "…"
    return f"{prefix}{trimmed}{suffix}"


def _hashtags(cfg: dict, subreddit: str) -> list[str]:
    """Ordered, de-duplicated hashtag words (no '#'), capped at hashtag_max.

    Order = core → common → subreddit-specific, so the first 3 (which YouTube
    surfaces above the title) are the broad-reach ones.
    """
    h = cfg.get("hashtags")
    if not isinstance(h, dict):  # legacy fallback
        pool = list(cfg.get("tags", []))
    else:
        pool = (list(h.get("core", [])) + list(h.get("common", []))
                + list(h.get("by_subreddit", {}).get(subreddit, [])))
    seen, out = set(), []
    for t in pool:
        t = str(t).lstrip("#").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    cap = cfg.get("hashtag_max", 12) if isinstance(h, dict) else 15
    return out[:cap]


def upload(video_path: str | Path, meta: dict, cfg: dict,
           token_file: str | Path | None = None,
           secrets_file: str | Path | None = None,
           on_progress: ProgressFn | None = None) -> str:
    """meta keys: title, subreddit, source_url. cfg is the `youtube` block.

    token_file picks which channel to upload to (default = the env/legacy token);
    secrets_file picks the Cloud project / quota (default = shared client_secret).
    on_progress (optional) is called with (bytes_uploaded, bytes_total) as the
    file streams up; supplying it switches to a chunked resumable upload.
    """
    from googleapiclient.http import MediaFileUpload

    service = _get_service(token_file, secrets_file)

    subreddit = meta["subreddit"]
    hashtags = _hashtags(cfg, subreddit)
    hashtag_line = " ".join(f"#{t}" for t in hashtags)

    title = _compose_title(cfg, subreddit, meta["title"])
    desc_tmpl = cfg["description_template"]
    if "{hashtags}" not in desc_tmpl:        # back-compat with old templates
        desc_tmpl = desc_tmpl.rstrip() + "\n\n{hashtags}"
    description = desc_tmpl.format(
        title=meta["title"], subreddit=subreddit,
        source_url=meta.get("source_url", ""), hashtags=hashtag_line,
    )

    privacy = cfg.get("privacy", "private")
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": cfg.get("made_for_kids", False),
    }
    if cfg.get("publish_at"):
        status["privacyStatus"] = "private"
        status["publishAt"] = cfg["publish_at"]

    body = {
        "snippet": {
            "title": title,
            "description": _truncate(description, 4900),
            "tags": hashtags or cfg.get("tags", []),
            "categoryId": str(cfg.get("category_id", "24")),
        },
        "status": status,
    }

    # With a progress callback, stream in fixed chunks so each next_chunk()
    # returns incremental status; otherwise keep the fast single-shot upload.
    chunksize = _PROGRESS_CHUNK if on_progress else -1
    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            resumable=True, chunksize=chunksize)
    request = service.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    total = media.size() or Path(video_path).stat().st_size
    if on_progress:
        on_progress(0, total)

    log.info("Uploading %s (privacy=%s)…", Path(video_path).name, privacy)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if on_progress and status:
            on_progress(status.resumable_progress, total)
    if on_progress:
        on_progress(total, total)

    vid = response["id"]
    url = f"https://youtube.com/shorts/{vid}"
    log.info("Uploaded → %s", url)
    return url
