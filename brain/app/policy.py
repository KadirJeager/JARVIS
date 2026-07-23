"""Action authorization matrix (North Star §9): every tool call passes here."""
from datetime import datetime, timezone
from typing import Any, Protocol

from . import config


class AuditWriter(Protocol):
    def write(self, entry: dict) -> None: ...


def check_zone(tool_name: str) -> str:
    return config.TOOL_ZONES.get(tool_name, config.DEFAULT_ZONE)


def make_policy_callback(audit: AuditWriter):
    def policy_callback(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
        zone = check_zone(tool.name)
        if zone == config.ZONE_RED:
            decision = "block"
        elif config.DRY_RUN:
            decision = "dry_run"
        else:
            decision = "allow"
        audit.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "orchestrator",
            "tool": tool.name,
            "args": {k: str(v)[:500] for k, v in (args or {}).items()},
            "zone": zone,
            "decision": decision,
        })
        if decision == "block":
            return {"result": (
                f"POLİTİKA ENGELİ: '{tool.name}' kırmızı bölgede — onaysız çalıştırılamaz. "
                "Kadir'e ne yapmak istediğini söyle ve onay iste."
            )}
        if decision == "dry_run":
            return {"result": f"DRY-RUN: '{tool.name}' şu argümanlarla çalışacaktı: {args}"}
        return None

    return policy_callback
