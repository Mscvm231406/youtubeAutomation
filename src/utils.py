"""Shared helpers: config loading, paths, JSON state, logging."""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

# Common winget install location, used as a fallback if FFmpeg isn't on PATH yet
# (winget adds it to PATH, but already-open shells won't see it until restarted).
_WINGET_FFMPEG = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
)


def _resolve_binary(name: str, env_var: str) -> str:
    """Find an executable by env override → PATH → known winget dir → bare name."""
    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    if _WINGET_FFMPEG.exists():
        for exe in _WINGET_FFMPEG.rglob(f"{name}.exe"):
            return str(exe)
    return name  # last resort; will raise a clear error if truly missing


def ffmpeg_bin() -> str:
    return _resolve_binary("ffmpeg", "FFMPEG_BIN")


def ffprobe_bin() -> str:
    return _resolve_binary("ffprobe", "FFPROBE_BIN")


def get_logger(name: str = "shorts") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


log = get_logger()


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    with open(ROOT / path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def ensure_dirs(*paths: str | Path) -> None:
    for p in paths:
        Path(ROOT / p).mkdir(parents=True, exist_ok=True)


# --- processed-post state (dedupe) ---

def load_processed(state_file: str | Path) -> set[str]:
    p = ROOT / state_file
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def mark_processed(state_file: str | Path, post_id: str) -> None:
    p = ROOT / state_file
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = load_processed(state_file)
    seen.add(post_id)
    p.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


# --- usage history (recency cooldown: repeats OK, but not back-to-back) ---

def load_history(history_file: str | Path) -> list[str]:
    """Ordered list of post IDs turned into videos (oldest → newest)."""
    p = ROOT / history_file
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def recent_ids(history_file: str | Path, cooldown: int) -> set[str]:
    """The last `cooldown` DISTINCT post IDs — these are skipped when choosing
    the next post, so the same post never airs within `cooldown` videos."""
    if cooldown <= 0:
        return set()
    distinct: list[str] = []
    for pid in reversed(load_history(history_file)):
        if pid not in distinct:
            distinct.append(pid)
        if len(distinct) >= cooldown:
            break
    return set(distinct)


def record_used(history_file: str | Path, post_id: str, keep: int = 1000) -> None:
    """Append a post ID to the usage history (trimmed to the last `keep`)."""
    p = ROOT / history_file
    hist = load_history(history_file)
    hist.append(post_id)
    if len(hist) > keep:
        hist = hist[-keep:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, indent=2), encoding="utf-8")


UPLOAD_LOG = "state/upload_log.json"


def log_upload(channel: str, video: str | Path, url: str, meta: dict,
               privacy: str, log_file: str | Path = UPLOAD_LOG) -> None:
    """Append one timestamped upload event to the dashboard's feed.

    Append-only list so the dashboard can show recent uploads per channel and
    estimate daily quota usage. Never raises — logging must not break a run.
    """
    p = ROOT / log_file
    try:
        events = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if not isinstance(events, list):
            events = []
    except (json.JSONDecodeError, OSError):
        events = []
    events.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "channel": channel,
        "video": Path(video).name,
        "url": url,
        "title": meta.get("title", ""),
        "subreddit": meta.get("subreddit", ""),
        "privacy": privacy,
    })
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(events, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


def slugify(text: str, maxlen: int = 60) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
    return "_".join(keep.split())[:maxlen] or "post"
