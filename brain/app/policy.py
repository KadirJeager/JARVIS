"""Action authorization matrix (North Star §9): every tool call passes here."""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import config, trust, vitals
from .voice_trust import VoiceSignals


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


def check_zone(tool_name: str) -> str:
    return config.TOOL_ZONES.get(tool_name, config.DEFAULT_ZONE)


def write_audit(
    audit: AuditWriter,
    *,
    actor: str,
    tool_name: str,
    args: dict | None,
    zone: str,
    decision: str,
    trust_level: str | None = None,
    voice_score: float | None = None,
    presence: str | None = None,
    device_hint: str | None = None,
) -> None:
    """Append one decision entry to the audit trail (spec §7: the trail must
    reconstruct WHY a decision was made, not just what it was).

    Shared by the ADK policy callback (actor "orchestrator") and the guest
    gate (actor "guest:<email>", app/guest_gate.py) so both paths emit the
    SAME entry shape. Args are stringified and cut at 500 chars per value:
    the audit is for reconstruction, not a full payload dump. The voice
    evidence fields stay None (not a fabricated "foreground") when there is
    no voice context -- e.g. every text-chat and every guest call."""
    audit.write({
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "tool": tool_name,
        "args": {k: str(v)[:500] for k, v in (args or {}).items()},
        "zone": zone,
        # "trust_level", NOT "trust": one concept, one name end to end --
        # spec §7, config.TRUST_STATE_KEY and VoiceSignals.trust_level all
        # use this spelling, and the audit is what a past decision is
        # reconstructed from.
        "trust_level": trust_level,
        "voice_score": voice_score,
        "presence": presence,
        "device_hint": device_hint,
        "decision": decision,
    })


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


def _count_tool_call(audit: AuditWriter) -> None:
    """North Star §4.5: allow kararlarını vitals/counters'a sayar (best effort).

    db'ye audit nesnesi üzerinden ulaşılır (FirestoreAudit.db); db taşımayan
    audit yazıcıları (test fake'leri gibi) sessizce atlanır. Herhangi bir
    Firestore hatası loglanır ve yutulur — kota defter tutması, güvenlik
    kararının kendisini asla bozmamalı."""
    db = getattr(audit, "db", None)
    if db is None:
        return
    try:
        vitals.bump(db, "tool_calls_today")
    except Exception:
        logging.exception("policy: vitals tool sayacı yazılamadı -- devam")


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
        write_audit(
            audit,
            actor="orchestrator",
            tool_name=tool.name,
            args=args,
            zone=zone,
            # None (not a fabricated "foreground") when there is no voice
            # evidence -- e.g. every text-chat call.
            trust_level=trust_level,
            voice_score=signals.voice_score if signals else None,
            presence=signals.presence if signals else None,
            device_hint=signals.device_hint if signals else None,
            decision=decision,
        )
        if decision == "allow":
            # North Star §4.5: gerçekten ÇALIŞACAK her araç çağrısı
            # tool_calls_today'i artırır. Block/confirm/dry_run sayılmaz (araç
            # hiç koşmadı). Sayım write_audit SONRASINDA yapılır: audit yazımı
            # fırlatırsa buraya hiç ulaşılmaz ("audit.write başarılıysa say").
            # Sayaç yazımı da asla aracın önünü kesmez — logla, geç.
            _count_tool_call(audit)
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
