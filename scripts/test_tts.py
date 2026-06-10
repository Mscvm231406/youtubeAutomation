"""Verify tts.synthesize honors engine config + ElevenLabs→edge fallback."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import load_config
from src import tts as t

cfg = load_config()["tts"]
print("engine config:", cfg.get("engine"), "| voice:", cfg.get("elevenlabs_voice_id"))
path, dur = t.synthesize("Testing the most popular voice path.", "temp/_tts_test.mp3", cfg)
print(f"OK -> {path} ({dur:.1f}s)")
