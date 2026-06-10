"""Stage 2 — turn a raw Reddit post into a clean narration script.

Pipeline:
  1. Strip markdown / URLs / Reddit-isms.
  2. Expand common abbreviations (TIFU, AITA, IMO...) so TTS reads them right.
  3. Assemble  hook (title) + body  and trim to the spoken-time budget.
  4. (optional) Hand off to an LLM to punch up the hook and tighten prose.
"""
from __future__ import annotations

import re

from .reddit_fetch import Post
from .utils import env, log

ABBREVIATIONS = {
    r"\bTIFU\b": "Today I messed up",
    r"\bAITA\b": "Am I the bad guy",
    r"\bAITAH\b": "Am I the bad guy",
    r"\bNTA\b": "not the bad guy",
    r"\bYTA\b": "you're the bad guy",
    r"\bIMO\b": "in my opinion",
    r"\bIMHO\b": "in my honest opinion",
    r"\bTL;DR\b": "in short",
    r"\bTLDR\b": "in short",
    r"\bIRL\b": "in real life",
    r"\bAFAIK\b": "as far as I know",
    r"\bEDIT:.*$": "",          # drop edit notes
    r"\bUPDATE:": "Update.",
}

_URL = re.compile(r"https?://\S+")
_MD = re.compile(r"[*_`>#~]+")
_SUBREF = re.compile(r"/?r/\w+|/?u/\w+")
_WS = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = _URL.sub("", text)
    text = _SUBREF.sub("", text)
    for pat, repl in ABBREVIATIONS.items():
        text = re.sub(pat, repl, text, flags=re.IGNORECASE | re.MULTILINE)
    text = _MD.sub("", text)
    text = text.replace("&amp;", "and").replace("&gt;", "").replace("&#x200B;", "")
    text = _WS.sub(" ", text).strip()
    return text


def _trim_to_budget(text: str, max_seconds: float, wps: float) -> str:
    max_words = int(max_seconds * wps)
    words = text.split()
    if len(words) <= max_words:
        return text
    # cut at a sentence boundary near the budget so it doesn't end mid-thought
    truncated = " ".join(words[:max_words])
    last_stop = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    return truncated[: last_stop + 1] if last_stop > len(truncated) * 0.5 else truncated + "..."


def _llm_rewrite(title: str, body: str, cfg: dict) -> tuple[str, str]:
    """Optional LLM punch-up. Returns (hook, body). Falls back on any error."""
    prompt = (
        "Rewrite this Reddit post as a punchy YouTube Shorts narration. "
        "Return the first line as a 1-sentence HOOK that creates curiosity, "
        "then the rest as tight, spoken-style body text. Keep it faithful, "
        "no preamble.\n\n"
        f"TITLE: {title}\n\nBODY: {body}"
    )
    provider = cfg.get("llm_provider", "anthropic")
    model = cfg.get("llm_model")
    try:
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
            msg = client.messages.create(
                model=model, max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            out = msg.content[0].text
        else:
            from openai import OpenAI

            client = OpenAI(api_key=env("OPENAI_API_KEY"))
            resp = client.chat.completions.create(
                model=model, max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            out = resp.choices[0].message.content
        lines = [l for l in out.strip().splitlines() if l.strip()]
        hook = lines[0]
        body_out = " ".join(lines[1:])
        return hook, body_out
    except Exception as e:  # noqa: BLE001
        log.warning("LLM rewrite failed (%s); using cleaned original", e)
        return title, body


def build_script(post: Post, cfg: dict) -> str:
    """Return the final narration string for a post."""
    title = clean_text(post.title)
    body = clean_text(post.body)
    if cfg.get("include_comments") and post.top_comment:
        body = f"{body} Top comment: {clean_text(post.top_comment)}"

    if cfg.get("use_llm"):
        hook, body = _llm_rewrite(title, body, cfg)
    else:
        hook = cfg.get("hook_template", "{title}").format(title=title)

    script = f"{hook}. {body}".strip()
    script = _trim_to_budget(
        script, cfg["max_speech_seconds"], cfg["words_per_second"]
    )
    log.info("Script: %d words (~%.0fs)", len(script.split()),
             len(script.split()) / cfg["words_per_second"])
    return script
