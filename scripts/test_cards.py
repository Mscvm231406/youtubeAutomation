"""Render one post card and one comment card to temp/ to eyeball the styling."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import reddit_card as rc

(ROOT / "temp").mkdir(exist_ok=True)
p = rc.render_post_card("AskReddit", "redditor",
                        "What's a small habit that completely changed your life?",
                        48700, 12300)
rc.save_card(p, ROOT / "temp" / "test_post.png")
print("post card:", p.size)

c = rc.render_comment_card(
    "twowater",
    "Drinking a full glass of water before coffee. My headaches basically "
    "disappeared within a week.", 9800)
rc.save_card(c, ROOT / "temp" / "test_comment.png")
print("comment card:", c.size)
