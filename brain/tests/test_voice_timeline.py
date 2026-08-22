"""Tests for F7 (ürün yüzeyi haritası): the voice session's transcript lands
on the chat timeline.

Pinned contract: closing a voice connection appends ONE kind="voice_session"
row into the fixed `voice-{user_id}` session AND touches the conversation
index -- because list_conversations returns ONLY touched sessions, a missing
touch would make the row invisible in the app even though it exists (the same
gap that hides tasks/retro reports).
"""
import pytest

from app import conversations, messages
from app.memory import Memory
from app.voice import VoiceBridge, _voice_timeline_body
from tests.fakes import FakeDB

USER = "kadir@example.com"
SID = f"voice-{USER}"


def _bridge(transcript):
    db = FakeDB()
    bridge = VoiceBridge(runner=None, session_service=None, memory=Memory(db))
    bridge.transcript = transcript
    return bridge, db


def test_persist_appends_one_voice_session_row():
    bridge, db = _bridge([
        {"role": "user", "text": "yarın saat 9'a hatırlatma koy"},
        {"role": "jarvis", "text": "Hatırlatma kuruldu."},
    ])

    bridge._persist_transcript(SID, USER)

    rows = messages.MessageStore(db).history(USER, SID)
    assert len(rows) == 1
    assert rows[0]["kind"] == "voice_session"
    assert rows[0]["role"] == "model"
    assert "Ben: yarın saat 9'a hatırlatma koy" in rows[0]["text"]
    assert "Jarvis: Hatırlatma kuruldu." in rows[0]["text"]
    assert rows[0]["meta"]["turns"] == 2


def test_persist_touches_the_index_with_the_first_utterance_title():
    bridge, db = _bridge([
        {"role": "jarvis", "text": "Dinliyorum."},
        {"role": "user", "text": "haftalık raporu özetler misin canım"},
        {"role": "jarvis", "text": "Özet: ..."},
    ])

    bridge._persist_transcript(SID, USER)

    listed = conversations.ConversationStore(db).list_conversations(USER)
    assert [c["session_id"] for c in listed] == [SID]
    # Title from the FIRST USER utterance, not the greeting that preceded it.
    assert listed[0]["title"].startswith("haftalık raporu")


def test_persist_voice_only_call_keeps_an_empty_title():
    """No user utterance -> no invented title; the row still lists."""
    bridge, db = _bridge([{"role": "jarvis", "text": "Bağlantı kapandı."}])

    bridge._persist_transcript(SID, USER)

    listed = conversations.ConversationStore(db).list_conversations(USER)
    assert len(listed) == 1
    assert listed[0]["title"] == ""


def test_persist_noops_without_memory_or_without_turns():
    db = FakeDB()
    bridge = VoiceBridge(runner=None, session_service=None, memory=Memory(db))
    bridge._persist_transcript(SID, USER)          # no transcript: silent no-op
    assert messages.MessageStore(db).history(USER, SID) == []

    bridge2, db2 = _bridge([{"role": "user", "text": "selam"}])
    bridge2.memory = None
    bridge2._persist_transcript(SID, USER)         # no memory: silent no-op
    assert messages.MessageStore(db2).history(USER, SID) == []


def test_timeline_body_caps_turns():
    many = [{"role": "user", "text": f"tur {i}"} for i in range(30)]
    body = _voice_timeline_body(many)
    assert body.count("\n") == 19  # last 20 turns only
    assert body.startswith("Ben: tur 10")
