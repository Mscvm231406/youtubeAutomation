# Scheduling the pipeline to run daily

## Option A — Windows Task Scheduler (built-in)

1. Edit `run.ps1` if your paths differ (it activates the venv and runs `main.py`).
2. Register a task that runs every day at 9:00 AM:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\rocky\Desktop\Coding\youtubeAutomation\run.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
Register-ScheduledTask -TaskName "RedditShorts" -Action $action -Trigger $trigger `
  -Description "Daily Reddit→YouTube Shorts" -RunLevel Limited
```

Remove later with: `Unregister-ScheduledTask -TaskName "RedditShorts" -Confirm:$false`

## Option B — spread uploads across the day (better for the algorithm)

YouTube favors consistent cadence. Instead of dumping 3 videos at once, schedule
three separate triggers (e.g. 9am, 2pm, 7pm) each producing **one** video:

```powershell
foreach ($t in @("9:00AM","2:00PM","7:00PM")) {
  $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\rocky\Desktop\Coding\youtubeAutomation\run.ps1`" -Args '--limit 1'"
  $trigger = New-ScheduledTaskTrigger -Daily -At $t
  Register-ScheduledTask -TaskName "RedditShorts_$($t -replace '[: ]','')" -Action $action -Trigger $trigger
}
```

Or use YouTube's native scheduling: set `youtube.publish_at` in `config.yaml` to a
future ISO8601 time and upload them all in one batch as scheduled-private.

## Option C — keep a human in the loop (recommended at first)

Run with `--privacy private`, then review each video in YouTube Studio and
publish manually for the first week or two. Once you trust the output quality,
switch to `unlisted` or `public` and let the scheduler run unattended.

## Scaling notes
- **API quota** caps you near 6 uploads/day per Google project (request more if needed).
- Rotate `reddit.subreddits`, `tts.edge_voice`, and multiple backgrounds so videos
  don't look mass-produced (helps with YouTube's authenticity policies).
- Watch `state/processed.json` grow — it guarantees no post is ever reused.
