"""Stage 1 — fetch and filter Reddit posts.

Uses PRAW when credentials are present, otherwise falls back to Reddit's public
JSON endpoint (no auth, gentler rate limits, read-only). Returns a list of
normalized post dicts ready for the script stage.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

import requests

from . import moderation
from .utils import env, log


@dataclass
class Post:
    id: str
    subreddit: str
    title: str
    body: str
    score: int
    url: str
    nsfw: bool
    top_comment: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comment:
    author: str
    body: str
    score: int

    def as_dict(self) -> dict:
        return asdict(self)


PULLPUSH = "https://api.pullpush.io/reddit/search"
# arctic-shift: another free, no-auth Reddit archive — much faster/more reliable
# than pullpush right now. It sorts by DATE (not score), so we pull an older
# window (so posts have accumulated comments) and rank by score in code.
ARCTIC = "https://arctic-shift.photon-reddit.com/api"
_BAD = {"[removed]", "[deleted]", ""}


def _comment_ok(body: str, max_chars: int) -> bool:
    b = (body or "").strip()
    if b in _BAD or b.lower() in {"this", "deleted by user"}:
        return False
    if "http://" in b or "https://" in b:
        return False
    if len(b) > max_chars:
        return False
    if b.startswith("&gt;") or b.startswith(">"):  # quote-only replies read poorly
        return False
    if moderation.is_blocked(b):  # hard-blocked slurs/explicit content
        return False
    if moderation.is_nsfw(b):     # sexual/adult content → keep cards ad-friendly
        return False
    return True


def _passes_filters(p: Post, cfg: dict) -> bool:
    text_len = len(p.title) + len(p.body)
    if p.nsfw and not cfg["allow_nsfw"]:
        return False
    if p.score < cfg["min_score"]:
        return False
    if text_len < cfg["min_chars"] or text_len > cfg["max_chars"]:
        return False
    if p.body.strip() in {"[removed]", "[deleted]"}:
        return False
    return True


def _via_praw(subreddit: str, cfg: dict) -> list[Post]:
    import praw  # imported lazily so the JSON fallback works without praw

    reddit = praw.Reddit(
        client_id=env("REDDIT_CLIENT_ID"),
        client_secret=env("REDDIT_CLIENT_SECRET"),
        user_agent=env("REDDIT_USER_AGENT", "shorts-bot/1.0"),
        check_for_async=False,
    )
    sub = reddit.subreddit(subreddit)
    listing = {
        "top": lambda: sub.top(time_filter=cfg["time_filter"], limit=cfg["limit"]),
        "hot": lambda: sub.hot(limit=cfg["limit"]),
        "rising": lambda: sub.rising(limit=cfg["limit"]),
    }[cfg["listing"]]()

    posts: list[Post] = []
    for s in listing:
        top_comment = ""
        if cfg.get("include_comments"):
            s.comments.replace_more(limit=0)
            if s.comments:
                top_comment = s.comments[0].body
        posts.append(
            Post(
                id=s.id,
                subreddit=subreddit,
                title=s.title,
                body=s.selftext or "",
                score=int(s.score),
                url=f"https://reddit.com{s.permalink}",
                nsfw=bool(s.over_18),
                top_comment=top_comment,
            )
        )
    return posts


def _via_json(subreddit: str, cfg: dict) -> list[Post]:
    """No-auth fallback using https://www.reddit.com/r/<sub>/<listing>.json"""
    listing = cfg["listing"]
    params = {"limit": cfg["limit"]}
    if listing == "top":
        params["t"] = cfg["time_filter"]
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    headers = {"User-Agent": env("REDDIT_USER_AGENT", "shorts-bot/1.0")}
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    children = resp.json()["data"]["children"]

    posts: list[Post] = []
    for c in children:
        d = c["data"]
        posts.append(
            Post(
                id=d["id"],
                subreddit=subreddit,
                title=d.get("title", ""),
                body=d.get("selftext", ""),
                score=int(d.get("score", 0)),
                url=f"https://reddit.com{d.get('permalink', '')}",
                nsfw=bool(d.get("over_18", False)),
            )
        )
    return posts


