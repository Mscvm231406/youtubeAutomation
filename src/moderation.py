"""Content moderation for Reddit text → advertiser-friendly Shorts.

Two tiers:
  * HARD BLOCK — slurs, hate, explicit sexual/graphic content. A comment or post
    containing any of these is skipped entirely (there are always more comments).
  * SOFT PROFANITY — common swears. Depending on `profanity_mode`:
      - "skip"  : skip the comment too
      - "censor": star out on the displayed card AND speak a clean euphemism
      - "allow" : leave untouched

The built-in lists are intentionally moderate. For production, append the full
LDNOOBW "List of Dirty, Naughty, Obscene, and Otherwise Bad Words" to
`assets/blocklist.txt` (one term per line) — it is loaded and merged at runtime.
"""
from __future__ import annotations

import re
from pathlib import Path

from .utils import ROOT, log

# --- HARD BLOCK: skip any text containing these (word-boundary matched) ---
# Kept deliberately compact + extensible via assets/blocklist.txt.
_HARD_BLOCK = {
    # racial / ethnic / homophobic slurs
    "nigger", "nigga", "faggot", "fag", "retard", "retarded", "tranny",
    "chink", "spic", "kike", "wetback", "gook", "coon",
    # explicit sexual
    "rape", "raped", "rapist", "incest", "pedophile", "pedo", "molest",
    "cum", "blowjob", "handjob", "creampie", "bukkake", "bestiality",
    # graphic violence / self-harm
    "suicide", "kill yourself", "kys", "behead", "lynch",
}

# --- NSFW / ADULT: sexual & adult-content terms ---------------------------
# Broader than the explicit subset of _HARD_BLOCK above: any post (or comment)
# whose text matches one of these is treated as NSFW and is never built/uploaded
# (unless reddit.allow_nsfw is true). Word-boundary matched, so substrings of
# innocent words don't trip it (e.g. "analysis" ≠ "anal", "assassin" ≠ "ass").
# Extend at runtime by dropping one term per line in assets/nsfwlist.txt.
_NSFW_TERMS = {
    "nsfw", "porn", "porno", "pornography", "xxx", "hardcore", "softcore",
    "nude", "nudes", "nudity", "naked", "topless", "lingerie", "thong",
    "sex", "sexual", "sexually", "sexy", "sexting", "intercourse", "foreplay",
    "orgasm", "orgasmic", "climax", "ejaculate", "ejaculation", "semen",
    "masturbate", "masturbation", "masturbating", "fap", "fapping",
    "horny", "aroused", "arousal", "kinky", "kink", "fetish", "bdsm", "bondage",
    "hentai", "milf", "gilf", "cougar", "camgirl", "camwhore",
    "anal", "deepthroat", "threesome", "foursome", "orgy", "gangbang",
    "dildo", "vibrator", "buttplug", "strapon", "fleshlight",
    "penis", "penises", "vagina", "vaginal", "vulva", "clitoris", "clit",
    "boob", "boobs", "tits", "titty", "titties", "nipple", "nipples",
    "genital", "genitals", "genitalia", "erection", "boner", "erotic", "erotica",
    "escort", "escorts", "brothel", "hooker", "prostitute", "prostitution",
    "stripper", "stripclub", "striptease", "onlyfans", "camsite",
    "twerk", "upskirt", "cameltoe", "thirst trap", "sugar daddy", "sugar baby",
    "69", "blowie", "rimjob", "footjob", "facial",
}

_nsfw_loaded = False


def _load_external_nsfw() -> None:
    global _nsfw_loaded
    if _nsfw_loaded:
        return
    _nsfw_loaded = True
    f = ROOT / "assets" / "nsfwlist.txt"
    if not f.exists():
        return
    added = 0
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        term = line.strip().lower()
        if term and not term.startswith("#"):
            _NSFW_TERMS.add(term)
            added += 1
    if added:
        log.info("Moderation: loaded %d extra NSFW terms from nsfwlist.txt", added)


