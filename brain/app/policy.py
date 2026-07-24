"""Action authorization matrix (North Star §9): every tool call passes here."""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import config, trust
from .voice_trust import VoiceSignals


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


def check_zone(tool_name: str) -> str:
    return config.TOOL_ZONES.get(tool_name, config.DEFAULT_ZONE)


def _read_trust(tool_context) -> str:
    """Trust level carried in ADK session state; default HIGH when absent (the
    text path, and any voice call with no identity signals yet) so nothing is
    restricted unless something explicitly lowered it. tool_context.state is
    ADK's State (backed by session.state); tolerate None/missing.

    NOTE: the voice bridge does NOT write here -- session state does not
    propagate from the bridge to the runner in ADK 1.36.2 (see voice_trust.py
    for the source-verified reason). This stays as the always-available
    fallback that pins the HIGH default for the text path."""
    state = getattr(tool_context, "state", None)
    if not state:
        return trust.HIGH
    try:
        return state.get(config.TRUST_STATE_KEY, trust.HIGH)
    except Exception:
        return trust.HIGH


def _decide(zone: str, trust_level: str) -> str:
    """zone × trust matrix (spec §7). RED always blocks. HIGH keeps today's
    behavior. MEDIUM/LOW escalate YELLOW -> confirm (reuse of the block-with-
    message pattern). GREEN stays allowed."""
    if zone == config.ZONE_RED:
        return "block"
    if trust_level == trust.HIGH:
        return "dry_run" if config.DRY_RUN else "allow"
    if zone == config.ZONE_YELLOW:      # MEDIUM or LOW
        return "confirm"
    return "dry_run" if config.DRY_RUN else "allow"


TrustProvider = Callable[[Any], VoiceSignals | None]


def _voice_signals(trust_provider: TrustProvider | None, tool_context) -> VoiceSignals | None:
    """Ask the (optional) provider for this call's voice identity signals.

    A provider failure degrades to "no signals" -> _read_trust -> HIGH, i.e.
    today's behaviour. That is fail-OPEN by design and matches _read_trust's own
    except branch: spec §12's hard rule is that Kadir is never locked out by the
    identity layer (written-input fallback always exists), so an identity
    subsystem fault must not start blocking his tools."""
    if trust_provider is None:
        return None
    try:
        return trust_provider(tool_context)
    except Exception:
        logging.exception("policy: trust provider failed, defaulting to no voice signals")
        return None


def make_policy_callback(audit: AuditWriter, trust_provider: TrustProvider | None = None):
    """`trust_provider` is how voice identity reaches the policy matrix: ONLY
    the voice runner's agent is built with one (main._init_voice), so the text
    runner's callback is structurally incapable of seeing voice trust and the
    /api/chat path keeps its exact previous behaviour. See voice_trust.py for
    why the signals cannot simply ride ADK session state."""

    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name)
        signals = _voice_signals(trust_provider, tool_context)
        trust_level = signals.trust_level if signals else _read_trust(tool_context)
        decision = _decide(zone, trust_level)
        # spec §7: the audit trail must be able to reconstruct WHY a decision
        # was made, not just what it was. None (not a fabricated "foreground")
        # when there is no voice evidence -- e.g. every text-chat call.
        audit.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "orchestrator",
            "tool": tool.name,
            "args": {k: str(v)[:500] for k, v in (args or {}).items()},
            "zone": zone,
            "trust": trust_level,
            "voice_score": signals.voice_score if signals else None,
            "presence": signals.presence if signals else None,
            "device_hint": signals.device_hint if signals else None,
            "decision": decision,
        })
        if decision == "block":
            return {"result": (
                f"POLİTİKA ENGELİ: '{tool.name}' kırmızı bölgede — onaysız çalıştırılamaz. "
                "Kadir'e ne yapmak istediğini söyle ve onay iste."
            )}
        if decision == "confirm":
            return {"result": (
                f"GÜVEN DÜŞÜK: '{tool.name}' şu an düşük-güven bağlamında (kimlik doğrulanmadı). "
                "Çalıştırmadan önce Kadir'den açık onay iste."
            )}
        if decision == "dry_run":
            return {"result": f"DRY-RUN: '{tool.name}' şu argümanlarla çalışacaktı: {args}"}
        return None

    return policy_callback
