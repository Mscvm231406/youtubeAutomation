"""Channel registry — the set of YouTube channels Shorts get posted to.

A "channel" is just a name mapped to its own OAuth token file (the channel is
chosen at the browser consent picker the first time that token is created). All
channels share the one client_secret.json.

The registry lives in `channels.json` (machine-managed by scripts/channels.py)
so it can be edited programmatically without touching config.yaml's comments.
Resolution order, most authoritative first:

  1. channels.json (if present)
  2. config.yaml  youtube.channels  (legacy / fallback)
  3. a single built-in "main" channel using the legacy default token
"""
from __future__ import annotations

import json
from pathlib import Path

from .utils import ROOT, log

CHANNELS_FILE = ROOT / "channels.json"
DEFAULT_CHANNELS = {"main": {"token_file": "state/youtube_token.json"}}


def default_token_file(name: str) -> str:
    """Conventional token path for a channel name (main → the legacy path)."""
    if name == "main":
        return "state/youtube_token.json"
    return f"state/youtube_token_{name}.json"


def load_channels(cfg: dict | None = None) -> dict:
    """Return the channel registry as {name: {token_file, ...overrides}}."""
    if CHANNELS_FILE.exists():
        try:
            data = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Couldn't read %s (%s) — falling back to config.",
                        CHANNELS_FILE.name, e)
    if cfg:
        block = cfg.get("youtube", {}).get("channels")
        if isinstance(block, dict) and block:
            return block
    return dict(DEFAULT_CHANNELS)


def save_channels(data: dict) -> None:
    CHANNELS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def is_authorized(channels: dict, name: str) -> bool:
    """True if the channel already has a cached OAuth token (so uploading to it
    won't trigger an interactive browser flow). NOTE: only checks that the token
    FILE exists — use token_health() to also verify it can still refresh."""
    entry = channels.get(name) or {}
    token_file = entry.get("token_file") or default_token_file(name)
    return (ROOT / token_file).exists()


def token_health(channels: dict, name: str) -> tuple[str, str]:
    """Live health of a channel's OAuth token: (status, detail).

    Unlike is_authorized (file-exists only), this actually refreshes the token,
    so it catches expired/revoked/restricted accounts before a run wastes work.
    status ∈ {'ok','missing','restricted','needs_reauth','error'}. Never blocks
    on a browser. See youtube_upload.token_status for the full contract.
    """
    from . import youtube_upload as yt  # lazy: avoids importing google libs early
    entry = channels.get(name) or {}
    token_file = entry.get("token_file") or default_token_file(name)
    secrets_file = entry.get("client_secret")
    return yt.token_status(token_file, secrets_file)


def resolve_targets(channels: dict, selector: str) -> list[str] | None:
    """Map a --channel value ('all' | a name) to a list of channel names.

    Returns None (and logs) if the name isn't configured.
    """
    if selector == "all":
        return list(channels)
    if selector in channels:
        return [selector]
    log.error("Unknown channel %r. Configured: %s",
              selector, ", ".join(channels) or "(none)")
    return None


_RESERVED = ("token_file", "client_secret")


def channel_cfg(channels: dict, name: str,
                youtube_cfg: dict) -> tuple[str, str | None, dict]:
    """For a channel, return (token_file, secrets_file, effective youtube cfg).

    token_file selects the channel; secrets_file selects the Cloud project /
    quota (None → the shared default). The channel's own non-reserved keys
    (e.g. privacy) override the shared youtube settings.
    """
    entry = channels.get(name) or {}
    token_file = entry.get("token_file") or default_token_file(name)
    secrets_file = entry.get("client_secret")  # None → shared default
    merged = {**youtube_cfg,
              **{k: v for k, v in entry.items() if k not in _RESERVED}}
    return token_file, secrets_file, merged
