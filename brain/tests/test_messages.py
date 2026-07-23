import pytest

from app.messages import MessageStore, sanitize_session_id
from tests.fakes import FakeDB


def _store():
    # Monotonic ISO-ish timestamps so ordering is deterministic (no sleep).
    counter = iter(f"2026-01-01T00:00:{i:02d}.000000+00:00" for i in range(60))
    return MessageStore(FakeDB(), now_fn=lambda: next(counter))


def test_append_and_history_roundtrip():
    store = _store()
    store.append("u@x.com", "s1", "user", "selam")
    store.append("u@x.com", "s1", "model", "merhaba")
    hist = store.history("u@x.com", "s1")
    assert [(h["role"], h["text"]) for h in hist] == [("user", "selam"), ("model", "merhaba")]


def test_history_isolates_by_user():
    store = _store()
    store.append("u@x.com", "s1", "user", "benim")
    store.append("other@x.com", "s1", "user", "baskasi")
    assert [h["text"] for h in store.history("u@x.com", "s1")] == ["benim"]


def test_history_isolates_by_session():
    store = _store()
    store.append("u@x.com", "s1", "user", "birinci")
    store.append("u@x.com", "s2", "user", "ikinci")
    assert [h["text"] for h in store.history("u@x.com", "s1")] == ["birinci"]


def test_history_returns_newest_within_limit_chronological():
    store = _store()
    for i in range(5):
        store.append("u@x.com", "s1", "user", f"m{i}")
    hist = store.history("u@x.com", "s1", limit=3)
    assert [h["text"] for h in hist] == ["m2", "m3", "m4"]  # newest 3, oldest→newest


def test_sanitize_session_id_accepts_uuid_like():
    assert sanitize_session_id("web-2026-07-24") == "web-2026-07-24"


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "x" * 300, "a\nb", "web-2026-07-24\n"])
def test_sanitize_session_id_rejects_invalid(bad):
    with pytest.raises(ValueError):
        sanitize_session_id(bad)
