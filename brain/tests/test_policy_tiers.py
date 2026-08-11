"""Tests for Phase C Tool Tiers and Voice Policy Matrix (spec §7, §9)."""
import pytest

from app import config, policy, trust
from app.voice_trust import VoiceSignals


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry: dict) -> None:
        self.entries.append(entry)


def test_tool_tiers_pin_snapshot():
    """Class guardian test: all tools in TOOL_ZONES must have pinned tiers in TOOL_TIERS."""
    assert config.DEFAULT_TIER == config.TIER_T2
    for tool_name in config.TOOL_ZONES:
        assert tool_name in config.TOOL_TIERS, f"Tool '{tool_name}' missing from config.TOOL_TIERS"

    # Specific T0 pins
    assert config.TOOL_TIERS["list_watched_repos"] == config.TIER_T0
    assert config.TOOL_TIERS["get_repo_updates"] == config.TIER_T0
    assert config.TOOL_TIERS["list_reminders"] == config.TIER_T0
    assert config.TOOL_TIERS["check_my_vitals"] == config.TIER_T0
    assert config.TOOL_TIERS["get_speaker_status"] == config.TIER_T0

    # Specific T1 pins
    assert config.TOOL_TIERS["set_reminder"] == config.TIER_T1
    assert config.TOOL_TIERS["watch_repo"] == config.TIER_T1
    assert config.TOOL_TIERS["unwatch_repo"] == config.TIER_T1

    # Specific T2 pins
    assert config.TOOL_TIERS["get_user_profile"] == config.TIER_T2
    assert config.TOOL_TIERS["search_memory"] == config.TIER_T2
    assert config.TOOL_TIERS["remember_fact"] == config.TIER_T2
    assert config.TOOL_TIERS["add_lesson"] == config.TIER_T2
    assert config.TOOL_TIERS["update_user_profile"] == config.TIER_T2
    assert config.TOOL_TIERS["consult_gemini"] == config.TIER_T2
    assert config.TOOL_TIERS["propose_tool"] == config.TIER_T2
    assert config.TOOL_TIERS["propose_agent"] == config.TIER_T2
    assert config.TOOL_TIERS["spawn_specialist"] == config.TIER_T2

    # Specific T3 pins
    assert config.TOOL_TIERS["cancel_reminder"] == config.TIER_T3

    # Unknown tool fails closed to DEFAULT_TIER (T2)
    assert policy.check_tier("unknown_future_tool") == config.TIER_T2


def test_unknown_tool_is_treated_as_irreversible():
    """Fail-closed: an unlisted tool must never be assumed undoable."""
    assert config.is_reversible("some_tool_nobody_declared") is False


def test_reversible_tools_are_declared_reversible():
    assert config.is_reversible("search_memory") is True


def test_destructive_tools_are_declared_irreversible():
    assert config.is_reversible("send_message") is False


def test_every_zoned_tool_declares_reversibility():
    """A tool with a zone but no reversibility entry silently becomes
    'irreversible' -- correct but accidental. Force the declaration."""
    missing = sorted(set(config.TOOL_ZONES) - set(config.TOOL_REVERSIBILITY))
    assert missing == [], f"reversibility undeclared for: {missing}"


def test_decide_voice_red_zone_always_blocks():
    """RED zone tools must always block regardless of trust, tier, or cm_ok."""
    sig = VoiceSignals(trust_level=trust.HIGH, voice_score=0.95, cm_ok=True)
    assert policy._decide_voice(config.ZONE_RED, trust.HIGH, config.TIER_T0, sig) == "block"
    assert policy._decide_voice(config.ZONE_RED, trust.HIGH, config.TIER_T3, sig) == "block"

    sig_spoof = VoiceSignals(trust_level=trust.HIGH, voice_score=0.95, cm_ok=False)
    assert policy._decide_voice(config.ZONE_RED, trust.HIGH, config.TIER_T0, sig_spoof) == "block"


