"""Validate the ElevenLabs key and print the most-used voices in the library.

Run after putting ELEVENLABS_API_KEY in .env:
  .venv\\Scripts\\python.exe scripts\\list_popular_voices.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests
from src.utils import env

KEY = env("ELEVENLABS_API_KEY")
if not KEY:
    print("ELEVENLABS_API_KEY not set in .env — add it first.")
    sys.exit(1)

# Confirm the key works + show remaining quota.
u = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                 headers={"xi-api-key": KEY}, timeout=30)
if u.status_code != 200:
    print(f"Key rejected (HTTP {u.status_code}). Check the key.")
    sys.exit(1)
sub = u.json()
used, lim = sub.get("character_count", 0), sub.get("character_limit", 0)
print(f"Key OK. Tier: {sub.get('tier')}  |  Used {used}/{lim} characters this period\n")

r = requests.get("https://api.elevenlabs.io/v1/shared-voices",
                 params={"page_size": 100, "sort": "trending", "category": "professional"},
                 headers={"xi-api-key": KEY}, timeout=30)
r.raise_for_status()
voices = sorted(r.json().get("voices", []),
                key=lambda v: v.get("usage_character_count_1y", 0), reverse=True)

print("Most-used voices (by characters/year):")
for v in voices[:12]:
    print(f"  {v.get('usage_character_count_1y',0):>14,}  "
          f"{v.get('name'):<22}  id={v.get('voice_id')}  ({v.get('use_case')})")
print("\nThe pipeline auto-selects the #1 here when tts.elevenlabs_voice_id = most_popular.")
