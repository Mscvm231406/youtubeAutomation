"""Stage 5 — compose the final vertical Short with FFmpeg.

Layers (bottom → top):
  1. Background video  (random clip from assets/backgrounds, looped, cropped 9:16)
  2. Optional music bed (ducked low under narration)
  3. Narration audio   (drives the final duration)
  4. Burned-in ASS captions

FFmpeg is invoked directly (no moviepy) for speed and fewer dependencies.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from .utils import ROOT, ffmpeg_bin, ffprobe_bin, log


import json

_BG_HISTORY = ROOT / "state" / "bg_history.json"


def _video_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _list_backgrounds(cfg: dict) -> list[Path]:
    bg_dir = ROOT / cfg["backgrounds_dir"]
    clips = sorted(bg_dir.glob("*.mp4")) + sorted(bg_dir.glob("*.mov"))
    # the synthetic placeholder is only a last resort; prefer real footage
    real = [c for c in clips if not c.stem.startswith("_")]
    return real or clips


def _last_background() -> str | None:
    if not _BG_HISTORY.exists():
        return None
    try:
        hist = json.loads(_BG_HISTORY.read_text(encoding="utf-8"))
        return hist[-1] if hist else None
    except (json.JSONDecodeError, OSError, IndexError):
        return None


def _record_background(name: str, keep: int = 200) -> None:
    try:
        hist = json.loads(_BG_HISTORY.read_text(encoding="utf-8")) if _BG_HISTORY.exists() else []
    except (json.JSONDecodeError, OSError):
        hist = []
    hist.append(name)
    _BG_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    _BG_HISTORY.write_text(json.dumps(hist[-keep:], indent=2), encoding="utf-8")


def _choose_background(cfg: dict, needed_dur: float) -> tuple[Path, float]:
    """Pick a background clip + a random start offset so outputs vary.

    Avoids reusing the immediately-previous clip (when more than one exists), and
    seeks to a random point in the clip so even a repeat looks different.
    Returns (clip_path, start_offset_seconds).
    """
    clips = _list_backgrounds(cfg)
    if not clips:
        raise FileNotFoundError(
            f"No background videos in {ROOT / cfg['backgrounds_dir']}. Add at least "
            f"one vertical-friendly .mp4 (gameplay, satisfying loop, etc.)."
        )

    if cfg.get("background_mode") == "sequential":
        # advance to the next clip after the last-used one
        last = _last_background()
        names = [c.name for c in clips]
        idx = (names.index(last) + 1) % len(clips) if last in names else 0
        choice = clips[idx]
    else:
        last = _last_background()
        pool = [c for c in clips if c.name != last] or clips
        choice = random.choice(pool)

    _record_background(choice.name)

    offset = 0.0
    if cfg.get("background_random_start", True):
        dur = _video_duration(choice)
        if dur > needed_dur + 1.0:
            offset = round(random.uniform(0, dur - needed_dur - 0.5), 2)
    return choice, offset


def _pick_music(cfg: dict) -> Path | None:
    music_dir = ROOT / cfg.get("music_dir", "")
    if not music_dir or not music_dir.exists():
        return None
    tracks = sorted(music_dir.glob("*.mp3")) + sorted(music_dir.glob("*.m4a"))
    return random.choice(tracks) if tracks else None


def _escape_for_filter(p: Path) -> str:
    """FFmpeg filter paths need escaping, especially on Windows (C:\\ → C\\:)."""
    s = str(p).replace("\\", "/")
    return s.replace(":", "\\:")


def compose(
    narration: str | Path,
    duration: float,
    ass_path: str | Path | None,
    out_path: str | Path,
    cfg: dict,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = cfg["width"], cfg["height"]
    dur = min(duration + 0.4, cfg["max_duration"])  # tiny tail of silence

    background, bg_offset = _choose_background(cfg, dur)
    music = _pick_music(cfg)

    # --- video filtergraph: scale-crop background to 9:16, then burn captions ---
    vf = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={cfg['fps']}[bg]"
    )
    if ass_path:
        fonts_dir = ROOT / "assets" / "fonts"
        fontsdir_opt = f":fontsdir='{_escape_for_filter(fonts_dir)}'" if fonts_dir.exists() else ""
        vf += f";[bg]ass='{_escape_for_filter(Path(ass_path))}'{fontsdir_opt}[v]"
        vmap = "[v]"
    else:
        vf += ";[bg]null[v]"
        vmap = "[v]"

    bg_seek = ["-ss", f"{bg_offset:.2f}"] if bg_offset > 0 else []
    cmd = [
        ffmpeg_bin(), "-y",
        "-stream_loop", "-1", *bg_seek, "-i", str(background),  # 0: looping bg
        "-i", str(narration),                          # 1: narration
    ]
    audio_inputs = 1
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]  # 2: music
        audio_inputs = 2

    # --- audio filtergraph ---
    if music:
        af = (
            f"[2:a]volume={cfg.get('music_volume', 0.08)}[m];"
            f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        amap = "[a]"
    else:
        af = "[1:a]anull[a]"
        amap = "[a]"

    filter_complex = vf + ";" + af

    cmd += [
        "-filter_complex", filter_complex,
        "-map", vmap, "-map", amap,
        "-t", f"{dur:.2f}",
        "-c:v", cfg.get("video_codec", "libx264"),
        "-crf", str(cfg.get("crf", 20)),
        "-pix_fmt", "yuv420p",
        "-c:a", cfg.get("audio_codec", "aac"), "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]

    log.info("FFmpeg: composing %s (%.1fs, bg=%s%s)", out_path.name, dur,
             background.name, f", music={music.name}" if music else "")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FFmpeg failed:\n%s", proc.stderr[-2000:])
        raise RuntimeError("FFmpeg composition failed")
    return out_path


def compose_reddit(segments, audio_files, out_path, cfg, music_override=None):
    """Compose a Reddit-card 'story' Short.

    segments: list of {"png": path, "start": float, "end": float}
              each card is shown (centered) only during [start, end].
    audio_files: per-segment narration mp3s, concatenated in order; their summed
                 duration defines the timeline the segment windows are built on.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = cfg["width"], cfg["height"]
    total = segments[-1]["end"] + 0.4
    total = min(total, cfg.get("max_duration", 59))

    background, bg_offset = _choose_background(cfg, total)
    music = music_override or _pick_music(cfg)
    y_off = cfg.get("card_y_offset", 80)  # push cards slightly above center

    # --- inputs ---
    bg_seek = ["-ss", f"{bg_offset:.2f}"] if bg_offset > 0 else []
    cmd = [ffmpeg_bin(), "-y", "-stream_loop", "-1", *bg_seek, "-i", str(background)]  # 0: bg
    for a in audio_files:                                                    # 1..K: audio
        cmd += ["-i", str(a)]
    n_audio = len(audio_files)
    for seg in segments:                                                    # cards
        cmd += ["-loop", "1", "-i", str(seg["png"])]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    # --- video graph: bg crop, then chained timed overlays ---
    parts = [f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{h},setsar=1,fps={cfg['fps']}[bg]"]
    prev = "bg"
    for i, seg in enumerate(segments):
        idx = 1 + n_audio + i  # input index of this card png
        out = f"v{i}"
        parts.append(
            f"[{prev}][{idx}:v]overlay=x=(W-w)/2:y=(H-h)/2-{y_off}:"
            f"enable='between(t,{seg['start']:.3f},{seg['end']:.3f})'[{out}]"
        )
        prev = out

    # --- audio graph: concat narration (+ optional ducked music) ---
    concat_in = "".join(f"[{1+i}:a]" for i in range(n_audio))
    parts.append(f"{concat_in}concat=n={n_audio}:v=0:a=1[narr]")
    if music:
        music_idx = 1 + n_audio + len(segments)
        parts.append(f"[{music_idx}:a]volume={cfg.get('music_volume',0.08)}[mus]")
        parts.append("[narr][mus]amix=inputs=2:duration=first:dropout_transition=0[a]")
        amap = "[a]"
    else:
        amap = "[narr]"

    filter_complex = ";".join(parts)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]", "-map", amap,
        "-t", f"{total:.2f}",
        "-c:v", cfg.get("video_codec", "libx264"), "-crf", str(cfg.get("crf", 20)),
        "-pix_fmt", "yuv420p",
        "-c:a", cfg.get("audio_codec", "aac"), "-b:a", "192k",
        str(out_path),
    ]

    log.info("FFmpeg: composing Reddit Short %s (%.1fs, %d cards, bg=%s @%.1fs)",
             out_path.name, total, len(segments), background.name, bg_offset)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FFmpeg failed:\n%s", proc.stderr[-2500:])
        raise RuntimeError("FFmpeg Reddit composition failed")
    return out_path
