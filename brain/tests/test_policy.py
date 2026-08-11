from types import SimpleNamespace
from app import config, trust
from app.policy import ACTOR_ORCHESTRATOR, check_zone, make_policy_callback


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _tool(name):
    return SimpleNamespace(name=name)


def test_known_tool_zone():
    assert check_zone("get_user_profile") == config.ZONE_GREEN


def test_unknown_tool_defaults_to_red():
    assert check_zone("launch_missiles") == config.ZONE_RED


def test_green_tool_allowed_and_audited():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"a": 1}, None)
    assert result is None  # None => ADK actually executes the tool
    assert audit.entries[0]["decision"] == "allow"
    assert audit.entries[0]["zone"] == config.ZONE_GREEN


def test_red_tool_blocked_with_turkish_message():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"


def test_dry_run_intercepts_green_tool(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"x": 2}, None)
    assert result is not None and "DRY-RUN" in result["result"]
    assert audit.entries[0]["decision"] == "dry_run"


def test_red_zone_blocked_even_in_dry_run(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "POLİTİKA ENGELİ" in result["result"]
    assert audit.entries[0]["decision"] == "block"  # not "dry_run"


def _ctx(level):
    # mirrors ADK ToolContext: .state is a dict-like mapping backed by session.state
    return SimpleNamespace(state={config.TRUST_STATE_KEY: level})


def test_yellow_tool_blocked_when_trust_medium():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.MEDIUM))
    assert result is not None and "onay" in result["result"].lower()
    # distinct "confirm" decision (not "block"): YELLOW zone + non-HIGH trust
    # escalates to a confirmation request, RED-zone "block" stays untouched.
    assert audit.entries[0]["decision"] == "confirm"
    assert audit.entries[0]["trust_level"] == trust.MEDIUM


def test_yellow_tool_allowed_when_trust_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.HIGH))
    assert result is None
    assert audit.entries[0]["decision"] == "allow"


def test_missing_tool_context_defaults_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    # green tool, tool_context None (text path / existing tests) -> allow, HIGH
    result = cb(_tool("get_user_profile"), {}, None)
    assert result is None
    assert audit.entries[0]["trust_level"] == trust.HIGH


def test_yellow_tool_blocked_when_trust_low():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.LOW))
    assert result is not None
    assert audit.entries[0]["decision"] == "confirm"
    assert audit.entries[0]["trust_level"] == trust.LOW


def test_read_trust_empty_state_defaults_high():
    """tool_context.state present but empty (falsy dict) -- the `if not
    state` branch -- must default HIGH just like tool_context=None does."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, SimpleNamespace(state={}))
    assert result is None
    assert audit.entries[0]["trust_level"] == trust.HIGH


def test_read_trust_state_get_raising_defaults_high():
    """A truthy .state whose .get() raises (e.g. a malformed ADK State) must
    be swallowed by _read_trust's except-Exception branch and default HIGH,
    not propagate and break tool execution."""
    class RaisingState:
        def get(self, key, default=None):
            raise RuntimeError("boom")

    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, SimpleNamespace(state=RaisingState()))
    assert result is None
    assert audit.entries[0]["trust_level"] == trust.HIGH


def test_green_zone_medium_trust_is_allowed():
    """(GREEN, MEDIUM) matrix cell: not RED, not HIGH, not YELLOW -> allow."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, _ctx(trust.MEDIUM))
    assert result is None
    assert audit.entries[0]["decision"] == "allow"


def test_red_zone_medium_trust_still_blocks():
    """(RED, MEDIUM) matrix cell: RED must block regardless of trust level,
    unlike YELLOW which only blocks below HIGH."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, _ctx(trust.MEDIUM))
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"


# --- C1 + M3: voice signals reach the matrix, and the audit records WHY -----

from app.voice_trust import VoiceSignals


def _signals(level=trust.MEDIUM, score=0.42, presence="locked", device="headset"):
    return VoiceSignals(trust_level=level, voice_score=score,
                        presence=presence, device_hint=device)


def test_trust_provider_supplies_the_level_when_session_state_cannot():
    """The whole point of C1: ADK hands the bridge and the runner independent
    session copies, so the level arrives through the provider, not the state.
    tool_context here carries an EMPTY state -- exactly what the runner's copy
    looks like -- and the decision must still tighten."""
    audit = FakeAudit()
    cb = make_policy_callback(audit, trust_provider=lambda ctx: _signals())
    result = cb(_tool("update_user_profile"), {}, SimpleNamespace(state={}))
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["trust_level"] == trust.MEDIUM
    assert audit.entries[0]["decision"] == "confirm"


def test_audit_records_the_full_signal_set_spec_requires():
    """Spec §7: trust_level, voice_score, presence and device_hint all go to the
    persistent audit trail -- a security decision has to be reconstructable
    later, not only visible in an ephemeral log line."""
    audit = FakeAudit()
    cb = make_policy_callback(audit, trust_provider=lambda ctx: _signals())
    cb(_tool("get_user_profile"), {}, None)
    entry = audit.entries[0]
    assert (entry["trust_level"], entry["voice_score"], entry["presence"], entry["device_hint"]) == (
        trust.MEDIUM, 0.42, "locked", "headset")


def test_audit_signal_fields_are_none_without_voice_evidence():
    """Text path: no provider at all -> the new fields default to None rather
    than a fabricated "foreground", and the decision is unchanged."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    assert cb(_tool("update_user_profile"), {}, None) is None
    entry = audit.entries[0]
    assert entry["trust_level"] == trust.HIGH
    assert (entry["voice_score"], entry["presence"], entry["device_hint"]) == (None, None, None)


