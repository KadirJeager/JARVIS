"""Action authorization matrix (North Star §9): every tool call passes here."""
from datetime import datetime, timezone
from typing import Any, Protocol

from . import config, trust


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


def check_zone(tool_name: str) -> str:
    return config.TOOL_ZONES.get(tool_name, config.DEFAULT_ZONE)


def _read_trust(tool_context) -> str:
    """Read the trust level the voice bridge wrote into session.state; default
    HIGH when absent (text path, or first utterance) so nothing is restricted
    unless a low-trust voice context explicitly lowered it. tool_context.state
    is ADK's State (backed by session.state); tolerate None/missing."""
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


def make_policy_callback(audit: AuditWriter):
    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name)
        trust_level = _read_trust(tool_context)
        decision = _decide(zone, trust_level)
        audit.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "orchestrator",
            "tool": tool.name,
            "args": {k: str(v)[:500] for k, v in (args or {}).items()},
            "zone": zone,
            "trust": trust_level,
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
