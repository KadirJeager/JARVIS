"""Action authorization matrix (North Star §9): every tool call passes here."""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import config, trust, vitals
from .voice_trust import VoiceSignals


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


# (tool name) -> zone | None. app/tool_registry.make_zone_resolver builds the
# production one; None means "this resolver knows nothing about that name".
ZoneResolver = Callable[[str], str | None]


def check_zone(tool_name: str, zone_resolver: "ZoneResolver | None" = None) -> str:
    """Resolve a tool's zone (North Star §9). Order is a SECURITY BOUNDARY, not
    a style choice (Y4 spec §4.2):

        config.TOOL_ZONES (code)  ->  registry (resolver)  ->  DEFAULT_ZONE (red)

    Code first, unconditionally: a granted registry entry can only assign a zone
    to a name the code does NOT know. If the registry could win, one approved
    `{name: "cancel_reminder", zone: "green"}` document would pull a red tool
    into green and bypass the approval centre entirely.

    `zone_resolver` is INJECTED rather than read from a module-level db so this
    function stays pure and db-free. Called without one -- as app/guest_gate.py
    and every pre-Y4 caller do -- the behaviour is byte-identical to before:
    known tool -> its code zone, unknown tool -> red.

    A resolver that raises, or answers with anything outside the grantable
    zones (green/yellow), degrades to DEFAULT_ZONE (red). Fail-closed: a
    Firestore hiccup or a corrupted document must never OPEN a tool.
    """
    zone = config.TOOL_ZONES.get(tool_name)
    if zone is not None:
        return zone
    if zone_resolver is not None:
        try:
            resolved = zone_resolver(tool_name)
        except Exception:
            logging.exception(
                "policy: zone resolver failed for tool=%s -- falling back to red", tool_name)
            resolved = None
        if resolved in (config.ZONE_GREEN, config.ZONE_YELLOW):
            return resolved
    return config.DEFAULT_ZONE