def test_provider_returning_none_falls_back_to_state_default_high():
    """A voice connection before its first verified utterance (or any call the
    provider cannot resolve) must behave exactly like the text path."""
    audit = FakeAudit()
    cb = make_policy_callback(audit, trust_provider=lambda ctx: None)
    assert cb(_tool("update_user_profile"), {}, None) is None
    assert audit.entries[0]["trust_level"] == trust.HIGH


def test_provider_raising_is_logged_and_fails_open_to_high(caplog):
    """Fail-OPEN is deliberate (spec §12: the identity layer must never lock
    Kadir out). It must be loud, though -- a silent identity outage is how a
    security control quietly stops working."""
    audit = FakeAudit()

    def boom(ctx):
        raise RuntimeError("provider exploded")

    cb = make_policy_callback(audit, trust_provider=boom)
    with caplog.at_level("ERROR"):
        assert cb(_tool("update_user_profile"), {}, None) is None
    assert audit.entries[0]["trust_level"] == trust.HIGH
    assert "trust provider failed" in caplog.text


def test_red_zone_blocks_regardless_of_voice_signals():
    """RED is identity-independent (spec §7): even a fully verified HIGH voice
    cannot unlock it."""
    audit = FakeAudit()
    cb = make_policy_callback(
        audit, trust_provider=lambda ctx: _signals(level=trust.HIGH, presence="foreground"))
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "POLİTİKA ENGELİ" in result["result"]
    assert audit.entries[0]["decision"] == "block"


# --- Görev 4 (Faz Y3): approval_sink — kırmızı engel bir onay kartına döner --
#
# İki pim taşıyıcıdır, silinmesi güvenlik sınırını kaldırır:
#   * test_red_block_text_without_a_sink_is_byte_identical -- sink YOKKEN
#     davranış Y3 öncesiyle birebir aynı (guest_gate bu yoldan geçer, §5).
#   * test_sink_failure_falls_back_to_the_block_text_and_the_tool_still_does_not_run
#     -- onay kaydı kurulamaması bir aracın çalışmasına ASLA yol açmaz.

BLOCK_TEXT = (
    "POLİTİKA ENGELİ: 'unknown_danger' kırmızı bölgede — onaysız çalıştırılamaz. "
    "Kadir'e ne yapmak istediğini söyle ve onay iste."
)
SINK_TEXT = "ONAY KARTI GÖNDERİLDİ: Kadir'in kararını bekliyorum."


class RecordingSink:
    """`(tool_name, args, tool_context, *, actor, trust_level) -> str` imzalı
    sahte sink; çağrıları sayar (sarı/yeşil dallarda "hiç çağrılmadı" ancak
    sayılırsa kanıtlanır). `actor`/`trust_level` Görev 2 (Onay Kartı 2.0) ile
    eklendi: policy_callback bunları artık sink'e taşıyor."""

    def __init__(self, text=SINK_TEXT, raises=None):
        self.calls = []
        self._text = text
        self._raises = raises

    def __call__(self, tool_name, args, tool_context, *, actor=None, trust_level=None):
        self.calls.append((tool_name, args, tool_context, actor, trust_level))
        if self._raises is not None:
            raise self._raises
        return self._text