def fetch_posts(subreddit: str, cfg: dict, exclude: set[str] | None = None) -> list[Post]:
    """Return filtered, deduped posts (best first)."""
    exclude = exclude or set()
    use_praw = bool(env("REDDIT_CLIENT_ID") and env("REDDIT_CLIENT_SECRET"))
    try:
        raw = _via_praw(subreddit, cfg) if use_praw else _via_json(subreddit, cfg)
        source = "PRAW" if use_praw else "JSON"
    except Exception as e:  # noqa: BLE001 - fall back gracefully
        log.warning("PRAW failed (%s); using JSON fallback", e)
        raw = _via_json(subreddit, cfg)
        source = "JSON"

    filtered = [p for p in raw if p.id not in exclude and _passes_filters(p, cfg)]
    filtered.sort(key=lambda p: p.score, reverse=True)
    log.info("r/%s: %d fetched via %s → %d usable", subreddit, len(raw), source, len(filtered))
    return filtered


def pick_subreddit(cfg: dict, override: str | None) -> str:
    if override:
        return override
    return random.choice(cfg["subreddits"])


# ---------------------------------------------------------------------------
# Post + comments fetch (for the Reddit-card "story" format).
# Primary source is pullpush.io (anonymous, returns posts AND comments).
# If Reddit API creds are present, PRAW is used for fresher/live data.
# ---------------------------------------------------------------------------

def _ua() -> str:
    return env("REDDIT_USER_AGENT", "shorts-bot/1.0")


