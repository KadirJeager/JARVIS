"""POST /api/voice/enroll: require_user-gated bootstrap enrollment of Kadir's
voiceprint anchors (Katman 2b Dilim 3a, spec §5). Torch-free: app.speaker.embed
is monkeypatched to a fixed vector, so this never touches the real ECAPA model."""
import base64

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.speaker_store as speaker_store_mod
from app.auth import require_user
from app.speaker_store import load_profile
from tests.fakes import FakeDB


@pytest.fixture
def enroll_client(monkeypatch):
    """A TestClient wired to a fresh FakeDB via _enroll_db, with embed() patched
    to a fixed vector (no torch). require_user is left un-overridden here so
    individual tests can opt in (auth-required tests rely on that)."""
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    # get_speaker_service() is a process-lifetime singleton; drop any instance a
    # previous test built so this one really gets a service bound to `db` above.
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    monkeypatch.setattr("app.speaker.embed", lambda pcm: [1.0, 0.0])
    with TestClient(main_mod.app) as c:
        yield c, db
    main_mod.app.dependency_overrides.clear()


def _clip(raw: bytes = b"\x00\x01\x00\x01") -> str:
    return base64.b64encode(raw).decode()


def test_enroll_stores_anchors(enroll_client):
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    r = c.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 200
    assert r.json() == {"anchors": 2}
    assert len(load_profile(db, "kadir@example.com").anchors) == 2


