"""Stage 4 — word-level captions via faster-whisper.

Transcribes the narration audio to get per-word timestamps, then writes an ASS
subtitle file with karaoke-style highlighting (the active word changes color).
Burning the ASS into the video happens in the video stage.
"""
from __future__ import annotations

from pathlib import Path

from .utils import log


def _fmt_ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _group_words(words, max_per_line: int):
    """Yield lists of (word, start, end) chunks of up to max_per_line words."""
    chunk = []
    for w in words:
        chunk.append(w)
        if len(chunk) >= max_per_line:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def transcribe_words(audio_path: str | Path, cfg: dict):
    """Return a flat list of (word, start, end) tuples."""
    from faster_whisper import WhisperModel

    # device="cpu" avoids ctranslate2 trying to load CUDA (cublas) on machines
    # without an NVIDIA GPU. int8 keeps CPU transcription fast & low-memory.
    device = cfg.get("device", "cpu")
    model = WhisperModel(cfg.get("whisper_model", "base"),
                         device=device, compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True)

    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append((w.word.strip(), w.start, w.end))
    log.info("Captions: %d words timed", len(words))
    return words


def write_ass(words, out_path: str | Path, cfg: dict, play_w: int, play_h: int) -> Path:
    """Write an ASS subtitle file with active-word highlighting."""
    out_path = Path(out_path)
    # `font` may be a font-family NAME (resolved from system/fontsdir) or a path
    # to a .ttf/.otf. Use the family name either way for the ASS Style line.
    font_cfg = cfg.get("font") or "Impact"
    font = Path(font_cfg).stem if font_cfg.lower().endswith((".ttf", ".otf")) else font_cfg
    fs = cfg.get("font_size", 86)
    primary = cfg.get("primary_color", "&H00FFFFFF")
    highlight = cfg.get("highlight_color", "&H0000F2FF")
    outline = cfg.get("outline", 6)
    margin_v = cfg.get("margin_v", 420)
    max_per_line = cfg.get("max_words_per_line", 4)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Main,{font},{fs},{primary},&H00000000,&H80000000,1,1,{outline},2,2,40,40,{margin_v}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    for chunk in _group_words(words, max_per_line):
        start = chunk[0][1]
        end = chunk[-1][2]
        # Build text where each word turns to highlight color during its window
        # using inline \t transforms keyed off the line start.
        parts = []
        for word, ws, we in chunk:
            on = max(0, (ws - start)) * 1000
            off = max(0, (we - start)) * 1000
            parts.append(
                f"{{\\c{primary}\\t({on:.0f},{off:.0f},\\c{highlight})}}{word} "
            )
        text = "".join(parts).strip()
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Main,,0,0,0,,{text}"
        )

    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    log.info("Captions: wrote %s (%d cues)", out_path.name, len(lines))
    return out_path


def build_captions(audio_path: str | Path, out_path: str | Path, cfg: dict,
                   play_w: int, play_h: int) -> Path | None:
    if not cfg.get("enabled", True):
        return None
    words = transcribe_words(audio_path, cfg)
    if not words:
        return None
    return write_ass(words, out_path, cfg, play_w, play_h)