# --- SOFT PROFANITY: (spoken euphemism, used in censor mode) ---
_SOFT_PROFANITY = {
    "fuck": "frick", "fucking": "fricking", "fucked": "fricked", "fucker": "fricker",
    "shit": "crap", "shitty": "crappy", "bullshit": "baloney",
    "ass": "butt", "asshole": "jerk", "dumbass": "dummy",
    "bitch": "jerk", "bastard": "rascal", "damn": "dang", "goddamn": "dang",
    "hell": "heck", "piss": "tick", "pissed": "ticked",
    "dick": "jerk", "cock": "jerk", "prick": "jerk", "slut": "person", "whore": "person",
}

_blocklist_loaded = False


def _load_external_blocklist() -> None:
    global _blocklist_loaded
    if _blocklist_loaded:
        return
    _blocklist_loaded = True
    f = ROOT / "assets" / "blocklist.txt"
    if not f.exists():
        return
    added = 0
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        term = line.strip().lower()
        if term and not term.startswith("#"):
            _HARD_BLOCK.add(term)
            added += 1
    if added:
        log.info("Moderation: loaded %d extra blocked terms from blocklist.txt", added)


def _word_re(terms) -> re.Pattern:
    # multi-word phrases allowed; \b around the whole phrase
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def is_blocked(text: str) -> bool:
    """True if the text contains any hard-blocked term."""
    _load_external_blocklist()
    if not text:
        return False
    return bool(_word_re(_HARD_BLOCK).search(text))


def is_nsfw(text: str) -> bool:
    """True if the text contains sexual / adult (NSFW) content.

    Independent of is_blocked: that one also covers slurs and graphic violence;
    this one is specifically the adult-content signal used to keep Shorts
    advertiser-friendly and off the upload path.
    """
    _load_external_nsfw()
    if not text:
        return False
    return bool(_word_re(_NSFW_TERMS).search(text))


def post_is_nsfw(title: str, body: str = "", flagged: bool = False) -> bool:
    """Decide whether a whole post is NSFW.

    Combines the platform's own over-18 flag with a text scan of the title and
    body, so a post is caught even when the flag is missing/unreliable (the
    anonymous pullpush path doesn't always set it correctly).
    """
    if flagged:
        return True
    return is_nsfw(f"{title or ''}\n{body or ''}")


def has_soft_profanity(text: str) -> bool:
    return bool(_word_re(_SOFT_PROFANITY.keys()).search(text or ""))


def _star(word: str) -> str:
    if len(word) <= 2:
        return word[0] + "*"
    return word[0] + "*" * (len(word) - 2) + word[-1]


def censor_display(text: str) -> str:
    """Star out soft profanity for the on-screen card (keeps it ad-friendly)."""
    def repl(m: re.Match) -> str:
        return _star(m.group(0))
    return _word_re(_SOFT_PROFANITY.keys()).sub(repl, text or "")


def clean_spoken(text: str) -> str:
    """Replace soft profanity with spoken euphemisms for narration."""
    def repl(m: re.Match) -> str:
        word = m.group(0)
        eup = _SOFT_PROFANITY.get(word.lower(), word)
        # preserve capitalization of the first letter
        return eup.capitalize() if word[:1].isupper() else eup
    return _word_re(_SOFT_PROFANITY.keys()).sub(repl, text or "")


def screen(text: str, cfg: dict) -> tuple[bool, str, str]:
    """Moderate a piece of text.

    Returns (ok, display_text, spoken_text):
      ok=False  → reject this comment/post entirely.
      display_text → text to show on the card.
      spoken_text  → text to feed the TTS.
    """
    if not cfg.get("enabled", True):
        return True, text, text
    if is_blocked(text):
        return False, text, text

    mode = cfg.get("profanity_mode", "censor")
    if has_soft_profanity(text):
        if mode == "skip":
            return False, text, text
        if mode == "censor":
            return True, censor_display(text), clean_spoken(text)
        # allow
    return True, text, text
