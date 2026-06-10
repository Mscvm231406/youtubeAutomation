"""Find a request recipe that Reddit's public JSON endpoint accepts from this machine."""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

attempts = [
    ("www top.json", "https://www.reddit.com/r/Showerthoughts/top.json?limit=5&t=day", {"User-Agent": UA}),
    ("old top.json", "https://old.reddit.com/r/Showerthoughts/top.json?limit=5&t=day", {"User-Agent": UA}),
    ("www hot.json", "https://www.reddit.com/r/Showerthoughts/hot.json?limit=5", {"User-Agent": UA}),
    ("json www-out", "https://www.reddit.com/r/Showerthoughts.json?limit=5", {"User-Agent": UA}),
]

for name, url, headers in attempts:
    try:
        r = requests.get(url, headers=headers, timeout=20)
        n = len(r.json().get("data", {}).get("children", [])) if r.ok else 0
        print(f"{name:14s} -> {r.status_code}  posts={n}")
    except Exception as e:  # noqa: BLE001
        print(f"{name:14s} -> ERROR {type(e).__name__}: {e}")
