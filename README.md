# Reddit → YouTube Shorts Automation

Turn trending Reddit threads into vertical (9:16) YouTube Shorts — **fully
automated** from finding a post to uploading the finished video. The classic
"Reddit story" format: authentic post + comment cards, narrated aloud, over a
looping gameplay background, posted to one or many channels.

```
 ┌──────────┐   ┌──────────┐   ┌────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
 │  Reddit  │──▶│  Script  │──▶│  Voice │──▶│  Cards   │──▶│  Compose │──▶│ YouTube  │
 │  fetch   │   │  clean   │   │  (TTS) │   │ (render) │   │  (FFmpeg)│   │  upload  │
 └──────────┘   └──────────┘   └────────┘   └──────────┘   └──────────┘   └──────────┘
  arctic-shift   regex/LLM      edge-tts /    Pillow card    1080×1920      Data API
  / pullpush                    ElevenLabs    images         H.264+AAC      v3 (OAuth)
```

> **New here? See [`SETUP.md`](SETUP.md) — it takes you from a fresh PC to your
> first upload in about 20 minutes.**

---

## ✨ Features

- **End-to-end automation** — discover → narrate → render → upload in one command.
- **Authentic Reddit-card format** — dark-mode post & comment cards, timed to the narration.
- **Free by default** — free Reddit archives + free `edge-tts` neural voice; no paid keys required to start.
- **Multi-channel posting** — fan one video out to many channels / Google accounts at once.
- **Parallel mode** — build several videos and upload them concurrently, with a **live terminal dashboard** (progress bars + throughput/ETA telemetry).
- **Built-in NSFW filter** — sexual/adult content is filtered out and never uploaded.
- **Stats dashboard** — a visual "mission control" of uploads, views, subscribers, and a top-videos leaderboard.
- **Resilient fetching** — primary fast archive with automatic fallback; uses the official Reddit API if you add a key.

---

## 🧩 How it works

| # | Stage | What it does | Module | Default (free) | Optional upgrade |
|---|-------|--------------|--------|----------------|------------------|
| 1 | **Fetch** | Pull a trending post + top comments; filter by score, length, NSFW; dedupe | `src/reddit_fetch.py` | arctic-shift / pullpush | Reddit API (PRAW) |
| 2 | **Script** | Clean markdown, expand abbreviations, trim to ~50s of speech | `src/script_gen.py` | regex rules | Claude / OpenAI rewrite |
| 3 | **Voice** | Narrate each segment | `src/tts.py` | `edge-tts` (neural) | ElevenLabs |
| 4 | **Cards** | Render the post & comment cards as images | `src/reddit_card.py` | Pillow | — |
| 5 | **Compose** | Vertical 1080×1920 video: cards timed over a looping background + audio | `src/video.py` | FFmpeg | — |
| 6 | **Upload** | Post to YouTube with SEO title/description/hashtags, as a Short | `src/youtube_upload.py` | YouTube Data API v3 | — |
| 7 | **Orchestrate** | Run the chain for N posts, dedupe, multi-channel, schedule | `main.py` / `run_parallel.py` | — | Task Scheduler |

---

## 🚀 Quick start

```powershell
# 1. Install the prerequisites first (see SETUP.md):
#      - Python 3.10+   (python.org — tick "Add to PATH")
#      - FFmpeg         (winget install Gyan.FFmpeg)

# 2. One-command setup: creates .venv, installs packages, makes your .env
setup.bat

# 3. Put your keys in .env, and your YouTube client_secret.json in this folder
#    (SETUP.md §4 shows the Google Cloud steps). Then connect a channel:
.\.venv\Scripts\python.exe scripts\channels.py add main --login

# 4. Drop a vertical background loop into assets/backgrounds/  (e.g. minecraft.mp4)

# 5. Verify everything is ready:
.\.venv\Scripts\python.exe scripts\check_setup.py
```

Then run it:

```powershell
.\.venv\Scripts\python.exe main.py --limit 1 --no-upload   # build only — inspect output/
run_main.bat                                               # build + upload (sequential)
run_parallel.bat --limit 3                                 # build + upload in parallel + live dashboard
dashboard.bat                                              # open the visual stats dashboard
```

---

## 🖥️ The runners

