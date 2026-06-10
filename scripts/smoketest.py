"""Quick end-to-end check of the stages that need no credentials:
Reddit fetch (public JSON) → script clean → edge-tts narration.
Run: .venv\\Scripts\\python.exe scripts\\smoketest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import ensure_dirs, load_config
from src import reddit_fetch as rf
from src import script_gen as sg
from src import tts as tts_mod

cfg = load_config()
ensure_dirs("temp", "output", "state")

posts = rf.fetch_posts("Showerthoughts", cfg["reddit"], exclude=set())
print(f"Fetched {len(posts)} usable posts")
assert posts, "No posts returned"

p = posts[0]
print("TOP:", p.title[:80], "| score", p.score)

script = sg.build_script(p, cfg["script"])
print("SCRIPT:", script[:200])

path, dur = tts_mod.synthesize(script, "temp/_smoketest.mp3", cfg["tts"])
print(f"NARRATION OK -> {path} ({dur:.1f}s)")