def test_decide_voice_spoof_evidence_cm_ok_false():
    """cm_ok is False (active spoof evidence): T0 allows (same-device screen), T1/T2/T3 require confirm."""
    sig_spoof = VoiceSignals(trust_level=trust.HIGH, voice_score=0.90, cm_ok=False)

    # T0 on GREEN zone -> allow
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T0, sig_spoof) == "allow"

    # T1 on GREEN or YELLOW zone -> confirm
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T1, sig_spoof) == "confirm"
    assert policy._decide_voice(config.ZONE_YELLOW, trust.HIGH, config.TIER_T1, sig_spoof) == "confirm"

    # T2 on GREEN zone -> confirm
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T2, sig_spoof) == "confirm"

    # T3 on GREEN zone -> confirm
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T3, sig_spoof) == "confirm"


def test_decide_voice_absence_of_evidence_cm_ok_none():
    """cm_ok is None or voice_score is None: T2+ with non-HIGH trust escalates GREEN to confirm."""
    sig_no_cm = VoiceSignals(trust_level=trust.MEDIUM, voice_score=None, cm_ok=None)

    # T2 on GREEN with MEDIUM trust -> confirm (The new tightening!)
    assert policy._decide_voice(config.ZONE_GREEN, trust.MEDIUM, config.TIER_T2, sig_no_cm) == "confirm"
    assert policy._decide_voice(config.ZONE_GREEN, trust.LOW, config.TIER_T2, sig_no_cm) == "confirm"

    # T2 on GREEN with HIGH trust -> allow
    sig_high_no_cm = VoiceSignals(trust_level=trust.HIGH, voice_score=0.85, cm_ok=None)
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T2, sig_high_no_cm) == "allow"

    # T0 on GREEN with MEDIUM trust -> allow
    assert policy._decide_voice(config.ZONE_GREEN, trust.MEDIUM, config.TIER_T0, sig_no_cm) == "allow"

    # T1 on GREEN with MEDIUM trust -> allow
    assert policy._decide_voice(config.ZONE_GREEN, trust.MEDIUM, config.TIER_T1, sig_no_cm) == "allow"

    # T1 on YELLOW with MEDIUM trust -> confirm
    assert policy._decide_voice(config.ZONE_YELLOW, trust.MEDIUM, config.TIER_T1, sig_no_cm) == "confirm"


def test_decide_voice_bonafide_cm_ok_true():
    """cm_ok is True (bonafide audio): follows standard _decide matrix."""
    sig_ok = VoiceSignals(trust_level=trust.HIGH, voice_score=0.88, cm_ok=True)
    assert policy._decide_voice(config.ZONE_GREEN, trust.HIGH, config.TIER_T2, sig_ok) == "allow"
    assert policy._decide_voice(config.ZONE_YELLOW, trust.HIGH, config.TIER_T2, sig_ok) == "allow"

    sig_ok_med = VoiceSignals(trust_level=trust.MEDIUM, voice_score=0.40, cm_ok=True)
    assert policy._decide_voice(config.ZONE_GREEN, trust.MEDIUM, config.TIER_T2, sig_ok_med) == "allow"
    assert policy._decide_voice(config.ZONE_YELLOW, trust.MEDIUM, config.TIER_T2, sig_ok_med) == "confirm"


def test_text_channel_behavior_unchanged():
    """Text channel (signals=None) behavior remains identical to _decide."""
    audit = FakeAudit()
    callback = policy.make_policy_callback(audit, trust_provider=lambda ctx: None)

    class FakeTool:
        name = "get_user_profile"  # GREEN, T2

    class FakeContext:
        state = {config.TRUST_STATE_KEY: trust.HIGH}

    # GREEN + HIGH trust -> None (allow execution)
    res = callback(FakeTool(), {}, FakeContext())
    assert res is None
    assert audit.entries[-1]["decision"] == "allow"
    assert audit.entries[-1]["tier"] == config.TIER_T2
    assert audit.entries[-1]["cm_ok"] is None


def test_policy_callback_spoof_confirm_text():
    """When cm_ok is False and decision is confirm, returned message must warn about synthetic voice."""
    audit = FakeAudit()
    sig_spoof = VoiceSignals(trust_level=trust.HIGH, voice_score=0.90, cm_ok=False)
    callback = policy.make_policy_callback(audit, trust_provider=lambda ctx: sig_spoof)

    class FakeTool:
        name = "search_memory"  # GREEN, T2 -> confirm under spoof

    class FakeContext:
        state = {}

    res = callback(FakeTool(), {}, FakeContext())
    assert res is not None
    assert "SES KİMLİĞİ ŞÜPHELİ" in res["result"]
    assert "sentetik" in res["result"]