def test_red_block_text_without_a_sink_is_byte_identical():
    """REGRESYON PİMİ (spec §5): sink verilmediğinde kırmızı metin Y3
    öncesiyle KARAKTERİ KARAKTERİNE aynıdır — guest_gate ve mevcut her çağıran
    bu yoldan geçer."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    assert cb(_tool("unknown_danger"), {"a": 1}, None) == {"result": BLOCK_TEXT}


def test_sink_text_replaces_the_block_text_but_the_tool_still_does_not_run():
    audit = FakeAudit()
    sink = RecordingSink()
    cb = make_policy_callback(audit, approval_sink=sink)

    result = cb(_tool("unknown_danger"), {"reminder_id": "r1"}, None)

    # Dönen dict None DEĞİL => ADK aracı çalıştırmaz; çalışma anı onay anıdır.
    assert result == {"result": SINK_TEXT}
    assert len(sink.calls) == 1


def test_sink_receives_the_tool_name_args_and_tool_context():
    audit = FakeAudit()
    sink = RecordingSink()
    ctx = SimpleNamespace(state={})
    cb = make_policy_callback(audit, approval_sink=sink)

    cb(_tool("unknown_danger"), {"reminder_id": "r1"}, ctx)

    assert sink.calls == [("unknown_danger", {"reminder_id": "r1"}, ctx,
                           ACTOR_ORCHESTRATOR, trust.HIGH)]


def test_sink_receives_actor_and_trust_level_from_the_callback():
    """Görev 2: sink kendi başına trust_level'ı YENİDEN OKUMAZ (bu, sesli
    çağrılarda yanlış olurdu -- voice_trust sinyalleri tool_context.state'e
    yazılmaz); policy_callback'in zaten hesapladığı değer aynen taşınır."""
    audit = FakeAudit()
    sink = RecordingSink()
    cb = make_policy_callback(audit, approval_sink=sink, actor="factory:tpl#i1")

    cb(_tool("unknown_danger"), {}, _ctx(trust.LOW))

    [(_tool_name, _args, _ctx_arg, actor, trust_level)] = sink.calls
    assert actor == "factory:tpl#i1"
    assert trust_level == trust.LOW


def test_sink_failure_falls_back_to_the_block_text_and_the_tool_still_does_not_run(caplog):
    """FAIL-CLOSED PİMİ (spec §5): onay kaydı kurulamazsa eski metne düşülür ve
    araç YİNE çalışmaz. Bu testin silinmesi, bir Firestore hıçkırığının kırmızı
    bir aracı serbest bırakmasına açık kapı bırakır."""
    audit = FakeAudit()
    sink = RecordingSink(raises=RuntimeError("firestore öldü"))
    cb = make_policy_callback(audit, approval_sink=sink)

    with caplog.at_level("ERROR"):
        result = cb(_tool("unknown_danger"), {}, None)

    assert result == {"result": BLOCK_TEXT}   # None DEĞİL: araç çalışmaz
    assert len(sink.calls) == 1
    assert "onay kartı" in caplog.text.lower()


def test_sink_is_never_called_in_the_yellow_confirm_branch():
    audit = FakeAudit()
    sink = RecordingSink()
    cb = make_policy_callback(audit, approval_sink=sink)

    result = cb(_tool("update_user_profile"), {}, _ctx(trust.MEDIUM))

    assert audit.entries[0]["decision"] == "confirm"
    assert "GÜVEN DÜŞÜK" in result["result"]
    assert sink.calls == []


def test_sink_is_never_called_in_the_green_allow_branch():
    audit = FakeAudit()
    sink = RecordingSink()
    cb = make_policy_callback(audit, approval_sink=sink)

    assert cb(_tool("get_user_profile"), {}, None) is None
    assert sink.calls == []


def test_sink_is_never_called_in_the_dry_run_branch(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    sink = RecordingSink()
    cb = make_policy_callback(audit, approval_sink=sink)

    result = cb(_tool("get_user_profile"), {}, None)

    assert "DRY-RUN" in result["result"]
    assert sink.calls == []


def test_audit_still_records_the_block_when_the_sink_handles_it():
    """Onay kartına dönen bir engel yine bir ENGELDİR: audit satırı
    decision="block" olarak yazılır (spec §7 — karar yeniden kurulabilmeli)."""
    audit = FakeAudit()
    cb = make_policy_callback(audit, approval_sink=RecordingSink())

    cb(_tool("unknown_danger"), {"reminder_id": "r1"}, None)

    entry = audit.entries[0]
    assert entry["decision"] == "block"
    assert entry["zone"] == config.ZONE_RED
    assert entry["args"] == {"reminder_id": "r1"}


def test_audit_still_records_the_block_when_the_sink_fails():
    audit = FakeAudit()
    cb = make_policy_callback(
        audit, approval_sink=RecordingSink(raises=RuntimeError("firestore öldü")))

    cb(_tool("unknown_danger"), {}, None)

    assert audit.entries[0]["decision"] == "block"
