"""Setup checker — verifies a fresh install is ready, and tells you what's left.

Run it any time:
    .venv\\Scripts\\python.exe scripts\\check_setup.py

It checks Python, FFmpeg, Python packages, your .env keys, the YouTube client
secret, background videos, and channel authorization — printing a PASS/TODO
checklist with the exact next step for anything missing. It never changes
anything and never uploads.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# UTF-8 + ASCII marks so output is safe on any Windows console / pipe.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

OK, TODO, WARN = "[ OK ]", "[TODO]", "[WARN]"
results: list[tuple[str, str, str]] = []  # (mark, label, hint)


def add(mark: str, label: str, hint: str = "") -> None:
    results.append((mark, label, hint))


def _load_env() -> dict:
    """Read .env into a dict without importing dotenv (so it works pre-install)."""
    env: dict[str, str] = {}
    f = ROOT / ".env"
    if not f.exists():
        return env
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 10):
        add(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        add(TODO, f"Python {v.major}.{v.minor} is too old",
            "install Python 3.10+ from python.org (tick 'Add to PATH')")


def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool) or os.environ.get(f"{tool.upper()}_BIN")
        # also accept the winget Packages location the app's resolver knows
        if not found:
            la = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            if la.exists() and any(la.rglob(f"{tool}.exe")):
                found = "winget"
        if found:
            add(OK, f"{tool} found")
        else:
            add(TODO, f"{tool} not found",
                "install FFmpeg: 'winget install Gyan.FFmpeg' then reopen the terminal")


def check_packages() -> None:
    required = {
        "praw": "praw", "edge_tts": "edge-tts", "faster_whisper": "faster-whisper",
        "yaml": "PyYAML", "dotenv": "python-dotenv", "requests": "requests",
        "googleapiclient": "google-api-python-client",
        "google_auth_oauthlib": "google-auth-oauthlib",
        "PIL": "Pillow", "rich": "rich",
    }
    missing = [pip for mod, pip in required.items()
               if importlib.util.find_spec(mod) is None]
    if not missing:
        add(OK, "Python packages installed")
    else:
        add(TODO, f"missing packages: {', '.join(missing)}",
            "run: .venv\\Scripts\\pip install -r requirements.txt")


def check_env() -> None:
    if not (ROOT / ".env").exists():
        add(TODO, ".env file missing",
            "copy .env.example to .env, then fill in your keys")
        return
    add(OK, ".env file present")
    env = _load_env()

    # YouTube client secret (required to upload)
    secret_name = env.get("YOUTUBE_CLIENT_SECRETS", "client_secret.json")
    if (ROOT / secret_name).exists():
        add(OK, f"YouTube client secret ({secret_name})")
    else:
        add(TODO, f"YouTube client secret '{secret_name}' not found",
            "download an OAuth Desktop client JSON from Google Cloud (SETUP.md §4)")

    # Optional keys — informational only
    if env.get("REDDIT_CLIENT_ID") and env.get("REDDIT_CLIENT_SECRET"):
        add(OK, "Reddit API key set (faster fetching)")
    else:
        add(WARN, "Reddit API key not set (optional)",
            "fine to skip — free archives are used automatically")
    if env.get("ELEVENLABS_API_KEY"):
        add(OK, "ElevenLabs key set (premium voice)")
    else:
        add(WARN, "ElevenLabs key not set (optional)",
            "fine to skip — free edge-tts voice is used")


def check_backgrounds() -> None:
    bg = ROOT / "assets" / "backgrounds"
    clips = list(bg.glob("*.mp4")) + list(bg.glob("*.mov")) if bg.exists() else []
    real = [c for c in clips if not c.stem.startswith("_")]
    if real:
        add(OK, f"background videos ({len(real)} clip(s))")
    elif clips:
        add(WARN, "only the placeholder background is present",
            "drop a real vertical clip into assets/backgrounds/ before monetizing")
    else:
        add(TODO, "no background videos",
            "add at least one vertical .mp4 to assets/backgrounds/ (gameplay/loop)")


def check_channels() -> None:
    try:
        from src import channels as ch
        from src.utils import load_config
        channels = ch.load_channels(load_config())
    except Exception as e:  # noqa: BLE001
        add(WARN, f"couldn't read channel registry ({type(e).__name__})", "")
        return
    authed = [n for n in channels if ch.is_authorized(channels, n)]
    if authed:
        add(OK, f"channels authorized: {', '.join(authed)}")
    else:
        add(TODO, "no channels authorized yet",
            "run: .venv\\Scripts\\python.exe scripts\\channels.py add main --login")


def main() -> int:
    print("\n=== Reddit -> YouTube Shorts : setup check ===\n")
    check_python()
    check_ffmpeg()
    check_packages()
    check_env()
    check_backgrounds()
    check_channels()

    width = max(len(label) for _, label, _ in results) + 2
    todo = 0
    for mark, label, hint in results:
        todo += (mark == TODO)
        line = f"  {mark}  {label:<{width}}"
        if hint and mark != OK:
            line += f" -> {hint}"
        print(line)

    print()
    if todo == 0:
        print("All required checks passed. You're ready:")
        print("  Build only (no upload):  .venv\\Scripts\\python.exe main.py --limit 1 --no-upload")
        print("  Build + upload private:  .venv\\Scripts\\python.exe main.py --limit 1 --privacy private")
        print("  Parallel batch:          run_parallel.bat --limit 3")
    else:
        print(f"{todo} required item(s) still TODO — see the -> hints above, "
              f"then re-run this checker.")
    return 0 if todo == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
