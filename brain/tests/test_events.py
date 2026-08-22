"""Olay katmanı iskeleti (North Star §4.4): events.record + POST /api/jobs/event.

Gerçek ağ yok: health çağrıları `fetch` enjeksiyonu/monkeypatch ile sınanır,
scheduler OIDC ise test_repo_watch_api.py deseniyle (id_token monkeypatch).
"""
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app import config, events, tasks
from app.memory import Memory
from tests.fakes import FakeDB

SA = "jarvis-events@proj.iam.gserviceaccount.com"
AUD = "https://jarvis-brain-xyz.run.app"

SERVICES = {
    "services": [
        {"name": "brain", "url": "https://brain.example/api/health"},
        {"name": "proxy", "url": "https://proxy.example/api/health"},
    ]
}


def _stored(db):
    return list(db.collection(events.EVENTS_COLLECTION).docs.values())


# --- events.record: kind'lar ve kural motoru -----------------------------------


def test_ping_is_recorded_and_handled():
    db = FakeDB()
    out = events.record(db, source="scheduler", kind="ping", payload={})
    assert out == {"handled": True, "notify": False, "summary": "ping kaydedildi"}
    (doc,) = _stored(db)
    assert doc["kind"] == "ping" and doc["handled"] is True


def test_health_check_all_ok_notifies_false():
    db = FakeDB()
    out = events.record(
        db, source="scheduler", kind="health_check", payload=SERVICES,
        fetch=lambda url: 200,
    )
    assert out["handled"] is True and out["notify"] is False
    (doc,) = _stored(db)
    assert all(c["ok"] for c in doc["result"]["servisler"])


def test_health_check_any_failure_notifies_true_with_turkish_summary():
    db = FakeDB()
    statuses = {"https://brain.example/api/health": 200,
                "https://proxy.example/api/health": 503}
    out = events.record(
        db, source="scheduler", kind="health_check", payload=SERVICES,
        fetch=lambda url: statuses[url],
    )
    assert out["handled"] is True and out["notify"] is True
    assert "proxy" in out["summary"] and "sorunlu" in out["summary"]
    (doc,) = _stored(db)
    checks = {c["name"]: c for c in doc["result"]["servisler"]}
    assert checks["proxy"]["status"] == 503 and checks["proxy"]["ok"] is False
    assert checks["brain"]["ok"] is True


def test_health_check_unreachable_service_counts_as_failure():
    """Ağ hatası (fetch -> None) hata değil gözlem: satıra status=None yazılır,
    tur diğer servisle devam eder (repo-watch hata izolasyonu deseni)."""
    db = FakeDB()
    statuses = {"https://brain.example/api/health": 200,
                "https://proxy.example/api/health": None}
    out = events.record(
        db, source="scheduler", kind="health_check", payload=SERVICES,
        fetch=lambda url: statuses[url],
    )
    assert out["notify"] is True
    (doc,) = _stored(db)
    checks = {c["name"]: c for c in doc["result"]["servisler"]}
    assert checks["proxy"]["status"] is None and checks["proxy"]["ok"] is False


def test_unknown_kind_is_recorded_not_rejected():
    """Bilinmeyen kind 400 DEĞİLDİR: handled=False gözlemi olarak kalıcılaşır —
    yeni kind'lar kod eklenmeden önce gönderilmeye başlanabilir."""
    db = FakeDB()
    out = events.record(db, source="pubsub", kind="mail_received", payload={"x": 1})
    assert out["handled"] is False and out["notify"] is False
    assert "mail_received" in out["summary"]
    (doc,) = _stored(db)
    assert doc["kind"] == "mail_received" and doc["handled"] is False
    assert doc["payload"] == {"x": 1}


def test_event_document_schema():
    """Koleksiyon şeması: source, kind, payload, ts, handled, result."""
    db = FakeDB()
    events.record(db, source="scheduler", kind="ping", payload={"a": 1},
                  now_fn=lambda: "2026-07-31T12:00:00+00:00")
    (doc,) = _stored(db)
    assert set(doc) == {"source", "kind", "payload", "ts", "handled", "result"}
    assert doc["ts"] == "2026-07-31T12:00:00+00:00"
    assert doc["source"] == "scheduler"


