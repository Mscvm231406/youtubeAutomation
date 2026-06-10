# What's done & what YOU need to do

This file is the single source of truth for the current state of the project.

## ✅ Done automatically (already working on this machine)

| Item | Status |
|------|--------|
| Python virtual env (`.venv`, Python 3.14) | ✅ created |
| Core libraries (praw, edge-tts, faster-whisper, google-api…) | ✅ installed |
| **FFmpeg** (video engine) | ✅ installed via winget |
| **yt-dlp** (background downloader) | ✅ installed via winget |
| Caption engine (faster-whisper) | ✅ installed & working on 3.14 |
| AI narration (edge-tts, **free, no key**) | ✅ working |
| Minecraft parkour background (720p, 3 min, ~76 MB) | ✅ `assets/backgrounds/minecraft_parkour.mp4` |
| Full pipeline code (fetch→script→TTS→captions→video→upload) | ✅ written |
| **Sample Short built & verified** (1080×1920, H.264+AAC, 14.5s) | ✅ `output/_sample.mp4` — **play it!** |

The **narration + captions + video composition** half of the pipeline runs with
**zero credentials**. The two ends — pulling Reddit posts and uploading to
YouTube — need accounts only you can create.

## 📋 What YOU need to do (≈ 12 minutes total)

### 1. Reddit API access — REQUIRED (Reddit blocks anonymous access)
Anonymous fetching now returns HTTP 403, so you must register a free app:
1. Go to <https://www.reddit.com/prefs/apps> → **create another app…**
2. Pick **script**. Name it anything; redirect URI `http://localhost:8080`.
3. Copy the **client ID** (under the app name) and **secret**.
4. Put them in `.env` (copy from `.env.example`):
   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USER_AGENT=shorts-bot/1.0 by u/YOUR_REDDIT_USERNAME
   ```

### 2. YouTube upload access — REQUIRED only when you want auto-upload
1. <https://console.cloud.google.com/> → new project.
2. **APIs & Services → Library →** enable **YouTube Data API v3**.
3. **OAuth consent screen:** External; add your email **canadamoscow109@gmail.com** as a Test user.
4. **Credentials → Create → OAuth client ID → Desktop app →** download JSON.
5. Save it as `client_secret.json` in the project root.
   (First upload opens a browser once to authorize; token then cached.)

### 3. ElevenLabs premium voice — now the DEFAULT (set `engine: elevenlabs`)
The project is configured to narrate with the **most popular ElevenLabs voice**.
`tts.elevenlabs_voice_id: most_popular` auto-discovers the library's most-used
voice, adds it to your account, and caches it (falls back to "Natasha — Valley
girl", the top social/Shorts voice, id `uxKr2vlA4hYgXZR1oPRT`). It needs a key:
1. Get one at <https://elevenlabs.io/app/settings/api-keys> (free tier works to test).
2. Paste it into `.env` → `ELEVENLABS_API_KEY=...` (the file is already created for you).
3. Verify + see the ranking:  `.venv\Scripts\python.exe scripts\list_popular_voices.py`
4. Rebuild the sample:        `.venv\Scripts\python.exe scripts\build_sample.py`

Until a key is present, narration automatically uses free edge-tts, so the
pipeline keeps working — it just upgrades to ElevenLabs once the key is set.

### 4. (Optional but recommended) Better background footage
The auto-downloaded clip is a search result grabbed for demo purposes. Gameplay
footage is still owned by its creator — before monetizing, replace it with your
own recordings or clips explicitly licensed "free/no-copyright":
```
python scripts/get_background.py --url https://youtu.be/THE_CLIP_YOU_CHOSE
```
Drop as many `.mp4`s as you like into `assets/backgrounds/`; one is picked per video.

## ▶️ Running it (after step 1)

```powershell
.\.venv\Scripts\Activate.ps1

# Build (no upload) — review the file in output/
python main.py --subreddit AskReddit --limit 1 --no-upload

# Build + upload as PRIVATE once YouTube is set up
python main.py --limit 1 --privacy private
```

### Uploading videos you ALREADY rendered (no re-render)
You currently have finished `.mp4`s in `output/`. Once `client_secret.json` is in
place (step 2), push them all with one command:

```powershell
# Uploads every not-yet-uploaded video in output/ as PRIVATE (safe default).
# First run opens a browser ONCE to authorize; the token is then cached.
.\.venv\Scripts\python.exe scripts\upload_existing.py

# Variations:
.\.venv\Scripts\python.exe scripts\upload_existing.py --privacy unlisted --limit 2
.\.venv\Scripts\python.exe scripts\upload_existing.py output\AskReddit_draola.mp4
```

- Each render now writes a `<video>.json` sidecar with the real Reddit title; the
  uploader uses it. The 2 videos rendered before this change get a title derived
  from their filename — re-run `main.py` if you want clean titles on those.
- Every successful upload is logged to `state/uploaded.json`, so re-running the
  command never double-posts. Use `--force` to override.

Then schedule daily runs — see `AUTOMATE.md`.

## 🔎 Want to see it work right now (no accounts)?
```powershell
.\.venv\Scripts\python.exe scripts\build_sample.py   # makes output/_sample.mp4
```
