import pytest

from app.messages import COLLECTION, MessageStore, sanitize_session_id
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


# --- kind/meta (Faz Y3: onay kartı transcript'te taşınır, spec §7) ----------


def test_append_writes_kind_and_meta_when_given():
    store = _store()
    store.append("u@x.com", "s1", "model", "Hatırlatma silinecek",
                 kind="approval", meta={"approval_id": "a1"})
    hist = store.history("u@x.com", "s1")
    assert hist == [{
        "role": "model", "text": "Hatırlatma silinecek",
        "ts": "2026-01-01T00:00:00.000000+00:00",
        "kind": "approval", "meta": {"approval_id": "a1"},
    }]


def test_plain_append_stays_byte_identical_to_today():
    """GERİYE DÖNÜK UYUM PİMİ: kind/meta verilmeyen satır ne Firestore'da ne de
    projeksiyonda yeni alan taşır. Bu test düşerse eski istemciler (ve eski
    transcript satırları) yeni bir alanla karşılaşır."""
    store = _store()
    store.append("u@x.com", "s1", "user", "selam")

    written = [s.to_dict() for s in store.db.collection(COLLECTION).stream()]
    assert set(written[0]) == {"user_id", "session_id", "role", "text", "ts"}
    assert store.history("u@x.com", "s1") == [
        {"role": "user", "text": "selam", "ts": "2026-01-01T00:00:00.000000+00:00"}
    ]


def test_history_of_rows_written_before_kind_existed_is_unchanged():
    """Y3 ÖNCESİ yazılmış satır (koleksiyona doğrudan konur) bugünkü üç alanla
    BİREBİR aynı döner — yeni kod eski veriyi bozmaz."""
    db = FakeDB()
    db.collection(COLLECTION).add({
        "user_id": "u@x.com", "session_id": "s1", "role": "model",
        "text": "eski satır", "ts": "2026-01-01T00:00:00.000000+00:00",
    })
    store = MessageStore(db)

    assert store.history("u@x.com", "s1") == [
        {"role": "model", "text": "eski satır", "ts": "2026-01-01T00:00:00.000000+00:00"}
    ]


@pytest.mark.parametrize("kind,meta", [(None, None), ("", {}), (None, {}), ("", None)])
def test_empty_kind_or_meta_is_never_projected(kind, meta):
    """Yalnız DOLU alanlar yazılır: boş string / boş dict, alanın varlığından
    daha kötüdür — istemci 'kind var ama boş' hâlini ayrıca ele almak zorunda
    kalırdı."""
    store = _store()
    store.append("u@x.com", "s1", "model", "düz metin", kind=kind, meta=meta)
    assert set(store.history("u@x.com", "s1")[0]) == {"role", "text", "ts"}


def test_kind_and_meta_are_independent():
    store = _store()
    store.append("u@x.com", "s1", "model", "sadece kind", kind="approval")
    hist = store.history("u@x.com", "s1")
    assert hist[0]["kind"] == "approval" and "meta" not in hist[0]


# --- delete_session (Katman 2b conversations feature) -----------------------


def test_delete_session_removes_only_that_sessions_messages():
    store = _store()
    store.append("u@x.com", "s1", "user", "silinecek")
    store.append("u@x.com", "s2", "user", "kalacak")
    deleted = store.delete_session("u@x.com", "s1")
    assert deleted == 1
    assert store.history("u@x.com", "s1") == []
    assert [h["text"] for h in store.history("u@x.com", "s2")] == ["kalacak"]


def test_delete_session_isolates_by_user():
    """HARD CONSTRAINT: deleting user A's session_id must never remove user
    B's messages under the same session_id."""
    store = _store()
    store.append("u@x.com", "s1", "user", "A nin mesaji")
    store.append("other@x.com", "s1", "user", "B nin mesaji")
    store.delete_session("u@x.com", "s1")
    assert store.history("u@x.com", "s1") == []
    assert [h["text"] for h in store.history("other@x.com", "s1")] == ["B nin mesaji"]


# --- F9: deterministik-id satır (upsert/delete) --------------------------------


def test_upsert_row_creates_then_updates_a_single_doc():
    """F9 ilerleme satırının temeli: aynı row_id'ye ikinci yazım YENİ doküman
    açmaz, mevcudun metnini/ts'sini yerinde günceller."""
    db = FakeDB()
    store = MessageStore(db, now_fn=lambda: "2026-08-23T10:00:00+00:00")
    store.upsert_row("u@x.com", "tasks", "task-progress-u@x.com-t1",
                     "model", "adım 1/5", kind="task_progress")
    store.upsert_row("u@x.com", "tasks", "task-progress-u@x.com-t1",
                     "model", "adım 2/5", kind="task_progress")

    rows = db.collection(COLLECTION).stream()
    assert len(list(rows)) == 1  # append değil: iki yazım, TEK doküman
    (row,) = store.history("u@x.com", "tasks")
    assert row["text"] == "adım 2/5"
    assert row["kind"] == "task_progress"
    assert "meta" not in row  # yazılmadı; _project boş alanı taşımaz
    assert row["ts"] == "2026-08-23T10:00:00+00:00"


def test_delete_row_removes_and_reports_absence():
    db = FakeDB()
    store = MessageStore(db)
    rid = "task-progress-u@x.com-t1"
    assert store.delete_row("u@x.com", "tasks", rid) is False  # hiç yokken
    store.upsert_row("u@x.com", "tasks", rid, "model", "adım 1/5")
    assert store.delete_row("u@x.com", "tasks", rid) is True
    assert store.delete_row("u@x.com", "tasks", rid) is False  # ikinci silme
    assert store.history("u@x.com", "tasks") == []