| Command | What it does |
|---------|--------------|
| `main.py` | Core CLI orchestrator. Flags: `--limit`, `--subreddit`, `--privacy`, `--no-upload`, `--channel`. |
| `run_main.bat` | Build `daily_quota` Shorts and upload to all channels, one at a time. |
| `run_parallel.bat` | Build several Shorts **at once**, then upload every video × channel **concurrently**, with a live progress + telemetry dashboard. |
| `dashboard.bat` | Opens a self-contained HTML dashboard (uploads, views, subscribers, top-videos leaderboard). |
| `scripts/check_setup.py` | Prints a PASS/TODO checklist of your setup with next-step hints. |
| `scripts/channels.py` | Manage channels: `add`, `login`, `doctor`, `list`, `remove`. |
| `scripts/upload_existing.py` | Upload already-rendered `output/*.mp4` without re-rendering. |

---

## 📡 Multiple channels

Each channel is a name with its own cached login. Post to all of them at once.

```powershell
.\.venv\Scripts\python.exe scripts\channels.py add second --login   # browser: pick the channel
.\.venv\Scripts\python.exe scripts\channels.py doctor               # health of every channel
.\.venv\Scripts\python.exe main.py --channel all                    # post to all (parallel runner does this by default)
```

Each upload costs ~1600 of the default **10,000 YouTube API units/day** per
Google Cloud project (≈ 6 uploads/day). To scale past that, give some channels
their own Cloud project + `client_secret` (`channels.py add --client-secret …`)
so each gets its own quota.

---

## ⚙️ Configuration

All tunables live in [`config.yaml`](config.yaml):

- `reddit.subreddits` — which subreddits to pull from.
- `reddit.allow_nsfw` — `false` (default) filters adult content and blocks it from upload.
- `tts` — voice engine and voice id (free `edge` or premium `elevenlabs`).
- `youtube.privacy` — `private` / `unlisted` / `public`, plus title/hashtag templates.
- `run.daily_quota` — how many videos a run produces.

Secrets go in `.env` (copied from [`.env.example`](.env.example)) — see
[`SETUP.md`](SETUP.md) §3.

---

## 📁 Project structure

```
main.py                 Orchestrator / CLI
run_parallel.py         Parallel build + upload with live dashboard
dashboard.py            Visual stats dashboard (HTML)
config.yaml             All settings
.env.example            Secrets template (copy to .env)
setup.bat               One-command environment setup
requirements.txt        Python dependencies
src/
  reddit_fetch.py       Stage 1 — fetch posts + comments (arctic-shift / pullpush / PRAW)
  script_gen.py         Stage 2 — clean & trim narration text
  tts.py                Stage 3 — text-to-speech
  reddit_card.py        Stage 4 — render Reddit cards (Pillow)
  video.py              Stage 5 — compose the vertical Short (FFmpeg)
  youtube_upload.py     Stage 6 — upload via YouTube Data API v3
  channels.py           Multi-channel registry + token health
  moderation.py         Profanity + NSFW filtering
  youtube_stats.py      Analytics for the dashboard
scripts/                channels.py, check_setup.py, upload_existing.py, …
assets/backgrounds/     Your looping background videos (you provide these)
```

---

## 🛠️ Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ffmpeg not found` | Install FFmpeg and reopen the terminal, or set `FFMPEG_BIN` in `.env`. |
| Slow fetch / `ReadTimeout` | Normal — free archives retry automatically. Add a Reddit API key for speed. |
| `access_denied: Account restricted` | Google restricted that account's upload access. Resolve it at myaccount.google.com, then `channels.py login <name> --relogin`; or use a different account. |
| Token expired weekly | OAuth consent screen is in "Testing" mode (7-day tokens). **Publish** it, or re-auth. |
| `quota exceeded` | ~6 uploads/day per Cloud project. Wait for reset, request more, or split channels across projects. |

Full details in [`SETUP.md`](SETUP.md) §10. Run `scripts/check_setup.py` and
`scripts/channels.py doctor` to self-diagnose.

---

## ⚖️ Legal & policy (read before scaling)

- **Reddit content is user-owned.** For monetization, prefer subreddits that allow reuse, credit the source, and transform the content (narration + visuals) — don't raw-repost.
- **YouTube discourages "inauthentic, mass-produced" content.** Vary backgrounds/voices, keep per-channel volume reasonable, and review uploads — start with `private`/`unlisted`.
- **Use background footage you have the right to use** before enabling monetization.
- **Respect Reddit's and YouTube's terms and rate limits.** Aggressive automation on new accounts is the most common cause of account restrictions.

This project is provided as-is for educational and personal use. You are
responsible for complying with the terms of every service it touches.
