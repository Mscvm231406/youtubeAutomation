"""Mission-control dashboard for the Reddit → YouTube Shorts pipeline.

Gathers everything worth knowing — channels & auth status, upload feed, daily
API-quota burn per Cloud project, pipeline history, and the live config — and
renders a single self-contained futuristic HTML page, then opens it in the
browser. No server, no external data dependencies; just re-run to refresh.

Launch it with `dashboard.bat` (or `python dashboard.py`).
"""
from __future__ import annotations

import json
import os
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src import channels as ch
from src.utils import ROOT, load_config, log

# YouTube Data API quota accounting (units, not uploads). Real usage comes from
# Cloud Monitoring (state/analytics.json → quota_live, written by fetch_stats);
# when that's unavailable we estimate from the local upload count.
DAILY_UNIT_LIMIT = 10_000   # default per-project per-day quota (units)
UPLOAD_UNIT_COST = 1600     # units a single video insert costs


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (json.JSONDecodeError, OSError):
        return default


def _account_of(entry: dict) -> str:
    """Derive a short account/project label from the channel's client_secret
    filename, e.g. client_secret_cm.json -> 'cm'. Falls back to 'default'."""
    secret = (entry or {}).get("client_secret", "")
    stem = Path(secret).stem  # client_secret_cm
    if stem.startswith("client_secret_"):
        return stem[len("client_secret_"):] or "default"
    return "default"


def gather() -> dict:
    cfg = load_config()
    yt = cfg.get("youtube", {})
    registry = ch.load_channels(cfg)

    analytics = _read_json(ROOT / "state" / "analytics.json", {})

    events = _read_json(ROOT / "state" / "upload_log.json", [])
    events = [e for e in events if isinstance(e, dict)]
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    history = _read_json(ROOT / "state" / "history.json", [])
    today = datetime.now().date().isoformat()

    uploads_by_channel = Counter(e.get("channel") for e in events)
    stats_by_channel = {c.get("name"): c for c in analytics.get("channels", [])}

    # --- channels (merged with fetched stats where available) ---
    channels = []
    for name, entry in registry.items():
        entry = entry or {}
        token_file = entry.get("token_file") or ch.default_token_file(name)
        st = stats_by_channel.get(name, {})
        channels.append({
            "name": name,
            "account": _account_of(entry),
            "authorized": ch.is_authorized(registry, name),
            "token_file": token_file,
            "client_secret": entry.get("client_secret", yt.get("client_secret", "client_secret.json")),
            "privacy": entry.get("privacy", yt.get("privacy", "public")),
            "uploads": uploads_by_channel.get(name, 0),
            "yt_title": st.get("title"),
            "subscribers": st.get("subscribers"),
            "views_total": st.get("views_total"),
            "yt_videos": st.get("videos"),
            "stats_ok": bool(st) and not any(
                er.get("kind") in ("scope", "auth") for er in st.get("errors", [])),
        })

    acct_of_channel = {c["name"]: c["account"] for c in channels}
    today_events = [e for e in events if str(e.get("ts", "")).startswith(today)]

    # --- per-project YouTube Data API quota (units/day) ---
    # Prefer real usage from Cloud Monitoring (analytics.json → quota_live);
    # otherwise estimate from uploads in the last 24h × ~1600 units each.
    cutoff = datetime.now() - timedelta(hours=24)

    def _within_24h(e) -> bool:
        try:
            return datetime.fromisoformat(str(e.get("ts", ""))) >= cutoff
        except ValueError:
            return False

    window_events = [e for e in events if _within_24h(e)]
    per_project = Counter(acct_of_channel.get(e.get("channel"), "default")
                          for e in window_events)
    quota_live = analytics.get("quota_live", {}) if isinstance(analytics, dict) else {}
    quota = []
    for acct in sorted({c["account"] for c in channels}):
        n = per_project.get(acct, 0)               # uploads in last 24h (local)
        live = quota_live.get(acct) or {}
        used = live.get("used")
        if used is not None:                       # real Cloud Monitoring data
            mode, units = "live", used
            limit = live.get("limit") or DAILY_UNIT_LIMIT
        else:                                      # local fallback estimate
            mode, units = "estimate", n * UPLOAD_UNIT_COST
            limit = DAILY_UNIT_LIMIT
        quota.append({
            "project": acct,
            "project_id": live.get("project_id"),
            "mode": mode,
            "error": live.get("error"),
            "uploads": n,
            "units": units,
            "limit": limit,
            "pct": round(min(100, units / limit * 100)) if limit else 0,
            "remaining": max(0, limit - units),
        })

    # --- distinct videos (a video usually fans out to several channels) ---
    vids = {}
    for e in events:
        v = e.get("video", "")
        d = vids.setdefault(v, {"video": v, "title": e.get("title", ""),
                                "subreddit": e.get("subreddit", ""),
                                "ts": e.get("ts", ""), "channels": []})
        d["channels"].append(e.get("channel"))
    videos = sorted(vids.values(), key=lambda d: d["ts"], reverse=True)

    # Map each YouTube URL back to the local mp4 that produced it, so the
    # dashboard can loop the real file (the YouTube embed throws error 153 from
    # a file:// page / on Shorts). Only attach paths that actually exist.
    url_to_video = {}
    for e in events:
        u = e.get("url")
        if u and u not in url_to_video:
            url_to_video[u] = e.get("video", "")
    out_dir = ROOT / "output"
    leaderboard = analytics.get("leaderboard", [])
    for item in leaderboard:
        vid = url_to_video.get(item.get("url", ""))
        if vid and (out_dir / vid).exists():
            # relative path → resolves under both file:// and the local http server
            item["local"] = f"output/{vid}"

    atotals = analytics.get("totals", {})
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kpis": {
            "total_uploads": len(events),
            "uploads_today": len(today_events),
            "videos_produced": len(videos),
            "shorts_today": len({e.get("video") for e in today_events}),
            "channels_auth": sum(c["authorized"] for c in channels),
            "channels_total": len(channels),
            "posts_processed": len(set(history)),
            "subscribers": atotals.get("subscribers"),
            "views": atotals.get("views"),
            "revenue": atotals.get("revenue"),
        },
        "analytics": {
            "generated": analytics.get("generated"),
            "days": analytics.get("days", 30),
            "daily": analytics.get("daily", []),
            "totals": atotals,
            "have_stats": bool(analytics.get("channels")),
            "have_timeseries": any(p.get("views") for p in analytics.get("daily", [])),
            "monetized": atotals.get("monetized", False),
            "errors": sorted({er.get("kind") for c in analytics.get("channels", [])
                              for er in c.get("errors", [])}),
            "per_channel": [{"name": c.get("name"), "title": c.get("title"),
                             "subscribers": c.get("subscribers"),
                             "views": c.get("views_total")}
                            for c in analytics.get("channels", [])],
        },
        "channels": channels,
        "quota": quota,
        "leaderboard": leaderboard,
        "uploads": events[:40],
        "videos": videos[:12],
        "config": {
            "subreddits": cfg.get("reddit", {}).get("subreddits", []),
            "listing": cfg.get("reddit", {}).get("listing", ""),
            "time_filter": cfg.get("reddit", {}).get("time_filter", ""),
            "daily_quota": cfg.get("run", {}).get("daily_quota", ""),
            "privacy": yt.get("privacy", ""),
            "tts_engine": cfg.get("tts", {}).get("engine", ""),
            "edge_voice": cfg.get("tts", {}).get("edge_voice", ""),
            "elevenlabs_voice": cfg.get("tts", {}).get("elevenlabs_voice_id", ""),
            "resolution": f'{cfg.get("video", {}).get("width", "")}x{cfg.get("video", {}).get("height", "")}',
            "fps": cfg.get("video", {}).get("fps", ""),
            "use_llm": cfg.get("script", {}).get("use_llm", False),
            "max_speech": cfg.get("script", {}).get("max_speech_seconds", ""),
        },
    }


