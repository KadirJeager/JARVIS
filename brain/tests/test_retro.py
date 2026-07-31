"""Tests for weekly retrospective module (app/retro.py, Phase Y1.4)."""
import pytest
from app import retro
from tests.fakes import FakeDB

NOW = "2026-07-31T12:00:00+00:00"


def _now_fn():
    return NOW


def test_collect_week_counts_and_filters_7d():
    db = FakeDB()

    # 1. audit_log entries (3 within 7 days, 1 older)
    db.collection("audit_log").add({
        "ts": "2026-07-30T10:00:00+00:00", "decision": "allow", "tool": "search_memory"
    })
    db.collection("audit_log").add({
        "ts": "2026-07-29T10:00:00+00:00", "decision": "block", "tool": "watch_repo"
    })
    db.collection("audit_log").add({
        "ts": "2026-07-28T10:00:00+00:00", "decision": "block", "tool": "watch_repo"
    })
    db.collection("audit_log").add({
        "ts": "2026-07-27T10:00:00+00:00", "decision": "confirm", "tool": "unwatch_repo"
    })
    # Older than 7 days (ignored for 7d stats)
    db.collection("audit_log").add({
        "ts": "2026-07-20T10:00:00+00:00", "decision": "block", "tool": "watch_repo"
    })

    # 2. events entries (1 recent notify, 1 recent non-notify, 1 obsolete > 90d)
    db.collection("events").add({
        "ts": "2026-07-30T10:00:00+00:00",
        "kind": "health_check",
        "result": {"servisler": [{"name": "proxy", "ok": False}]},
    })
    db.collection("events").add({
        "ts": "2026-07-29T10:00:00+00:00",
        "kind": "ping",
        "result": None,
    })
    db.collection("events").add({
        "ts": "2026-04-01T10:00:00+00:00",  # > 90 days ago
        "kind": "ping",
        "result": None,
    })

    # 3. tasks entries (2 created within 7 days, 1 older)
    db.collection("tasks").add({
        "created_at": "2026-07-30T10:00:00+00:00", "status": "done"
    })
    db.collection("tasks").add({
        "created_at": "2026-07-29T10:00:00+00:00", "status": "budget_exhausted"
    })
    db.collection("tasks").add({
        "created_at": "2026-07-01T10:00:00+00:00", "status": "active"
    })

    # 4. facts entries (1 within 7 days, 1 older)
    db.collection("facts").add({
        "ts": "2026-07-30T10:00:00+00:00", "text": "fact 1"
    })
    db.collection("facts").add({
        "ts": "2026-07-01T10:00:00+00:00", "text": "fact 2"
    })

    # 5. lessons entries (2 within 7 days, 1 older)
    db.collection("lessons").add({
        "ts": "2026-07-30T10:00:00+00:00",
        "context": "c1", "tried": "t1", "went_wrong": "w1", "correct": "k1"
    })
    db.collection("lessons").add({
        "ts": "2026-07-29T10:00:00+00:00",
        "context": "c2", "tried": "t2", "went_wrong": "w2", "correct": "k2"
    })
    db.collection("lessons").add({
        "ts": "2026-07-01T10:00:00+00:00",
        "context": "c3", "tried": "t3", "went_wrong": "w3", "correct": "k3"
    })

    data = retro.collect_week(db, now_fn=_now_fn)

    # Assertions
    assert data["audit_log"]["total_7d"] == 4
    assert data["audit_log"]["decisions"]["allow"] == 1
    assert data["audit_log"]["decisions"]["block"] == 2
    assert data["audit_log"]["decisions"]["confirm"] == 1
    assert data["audit_log"]["top_blocked_tools"] == {"watch_repo": 2}

    assert data["events"]["total_7d"] == 2
    assert data["events"]["kinds"] == {"health_check": 1, "ping": 1}
    assert data["events"]["notify_count"] == 1
    assert data["events"]["obsolete_90d_count"] == 1

    assert data["tasks"]["total_7d"] == 2
    assert data["tasks"]["statuses"]["done"] == 1
    assert data["tasks"]["statuses"]["budget_exhausted"] == 1

    assert data["facts"]["added_7d_count"] == 1
    assert data["lessons"]["added_7d_count"] == 2
    assert len(data["lessons"]["recent_lessons"]) == 3
    # Check descending timestamp sort
    assert data["lessons"]["recent_lessons"][0]["context"] == "c1"


def test_run_success_delivers_report_to_retro_session():
    db = FakeDB()
    prompts_captured = []

    def mock_llm(prompt: str) -> str:
        prompts_captured.append(prompt)
        return "Öğrenilenler: Test adımı başarılı.\nTekrarlanmayacak hatalar: Yok."

    out = retro.run(db, llm_fn=mock_llm, owner="owner@example.com", now_fn=_now_fn)

    assert out["ok"] is True
    assert "retro oturumuna gönderildi" in out["summary"]
    assert len(prompts_captured) == 1
    assert "Haftalık Performans ve Gözlem Verileri" in prompts_captured[0]

    # Verify report stored in messages collection under session "retro"
    messages_docs = list(db.collection("messages").docs.values())
    assert len(messages_docs) == 1
    msg = messages_docs[0]
    assert msg["session_id"] == "retro"
    assert msg["user_id"] == "owner@example.com"
    assert msg["role"] == "model"
    assert "Öğrenilenler" in msg["text"]


def test_run_llm_failure_returns_ok_false_no_report_no_exception(caplog):
    db = FakeDB()

    def failing_llm(prompt: str) -> str:
        raise TimeoutError("Ağ zaman aşımı")

    out = retro.run(db, llm_fn=failing_llm, owner="owner@example.com", now_fn=_now_fn)

    assert out["ok"] is False
    assert "LLM hatası" in out["reason"]
    assert "Haftalık retro çalıştırılamadı" in out["summary"]

    # Verify no report message was written
    messages_docs = list(db.collection("messages").docs.values())
    assert len(messages_docs) == 0


def test_run_empty_llm_response_returns_ok_false_no_report():
    db = FakeDB()

    out = retro.run(db, llm_fn=lambda p: "  ", owner="owner@example.com", now_fn=_now_fn)

    assert out["ok"] is False
    assert "LLM boş yanıt döndürdü" in out["reason"]

    messages_docs = list(db.collection("messages").docs.values())
    assert len(messages_docs) == 0


def test_obsolete_events_counted_and_not_deleted():
    db = FakeDB()
    # 2 recent, 1 obsolete (> 90 days)
    db.collection("events").add({"ts": "2026-07-30T10:00:00+00:00", "kind": "ping"})
    db.collection("events").add({"ts": "2026-07-29T10:00:00+00:00", "kind": "ping"})
    db.collection("events").add({"ts": "2026-01-01T10:00:00+00:00", "kind": "ping"})

    out = retro.run(db, llm_fn=lambda p: "Özet rapor", owner="owner@example.com", now_fn=_now_fn)

    assert out["ok"] is True
    assert out["data"]["events"]["obsolete_90d_count"] == 1

    # Verify events collection was NOT mutated (deletion is forbidden in this phase)
    assert len(db.collection("events").docs) == 3
