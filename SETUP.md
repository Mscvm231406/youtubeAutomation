# Setup Guide — Reddit → YouTube Shorts

This app turns trending Reddit posts into vertical YouTube Shorts and (optionally)
uploads them automatically. This guide takes you from a fresh computer to your
first uploaded Short. **Plan ~20 minutes** (most of it is the one-time YouTube
permission setup).

> **TL;DR for the impatient**
> 1. Install **Python 3.10+** and **FFmpeg**.
> 2. Double-click **`setup.bat`** (installs everything, makes your `.env`).
> 3. Put your keys in **`.env`** and your YouTube `client_secret.json` in the folder.
> 4. Run **`scripts\channels.py add main --login`** to connect your channel.
> 5. Drop a background video into **`assets/backgrounds/`**.
> 6. Run **`run_main.bat`** (or `run_parallel.bat`).
>
> At any point, run the checker to see what's done and what's left:
> `.venv\Scripts\python.exe scripts\check_setup.py`

---

## 1. Install the prerequisites (do this first)

These two are system software the app depends on — install them yourself.

### Python 3.10 or newer
- Download from <https://python.org> and **tick "Add Python to PATH"** during install.
- Verify in a new terminal: `python --version`

### FFmpeg (does all the video work — required)
- **Windows (easiest):**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  Then **close and reopen your terminal** and verify: `ffmpeg -version`
- **Mac:** `brew install ffmpeg`   •   **Linux:** `sudo apt install ffmpeg`

---

## 2. Set up the app (one command)

From the project folder, **double-click `setup.bat`** (or run it in a terminal).
It creates the Python environment, installs all packages, and creates your `.env`
file from the template. It finishes by running the **setup checker**, which prints
a checklist of what's ready and what still needs doing.

> Prefer to do it by hand?
> ```powershell
> python -m venv .venv
> .\.venv\Scripts\Activate.ps1
> pip install -r requirements.txt
> copy .env.example .env
> ```
> Note: `faster-whisper` downloads a model (~150 MB) the first time it's used.

---

## 3. Add your secrets (`.env`)

Open the **`.env`** file (created in step 2) in any text editor. It's fully
commented — each key says where to get it and whether it's required. Summary:

| Key | Required? | What it's for |
|-----|-----------|---------------|
| `YOUTUBE_CLIENT_SECRETS` | **Yes, to upload** | Points to your YouTube OAuth file (step 4) |
| `REDDIT_CLIENT_ID` / `_SECRET` | Optional | Faster, more reliable post fetching |
| `ELEVENLABS_API_KEY` | Optional | Premium narration voice (free `edge-tts` used otherwise) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Optional | LLM script polish (only if enabled in `config.yaml`) |

**You can build videos with an empty `.env`.** Only uploading needs the YouTube
file below. Everything else has a free default.

### (Optional) Reddit key — 2 minutes
Makes fetching faster/more reliable (the app uses free public archives otherwise):
1. Go to <https://www.reddit.com/prefs/apps> → **create app**.
2. Type **script**; redirect URI `http://localhost:8080` (required, unused).
3. Copy the **client id** (short string under the app name) and **secret** into `.env`.

---

## 4. Connect YouTube (one-time, ~8 minutes)

YouTube uploads use a Google "OAuth client" — a small **file** you download once.

1. Open <https://console.cloud.google.com/> and **create a project** (any name).
2. **APIs & Services → Library →** search **YouTube Data API v3** → **Enable**.
   *(If you also want the dashboard's view/subscriber stats, also enable
   **YouTube Analytics API**.)*
3. **APIs & Services → OAuth consent screen:**
   - User type **External** → Create.
   - Fill the required app name / support email (your own email).
   - **Add your Google account as a Test user.** This keeps the app in "Testing"
     mode so Google doesn't require a verification review.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID →**
   Application type **Desktop app** → Create → **Download JSON**.
5. Save that file in the **project root** as **`client_secret.json`**
   (or any name — just match `YOUTUBE_CLIENT_SECRETS` in your `.env`).

> **Testing-mode caveat:** while the consent screen is in "Testing", refresh
> tokens expire after **7 days**, so you'd re-authorize weekly. To run unattended
> long-term, click **"Publish app"** on the consent screen (for a personal
> upload-only app this is fine and usually needs no review).

---

## 5. Authorize your channel

Tell the app which YouTube channel to post to. This opens a browser **once** to
sign in; the login is cached afterward.

```powershell
.\.venv\Scripts\python.exe scripts\channels.py add main --login
```
At the browser screen, sign in as the account that owns the channel and pick the
channel. Check status anytime with:
```powershell
.\.venv\Scripts\python.exe scripts\channels.py doctor
```

