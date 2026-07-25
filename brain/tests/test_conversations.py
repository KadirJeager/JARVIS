from app.conversations import ConversationStore, derive_title, TITLE_MAX_LEN
from tests.fakes import FakeDB


class _InterleavedDoc:
    """Test-only wrapper that simulates a genuine race landing on the FIRST
    call ConversationStore.touch() makes against a document -- something a
    synchronous, single-threaded fake cannot otherwise produce, since
    sequential calls to touch() never truly interleave.

    - get(): returns the PRE-race snapshot (what this reader legitimately
      saw a moment before the other writer's commit), THEN lets the
      concurrent write land. This reproduces the TOCTOU gap a
      get()-then-conditional-set() pattern is exposed to.
    - create(): Firestore's create() is a single atomic check-and-write, so
      there is no "moment before" to expose to it -- the concurrent write
      must already be visible to this call, which is exactly the guarantee
      that makes create()-based touch() race-safe.

    Only the first call (of either kind) triggers the injection; every call
    after that behaves like the real, unwrapped document.
    """
    def __init__(self, real_doc, inject):
        self._real = real_doc
        self._inject = inject
        self._injected = False

    def _inject_once(self):
        if not self._injected:
            self._injected = True
            self._inject()

    def get(self):
        pre_race_snap = self._real.get()  # captured before the race lands
        self._inject_once()
        return pre_race_snap

    def create(self, data):
        self._inject_once()  # the race lands before the atomic check
        return self._real.create(data)

    def set(self, data, merge=False):
        return self._real.set(data, merge=merge)

    def delete(self):
        return self._real.delete()


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


def test_touch_create_race_second_writer_does_not_clobber_first(monkeypatch):
    """IMPORTANT: pins the create-branch race. Two near-simultaneous first
    touches for the same brand-new session_id (client double-submit, or a
    retry after a Cloud Run timeout) must not both win the "new
    conversation" branch -- whoever loses the race must merge into the
    winner's doc, not overwrite it with an unconditional set().

    A synchronous fake can't produce a genuine race from two sequential
    touch() calls (by the time the second call's own get() runs, the first
    call has already fully committed -- no TOCTOU gap to exploit). So this
    test forces the interleaving directly via _InterleavedDoc: writer A's
    commit is injected exactly in the gap writer B's own touch() call
    exposes, whichever shape that gap takes."""
    store = _store()
    doc_id = "u@x.com::s1"
    collection = store.db.collection("conversations")
    real_document = collection.document
    real_doc = real_document(doc_id)

    def inject_writer_a():
        # Writer A's create wins the race, landing in the shared backing
        # store exactly in the gap writer B's check-then-act exposes.
        real_doc.create({
            "user_id": "u@x.com",
            "session_id": "s1",
            "title": "yazici A kazandi",
            "created_ts": "2026-01-01T00:00:00.000000+00:00",
            "last_ts": "2026-01-01T00:00:00.000000+00:00",
            "message_count": 1,
        })

    racy_doc = _InterleavedDoc(real_doc, inject_writer_a)
    monkeypatch.setattr(
        collection, "document",
        lambda doc_id_arg=None: racy_doc if doc_id_arg == doc_id else real_document(doc_id_arg),
    )

    # Writer B's touch() call -- from B's own point of view this session_id
    # is brand new; unbeknownst to it, writer A wins the race.
    store.touch("u@x.com", "s1", "user", "yazici B nin mesaji")

    convs = store.list_conversations("u@x.com")
    assert len(convs) == 1
    assert convs[0]["title"] == "yazici A kazandi"  # winner's title survives
    assert convs[0]["message_count"] == 2  # counted as an update, not reset to 1


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
