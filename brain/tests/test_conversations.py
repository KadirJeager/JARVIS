from app.conversations import ConversationStore, derive_title, TITLE_MAX_LEN
from tests.fakes import FakeDB


def _store():
    # Monotonic ISO-ish timestamps so ordering is deterministic (no sleep),
    # same convention as tests/test_messages.py's _store().
    counter = iter(f"2026-01-01T00:00:{i:02d}.000000+00:00" for i in range(60))
    return ConversationStore(FakeDB(), now_fn=lambda: next(counter))


# --- derive_title -------------------------------------------------------


def test_derive_title_short_text_is_unchanged():
    assert derive_title("selam nasılsın") == "selam nasılsın"


def test_derive_title_collapses_internal_whitespace_and_newlines():
    assert derive_title("selam   \n  nasılsın\n") == "selam nasılsın"


def test_derive_title_truncates_long_text_with_ellipsis():
    text = "a" * (TITLE_MAX_LEN + 20)
    title = derive_title(text)
    assert len(title) == TITLE_MAX_LEN
    assert title.endswith("…")


# --- ConversationStore.touch: title derivation --------------------------


def test_touch_creates_summary_with_title_from_first_user_message():
    store = _store()
    store.touch("u@x.com", "s1", "user", "ilk mesaj burada")
    convs = store.list_conversations("u@x.com")
    assert len(convs) == 1
    assert convs[0]["session_id"] == "s1"
    assert convs[0]["title"] == "ilk mesaj burada"
    assert convs[0]["message_count"] == 1


def test_touch_does_not_overwrite_title_on_later_user_messages():
    """The load-bearing guarantee: first user message wins the title,
    permanently -- a later, longer/different user message in the same
    session must NOT change it."""
    store = _store()
    store.touch("u@x.com", "s1", "user", "birinci mesaj")
    store.touch("u@x.com", "s1", "model", "cevap")
    store.touch("u@x.com", "s1", "user", "tamamen farkli ikinci mesaj")
    convs = store.list_conversations("u@x.com")
    assert convs[0]["title"] == "birinci mesaj"
    assert convs[0]["message_count"] == 3


def test_touch_increments_message_count_and_advances_last_ts():
    store = _store()
    store.touch("u@x.com", "s1", "user", "birinci")
    store.touch("u@x.com", "s1", "model", "cevap")
    convs = store.list_conversations("u@x.com")
    assert convs[0]["message_count"] == 2
    assert convs[0]["last_ts"] == "2026-01-01T00:00:01.000000+00:00"


# --- list_conversations --------------------------------------------------


def test_list_conversations_newest_first_by_last_ts():
    store = _store()
    store.touch("u@x.com", "s1", "user", "eski konusma")
    store.touch("u@x.com", "s2", "user", "yeni konusma")
    convs = store.list_conversations("u@x.com")
    assert [c["session_id"] for c in convs] == ["s2", "s1"]


def test_list_conversations_isolates_by_user():
    """HARD CONSTRAINT: a user must never see another user's conversations."""
    store = _store()
    store.touch("u@x.com", "s1", "user", "benim konusmam")
    store.touch("other@x.com", "s1", "user", "baskasinin konusmasi")
    convs = store.list_conversations("u@x.com")
    assert [c["session_id"] for c in convs] == ["s1"]
    assert convs[0]["title"] == "benim konusmam"


def test_list_conversations_bounded_by_limit():
    store = _store()
    for i in range(5):
        store.touch("u@x.com", f"s{i}", "user", f"mesaj {i}")
    convs = store.list_conversations("u@x.com", limit=2)
    assert len(convs) == 2
    assert [c["session_id"] for c in convs] == ["s4", "s3"]  # newest 2


# --- delete ---------------------------------------------------------------


def test_delete_removes_the_conversation():
    store = _store()
    store.touch("u@x.com", "s1", "user", "silinecek")
    store.delete("u@x.com", "s1")
    assert store.list_conversations("u@x.com") == []


def test_delete_does_not_touch_another_users_conversation_with_the_same_session_id():
    """HARD CONSTRAINT pin: doc-id is user-scoped, so deleting user A's
    session_id="s1" must never affect user B's own "s1"."""
    store = _store()
    store.touch("u@x.com", "s1", "user", "A nin konusmasi")
    store.touch("other@x.com", "s1", "user", "B nin konusmasi")
    store.delete("u@x.com", "s1")
    assert store.list_conversations("u@x.com") == []
    assert [c["title"] for c in store.list_conversations("other@x.com")] == ["B nin konusmasi"]
