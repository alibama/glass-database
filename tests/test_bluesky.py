"""ATProto post payload construction (offline)."""
from glowtbook import bluesky


def test_compose_text_caps_at_300():
    txt = bluesky.compose_text("A" * 400, "maker", "hash", "https://x")
    assert len(txt) <= 300


def test_post_record_shapes():
    txt = bluesky.compose_text("Vase", "AP", "abcd", "https://glassdatabase.org")
    rec = bluesky.build_post_record(txt, {"$type": "blob"}, alt="Vase")
    assert rec["$type"] == "app.bsky.feed.post"
    assert rec["embed"]["images"][0]["alt"] == "Vase"
    assert "createdAt" in rec
    assert "embed" not in bluesky.build_post_record(txt, None)