def render(data: dict) -> str:
    return HTML_TEMPLATE.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SHORTS · MISSION CONTROL</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#05060d; --bg2:#0a0e1c; --panel:rgba(18,24,44,.55); --panel-brd:rgba(110,170,255,.18);
  --cyan:#46f7ff; --mag:#ff5cd6; --lime:#9dff6a; --amber:#ffd36b; --red:#ff6b79;
  --ink:#f1f6ff; --dim:#c0cdec; --grid:rgba(80,140,255,.06);
  --shadow:0 0 40px rgba(55,245,255,.08);
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{
  font-family:'Rajdhani','Segoe UI',system-ui,sans-serif;
  font-weight:500;
  background:radial-gradient(1200px 700px at 80% -10%,rgba(40,80,200,.18),transparent 60%),
             radial-gradient(900px 600px at -10% 110%,rgba(255,69,209,.10),transparent 55%),
             linear-gradient(180deg,var(--bg),var(--bg2));
  color:var(--ink); min-height:100vh; overflow-x:hidden; letter-spacing:.2px;
}
body::before{ /* animated grid */
  content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:46px 46px; mask-image:radial-gradient(ellipse at 50% 30%,#000 40%,transparent 85%);
  animation:drift 22s linear infinite;
}
@keyframes drift{from{background-position:0 0,0 0}to{background-position:46px 920px,920px 46px}}
.wrap{position:relative; z-index:1; width:100%; margin:0 auto; padding:30px 46px 64px}
header{display:flex; align-items:center; justify-content:space-between; gap:20px; flex-wrap:wrap; margin-bottom:8px}
.brand{display:flex; align-items:center; gap:16px}
.logo{width:46px;height:46px;border-radius:12px;
  background:conic-gradient(from 0deg,var(--cyan),var(--mag),var(--cyan));
  filter:drop-shadow(0 0 14px rgba(55,245,255,.55)); animation:spin 8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
h1{font-family:'Orbitron',sans-serif; font-weight:900; font-size:26px; margin:0; line-height:1;
   background:linear-gradient(90deg,#fff,var(--cyan)); -webkit-background-clip:text; background-clip:text; color:transparent}
.sub{color:var(--dim); font-size:13px; text-transform:uppercase; letter-spacing:3px; margin-top:5px}
.status{display:flex; align-items:center; gap:10px; font-family:'Orbitron'; font-size:12px; letter-spacing:2px;
  padding:10px 16px; border:1px solid var(--panel-brd); border-radius:30px; background:var(--panel); backdrop-filter:blur(10px)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--lime);box-shadow:0 0 10px var(--lime);animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.gen{color:var(--dim);font-size:12px;margin-top:4px;text-align:right}

.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin:22px 0}
.kpi{background:var(--panel);border:1px solid var(--panel-brd);border-radius:16px;padding:16px 16px 14px;
  position:relative;overflow:hidden;backdrop-filter:blur(8px);box-shadow:var(--shadow)}
.kpi::after{content:"";position:absolute;left:0;top:0;height:3px;width:100%;
  background:linear-gradient(90deg,var(--cyan),transparent)}
.kpi .v{font-family:'Orbitron';font-size:34px;font-weight:800;line-height:1}
.kpi .l{color:var(--dim);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:8px}
.kpi.alt::after{background:linear-gradient(90deg,var(--mag),transparent)}
.kpi.ok::after{background:linear-gradient(90deg,var(--lime),transparent)}

.grid{display:grid;grid-template-columns:1.4fr 1fr;gap:18px;margin-bottom:18px}
.grid3{grid-template-columns:1fr 1fr 1fr}
.chartbox{position:relative}
.chartbox svg{width:100%;height:170px;display:block;overflow:visible}
.chartbox .big{font-family:'Orbitron';font-size:26px;margin-bottom:2px}
.chartbox .cap{color:var(--dim);font-size:13px;font-weight:600;letter-spacing:.6px}
.chartbox .delta{font-size:12px;margin-left:8px}
.up{color:var(--lime)} .down{color:var(--red)}
.note{border:1px dashed rgba(255,204,85,.4);background:rgba(255,204,85,.06);color:var(--amber);
  border-radius:12px;padding:11px 14px;font-size:12.5px;margin:6px 0 16px;letter-spacing:.3px}
.note b{color:#ffe1a0}
.hbar{margin:11px 0}
.hbar .row{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px}
.hbar .row b{font-family:'Orbitron';color:var(--ink)}
.track{height:12px;border-radius:8px;background:rgba(120,160,255,.12);overflow:hidden}
.fill{height:100%;border-radius:8px;background:linear-gradient(90deg,var(--cyan),var(--mag));
  width:0;transition:width 1.2s cubic-bezier(.2,.8,.2,1);box-shadow:0 0 12px rgba(55,245,255,.4)}
.fill.s{background:linear-gradient(90deg,var(--lime),var(--cyan))}
.card{background:var(--panel);border:1px solid var(--panel-brd);border-radius:18px;padding:20px;
  backdrop-filter:blur(8px);box-shadow:var(--shadow)}
.card h2{font-family:'Orbitron';font-size:14px;letter-spacing:3px;text-transform:uppercase;margin:0 0 16px;
  color:var(--cyan);display:flex;align-items:center;gap:10px}
.card h2 .bar{flex:1;height:1px;background:linear-gradient(90deg,var(--panel-brd),transparent)}

.chgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.ch{border:1px solid var(--panel-brd);border-radius:14px;padding:14px;background:rgba(10,16,34,.5);position:relative}
.ch .top{display:flex;align-items:center;justify-content:space-between}
.ch .nm{font-family:'Orbitron';font-weight:700;font-size:16px}
.ch .ytname{font-size:13.5px;font-weight:700;color:var(--cyan);margin-top:6px;line-height:1.3;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{font-size:10px;letter-spacing:2px;text-transform:uppercase;padding:3px 9px;border-radius:20px;border:1px solid var(--panel-brd);color:var(--dim)}
.led{display:inline-flex;align-items:center;gap:7px;font-size:12px;letter-spacing:1px;margin-top:10px}
.led i{width:9px;height:9px;border-radius:50%}
.led.on i{background:var(--lime);box-shadow:0 0 10px var(--lime);animation:pulse 1.8s infinite}
.led.off i{background:var(--red);box-shadow:0 0 10px var(--red)}
.ch .meta{color:var(--dim);font-size:12.5px;font-weight:500;line-height:1.55;margin-top:8px;word-break:break-all}
.ch .up{position:absolute;right:14px;bottom:12px;font-family:'Orbitron';font-size:20px;color:var(--cyan)}
.ch .up small{font-size:10px;color:var(--dim);letter-spacing:1px}

.gauge{display:flex;align-items:center;gap:16px;margin-bottom:14px}
.gauge svg{flex:none}
.gauge .info{min-width:0}
.gauge .info .p{font-family:'Orbitron';font-size:22px}
.gauge .info .t{color:var(--dim);font-size:12px;letter-spacing:1px;text-transform:uppercase}
.gauge .info .r{font-size:13px;margin-top:3px}
.gauge .info .leg{display:inline-flex;align-items:center;gap:4px;margin-left:10px;font-size:11px;color:var(--dim);letter-spacing:.5px}
.gauge .info .leg i{width:9px;height:9px;border-radius:3px;display:inline-block}

.feed{max-height:430px;overflow:auto}
.evt{display:flex;gap:12px;padding:11px 0;border-bottom:1px dashed rgba(120,160,255,.12)}
.evt:last-child{border-bottom:0}
.evt .pill{font-family:'Orbitron';font-size:10px;letter-spacing:1px;padding:4px 8px;border-radius:8px;height:fit-content;
  border:1px solid var(--panel-brd);color:var(--cyan);white-space:nowrap}
.evt .body{flex:1;min-width:0}
.evt .ti{font-size:14.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.evt .mi{color:var(--dim);font-size:12.5px;font-weight:500;margin-top:4px;letter-spacing:.3px}
.evt a{color:var(--cyan);text-decoration:none}
.evt a:hover{text-decoration:underline}

.cfg{display:flex;flex-wrap:wrap;gap:9px}
.chip{border:1px solid var(--panel-brd);border-radius:10px;padding:8px 12px;background:rgba(10,16,34,.5);font-size:13.5px;font-weight:600}
.chip b{color:var(--dim);font-weight:700;text-transform:uppercase;font-size:10.5px;letter-spacing:1.2px;display:block;margin-bottom:3px}
.subs{display:flex;flex-wrap:wrap;gap:7px;margin-top:4px}
.subs span{font-size:12px;padding:4px 10px;border-radius:20px;background:rgba(55,245,255,.08);border:1px solid var(--panel-brd);color:var(--cyan)}
.vids{display:grid;grid-template-columns:1fr;gap:10px}
.vid{display:flex;gap:10px;align-items:center;border:1px solid var(--panel-brd);border-radius:12px;padding:10px 12px;background:rgba(10,16,34,.45)}
.vid .n{flex:1;min-width:0}
.vid .n .t{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vid .n .s{color:var(--dim);font-size:11px}
.vid .cnt{font-family:'Orbitron';color:var(--lime);font-size:15px}
.full{grid-column:1/-1}
.board{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:15px}
.tile{position:relative;border:1px solid var(--panel-brd);border-radius:14px;overflow:hidden;
  background:rgba(10,16,34,.55);transition:transform .18s,box-shadow .18s,border-color .18s}
.tile:hover{transform:translateY(-4px);box-shadow:0 12px 34px rgba(55,245,255,.28);border-color:rgba(70,247,255,.5)}
.tile .thumb{position:relative;aspect-ratio:16/9;background:#0a0e1c;overflow:hidden}
.tile img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s,filter .25s}
.tile:hover img{transform:scale(1.06);filter:brightness(.5) saturate(1.1)}
.tile .ph{display:flex;align-items:center;justify-content:center;height:100%;color:var(--dim);font-size:11px;letter-spacing:1px}
.tile .rank{position:absolute;top:8px;left:8px;width:30px;height:30px;border-radius:9px;display:flex;align-items:center;justify-content:center;
  font-family:'Orbitron';font-weight:800;font-size:14px;background:rgba(5,6,13,.8);border:1px solid var(--panel-brd);color:var(--cyan);z-index:4}
.tile.g1 .rank{color:#0a0e1c;background:linear-gradient(135deg,#ffe27a,#ffb800);border:0}
.tile.g2 .rank{color:#0a0e1c;background:linear-gradient(135deg,#eef2f8,#aab8d0);border:0}
.tile.g3 .rank{color:#0a0e1c;background:linear-gradient(135deg,#ffc08a,#d97b3c);border:0}
.tile .vbadge{position:absolute;bottom:8px;right:8px;background:rgba(5,6,13,.82);border:1px solid var(--panel-brd);
  border-radius:8px;padding:3px 9px;font-family:'Orbitron';font-size:12px;color:var(--lime);z-index:4;transition:opacity .2s}
.tile:hover .vbadge{opacity:0}
/* info hidden until hover — thumbnails-only by default */
.tile .info{position:absolute;inset:0;z-index:3;display:flex;flex-direction:column;justify-content:flex-end;
  padding:46px 13px 13px;background:linear-gradient(180deg,rgba(5,6,13,.1) 0%,rgba(5,6,13,.55) 45%,rgba(5,6,13,.92) 100%);
  opacity:0;transition:opacity .2s ease}
.tile:hover .info{opacity:1}
.tile .vt{font-size:13.5px;font-weight:600;line-height:1.38;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tile .vm{display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:12px;color:var(--dim);font-weight:600}
.tile .chpill{color:var(--cyan);border:1px solid var(--panel-brd);border-radius:7px;padding:2px 8px;letter-spacing:1px;text-transform:uppercase;font-size:10px;background:rgba(5,6,13,.6)}
.tile a.lk{position:absolute;inset:0;z-index:5}

/* ===== Now-playing #1 looping player ===== */
.player{display:grid;grid-template-columns:auto 1fr;gap:26px;align-items:center}
.player .screen{position:relative;width:300px;max-width:62vw;aspect-ratio:9/16;border-radius:18px;overflow:hidden;
  border:1px solid rgba(70,247,255,.35);box-shadow:0 0 40px rgba(70,247,255,.22),inset 0 0 0 1px rgba(255,255,255,.04);background:#000}
.player .screen iframe,.player .screen video{position:absolute;inset:0;width:100%;height:100%;border:0;object-fit:cover;background:#000}
.player .screen .ph{display:flex;align-items:center;justify-content:center;height:100%;color:var(--dim);font-size:13px;letter-spacing:1px;text-align:center;padding:0 20px}
.player .screen .poster{position:absolute;inset:0;display:block;text-decoration:none}
.player .screen .poster img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.player .screen .poster .playbtn{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:62px;height:62px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:24px;color:#04050b;background:linear-gradient(135deg,var(--cyan),var(--mag));
  box-shadow:0 0 26px rgba(70,247,255,.6);padding-left:4px}
.player .screen .poster .ytnote{position:absolute;bottom:0;left:0;right:0;text-align:center;padding:10px;
  font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#dceaff;background:linear-gradient(0deg,rgba(5,6,13,.85),transparent)}
.player .det .lbl{font-family:'Orbitron';font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--mag)}
.player .det .nptitle{font-family:'Orbitron';font-weight:700;font-size:22px;line-height:1.3;margin:10px 0 6px}
.player .det .npmeta{color:var(--dim);font-size:13.5px;font-weight:600;letter-spacing:.4px}
.player .det .npstat{display:flex;gap:24px;margin-top:18px}
.player .det .npstat .b{font-family:'Orbitron';font-size:26px;color:var(--lime)}
.player .det .npstat .s{color:var(--dim);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;margin-top:3px}
.player .det a.watch{display:inline-block;margin-top:20px;font-family:'Orbitron';font-size:12px;letter-spacing:2px;
  color:var(--cyan);text-decoration:none;border:1px solid var(--panel-brd);border-radius:30px;padding:11px 22px;transition:.15s}
.player .det a.watch:hover{box-shadow:0 0 18px rgba(70,247,255,.4);background:rgba(70,247,255,.08)}
footer{color:var(--dim);font-size:12.5px;font-weight:500;text-align:center;margin-top:28px;letter-spacing:.6px}
::-webkit-scrollbar{width:8px}::-webkit-scrollbar-thumb{background:rgba(80,140,255,.25);border-radius:8px}
@media(max-width:980px){.wrap{padding:22px 16px 50px}.kpis{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}.grid3{grid-template-columns:1fr}.chgrid{grid-template-columns:1fr}
  .player{grid-template-columns:1fr;justify-items:center;text-align:center;gap:18px}
  .player .det .npstat{justify-content:center}.player .screen{width:248px}}
@media(max-width:560px){.kpis{grid-template-columns:repeat(2,1fr)}.board{grid-template-columns:repeat(2,1fr);gap:11px}}

/* ===== Boot screen ===== */
#boot{position:fixed;inset:0;z-index:1000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;
  background:radial-gradient(900px 600px at 50% 38%,rgba(40,90,220,.16),transparent 60%),linear-gradient(180deg,#04050b,#070b18);
  transition:opacity .7s ease, filter .7s ease}
#boot.gone{opacity:0;pointer-events:none;filter:blur(8px)}
.boot-logo{width:88px;height:88px;border-radius:20px;
  background:conic-gradient(from 0deg,var(--cyan),var(--mag),var(--cyan));
  filter:drop-shadow(0 0 26px rgba(70,247,255,.7));animation:spin 5s linear infinite}
.boot-title{font-family:'Orbitron';font-weight:900;font-size:34px;letter-spacing:6px;
  background:linear-gradient(90deg,#fff,var(--cyan));-webkit-background-clip:text;background-clip:text;color:transparent;
  text-shadow:0 0 30px rgba(70,247,255,.25);animation:glitch 3.5s infinite}
@keyframes glitch{0%,92%,100%{transform:none;opacity:1}93%{transform:translate(-2px,1px);opacity:.85}95%{transform:translate(2px,-1px)}97%{transform:translate(-1px,0)}}
.boot-sub{color:var(--dim);font-size:12px;letter-spacing:5px;text-transform:uppercase;margin-top:-12px}
.boot-log{font-family:'Consolas','Courier New',monospace;width:min(620px,86vw);height:182px;overflow:hidden;
  font-size:13px;line-height:1.65;color:#9fe9ff;text-align:left;
  border:1px solid var(--panel-brd);border-radius:12px;padding:14px 16px;background:rgba(8,12,26,.6)}
.boot-log .ln{opacity:0;animation:lineIn .25s forwards}
.boot-log .ok{color:var(--lime);float:right;font-weight:700}
.boot-log .warn{color:var(--amber);float:right;font-weight:700}
@keyframes lineIn{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
.boot-barwrap{width:min(620px,86vw);display:flex;align-items:center;gap:14px}
.boot-bar{flex:1;height:12px;border-radius:8px;border:1px solid var(--panel-brd);background:rgba(120,160,255,.1);overflow:hidden}
.boot-bar i{display:block;height:100%;width:0;border-radius:8px;
  background:linear-gradient(90deg,var(--cyan),var(--mag));box-shadow:0 0 16px rgba(70,247,255,.6);transition:width .25s ease}
.boot-pct{font-family:'Orbitron';font-size:15px;min-width:48px;text-align:right;color:var(--cyan)}
.scanlines{position:fixed;inset:0;z-index:1500;pointer-events:none;opacity:.02;
  background:repeating-linear-gradient(0deg,#fff 0 1px,transparent 1px 5px)}
.mutebtn{position:fixed;top:16px;right:16px;z-index:1600;width:42px;height:42px;border-radius:12px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:18px;background:var(--panel);
  border:1px solid var(--panel-brd);backdrop-filter:blur(8px);color:var(--cyan);transition:.15s}
.mutebtn:hover{box-shadow:0 0 16px rgba(70,247,255,.4);transform:scale(1.06)}

/* ===== Chroma digital clock ===== */
.clock-wrap{flex:1;text-align:center;min-width:230px;order:2}
.clock{display:inline-block;font-family:'Orbitron';font-weight:900;font-size:34px;letter-spacing:5px;line-height:1;
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;
  background:linear-gradient(90deg,#ff5cd6,#46f7ff,#9dff6a,#ffd36b,#46f7ff,#ff5cd6);background-size:300% 100%;
  -webkit-background-clip:text;background-clip:text;color:transparent;
  text-shadow:0 0 16px rgba(70,247,255,.4);will-change:background-position;
  animation:chroma 7s linear infinite}
.clock-date{font-family:'Rajdhani';font-weight:600;color:var(--dim);font-size:12px;letter-spacing:4px;text-transform:uppercase;margin-top:5px}
@keyframes chroma{to{background-position:300% 0}}

@media(max-width:980px){.clock-wrap{order:0;flex-basis:100%;margin:6px 0}}
</style>
</head>
<body>
<div id="boot">
  <div class="boot-logo"></div>
  <div class="boot-title">MISSION CONTROL</div>
  <div class="boot-sub">Reddit → YouTube · automation core</div>
  <div class="boot-log" id="bootlog"></div>
  <div class="boot-barwrap"><div class="boot-bar"><i id="bootbar"></i></div><div class="boot-pct" id="bootpct">0%</div></div>
</div>
<div class="scanlines"></div>
<div class="mutebtn" id="muteBtn" title="Toggle sound">🔊</div>

<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo"></div>
      <div>
        <h1>SHORTS · MISSION CONTROL</h1>
        <div class="sub">Reddit → YouTube automation</div>
      </div>
    </div>
    <div class="clock-wrap">
      <div class="clock" id="clock">00:00:00</div>
      <div class="clock-date" id="clockdate">— · — —</div>
    </div>
    <div>
      <div class="status"><span class="dot"></span><span id="sysstat">SYSTEMS NOMINAL</span></div>
      <div class="gen">SNAPSHOT · <span id="gen"></span></div>
    </div>
  </header>

  <section class="kpis" id="kpis"></section>
  <div id="anote"></div>

  <div class="grid grid3">
    <div class="card">
      <h2>Views · <span id="winlbl">30d</span> <span class="bar"></span></h2>
      <div id="viewsChart" class="chartbox"></div>
    </div>
    <div class="card">
      <h2>Subscribers · net <span class="bar"></span></h2>
      <div id="subsChart" class="chartbox"></div>
    </div>
    <div class="card">
      <h2>Est. Revenue · <span id="revlbl">30d</span> <span class="bar"></span></h2>
      <div id="revChart" class="chartbox"></div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Subscribers by Channel <span class="bar"></span></h2>
      <div id="subsByCh"></div>
    </div>
    <div class="card">
      <h2>Lifetime Views by Channel <span class="bar"></span></h2>
      <div id="viewsByCh"></div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Channels <span class="bar"></span><span id="chsummary" style="color:var(--dim);font-size:12px"></span></h2>
      <div class="chgrid" id="channels"></div>
    </div>
    <div class="card">
      <h2>API Quota · per project <span class="bar"></span><span style="color:var(--dim);font-size:12px">YouTube Data API · units/day</span></h2>
      <div id="quota"></div>
    </div>
  </div>

  <div class="card full" id="nowplaying" style="display:none">
    <h2>▶ Now Playing · #1 <span class="bar"></span><span style="color:var(--dim);font-size:12px">auto-looping</span></h2>
    <div class="player" id="player"></div>
  </div>

  <div class="card full">
    <h2>🏆 Top 10 Videos · Leaderboard <span class="bar"></span><span id="lbnote" style="color:var(--dim);font-size:12px"></span></h2>
    <div class="board" id="leaderboard"></div>
  </div>

  <div class="card full">
    <h2>Upload Feed <span class="bar"></span></h2>
    <div class="feed" id="feed"></div>
  </div>

  <div class="card full">
    <h2>Pipeline Configuration <span class="bar"></span></h2>
    <div class="cfg" id="config"></div>
    <div style="margin-top:14px"><b style="color:var(--dim);font-size:10px;letter-spacing:1.5px;text-transform:uppercase">Subreddit rotation</b>
      <div class="subs" id="subs"></div></div>
  </div>

  <footer>Re-run <b style="color:var(--cyan)">dashboard.bat</b> to refresh · generated locally, no data leaves this machine</footer>
</div>

<script>
const DATA = /*__DATA__*/;
const $=(s)=>document.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const fmt=(n)=>{n=+n||0;return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':(''+Math.round(n));};
const money=(n)=>'$'+(+n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const esc=(s)=>(''+(s==null?'':s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

// --- KPIs with count-up ---
const K=DATA.kpis;
const cards=[
  {v:K.total_uploads,l:'Total Uploads',cls:''},
  {v:K.uploads_today,l:'Uploads Today',cls:'alt'},
  {v:K.videos_produced,l:'Videos',cls:''},
  {v:K.channels_auth+'/'+K.channels_total,l:'Channels Live',cls:'ok',raw:true},
  {v:K.posts_processed,l:'Posts Used',cls:''},
  {v:K.shorts_today,l:'Shorts Today',cls:'alt'},
];
const kw=$('#kpis');
cards.forEach(c=>{
  const k=el('div','kpi '+c.cls);
  const v=el('div','v',c.raw?c.v:'0'); k.appendChild(v); k.appendChild(el('div','l',c.l)); kw.appendChild(k);
  if(!c.raw){const tgt=+c.v||0;let n=0;const step=Math.max(1,Math.round(tgt/28));
    const id=setInterval(()=>{n+=step;if(n>=tgt){n=tgt;clearInterval(id);}v.textContent=n;},22);}
});

// --- Channels ---
$('#chsummary').textContent=DATA.channels.length+' configured';
const cw=$('#channels');
DATA.channels.forEach(c=>{
  const card=el('div','ch');
  card.appendChild(el('div','top',
    `<span class="nm">${esc(c.name)}</span><span class="tag">${esc(c.account).toUpperCase()} · ${esc(c.privacy)}</span>`));
  card.appendChild(el('div','ytname',
    c.yt_title?`▶ ${esc(c.yt_title)}`:'<span style="color:var(--dim)">channel name unavailable</span>'));
  card.appendChild(el('div','led '+(c.authorized?'on':'off'),
    `<i></i>${c.authorized?'AUTHORIZED':'NOT AUTHORIZED'}`));
  card.appendChild(el('div','meta',`🔑 ${c.client_secret}<br>🎫 ${c.token_file}`));
  card.appendChild(el('div','up',`${c.uploads}<br><small>UPLOADS</small>`));
  cw.appendChild(card);
});

// --- API-quota PIE charts: YouTube Data API units used vs remaining per project.
//     mode 'live' = real Cloud Monitoring data; 'estimate' = uploads×1600 fallback. ---
const qw=$('#quota');
const QHINT={api_disabled:'Enable the Cloud Monitoring API in this project',
  billing:'Enable billing on this Cloud project (Monitoring reads require it)',
  scope:'Re-auth needed: channels.py login <name> --relogin',
  no_project:'No project_id in client secret',
  error:'Monitoring query failed — showing estimate'};
const col=(p)=>p>=90?'#ff5d6c':p>=65?'#ffcc55':'#37f5ff';
const CX=46,CY=46,RAD=40;
// One pie slice path for `frac` of the circle, starting at 12 o'clock, clockwise.
function pie(frac,color){
  frac=Math.max(0,Math.min(1,frac));
  const track=`<circle cx="${CX}" cy="${CY}" r="${RAD}" fill="rgba(120,160,255,.13)"/>`;
  let used='';
  if(frac>=1){
    used=`<circle cx="${CX}" cy="${CY}" r="${RAD}" fill="${color}"/>`;
  }else if(frac>0){
    const a=2*Math.PI*frac, x=CX+RAD*Math.sin(a), y=CY-RAD*Math.cos(a), lg=frac>0.5?1:0;
    used=`<path d="M${CX} ${CY} L${CX} ${CY-RAD} A${RAD} ${RAD} 0 ${lg} 1 ${x.toFixed(2)} ${y.toFixed(2)} Z"
            fill="${color}" style="filter:drop-shadow(0 0 5px ${color})"/>`;
  }
  return `<svg width="96" height="96" viewBox="0 0 92 92">${track}${used}
    <circle cx="${CX}" cy="${CY}" r="${RAD}" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="1"/></svg>`;
}
const num=(n)=>(+n||0).toLocaleString();
DATA.quota.forEach(q=>{
  const c=col(q.pct);
  const g=el('div','gauge');
  const live=q.mode==='live';
  const tag=live?'<span style="color:var(--lime);font-size:11px">● live</span>'
                :'<span style="color:var(--amber);font-size:11px">~ estimate</span>';
  const pid=q.project_id?` <span style="color:var(--dim);font-size:11px">· ${esc(q.project_id)}</span>`:'';
  const hint=(!live&&q.error)?
    `<div class="r" style="color:var(--amber);margin-top:5px;font-size:11px">${esc(QHINT[q.error]||QHINT.error)}</div>`:'';
  g.innerHTML=pie(q.units/q.limit, c)+
    `<div class="info"><div class="t">Project ${esc(q.project).toUpperCase()} · API units ${tag}${pid}</div>
     <div class="p">${num(q.units)} <span style="font-size:12px;color:var(--dim)">/ ${num(q.limit)} units/day</span></div>
     <div class="r">${q.pct}% used · <b style="color:var(--lime)">${num(q.remaining)}</b> left
       <span class="leg"><i style="background:${c}"></i>used <i style="background:rgba(120,160,255,.4);margin-left:8px"></i>free</span></div>
     ${hint}</div>`;
  qw.appendChild(g);
});
if(!DATA.quota.length)qw.appendChild(el('div','mi','No channels configured.'));

// --- Upload feed ---
const fw=$('#feed');
if(!DATA.uploads.length)fw.appendChild(el('div','mi','No uploads logged yet — run run_main.bat.'));
DATA.uploads.forEach(e=>{
  const v=el('div','evt');
  const t=(e.ts||'').replace('T',' ').slice(5,16);
  v.innerHTML=`<span class="pill">${e.channel}</span>
    <div class="body"><div class="ti">${e.title||e.video}</div>
    <div class="mi">${t} · r/${e.subreddit||'?'} · <a href="${e.url}" target="_blank">${(e.url||'').replace('https://youtube.com/shorts/','▶ ')}</a></div></div>`;
  fw.appendChild(v);
});

// Pull the YouTube video id out of a shorts/watch/embed url.
const vidId=(u)=>{const m=(''+(u||'')).match(/(?:shorts\/|watch\?v=|embed\/|youtu\.be\/)([\w-]{6,})/);return m?m[1]:'';};

// --- Now Playing: AUTOPLAY the #1 short on loop. Prefer the local mp4; else a
//     YouTube embed (autoplays+loops fine when served over http://localhost —
//     the file:// origin is what triggers error 153, so on file:// we fall back
//     to a clickable thumbnail rather than a broken embed). ---
(function(){
  const top=(DATA.leaderboard||[])[0]; if(!top)return;
  const np=$('#nowplaying'), pw=$('#player'); if(!np||!pw)return;
  np.style.display='';
  const fileProto=location.protocol==='file:';
  let frame;
  if(top.local){
    frame=`<video src="${esc(top.local)}" autoplay muted loop playsinline controls
             poster="${esc(top.thumb||'')}"></video>`;
  }else{
    const id=vidId(top.url);
    if(id && !fileProto){
      frame=`<iframe src="https://www.youtube.com/embed/${esc(id)}?autoplay=1&mute=1&loop=1&playlist=${esc(id)}&controls=1&rel=0&modestbranding=1&playsinline=1"
               allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
    }else{
      const t=top.thumb?`<img src="${esc(top.thumb)}" alt="" onerror="this.style.display='none'">`:'';
      frame=`<a class="poster" href="${esc(top.url)}" target="_blank" title="Open on YouTube">
               ${t}<span class="playbtn">▶</span>
               <span class="ytnote">Run dashboard.bat for autoplay · or tap to watch</span></a>`;
    }
  }
  pw.innerHTML=`<div class="screen">${frame}</div>
    <div class="det">
      <div class="lbl">★ Top Performer · on loop</div>
      <div class="nptitle">${esc(top.title)}</div>
      <div class="npmeta">r/${esc(top.subreddit)||'?'} · ${esc(top.channel)}</div>
      <div class="npstat">
        <div><div class="b">${fmt(top.views)}</div><div class="s">Views</div></div>
        ${top.likes!=null?`<div><div class="b">${fmt(top.likes)}</div><div class="s">Likes</div></div>`:''}
      </div>
      <a class="watch" href="${esc(top.url)}" target="_blank">OPEN ON YOUTUBE ↗</a>
    </div>`;
})();

// --- Top-videos leaderboard (thumbnails only, info on hover; capped at top 10) ---
const lb=(DATA.leaderboard||[]).slice(0,10);
const lw=$('#leaderboard');
const totalViews=lb.reduce((a,v)=>a+(+v.views||0),0);
$('#lbnote').textContent=lb.length?`top ${lb.length} · ${fmt(totalViews)} views · hover for details`:'';
if(!lb.length){
  lw.innerHTML='<div class="mi" style="color:var(--dim)">No videos yet — run run_main.bat, then fetch_stats.</div>';
}else lb.forEach((v,i)=>{
  const t=el('div','tile'+(i<3?' g'+(i+1):''));
  const thumb=v.thumb?`<img src="${esc(v.thumb)}" alt="" loading="lazy" onerror="this.parentNode.innerHTML='<div class=&quot;ph&quot;>thumbnail offline</div>'">`:'<div class="ph">no thumbnail</div>';
  t.innerHTML=`<div class="thumb"><div class="rank">${i+1}</div>${thumb}<div class="vbadge">▶ ${fmt(v.views)}</div></div>
    <div class="info"><div class="vt">${esc(v.title)}</div>
      <div class="vm"><span class="chpill">${esc(v.channel)}</span><span>r/${esc(v.subreddit)||'?'}</span></div></div>
    <a class="lk" href="${esc(v.url)}" target="_blank" title="Open on YouTube"></a>`;
  lw.appendChild(t);
});

// ===== Analytics graphs =====
const A=DATA.analytics||{};
$('#winlbl').textContent=$('#revlbl').textContent=(A.days||30)+'d';

// Status banner explaining any gaps in the data.
(function(){
  const n=$('#anote'); const e=A.errors||[]; const msgs=[];
  if(!A.have_stats) msgs.push('No stats fetched yet — run <b>fetch_stats.bat</b> (or dashboard.bat) after authorizing channels.');
  if(e.includes('scope')) msgs.push('Some channels need re-auth for stats: <b>python scripts/channels.py login &lt;name&gt; --relogin</b>.');
  if(e.includes('api_disabled')) msgs.push('Enable the <b>YouTube Analytics API</b> in each Cloud project for the day-by-day views/subs/revenue lines (totals still show).');
  if(A.have_stats && !A.monetized) msgs.push('Revenue reads <b>$0.00</b> — channels aren’t in the YouTube Partner Program yet (expected for new channels).');
  if(msgs.length){n.className='note';n.innerHTML='⚠ '+msgs.join('  ·  ');}
})();

// SVG area+line chart from a series of {date,<key>} points.
function lineChart(box, pts, key, color){
  const W=420,H=170,P=18, n=pts.length;
  const vals=pts.map(p=>+p[key]||0); const max=Math.max(1,...vals);
  const X=i=>P+(W-2*P)*(n<=1?0.5:i/(n-1));
  const Y=v=>H-P-(H-2*P)*(v/max);
  let line='',area='';
  pts.forEach((p,i)=>{const x=X(i),y=Y(vals[i]);line+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' ';});
  area=line+`L${X(n-1).toFixed(1)} ${H-P} L${X(0).toFixed(1)} ${H-P} Z`;
  const gid='g'+Math.abs(hash(key+color));
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".45"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="rgba(140,162,201,.25)"/>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2.4"
      style="filter:drop-shadow(0 0 5px ${color})"/>
    <circle cx="${X(n-1)}" cy="${Y(vals[n-1]||0)}" r="3.6" fill="${color}"
      style="filter:drop-shadow(0 0 6px ${color})"/></svg>`;
}
function hash(s){let h=0;for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))|0;return h;}
function head(box,big,cap,deltaHtml){
  const d=document.createElement('div');
  d.innerHTML=`<div class="big">${big}${deltaHtml||''}</div><div class="cap">${cap}</div>`;
  box.parentNode.insertBefore(d,box);
}
const daily=A.daily||[]; const T=A.totals||{};
const sum=(k)=>daily.reduce((a,p)=>a+(+p[k]||0),0);

// Views
(function(){const b=$('#viewsChart');
  head(b, fmt(T.views??0), `lifetime · ${fmt(sum('views'))} in ${A.days||30}d`);
  lineChart(b, daily.length?daily:[{views:0},{views:0}], 'views', '#37f5ff');})();
// Subscribers (net per day)
(function(){const b=$('#subsChart');const net=sum('subs');
  head(b, fmt(T.subscribers??0), `total · net ${net>=0?'+':''}${net} in ${A.days||30}d`,
      ` <span class="delta ${net>=0?'up':'down'}">${net>=0?'▲':'▼'}</span>`);
  lineChart(b, daily.length?daily:[{subs:0},{subs:0}], 'subs', '#8dff5a');})();
// Revenue
(function(){const b=$('#revChart');
  head(b, money(T.revenue??0), A.monetized?`${A.days||30}d estimated`:'not monetized');
  lineChart(b, daily.length?daily:[{revenue:0},{revenue:0}], 'revenue', '#ff45d1');})();

// Horizontal bars per channel
function bars(box, rows, key, cls){
  const max=Math.max(1,...rows.map(r=>+r[key]||0));
  rows.forEach(r=>{
    const v=+r[key]||0; const w=el('div','hbar');
    w.innerHTML=`<div class="row"><span>${r.title||r.name} <span style="color:var(--dim);font-size:11px">(${r.name})</span></span><b>${fmt(v)}</b></div>
      <div class="track"><div class="fill ${cls||''}" data-w="${(v/max*100).toFixed(1)}"></div></div>`;
    box.appendChild(w);
  });
}
const pc=A.per_channel||[];
if(pc.length){bars($('#subsByCh'),pc,'subscribers','s');bars($('#viewsByCh'),pc,'views','');}
else{$('#subsByCh').innerHTML='<div class="mi" style="color:var(--dim)">No channel stats yet.</div>';
     $('#viewsByCh').innerHTML='<div class="mi" style="color:var(--dim)">No channel stats yet.</div>';}
setTimeout(()=>document.querySelectorAll('.fill[data-w]').forEach(f=>f.style.width=f.dataset.w+'%'),140);

// --- Config ---
const C=DATA.config, cf=$('#config');
const rows=[
  ['Daily quota',C.daily_quota+' shorts/run'],['Privacy',C.privacy],
  ['TTS engine',C.tts_engine],['Edge voice',C.edge_voice],
  ['ElevenLabs voice',C.elevenlabs_voice],['LLM rewrite',C.use_llm?'on':'off'],
  ['Listing',C.listing+' · '+C.time_filter],['Resolution',C.resolution+' @'+C.fps+'fps'],
  ['Max speech',C.max_speech+'s'],
];
rows.forEach(([k,val])=>cf.appendChild(el('div','chip',`<b>${k}</b>${val}`)));
const sw=$('#subs');
(C.subreddits||[]).forEach(s=>sw.appendChild(el('span',null,'r/'+s)));
$('#gen').textContent=DATA.generated;

// ===== Sound effects (Web Audio — synthesized, no files) =====
let _ac=null, MUTED=false;
function ac(){ try{ if(!_ac)_ac=new (window.AudioContext||window.webkitAudioContext)(); if(_ac.state==='suspended')_ac.resume(); }catch(e){} return _ac; }
function tone(freq,dur,type,vol,when){ const a=ac(); if(!a||MUTED)return;
  const t=a.currentTime+(when||0), o=a.createOscillator(), g=a.createGain();
  o.type=type||'sine'; o.frequency.setValueAtTime(freq,t);
  g.gain.setValueAtTime(0.0001,t); g.gain.exponentialRampToValueAtTime(vol||0.05,t+0.008);
  g.gain.exponentialRampToValueAtTime(0.0001,t+dur);
  o.connect(g).connect(a.destination); o.start(t); o.stop(t+dur+0.02); }
function sHover(){ tone(1500,0.05,'sine',0.03); }
function sClick(){ tone(440,0.08,'square',0.05); tone(900,0.07,'square',0.035,0.03); }
function sTick(){ tone(1900,0.02,'square',0.02); }
function sPowerOn(){ const a=ac(); if(!a||MUTED)return;
  const o=a.createOscillator(), g=a.createGain(); o.type='sawtooth';
  o.frequency.setValueAtTime(110,a.currentTime); o.frequency.exponentialRampToValueAtTime(880,a.currentTime+0.5);
  g.gain.setValueAtTime(0.0001,a.currentTime); g.gain.exponentialRampToValueAtTime(0.06,a.currentTime+0.05);
  g.gain.exponentialRampToValueAtTime(0.0001,a.currentTime+0.7);
  o.connect(g).connect(a.destination); o.start(); o.stop(a.currentTime+0.75);
  tone(523,0.5,'triangle',0.04,0.10); tone(784,0.5,'triangle',0.04,0.15); tone(1046,0.6,'triangle',0.035,0.20); }

$('#muteBtn').addEventListener('click',(e)=>{ e.stopPropagation(); MUTED=!MUTED;
  $('#muteBtn').textContent=MUTED?'🔇':'🔊'; if(!MUTED)sClick(); });

// ===== Boot sequence =====
const BOOT=['INITIALIZING CORE SYSTEMS','MOUNTING channels.json',
 'LINKING CHANNEL CLUSTERS // cm · rg','AUTHENTICATING OAUTH TOKENS',
 'ESTABLISHING YOUTUBE DATA LINK','FETCHING ANALYTICS DATA',
 'COMPILING UPLOAD FEED','CALIBRATING QUOTA GAUGES',
 'INDEXING TOP-VIDEO LEADERBOARD','RENDERING MISSION CONTROL UI'];
// Browsers block audio until a user gesture. We auto-boot anyway and arm a
// one-time unlock on the first pointer/key/touch so SFX kick in the instant
// the user interacts — no "press to start" gate.
let audioUnlocked=false;
function unlockAudio(){ if(audioUnlocked)return; audioUnlocked=true; ac();
  ['pointerdown','keydown','mousemove','touchstart','wheel'].forEach(ev=>
    document.removeEventListener(ev,unlockAudio,true)); }
['pointerdown','keydown','mousemove','touchstart','wheel'].forEach(ev=>
  document.addEventListener(ev,unlockAudio,{capture:true,once:false}));

let entered=false;
(function boot(){
  const logEl=$('#bootlog'), barEl=$('#bootbar'), pctEl=$('#bootpct'); let i=0;
  (function step(){
    if(i<BOOT.length){
      const ln=el('div','ln');
      ln.innerHTML=`<span style="color:var(--dim)">&gt;</span> ${BOOT[i]} <span class="ok">[ OK ]</span>`;
      logEl.appendChild(ln); logEl.scrollTop=logEl.scrollHeight;
      sTick();
      const pct=Math.round((i+1)/BOOT.length*100);
      barEl.style.width=pct+'%'; pctEl.textContent=pct+'%';
      i++; setTimeout(step, 200+Math.random()*150);
    } else {
      const bs=document.querySelector('.boot-sub'); if(bs)bs.textContent='● SYSTEM ONLINE — ENGAGING';
      setTimeout(enterSite, 520);   // auto-enter; no button to press
    }
  })();
})();

function enterSite(){
  if(entered) return; entered=true;
  ac(); sPowerOn();
  const b=$('#boot'); b.classList.add('gone'); setTimeout(()=>{ if(b)b.remove(); },760);
  wireSounds();
}

// ===== Hover / click sound wiring (activated after entering) =====
function wireSounds(){
  if(wireSounds.done) return; wireSounds.done=true;
  const SEL='.kpi,.ch,.tile,.chip,.gauge,.hbar,.evt,a,button,.mutebtn,.led';
  let last=0, lastEl=null;
  document.addEventListener('mouseover',(e)=>{ const t=e.target.closest(SEL); if(!t||t===lastEl)return;
    lastEl=t; const now=performance.now(); if(now-last<30)return; last=now; sHover(); });
  document.addEventListener('mouseout',(e)=>{ if(e.target.closest(SEL)===lastEl) lastEl=null; });
  document.addEventListener('click',()=>sClick());
}

// ===== Live chroma digital clock =====
(function(){
  const c=$('#clock'), d=$('#clockdate');
  const DAYS=['SUN','MON','TUE','WED','THU','FRI','SAT'];
  const MON=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const p2=(n)=>(n<10?'0':'')+n;
  function tick(){
    const t=new Date();
    c.textContent=`${p2(t.getHours())}:${p2(t.getMinutes())}:${p2(t.getSeconds())}`;
    if(d)d.textContent=`${DAYS[t.getDay()]} · ${p2(t.getDate())} ${MON[t.getMonth()]} ${t.getFullYear()}`;
  }
  tick();
  // Tick on the second boundary, then every 1s — steady, no sub-second jitter.
  setTimeout(()=>{ tick(); setInterval(tick, 1000); }, 1000-(new Date()).getMilliseconds());
})();

// ===== Fullscreen: browser launches with --start-fullscreen; also upgrade to
//       the Fullscreen API on the first real user gesture (covers normal opens). =====
(function(){
  let done=false;
  function go(){ if(done)return; done=true;
    const r=document.documentElement, rf=r.requestFullscreen||r.webkitRequestFullscreen;
    if(rf){ try{ const pr=rf.call(r); if(pr&&pr.catch)pr.catch(()=>{}); }catch(e){} }
    ['pointerdown','keydown','click','touchstart'].forEach(ev=>document.removeEventListener(ev,go,true));
  }
  ['pointerdown','keydown','click','touchstart'].forEach(ev=>document.addEventListener(ev,go,{capture:true}));
})();

</script>
</body>
</html>"""


def _open_fullscreen(url: str) -> None:
    """Open `url` in a fullscreen Chromium window (Edge/Chrome --start-fullscreen);
    fall back to the default browser. The page also upgrades to the Fullscreen
    API on first user gesture."""
    import subprocess

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        Path(pfx) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pfx) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for b in candidates:
        if b.exists():
            try:
                subprocess.Popen([str(b), "--new-window", "--start-fullscreen", url])
                return
            except OSError:
                pass
    webbrowser.open(url)


class _DashHandler:
    """Factory for a locked-down static handler that only serves dashboard.html
    and the output/ videos (so the project's secrets aren't exposed), with HTTP
    Range support so <video> seeking/looping works."""

    @staticmethod
    def make():
        import http.server

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(ROOT), **k)

            def log_message(self, *a):  # quiet
                pass

            def _allowed(self) -> bool:
                p = self.path.split("?", 1)[0]
                if p == "/":
                    self.path = "/dashboard.html"
                    return True
                return p == "/dashboard.html" or p.startswith("/output/")

            def do_GET(self):
                if not self._allowed():
                    self.send_error(404, "Not found")
                    return
                super().do_GET()

            def do_HEAD(self):
                if not self._allowed():
                    self.send_error(404, "Not found")
                    return
                super().do_HEAD()

        return H


def _serve(out: Path) -> int:
    """Serve the dashboard over http://localhost (so the #1 YouTube short can
    autoplay/loop — the file:// origin is what triggers embed error 153) and
    open it fullscreen. Blocks until Ctrl+C."""
    import http.server
    import threading

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _DashHandler.make())
    port = httpd.server_address[1]
    url = f"http://localhost:{port}/dashboard.html"
    threading.Thread(target=_open_fullscreen, args=(url,), daemon=True).start()
    log.info("Dashboard live → %s", url)
    log.info("  (serving locally · press Ctrl+C in this window to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Dashboard server stopped.")
    finally:
        httpd.server_close()
    return 0


def main() -> int:
    data = gather()
    out = ROOT / "dashboard.html"
    out.write_text(render(data), encoding="utf-8")
    log.info("Dashboard written → %s", out)
    log.info("  %d uploads · %d/%d channels live · %d videos",
             data["kpis"]["total_uploads"], data["kpis"]["channels_auth"],
             data["kpis"]["channels_total"], data["kpis"]["videos_produced"])
    return _serve(out)


if __name__ == "__main__":
    raise SystemExit(main())