def test_enroll_accumulates_across_calls(enroll_client):
    """A second enrollment call for the same user must ADD to, not replace,
    the existing anchors -- proves enroll_anchors (append) is wired, not
    save_profile (overwrite) called directly."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    c.post("/api/voice/enroll", json={"clips": [_clip()]})
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 200
    assert r.json() == {"anchors": 2}
    assert len(load_profile(db, "kadir@example.com").anchors) == 2


def test_enroll_requires_auth(enroll_client):
    """No dependency override -> require_user runs for real and rejects the
    unauthenticated request (matches test_api.py's *_requires_auth pattern)."""
    c, _db = enroll_client
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code in (401, 403)


def test_enroll_rejects_empty_clips(enroll_client):
    c, _db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"
    r = c.post("/api/voice/enroll", json={"clips": []})
    assert r.status_code == 400


def test_enroll_wraps_failure_as_502(enroll_client, monkeypatch):
    """A speaker.embed blowup (bad clip, model hiccup) must come back as the
    same graceful Turkish 502 pattern as /api/chat and /api/history, not a
    raw 500 -- and must not partially store anchors."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"

    def boom(pcm):
        raise RuntimeError("embed blew up")

    monkeypatch.setattr("app.speaker.embed", boom)
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 502
    assert r.json()["detail"] == "Ses kaydı işlenemedi, tekrar dene"
    assert load_profile(db, "kadir@example.com").anchors == []


def test_enroll_final_read_failure_is_502_not_500(enroll_client, monkeypatch):
    """The final speaker_store.load_profile() call (to compute the returned
    anchor count, now inside SpeakerService.enroll) used to sit OUTSIDE the
    try/except that converts failures into the Turkish 502 -- so a transient
    Firestore error on THAT specific read (embed + enroll_anchors both already
    succeeded) escaped as a raw, unhandled 500 instead of the same 502 pattern
    every other failure in this endpoint gets. Only the SECOND load_profile
    call (the count read-back) is made to fail; the FIRST call (enroll_anchors'
    internal load-then-extend, called via the same module-level name) must
    still succeed, isolating the exact line under test."""
    c, db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"

    real_load_profile = speaker_store_mod.load_profile
    calls = {"n": 0}

    def flaky_load_profile(db_, user_id):
        calls["n"] += 1
        if calls["n"] > 1:            # 1st call = inside enroll_anchors (must succeed)
            raise RuntimeError("firestore hiccup on final read")
        return real_load_profile(db_, user_id)

    monkeypatch.setattr(speaker_store_mod, "load_profile", flaky_load_profile)
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 502
    assert r.json()["detail"] == "Ses kaydı işlenemedi, tekrar dene"


# --- the enroll path must not repeat the I2 mistakes ------------------------


def test_enroll_runs_embedding_off_the_event_loop(enroll_client, monkeypatch):
    """`speaker.embed` is real ECAPA inference plus, on the first call in a
    process, the ~89 MB lazy model load: seconds of blocking CPU per clip. Run
    inline in this `async def` endpoint it stalls the whole event loop -- and
    jarvis-brain serves /api/chat and /ws/voice from that same loop, so one
    enrollment would freeze every text turn. The live verify path was moved off
    the loop; this one must be too."""
    import threading

    c, _db = enroll_client
    loop_thread = {}
    embed_threads = []

    async def _user():
        # An ASYNC dependency is awaited on the event loop itself (a sync one
        # would be offloaded to Starlette's threadpool), so this records the
        # exact thread the endpoint coroutine runs on.
        loop_thread["id"] = threading.get_ident()
        return "kadir@example.com"

    main_mod.app.dependency_overrides[require_user] = _user
    monkeypatch.setattr(
        "app.speaker.embed",
        lambda pcm: (embed_threads.append(threading.get_ident()), [1.0, 0.0])[1],
    )

    r = c.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 200
    assert len(embed_threads) == 2, "both clips must be embedded"
    assert loop_thread.get("id") is not None
    assert all(t != loop_thread["id"] for t in embed_threads), (
        "embed ran ON the event loop thread -- exactly the I2 failure mode "
        f"(embed={embed_threads}, loop={loop_thread['id']})")


def test_enroll_takes_the_same_gallery_lock_as_identify(enroll_client, monkeypatch):
    """`speaker_store.enroll_anchors` is a load -> extend -> save read-modify-
    write, and so is identify()'s adapt path. Both used to run to completion on
    the single event loop with no await inside their windows, so interleaving
    was structurally impossible -- moving inference to asyncio.to_thread made
    the race real, which is why enrollment now goes through SpeakerService and
    shares its gallery lock instead of touching speaker_store directly."""
    c, _db = enroll_client
    main_mod.app.dependency_overrides[require_user] = lambda: "kadir@example.com"

    svc = main_mod.get_speaker_service()
    held = []
    real_enroll_anchors = speaker_store_mod.enroll_anchors

    def checking_enroll_anchors(db_, user_id, vecs):
        # locked() is True from any thread while the lock is held; if enrollment
        # bypassed the service this callback would see it free.
        held.append(svc._gallery_lock.locked())
        return real_enroll_anchors(db_, user_id, vecs)

    monkeypatch.setattr(speaker_store_mod, "enroll_anchors", checking_enroll_anchors)
    r = c.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 200
    assert held == [True], (
        "the enroll read-modify-write ran without the gallery lock identify() "
        "takes -- a concurrent adapt can silently drop one side's write")


def test_concurrent_enroll_and_identify_do_not_lose_a_write():
    """The race the lock exists for, driven directly against SpeakerService (no
    HTTP): an identify() that adapts and an enroll() interleaved from two
    threads must both survive. Without a shared lock one load->save window
    overwrites the other and a whole enrollment silently disappears."""
    import threading

    from app.speaker import SpeakerService

    db = FakeDB()
    svc = SpeakerService(db, embed_fn=lambda pcm: [1.0, 0.0, 0.0], now_fn=lambda: "t",
                         accept=0.35, adapt=0.6, cap=20, top_k=3)
    svc.enroll("kadir@example.com", [[1.0, 0.0, 0.0]])       # a matching anchor exists

    start = threading.Barrier(2)
    errors = []

    def do_enroll():
        try:
            start.wait(timeout=5)
            svc.enroll("kadir@example.com", [[0.9, 0.1, 0.0]])
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    def do_identify():
        try:
            start.wait(timeout=5)
            svc.identify("kadir@example.com", b"\x00\x01", "phone", auth_is_kadir=True)
        except Exception as exc:            # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=do_enroll), threading.Thread(target=do_identify)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, errors
    profile = load_profile(db, "kadir@example.com")
    assert len(profile.anchors) == 2, "the enrolled anchor was lost to the adapt write"
    assert len(profile.adaptive) == 1, "the adaptive sample was lost to the enroll write"