def test_weekly_retro_event_success_notifies_true():
    db = FakeDB()
    payload = {"llm_fn": lambda p: "Haftalık özet raporu"}
    out = events.record(db, source="scheduler", kind="weekly_retro", payload=payload)
    assert out["handled"] is True and out["notify"] is True
    assert "retro" in out["summary"]
    (doc,) = _stored(db)
    assert doc["kind"] == "weekly_retro" and doc["handled"] is True
    assert doc["result"]["ok"] is True


def test_weekly_retro_event_llm_failure_notifies_false():
    db = FakeDB()
    def bad_llm(p):
        raise TimeoutError("timeout")
    payload = {"llm_fn": bad_llm}
    out = events.record(db, source="scheduler", kind="weekly_retro", payload=payload)
    assert out["handled"] is True and out["notify"] is False
    (doc,) = _stored(db)
    assert doc["kind"] == "weekly_retro" and doc["handled"] is True
    assert doc["result"]["ok"] is False


# --- POST /api/jobs/event -------------------------------------------------------


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_SA", SA)
    monkeypatch.setattr(config, "SCHEDULER_AUD", AUD)


def _post(c, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/api/jobs/event", json=body, headers=headers)


def test_event_job_without_token_returns_401(configured):
    with TestClient(main_mod.app) as c:
        r = _post(c, {"source": "s", "kind": "ping", "payload": {}})
    assert r.status_code == 401


def test_event_job_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_SA", "")
    monkeypatch.setattr(config, "SCHEDULER_AUD", "")
    with TestClient(main_mod.app) as c:
        r = _post(c, {"source": "s", "kind": "ping", "payload": {}}, "tok")
    assert r.status_code == 503


def test_event_job_correct_token_records_and_returns_decision(configured, monkeypatch):
    def fake_verify(token, request, audience):
        assert audience == AUD
        return {"email": SA}

    db = FakeDB()
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    # Gerçek ağ yok: health çağrıları mock'lanır (record fetch'i çağrı anında
    # http_status'a çözümlenir — def-time default değil).
    monkeypatch.setattr(events, "http_status", lambda url: 200)

    with TestClient(main_mod.app) as c:
        r = _post(c, {"source": "scheduler", "kind": "health_check",
                      "payload": SERVICES}, "good-token")

    assert r.status_code == 200
    assert r.json() == {"handled": True, "notify": False,
                        "summary": "Sağlık kontrolü: 2 servisin hepsi sağlıklı"}
    (doc,) = _stored(db)
    assert doc["kind"] == "health_check" and doc["handled"] is True


def test_event_job_unknown_kind_returns_handled_false(configured, monkeypatch):
    def fake_verify(token, request, audience):
        return {"email": SA}

    db = FakeDB()
    monkeypatch.setattr(auth_mod.id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))

    with TestClient(main_mod.app) as c:
        r = _post(c, {"source": "pubsub", "kind": "yeni_tur", "payload": {}}, "tok")

    assert r.status_code == 200  # 400 DEĞİL: gözlem kaydı
    assert r.json()["handled"] is False
    assert len(_stored(db)) == 1


# --- F9: task_tick canlı ilerleme satırı ----------------------------------------


def test_task_tick_writes_live_progress_then_cleans_up_on_done(monkeypatch):
    """Uçtan uca üretim kablosu: task_tick ACTIVE görevde REPORT_SESSION_ID'de
    TEK kind=task_progress satırı upsert eder ("adım 1/3"); görev done olunca
    satır silinir, yerini nihai rapora bırakır."""
    from app.messages import MessageStore

    owner = sorted(config.ALLOWED_EMAILS)[0]
    db = FakeDB()
    tasks.enqueue(db, "devriye", "siteyi yokla", max_steps=3)

    out = events.record(db, source="scheduler", kind="task_tick",
                        payload={}, fetch=lambda url: 200)
    assert out["handled"] is True
    rows = MessageStore(db).history(owner, tasks.REPORT_SESSION_ID)
    assert len(rows) == 1
    assert rows[0]["kind"] == tasks.PROGRESS_KIND
    assert "devriye" in rows[0]["text"] and "adım 1/3" in rows[0]["text"]

    monkeypatch.setattr(
        tasks, "health_patrol_step",
        lambda fetch: lambda cp: {"done": True, "result": "bitti"},
    )
    events.record(db, source="scheduler", kind="task_tick",
                  payload={}, fetch=lambda url: 200)
    rows = MessageStore(db).history(owner, tasks.REPORT_SESSION_ID)
    assert [r.get("kind") for r in rows] == [None]
    assert "tamamlandı" in rows[0]["text"]
