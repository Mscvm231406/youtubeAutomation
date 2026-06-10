"""Stage 3 — text-to-speech narration.

Engines:
  - edge   : free neural voices (no key). Default fallback.
  - elevenlabs : premium voices via REST API (needs ELEVENLABS_API_KEY).

For ElevenLabs you can either pin a specific `elevenlabs_voice_id`, or set it to
"most_popular" to auto-resolve the library's most-used voice (by yearly usage),
add it to your account, and cache the result. The widely-used social/Shorts
voice "Natasha (Valley girl)" is the built-in fallback.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import requests

from .utils import ROOT, env, ffprobe_bin, log

ELEVEN_API = "https://api.elevenlabs.io/v1"
# "Natasha - Valley girl": the most-used ElevenLabs voice for social/Shorts.
NATASHA_VOICE_ID = "uxKr2vlA4hYgXZR1oPRT"
_VOICE_CACHE = ROOT / "state" / "elevenlabs_voice.json"
_resolved_voice: str | None = None  # in-process memo


def _audio_duration(path: Path) -> float:
    """Probe duration with ffprobe (ships with FFmpeg)."""
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


async def _edge_tts(text: str, out_path: Path, voice: str, rate: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out_path))


# --- ElevenLabs (REST) -----------------------------------------------------

def _eleven_headers(key: str) -> dict:
    return {"xi-api-key": key, "Content-Type": "application/json"}


def _discover_most_popular_voice(key: str) -> tuple[str, str] | None:
    """Return (public_owner_id, voice_id, name) for the most-used shared voice."""
    try:
        r = requests.get(
            f"{ELEVEN_API}/shared-voices",
            params={"page_size": 100, "sort": "trending", "category": "professional"},
            headers={"xi-api-key": key}, timeout=30,
        )
        r.raise_for_status()
        voices = r.json().get("voices", [])
        if not voices:
            return None
        voices.sort(key=lambda v: v.get("usage_character_count_1y", 0), reverse=True)
        top = voices[0]
        log.info("ElevenLabs most-popular voice: %s (%s chars/yr)",
                 top.get("name"), top.get("usage_character_count_1y"))
        return top.get("public_owner_id"), top.get("voice_id"), top.get("name")
    except Exception as e:  # noqa: BLE001
        log.warning("Most-popular discovery failed (%s)", type(e).__name__)
        return None


def _add_shared_voice(key: str, owner_id: str, voice_id: str, name: str) -> str | None:
    """Add a library voice to the account; return the account-usable voice_id."""
    try:
        r = requests.post(
            f"{ELEVEN_API}/voices/add/{owner_id}/{voice_id}",
            headers=_eleven_headers(key),
            json={"new_name": name or "shorts_voice"}, timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("voice_id", voice_id)
        # Already added (400) → the original voice_id is usable in the account.
        log.info("Add-shared-voice returned %s; using original id", r.status_code)
        return voice_id
    except Exception as e:  # noqa: BLE001
        log.warning("Add shared voice failed (%s)", type(e).__name__)
        return None


def _resolve_voice(cfg: dict, key: str) -> str:
    """Resolve the configured ElevenLabs voice to an account-usable voice_id."""
    global _resolved_voice
    if _resolved_voice:
        return _resolved_voice

    configured = (cfg.get("elevenlabs_voice_id") or "most_popular").strip()
    if configured and configured != "most_popular":
        _resolved_voice = configured
        return configured

    # cached from a previous run?
    if _VOICE_CACHE.exists():
        try:
            _resolved_voice = json.loads(_VOICE_CACHE.read_text())["voice_id"]
            return _resolved_voice
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    # discover → add → cache
    found = _discover_most_popular_voice(key)
    if found:
        owner_id, voice_id, name = found
        usable = _add_shared_voice(key, owner_id, voice_id, name) or voice_id
    else:
        usable, name = NATASHA_VOICE_ID, "Natasha (Valley girl)"
    _VOICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _VOICE_CACHE.write_text(json.dumps({"voice_id": usable, "name": name}, indent=2))
    _resolved_voice = usable
    return usable


def _elevenlabs(text: str, out_path: Path, cfg: dict, key: str) -> None:
    voice_id = _resolve_voice(cfg, key)
    model = cfg.get("elevenlabs_model", "eleven_turbo_v2_5")
    r = requests.post(
        f"{ELEVEN_API}/text-to-speech/{voice_id}",
        headers={**_eleven_headers(key), "Accept": "audio/mpeg"},
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": cfg.get("elevenlabs_stability", 0.4),
                "similarity_boost": cfg.get("elevenlabs_similarity", 0.8),
                "style": cfg.get("elevenlabs_style", 0.3),
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    r.raise_for_status()
    out_path.write_bytes(r.content)


def synthesize(text: str, out_path: str | Path, cfg: dict) -> tuple[Path, float]:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = cfg.get("engine", "edge")
    key = env("ELEVENLABS_API_KEY")

    if engine == "elevenlabs":
        if not key:
            log.warning("ELEVENLABS_API_KEY not set; falling back to edge-tts")
            engine = "edge"
        else:
            try:
                _elevenlabs(text, out_path, cfg, key)
            except Exception as e:  # noqa: BLE001
                log.warning("ElevenLabs TTS failed (%s); falling back to edge-tts", e)
                engine = "edge"

    if engine == "edge":
        asyncio.run(
            _edge_tts(text, out_path, cfg.get("edge_voice", "en-US-AndrewNeural"),
                      cfg.get("edge_rate", "+0%"))
        )

    dur = _audio_duration(out_path)
    log.info("TTS (%s): %s  %.1fs", engine, out_path.name, dur)
    return out_path, dur
