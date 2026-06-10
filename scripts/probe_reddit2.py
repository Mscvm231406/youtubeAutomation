"""Second attempt at anonymous Reddit scraping using a cookie-priming session
and richer browser headers. Prints what works so we know the sample's data source.
"""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

s = requests.Session()
s.headers.update(HEADERS)

# Prime cookies by hitting the homepage first.
try:
    h = s.get("https://www.reddit.com/", timeout=20)
    print("homepage:", h.status_code, "cookies:", len(s.cookies))
except Exception as e:  # noqa: BLE001
    print("homepage ERROR", e)

targets = [
    "https://www.reddit.com/r/AskReddit/top.json?t=day&limit=5",
    "https://www.reddit.com/r/AskReddit/hot.json?limit=5",
    "https://old.reddit.com/r/AskReddit/top.json?t=day&limit=5",
]
for url in targets:
    try:
        r = s.get(url, timeout=20)
        n = 0
        if r.ok:
            n = len(r.json().get("data", {}).get("children", []))
        print(f"{r.status_code}  n={n}  {url}")
        if r.ok and n:
            d = r.json()["data"]["children"][0]["data"]
            print("   top:", d.get("title", "")[:70], "| score", d.get("score"))
            break
    except Exception as e:  # noqa: BLE001
        print("ERR", type(e).__name__, url)