def _pp_get(endpoint: str, params: dict, tries: int = 3) -> dict:
    """GET pullpush with retries/backoff — it's a free API and often slow."""
    import time

    last = None
    for attempt in range(tries):
        try:
            r = requests.get(f"{PULLPUSH}/{endpoint}/", params=params,
                             headers={"User-Agent": _ua()}, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("pullpush %s attempt %d/%d failed (%s)",
                        endpoint, attempt + 1, tries, type(e).__name__)
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def _pullpush_posts(subreddit: str, limit: int, days: int) -> list[Post]:
    import time

    params = {
        "subreddit": subreddit, "sort": "desc", "sort_type": "score",
        "size": limit, "over_18": "false",
    }
    # pullpush indexes comments/score with a lag, so a narrow recent window often
    # returns posts with no usable comments. days<=0 → all-time top (most reliable).
    if days and days > 0:
        params["after"] = int(time.time()) - days * 86400
    data = _pp_get("submission", params)
    out = []
    for d in data.get("data", []):
        out.append(Post(
            id=d.get("id", ""), subreddit=subreddit,
            title=d.get("title", ""), body=d.get("selftext", "") or "",
            score=int(d.get("score", 0) or 0),
            url=f"https://reddit.com/comments/{d.get('id','')}",
            nsfw=bool(d.get("over_18", False)),
        ))
    return out


def fetch_title_by_id(post_id: str) -> tuple[str, str] | None:
    """Best-effort (title, subreddit) for a single post id via pullpush.

    Used to recover real titles for videos whose metadata sidecar is missing
    (e.g. rendered before sidecars existed). Returns None on any failure so
    callers can fall back gracefully.
    """
    if not post_id:
        return None
    try:
        data = _pp_get("submission", {"ids": post_id})
        for d in data.get("data", []):
            title = (d.get("title") or "").strip()
            if title:
                return title, d.get("subreddit", "") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("Could not fetch title for post %s (%s)", post_id, type(e).__name__)
    return None


def _pullpush_comments(link_id: str, limit: int, cfg: dict) -> list[Comment]:
    params = {"link_id": link_id, "sort": "desc", "sort_type": "score", "size": limit}
    data = _pp_get("comment", params)
    cmts = []
    for d in data.get("data", []):
        body = (d.get("body", "") or "").replace("&amp;", "and").strip()
        if _comment_ok(body, cfg.get("comment_max_chars", 280)):
            cmts.append(Comment(author=d.get("author", "user") or "user",
                                body=body, score=int(d.get("score", 0) or 0)))
    cmts.sort(key=lambda c: c.score, reverse=True)
    return cmts


# ---------------------------------------------------------------------------
# arctic-shift archive (fast, anonymous). Primary source; pullpush is fallback.
# ---------------------------------------------------------------------------

def _arctic_get(endpoint: str, params: dict, tries: int = 3) -> dict:
    """GET arctic-shift with light retries (it's usually fast: ~1-3s)."""
    import time

    last = None
    for attempt in range(tries):
        try:
            r = requests.get(f"{ARCTIC}/{endpoint}", params=params,
                             headers={"User-Agent": _ua()}, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("arctic-shift %s attempt %d/%d failed (%s)",
                        endpoint, attempt + 1, tries, type(e).__name__)
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _arctic_posts(subreddit: str, scan: int, days_min: int, days_max: int,
                  min_comments: int) -> list[Post]:
    """Fetch a window of posts [days_max..days_min] ago, keep those with enough
    comments, and return them ranked by score (arctic sorts by date, not score)."""
    import time

    now = int(time.time())
    params = {
        "subreddit": subreddit,
        "after": now - days_max * 86400,
        "before": now - days_min * 86400,
        "limit": min(max(scan, 1), 100),  # arctic caps at 100
        "sort": "desc",
    }
    data = _arctic_get("posts/search", params)
    raw = [d for d in data.get("data", [])
           if int(d.get("num_comments", 0) or 0) >= min_comments]
    raw.sort(key=lambda d: int(d.get("score", 0) or 0), reverse=True)
    out = []
    for d in raw:
        out.append(Post(
            id=d.get("id", ""), subreddit=subreddit,
            title=d.get("title", "") or "", body=d.get("selftext", "") or "",
            score=int(d.get("score", 0) or 0),
            url=f"https://reddit.com/comments/{d.get('id', '')}",
            nsfw=bool(d.get("over_18", False)),
        ))
    return out


def _arctic_comments(link_id: str, limit: int, cfg: dict) -> list[Comment]:
    """Fetch comments for a post and return the moderated ones, score-ranked.

    arctic returns comments date-sorted, so we pull a generous pool and rank by
    score in code to surface the best ones.
    """
    data = _arctic_get("comments/search",
                       {"link_id": link_id, "limit": limit, "sort": "desc"})
    cmts = []
    for d in data.get("data", []):
        body = (d.get("body", "") or "").replace("&amp;", "and").strip()
        if _comment_ok(body, cfg.get("comment_max_chars", 280)):
            cmts.append(Comment(author=d.get("author", "user") or "user",
                                body=body, score=int(d.get("score", 0) or 0)))
    cmts.sort(key=lambda c: c.score, reverse=True)
    return cmts


def _arctic_path(subreddit: str, cfg: dict, exclude: set[str]):
    """Try arctic-shift for a (Post, [Comment]) with enough good comments.

    Returns (None, []) on any failure so the caller falls back to pullpush.
    """
    try:
        days_min = cfg.get("arctic_days_min", 14)
        days_max = cfg.get("arctic_days_max", 45)
        min_comments = cfg.get("min_comments", 3)
        max_comments = cfg.get("max_comments", 6)
        posts = _arctic_posts(subreddit, cfg.get("arctic_scan", 100),
                              days_min, days_max, min_comments)
        posts = [p for p in posts
                 if p.id and p.id not in exclude and _screen_post(p, cfg)]
        for post in posts:
            cmts = _arctic_comments(post.id, min(max_comments * 15, 100), cfg)
            cmts = cmts[: max_comments * 3]  # buffer for moderation
            if len(cmts) >= min_comments:
                log.info("r/%s via arctic-shift: '%s' (score %s) + %d comments",
                         subreddit, post.title[:50], post.score, len(cmts))
                return post, cmts
        log.info("arctic-shift: no comment-rich post for r/%s in window", subreddit)
    except Exception as e:  # noqa: BLE001
        log.warning("arctic-shift unavailable (%s) — trying pullpush",
                    type(e).__name__)
    return None, []


def _praw_post_with_comments(subreddit: str, cfg: dict):
    import praw

    reddit = praw.Reddit(
        client_id=env("REDDIT_CLIENT_ID"),
        client_secret=env("REDDIT_CLIENT_SECRET"),
        user_agent=_ua(), check_for_async=False,
    )
    for submission in reddit.subreddit(subreddit).top(time_filter="day", limit=15):
        if submission.over_18 or submission.stickied:
            continue
        if moderation.is_blocked(submission.title):
            continue
        submission.comment_sort = "top"
        submission.comments.replace_more(limit=0)
        cmts = []
        for c in submission.comments[: cfg.get("max_comments", 6) * 3]:
            body = (getattr(c, "body", "") or "").replace("&amp;", "and").strip()
            if _comment_ok(body, cfg.get("comment_max_chars", 280)):
                cmts.append(Comment(author=str(c.author or "user"),
                                    body=body, score=int(c.score)))
            if len(cmts) >= cfg.get("max_comments", 6):
                break
        if len(cmts) >= cfg.get("min_comments", 3):
            post = Post(id=submission.id, subreddit=subreddit, title=submission.title,
                        body=submission.selftext or "", score=int(submission.score),
                        url=f"https://reddit.com{submission.permalink}",
                        nsfw=bool(submission.over_18))
            return post, cmts
    return None, []


def _screen_post(post, cfg: dict | None = None) -> bool:
    """Reject a post that is unusable for an advertiser-friendly Short.

    Blocks: missing post, slur/graphic content in the title, and — unless
    reddit.allow_nsfw is set — anything NSFW (the over-18 flag OR sexual/adult
    text in the title or body). The text scan matters because the anonymous
    pullpush feed doesn't reliably set the over-18 flag.
    """
    if post is None or moderation.is_blocked(post.title):
        return False
    allow_nsfw = bool((cfg or {}).get("allow_nsfw", False))
    if not allow_nsfw and moderation.post_is_nsfw(
            post.title, getattr(post, "body", ""), getattr(post, "nsfw", False)):
        log.info("Skipping NSFW post %s: '%s'", post.id, post.title[:60])
        return False
    return True


def fetch_post_with_comments(subreddit: str, cfg: dict, exclude: set[str] | None = None):
    """Return (Post, [Comment]) for a trending post with enough good comments.

    Tries PRAW first when creds exist (freshest), else pullpush.io (anonymous).
    """
    exclude = exclude or set()
    use_praw = bool(env("REDDIT_CLIENT_ID") and env("REDDIT_CLIENT_SECRET"))

    if use_praw:
        try:
            post, cmts = _praw_post_with_comments(subreddit, cfg)
            if post and post.id not in exclude:
                log.info("r/%s via PRAW: '%s' + %d comments",
                         subreddit, post.title[:50], len(cmts))
                # buffer extra comments so the pipeline's profanity rules still
                # leave enough; it trims to max_comments after moderation
                if _screen_post(post, cfg):
                    return post, cmts[: cfg.get("max_comments", 6) * 3]
        except Exception as e:  # noqa: BLE001
            log.warning("PRAW post+comments failed (%s); using archives", e)

    # Primary anonymous source: arctic-shift (fast). Falls through on any miss.
    post, cmts = _arctic_path(subreddit, cfg, exclude)
    if post:
        return post, cmts

    # Fallback anonymous source: pullpush (slower, but a different backend).
    try:
        posts = _pullpush_posts(subreddit, cfg.get("scan_posts", 20),
                                cfg.get("trending_days", 0))
        posts = [p for p in posts
                 if p.id and p.id not in exclude and _screen_post(p, cfg)]
        posts.sort(key=lambda p: p.score, reverse=True)
        for post in posts:
            cmts = _pullpush_comments(post.id, cfg.get("max_comments", 6) * 4, cfg)
            cmts = cmts[: cfg.get("max_comments", 6) * 3]  # buffer for moderation
            if len(cmts) >= cfg.get("min_comments", 3):
                log.info("r/%s via pullpush: '%s' (score %s) + %d comments",
                         subreddit, post.title[:50], post.score, len(cmts))
                return post, cmts
        log.warning("No suitable post+comments found for r/%s", subreddit)
    except Exception as e:  # noqa: BLE001
        log.warning("pullpush unavailable (%s) — caller should use fallback", type(e).__name__)
    return None, []
