"""Try pullpush.io (Pushshift successor) for anonymous AskReddit posts + comments."""
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) shorts-bot/1.0"
s = requests.Session()
s.headers.update({"User-Agent": UA})

sub_url = ("https://api.pullpush.io/reddit/search/submission/"
           "?subreddit=askreddit&sort=desc&sort_type=score&size=5&over_18=false")
try:
    r = s.get(sub_url, timeout=30)
    print("submission:", r.status_code)
    data = r.json().get("data", []) if r.ok else []
    print("posts:", len(data))
    if data:
        p = data[0]
        print("TOP:", p.get("title", "")[:70], "| score", p.get("score"), "| id", p.get("id"))
        cid = p.get("id")
        c_url = ("https://api.pullpush.io/reddit/search/comment/"
                 f"?link_id={cid}&sort=desc&sort_type=score&size=5")
        rc = s.get(c_url, timeout=30)
        cdata = rc.json().get("data", []) if rc.ok else []
        print("comments:", rc.status_code, "count", len(cdata))
        for c in cdata[:3]:
            print("  -", str(c.get("body", ""))[:60], "| score", c.get("score"))
except Exception as e:  # noqa: BLE001
    print("ERROR", type(e).__name__, e)
