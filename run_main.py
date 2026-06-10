"""Default daily runner.

Builds `run.daily_quota` Shorts (set to 3 in config.yaml) and uploads each one
to EVERY configured channel (the 2 channels on the cm Google account + the 2 on
the rg account). It's just `main.py` with `--channel all` as the default.

Usage:
    python run_main.py                 # 3 Shorts → all 4 channels
    python run_main.py --limit 1       # override the count
    python run_main.py --channel rg1   # override the target(s)

Channels must be authorized once first (per channel):
    python scripts/channels.py login <name>
"""
from __future__ import annotations

import sys

from main import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    # Fan out to all channels unless the caller explicitly chose one.
    if "--channel" not in argv:
        argv = ["--channel", "all", *argv]
    sys.argv = [sys.argv[0], *argv]
    raise SystemExit(main())
