"""Risk-based identity trust fusion (Katman 2b Dilim 3a, spec §6).

Fuses whatever identity/context signals are available into a coarse TrustLevel
that the policy layer (policy.py) consumes to modulate the action zone matrix.
Extensible: new signals become new fields + fusion lines; the enum stays the
same. Missing signals degrade gracefully."""
from dataclasses import dataclass

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"


@dataclass
class TrustContext:
    auth_verified: bool
    presence: str = "foreground"       # foreground | locked | ambient | ...
    voice_score: float | None = None   # None = no audio evidence (e.g. text chat)
    device_hint: str = "unknown"       # phone | headset | tablet | unknown


def assess(ctx: TrustContext, accept_threshold: float) -> str:
    """foreground (unlocked) is always HIGH -- daily-life leniency, voice only
    annotates/feeds. locked/ambient is structurally capped at MEDIUM and drops
    to LOW when a present voice fails to match. See spec §6 table; the exact
    cells are tunable via config, not sacred."""
    if not ctx.auth_verified:
        return LOW
    if ctx.presence == "foreground":
        return HIGH
    if ctx.voice_score is None:
        return MEDIUM
    return MEDIUM if ctx.voice_score >= accept_threshold else LOW
