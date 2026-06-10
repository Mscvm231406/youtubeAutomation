"""Manage the set of YouTube channels Shorts are posted to.

A channel = a name + its own OAuth token file. Adding one is two steps that
this tool does together: record the channel, then open the browser ONCE so you
can pick that channel at Google's account/channel picker (its identity is then
baked into the token file and every later run is unattended).

All channels live under whatever Google account you sign in as and share the
one client_secret.json.

Examples
--------
  # See configured channels + whether each is authorized:
  .venv\\Scripts\\python.exe scripts\\channels.py list

  # Add a channel and authorize it now (opens the browser — pick the channel):
  .venv\\Scripts\\python.exe scripts\\channels.py add second --login

  # Add with a per-channel privacy override:
  .venv\\Scripts\\python.exe scripts\\channels.py add promos --privacy unlisted --login

  # (Re)authorize an existing channel (e.g. token revoked, or picked wrong one):
  .venv\\Scripts\\python.exe scripts\\channels.py login second --relogin

  # Stop posting to a channel (keeps the token file unless --delete-token):
  .venv\\Scripts\\python.exe scripts\\channels.py remove second
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import channels as ch                          # noqa: E402
from src import youtube_upload as yt                    # noqa: E402
from src.utils import ROOT, load_config, log            # noqa: E402

# Keep console output safe on a default Windows (cp1252) terminal / pipe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass


def _authorize(name: str, token_file: str, relogin: bool,
               secrets_file: str | None = None) -> bool:
    """Run the OAuth flow for a channel, creating/refreshing its token file.

    The browser picker only appears when the token doesn't already exist (or
    --relogin removed it). secrets_file selects the Cloud project (None →
    shared default). Returns True on success.
    """
    tf = ROOT / token_file
    if relogin and tf.exists():
        tf.unlink()
        log.info("Removed existing token %s — you'll re-pick the channel.", token_file)
    if tf.exists():
        log.info("%s already authorized (%s). Use --relogin to re-pick.",
                 name, token_file)
        return True
    if secrets_file and not (ROOT / secrets_file).exists():
        log.error("client_secret %r not found (relative to project root). "
                  "Download it from the Cloud project and put it there.", secrets_file)
        return False
    log.info("Opening browser to authorize %r → %s", name, token_file)
    log.info("⚠  Sign in as the account that owns this channel, then pick it at "
             "the channel chooser.")
    try:
        service = yt._get_service(token_file, secrets_file)
        title = _channel_title(service)
        log.info("✓ Authorized %r%s → token saved to %s",
                 name, f" as “{title}”" if title else "", token_file)
        return True
    except FileNotFoundError as e:
        log.error("%s", e)
        return False
    except Exception as e:  # noqa: BLE001
        log.error("Authorization failed for %r: %s", name, e)
        return False


def _channel_title(service) -> str | None:
    """Best-effort fetch of the signed-in channel's title (may fail on the
    upload-only scope — that's fine, it's only for confirmation)."""
    try:
        resp = service.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if items:
            return items[0]["snippet"]["title"]
    except Exception:  # noqa: BLE001
        return None
    return None


def cmd_list(args, channels: dict) -> int:
    if not channels:
        log.info("No channels configured. Add one: channels.py add <name> --login")
        return 0
    print(f"{'CHANNEL':<14} {'AUTHORIZED':<11} TOKEN FILE")
    for name, entry in channels.items():
        entry = entry or {}
        token_file = entry.get("token_file") or ch.default_token_file(name)
        authed = "yes" if (ROOT / token_file).exists() else "NO (run login)"
        extras = " ".join(f"{k}={v}" for k, v in entry.items() if k != "token_file")
        print(f"{name:<14} {authed:<11} {token_file}"
              + (f"   [{extras}]" if extras else ""))
    return 0


def cmd_doctor(args, channels: dict) -> int:
    """Check each channel's token health (refreshes good ones; flags dead ones)."""
    if not channels:
        log.info("No channels configured.")
        return 0
    print(f"{'CHANNEL':<10} {'STATUS':<13} DETAIL")
    bad = 0
    for name in channels:
        status, detail = ch.token_health(channels, name)
        bad += (status != "ok")
        mark = "[ ok ]" if status == "ok" else "[FAIL]"
        print(f"{name:<10} {mark} {status:<11} {detail}")
    if bad:
        print(f"\n{bad} channel(s) need attention.")
        print("  - 'restricted'   : the Google account is on hold; sign into that")
        print("    account at https://myaccount.google.com to clear it, then re-auth.")
        print("  - 'needs_reauth' : run  channels.py login <name> --relogin")
    else:
        print("\nAll channels healthy.")
    return 0