*(Posting to multiple channels? See section 8.)*

---

## 6. Add a background video

Drop at least one **vertical-friendly** clip into **`assets/backgrounds/`** as
`.mp4` (Minecraft parkour, Subway Surfers, satisfying loops, etc.). The app
auto-crops it to 9:16 and loops it to fit the narration.

- Use footage you have the right to use (your own, CC0, or licensed) before
  monetizing.
- Optional: background music in `assets/music/`, and a bold caption font (e.g.
  `Montserrat-ExtraBold.ttf`) in `assets/fonts/` — paths are set in `config.yaml`.

---

## 7. Run it

**Confirm you're ready:**
```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

**Build one video WITHOUT uploading** (inspect `output/*.mp4` first):
```powershell
.\.venv\Scripts\python.exe main.py --limit 1 --no-upload
```

**Build + upload as private** (review on YouTube Studio before going public):
```powershell
.\.venv\Scripts\python.exe main.py --limit 1 --privacy private
```

**Everyday runs (double-clickable):**
- `run_main.bat` — build N Shorts and upload to your channel(s), one at a time.
- `run_parallel.bat` — build several at once and upload them in parallel, with a
  live progress dashboard. e.g. `run_parallel.bat --limit 3`.
- `dashboard.bat` — opens a visual stats dashboard (uploads, views, leaderboard).

The number of videos per run is `run.daily_quota` in `config.yaml` (default 3).

---

## 8. Multiple channels (optional)

You can post each Short to several channels at once. Each channel = a name with
its own cached login.

```powershell
# Add and authorize more channels (browser opens to pick each one):
.\.venv\Scripts\python.exe scripts\channels.py add second --login
.\.venv\Scripts\python.exe scripts\channels.py add third  --login

# See them all + health:
.\.venv\Scripts\python.exe scripts\channels.py doctor

# Post to every channel:
.\.venv\Scripts\python.exe main.py --channel all
#   (run_parallel.bat already targets all channels by default)
```

**Quota note:** each upload costs ~1600 of the default **10,000 units/day** per
Google Cloud project ≈ **6 uploads/day per project**. To run more channels, give
some of them their own Cloud project + `client_secret` (see `--client-secret` in
`channels.py add`), which gives each its own quota.

---

## 9. Tuning (`config.yaml`)

Everything is adjustable in `config.yaml`, including:
- `reddit.subreddits` — which subreddits to pull from.
- `reddit.allow_nsfw` — **false** (default) filters out adult content and refuses
  to upload it. Keep it false to stay advertiser-friendly.
- `tts` — voice engine and voice id.
- `youtube.privacy` — `private` / `unlisted` / `public`.
- `run.daily_quota` — videos per run.

---

## 10. Troubleshooting

**`ffmpeg not found` / video step fails**
FFmpeg isn't on your PATH. Reinstall (`winget install Gyan.FFmpeg`) and **reopen
the terminal**, or set `FFMPEG_BIN`/`FFPROBE_BIN` in `.env` to the full `.exe` paths.

**Fetching posts is slow / `ReadTimeout` warnings**
The app uses free public Reddit archives (arctic-shift, then pullpush) which can
be slow and retry automatically — warnings are normal and non-fatal. Adding a
Reddit API key (section 3) makes it faster.

**Upload fails: `access_denied: Account restricted`**
Google has restricted that account from the upload service (often after lots of
automated uploads on a new/secondary account). This is **not fixable in the app** —
sign into that Google account at <https://myaccount.google.com>, resolve the
restriction/appeal, then re-authorize: `channels.py login <name> --relogin`. If it
can't be cleared, use a different (phone-verified) account. Run `channels.py doctor`
to see each channel's health.

**Upload fails: token expired / needs re-auth**
Your OAuth consent screen is in "Testing" mode (7-day token expiry). Re-run
`channels.py login <name> --relogin`, or **Publish** the consent screen (section 4).

**Quota exceeded**
You hit ~6 uploads/day for that Cloud project. Wait for the daily reset, request
more quota in the Cloud Console, or split channels across separate Cloud projects.

---

## 11. Legal / policy (read before scaling)

- **Reddit content is user-owned.** For monetization, prefer subreddits that allow
  reuse, credit the source, and transform it (narration + visuals) — don't raw-repost.
- **YouTube discourages "inauthentic, mass-produced" content.** Vary backgrounds and
  voices, keep per-channel volume reasonable, and review uploads — start with
  `privacy: private` or `unlisted`.
- **Use background footage you have the right to use** before enabling monetization.
- Respect Reddit's and YouTube's terms and rate limits. Aggressive automation on new
  accounts is the most common cause of the account restrictions above.
