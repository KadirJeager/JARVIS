from types import SimpleNamespace

from app import config, tools, voice_trust
from app.agent import build_agent
from app.memory import Memory
from app.speaker import make_sample
from app.speaker_store import enroll_anchors, load_profile, save_profile
from tests.fakes import FakeDB, wired_into_all_tools

APP_NAME = "jarvis"
USER = "kadir@example.com"
SESSION_ID = "voice-kadir@example.com"


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _tool_context(user_id=USER, session_id=SESSION_ID, app_name=APP_NAME):
    """Minimal duck-type of ToolContext: the tool and voice_trust.lookup only
    ever read tool_context.session.{app_name,user_id,id} (see voice_trust.py's
    own docstring evidence) so a SimpleNamespace is sufficient -- no need to
    build a real ADK InvocationContext for this unit-level test."""
    return SimpleNamespace(session=SimpleNamespace(app_name=app_name, user_id=user_id, id=session_id))


def _publish(score, *, trust_level="MEDIUM", presence="locked", device_hint="phone"):
    voice_trust.publish(
        voice_trust.key_for(APP_NAME, USER, SESSION_ID),
        voice_trust.VoiceSignals(
            trust_level=trust_level, voice_score=score, presence=presence, device_hint=device_hint,
        ),
    )


def _seed_profile(db, user_id=USER):
    enroll_anchors(db, user_id, [[1.0, 0.0]])
    profile = load_profile(db, user_id)
    profile.adaptive.append(make_sample([0.0, 1.0], "auto", "phone", "t-auto", "s-auto"))
    profile.adaptive.append(make_sample([0.0, 1.0], "manual", "phone", "t-manual", "s-manual"))
    save_profile(db, user_id, profile)


def _seed_history(db, user_id=USER, n=7):
    from app import speaker_history

    for i in range(n):
        speaker_history.record(
            db, user_id,
            {"id": f"e{i}", "ts": f"t{i}", "score": 0.5 + i * 0.01, "verified": i % 2 == 0,
             "device_hint": "phone", "presence": "locked", "trust_level": "MEDIUM",
             "adapted_sample_id": None, "correction": None, "vec": [0.1, 0.2]},
            cap=config.SPEAKER_HISTORY_CAP,
        )


def test_tools_delegate_to_memory():
    tools.init(Memory(FakeDB()))
    tools.remember_fact("test gerçeği")
    assert tools.search_memory("gerçeği")[0]["text"].startswith("test")
    assert tools.get_user_profile() == {}


def test_agent_wires_tools_and_policy():
    agent = build_agent(Memory(FakeDB()), FakeAudit())
    tool_names = {getattr(t, "__name__", getattr(t, "name", "")) for t in agent.tools}
    assert "get_user_profile" in tool_names
    assert agent.before_tool_callback is not None
    # instruction artık her turda çağrılan bir provider (saat çapası,
    # tests/test_agent_clock.py) -- içerik pinleri çıktısı üzerinden kurulur.
    assert "Jarvis" in agent.instruction(None)


def test_agent_defaults_to_config_model_name():
    from app import config

    agent = build_agent(Memory(FakeDB()), FakeAudit())
    assert agent.model == config.MODEL_NAME


def test_agent_uses_explicit_model_override():
    agent = build_agent(Memory(FakeDB()), FakeAudit(), model="gemini-3.1-flash-live-preview")
    assert agent.model == "gemini-3.1-flash-live-preview"


# --- get_speaker_status ------------------------------------------------------


def test_get_speaker_status_live_verified_utterance():
    db = FakeDB()
    tools.init(Memory(db))
    _seed_profile(db)
    _publish(0.82, trust_level="MEDIUM", presence="locked", device_hint="phone")

    result = tools.get_speaker_status(_tool_context())

    assert result["canli_ses_kanali"] is True
    assert result["guven_seviyesi"] == "MEDIUM"
    assert result["son_eslesme_skoru"] == 0.82
    assert result["cihaz"] == "phone"
    assert result["presence"] == "locked"
    assert "doğrulandı" in result["aciklama"]


def test_get_speaker_status_live_channel_no_utterance_yet():
    db = FakeDB()
    tools.init(Memory(db))
    _seed_profile(db)
    _publish(None, trust_level="MEDIUM", presence="locked", device_hint="phone")

    result = tools.get_speaker_status(_tool_context())

    assert result["canli_ses_kanali"] is True
    assert result["son_eslesme_skoru"] is None
    assert "henüz doğrulanmış söyleyiş yok" in result["aciklama"]


def test_get_speaker_status_text_channel_has_no_live_signal():
    db = FakeDB()
    tools.init(Memory(db))
    _seed_profile(db)
    # No voice_trust.publish() call at all -- exactly what a text-chat call sees.

    result = tools.get_speaker_status(_tool_context())

    assert result["canli_ses_kanali"] is False
    assert result["guven_seviyesi"] is None
    assert result["son_eslesme_skoru"] is None
    assert "metin" in result["aciklama"]
    # Profile summary must still be populated on the text path.
    assert result["profil"] == {"capa": 1, "otomatik": 1, "elle": 1}


def test_get_speaker_status_profile_and_history_summary():
    db = FakeDB()
    tools.init(Memory(db))
    _seed_profile(db)
    _seed_history(db, n=7)

    result = tools.get_speaker_status(_tool_context())

    assert result["profil"] == {"capa": 1, "otomatik": 1, "elle": 1}
    # Only the newest 5 history entries, oldest of the 5 first (load order preserved).
    assert [d["ts"] for d in result["son_dogrulamalar"]] == ["t2", "t3", "t4", "t5", "t6"]
    assert result["son_dogrulamalar"][0]["skor"] == 0.52
    assert result["son_dogrulamalar"][0]["tanindi"] is True
    assert result["son_dogrulamalar"][1]["tanindi"] is False


def _assert_no_vec(obj):
    """Recursive scan, same pattern as tests/test_voice_manage.py's spec §6
    privacy pin: `vec` (raw embeddings) must not appear anywhere in the body."""
    if isinstance(obj, dict):
        assert "vec" not in obj
        for v in obj.values():
            _assert_no_vec(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_vec(v)


def test_get_speaker_status_never_leaks_raw_vectors():
    db = FakeDB()
    tools.init(Memory(db))
    _seed_profile(db)
    _seed_history(db, n=3)
    _publish(0.9)

    result = tools.get_speaker_status(_tool_context())

    _assert_no_vec(result)


def test_get_speaker_status_swallows_infra_errors():
    class BoomDB:
        def collection(self, name):
            raise RuntimeError("firestore unavailable")

    tools.init(Memory(BoomDB()))

    result = tools.get_speaker_status(_tool_context())

    assert result == {"hata": "ses kimliği durumu şu an okunamıyor"}


def test_get_speaker_status_is_green_zone():
    assert config.TOOL_ZONES["get_speaker_status"] == config.ZONE_GREEN


def test_get_speaker_status_is_wired_into_all_tools():
    """Production wiring, not just existence: the accessor Agent actually
    consumes must include it (mutation guard -- removing the tool from
    ALL_TOOLS must fail this test)."""
    assert wired_into_all_tools(tools.get_speaker_status)


def test_instruction_mentions_get_speaker_status():
    from app.agent import INSTRUCTION

    assert "get_speaker_status" in INSTRUCTION