def cmd_add(args, channels: dict) -> int:
    name = args.name
    if name in channels and not args.force:
        log.error("Channel %r already exists. Use --force to overwrite, or "
                  "'login %s' to (re)authorize it.", name, name)
        return 1
    entry = {"token_file": args.token_file or ch.default_token_file(name)}
    if args.client_secret:
        entry["client_secret"] = args.client_secret
    if args.privacy:
        entry["privacy"] = args.privacy
    channels[name] = entry
    ch.save_channels(channels)
    log.info("Added channel %r → %s%s", name, entry["token_file"],
             f" (client_secret={args.client_secret})" if args.client_secret else "")
    if args.login:
        ok = _authorize(name, entry["token_file"], relogin=False,
                        secrets_file=entry.get("client_secret"))
        return 0 if ok else 1
    log.info("Not authorized yet. Run: channels.py login %s", name)
    return 0


def cmd_login(args, channels: dict) -> int:
    name = args.name
    if name not in channels:
        log.error("No channel %r. Add it first: channels.py add %s", name, name)
        return 1
    entry = channels[name] or {}
    token_file = entry.get("token_file") or ch.default_token_file(name)
    ok = _authorize(name, token_file, relogin=args.relogin,
                    secrets_file=entry.get("client_secret"))
    if ok:
        # Close the loop: confirm the fresh token actually refreshes (an account
        # still under a Google restriction will re-authorize but fail here).
        status, detail = ch.token_health(channels, name)
        if status == "ok":
            log.info("✓ %s is healthy and ready to upload.", name)
        else:
            log.warning("⚠ %s re-authorized but is still [%s]: %s", name, status, detail)
            log.warning("  The Google account hold isn't cleared yet — resolve it "
                        "at https://myaccount.google.com (sign in as that account).")
            return 1
    return 0 if ok else 1


def cmd_remove(args, channels: dict) -> int:
    name = args.name
    if name not in channels:
        log.error("No channel %r to remove.", name)
        return 1
    token_file = (channels[name] or {}).get("token_file") or ch.default_token_file(name)
    del channels[name]
    ch.save_channels(channels)
    log.info("Removed channel %r — Shorts will no longer post there.", name)
    if args.delete_token:
        tf = ROOT / token_file
        if tf.exists():
            tf.unlink()
            log.info("Deleted token file %s", token_file)
    else:
        log.info("Token file %s kept (use --delete-token to remove it).", token_file)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Manage YouTube post-target channels")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show configured channels and auth status")
    sub.add_parser("doctor", help="check each channel's token health (refresh test)")

    p_add = sub.add_parser("add", help="add a channel (optionally authorize now)")
    p_add.add_argument("name")
    p_add.add_argument("--token-file", help="custom token path (default: auto)")
    p_add.add_argument("--client-secret",
                       help="path to this channel's OAuth client_secret.json "
                            "(for a separate Cloud project / its own quota; "
                            "default: the shared client_secret.json)")
    p_add.add_argument("--privacy", choices=["private", "unlisted", "public"],
                       help="per-channel privacy override")
    p_add.add_argument("--login", action="store_true",
                       help="open the browser to authorize it right away")
    p_add.add_argument("--force", action="store_true",
                       help="overwrite an existing entry of the same name")

    p_login = sub.add_parser("login", help="authorize / re-pick a channel")
    p_login.add_argument("name")
    p_login.add_argument("--relogin", action="store_true",
                         help="discard the existing token and pick the channel again")

    p_rm = sub.add_parser("remove", help="stop posting to a channel")
    p_rm.add_argument("name")
    p_rm.add_argument("--delete-token", action="store_true",
                      help="also delete its cached OAuth token file")

    args = ap.parse_args()

    cfg = load_config(args.config)
    channels = ch.load_channels(cfg)

    return {
        "list": cmd_list,
        "doctor": cmd_doctor,
        "add": cmd_add,
        "login": cmd_login,
        "remove": cmd_remove,
    }[args.cmd](args, channels)


if __name__ == "__main__":
    sys.exit(main())