def check_tier(tool_name: str) -> str:
    """Resolve a tool's tier (North Star Phase C). Unknown tool defaults to DEFAULT_TIER (t2)."""
    return config.TOOL_TIERS.get(tool_name, config.DEFAULT_TIER)


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
    tier: str | None = None,
    cm_ok: bool | None = None,
) -> None:
    """Append one decision entry to the audit trail (spec §7: the trail must
    reconstruct WHY a decision was made, not just what it was).

    Shared by the ADK policy callback (actor "orchestrator", or
    "factory:<şablon>#<örnek>" for a derived agent -- app/factory.py) and the
    guest gate (actor "guest:<email>", app/guest_gate.py) so every path emits
    the SAME entry shape. Args are stringified and cut at 500 chars per value:
    the audit is for reconstruction, not a full payload dump. The voice
    evidence fields stay None (not a fabricated "foreground") when there is
    no voice context -- e.g. every text-chat and every guest call."""
    audit.write({
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "tool": tool_name,
        "args": {k: str(v)[:500] for k, v in (args or {}).items()},
        "zone": zone,
        "tier": tier,
        "trust_level": trust_level,
        "voice_score": voice_score,
        "presence": presence,
        "device_hint": device_hint,
        "cm_ok": cm_ok,
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


def _decide_voice(zone: str, trust_level: str, tier: str, signals: VoiceSignals) -> str:
    """Voice channel decision matrix considering tier and countermeasure (CM) signals (Phase C).

    Matrix rules:
    - zone RED -> "block" (card flow takes over).
    - signals.cm_ok is False (active spoof evidence): T0 allows (same-device screen), T1/T2/T3 require confirm.
    - signals.cm_ok is None or signals.voice_score is None (absence of evidence):
      - Tier T2+ (t2, t3) and trust != HIGH -> "confirm" (tightens GREEN T2+ under medium/low trust).
      - T0/T1 or HIGH trust -> uses existing _decide matrix.
    - signals.cm_ok is True (bonafide audio): uses existing _decide matrix.
    """
    if zone == config.ZONE_RED:
        return "block"

    if signals.cm_ok is False:
        if tier == config.TIER_T0:
            return _decide(zone, trust_level)
        return "confirm"

    if signals.cm_ok is None or signals.voice_score is None:
        if tier in (config.TIER_T2, config.TIER_T3) and trust_level != trust.HIGH:
            return "confirm"
        return _decide(zone, trust_level)

    # cm_ok is True
    return _decide(zone, trust_level)


TrustProvider = Callable[[Any], VoiceSignals | None]

# (tool_name, args, tool_context, *, actor, trust_level) -> modele gidecek
# Türkçe metin. `actor`/`trust_level` Görev 2 (Onay Kartı 2.0) ile eklendi:
# sink'in kendisi (main._approval_sink) bunları göremez -- yalnızca policy
# callback'in zaten hesapladığı değerlerdir, buradan aynen taşınır (Callable
# tipi kwonly parametreleri ifade edemediği için burada belgelenir).
ApprovalSink = Callable[[str, dict, Any], str]


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


RED_BLOCK_TEMPLATE = (
    "POLİTİKA ENGELİ: '{tool_name}' kırmızı bölgede — onaysız çalıştırılamaz. "
    "Kadir'e ne yapmak istediğini söyle ve onay iste."
)


def _red_block_text(tool_name: str, args: dict[str, Any],
                    tool_context, approval_sink: "ApprovalSink | None",
                    *, actor: str, trust_level: str | None) -> str:
    """Kırmızı bölge engelinin modele dönecek metni (spec §5, Faz Y3).

    `approval_sink` YOKSA metin Y3 öncesiyle BİREBİR aynıdır — guest_gate ve
    sink'siz kurulan her callback bu yoldan geçer.

    Sink varsa bir onay kaydı kurar ve "kart gönderildi" metnini döndürür. Sink
    FIRLARSA eski metne düşülür: onayın kurulamaması bir aracın çalışmasına ASLA
    yol açmaz (fail-closed). Her iki dalda da bu fonksiyonun dönüşü bir METİNDİR,
    yani callback None DEĞİL bir sonuç döndürür ve ADK aracı çalıştırmaz.

    `actor`/`trust_level` (Görev 2) buraya `policy_callback`'ten TAŞINIR, yeniden
    HESAPLANMAZ: sink'in kendi başına bir tool_context'ten trust_level'ı
    yeniden okuması sesli çağrılarda YANLIŞ olurdu -- voice_trust'ın sinyalleri
    ADK session state'ine yazılmaz (bkz. voice_trust.py), bu yüzden
    policy_callback'in zaten hesapladığı değer tek doğru kaynaktır."""
    if approval_sink is not None:
        try:
            return approval_sink(tool_name, args, tool_context,
                                 actor=actor, trust_level=trust_level)
        except Exception:
            logging.exception(
                "policy: onay kartı oluşturulamadı tool=%s -- kırmızı engel metnine "
                "düşülüyor (araç YİNE çalışmıyor)", tool_name)
    return RED_BLOCK_TEMPLATE.format(tool_name=tool_name)


ACTOR_ORCHESTRATOR = "orchestrator"


def make_policy_callback(audit: AuditWriter, trust_provider: TrustProvider | None = None,
                         approval_sink: ApprovalSink | None = None,
                         zone_resolver: "ZoneResolver | None" = None,
                         actor: str = ACTOR_ORCHESTRATOR):
    """`trust_provider` is how voice identity reaches the policy matrix: ONLY
    the voice runner's agent is built with one (main._init_voice), so the text
    runner's callback is structurally incapable of seeing voice trust and the
    /api/chat path keeps its exact previous behaviour. See voice_trust.py for
    why the signals cannot simply ride ADK session state.

    `approval_sink` (Faz Y3, spec §5) is the same idea one layer up: it turns a
    RED block into a queued approval card. It is passed ONLY by the text and
    voice runners (main._init / main._init_voice) -- never by the guest gate,
    which does not use this factory at all (§4.9: guests never reach RED). Left
    None -- as every other caller does -- the RED branch keeps its exact
    pre-Y3 text, and the tool is blocked exactly as before.

    `zone_resolver` (Faz Y4, spec §4.2) lets the tool registry assign zones to
    tools the CODE does not know -- granted MCP servers and capabilities Jarvis
    acquired after this build shipped. It can never loosen a zone that
    config.TOOL_ZONES already states; see check_zone for why that ordering is
    the security boundary. Left None, zone resolution is exactly pre-Y4.

    `actor` (Faz Y4.2, §8.5 değişmez 5) is WHO the audit line is attributed to.
    It changes NO decision -- only the trail. The factory (app/factory.py)
    passes "factory:<şablon>#<örnek>" so a derived agent's work can never be
    confused with the orchestrator's own; left at its default every caller
    writes "orchestrator" exactly as before Y4.2."""

    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name, zone_resolver)
        tier = check_tier(tool.name)
        signals = _voice_signals(trust_provider, tool_context)
        trust_level = signals.trust_level if signals else _read_trust(tool_context)
        if signals is not None:
            decision = _decide_voice(zone, trust_level, tier, signals)
        else:
            decision = _decide(zone, trust_level)
        write_audit(
            audit,
            actor=actor,
            tool_name=tool.name,
            args=args,
            zone=zone,
            # None (not a fabricated "foreground") when there is no voice
            # evidence -- e.g. every text-chat call.
            trust_level=trust_level,
            voice_score=signals.voice_score if signals else None,
            presence=signals.presence if signals else None,
            device_hint=signals.device_hint if signals else None,
            tier=tier,
            cm_ok=signals.cm_ok if signals else None,
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
            return {"result": _red_block_text(tool.name, args, tool_context, approval_sink,
                                              actor=actor, trust_level=trust_level)}
        if decision == "confirm":
            if signals and signals.cm_ok is False:
                confirm_msg = (
                    f"SES KİMLİĞİ ŞÜPHELİ: bu oturumdaki ses sentetik olabilir — "
                    f"Kadir'den açık onay almadan '{tool.name}' çalıştırılamaz."
                )
            else:
                confirm_msg = (
                    f"GÜVEN DÜŞÜK: '{tool.name}' şu an düşük-güven bağlamında (kimlik doğrulanmadı). "
                    "Çalıştırmadan önce Kadir'den açık onay iste."
                )
            return {"result": confirm_msg}
        if decision == "dry_run":
            return {"result": f"DRY-RUN: '{tool.name}' şu argümanlarla çalışacaktı: {args}"}
        return None

    return policy_callback
