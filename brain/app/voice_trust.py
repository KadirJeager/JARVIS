"""Out-of-band carrier for the voice identity signals (spec §7, option (b)).

WHY THIS EXISTS -- do not "simplify" it back into ADK session state. Verified
against the INSTALLED google-adk 1.36.2 source (not docs, not assumption):

  * ``InMemorySessionService.get_session()`` never returns the stored session,
    only a copy: ``_get_session_impl`` -> ``_copy_session``
    (in_memory_session_service.py:186-202, :54-58), whose light-copy branch
    rebinds ``copied_session.state = copy.copy(session.state)`` (:48-51) and
    whose other branch is a full ``copy.deepcopy``. Either way the returned
    session owns a NEW state dict.
  * ``Runner.run_live(user_id=..., session_id=...)`` then fetches its OWN
    session through ``_get_or_create_session`` -> ``session_service.get_session``
    (runners.py:1049-1054, :401) and builds the invocation context on that
    second copy (runners.py:1055-1059).
  * ``ToolContext`` IS ``Context`` (tools/tool_context.py:27), and
    ``Context.__init__`` binds ``State(value=invocation_context.session.state,
    ...)`` (agents/context.py:69-72) -- the runner's copy.

  => The voice bridge and the policy callback hold two independent state dicts.
     A trust level written into the bridge's ``session.state`` is invisible to
     ``policy_callback``; empirically the bridge held ``{'trust_level': 'LOW'}``
     while the runner held ``{}`` and the policy fell back to its HIGH default.

``Runner.run_live(session=...)`` would hand the runner our own object, but that
parameter is deprecated in 1.36.2 (runners.py:1016-1017 and the
``DeprecationWarning`` raised at :1042-1048) and slated for removal, so it is
not an option.

The signals therefore travel here instead, keyed by the ADK session identity
triple ``(app_name, user_id, session_id)``. Both ends can name that key from
PUBLIC api: the bridge knows all three, and the policy side reads them off
``tool_context.session`` -- a public ``ReadonlyContext`` property returning
``invocation_context.session`` (agents/readonly_context.py:59-62), which
``Context`` inherits (agents/context.py:42) -- whose ``id`` / ``app_name`` /
``user_id`` are public ``Session`` fields (sessions/session.py:39-45). So this
is a properly keyed holder, NOT a process-global "current trust" slot: several
concurrent sessions would each get their own entry.

Scope and lifetime: one process, one live WS connection's worth of signals.
That is sufficient and correct because the voice runner and the WS bridge are
always the same Cloud Run instance (voice.py takes the runner from main), and
the signals are only meaningful while that connection is open. The bridge
publishes at connection start and after every verified utterance, and clears on
teardown, so a dead connection can never leak trust into a later one.
"""
import logging
import threading
from dataclasses import dataclass

SessionKey = tuple[str, str, str]


@dataclass(frozen=True)
class VoiceSignals:
    """One voice connection's current identity evidence. `trust_level` is what
    the policy matrix consumes; the other three are the "why" the audit trail
    needs to reconstruct a decision (spec §7)."""

    trust_level: str
    voice_score: float | None = None
    presence: str = "foreground"
    device_hint: str = "unknown"


_signals: dict[SessionKey, VoiceSignals] = {}
# Guards _signals. Reads happen on the ADK event loop (policy callback) and
# writes on the voice bridge's loop; both are the same loop today, but the lock
# makes that an implementation detail rather than a correctness assumption --
# especially now that speaker inference runs in a worker thread.
_lock = threading.Lock()


def key_for(app_name: str, user_id: str, session_id: str) -> SessionKey:
    """The single place the key shape is defined, so the publishing side and
    the reading side can never drift apart."""
    return (app_name, user_id, session_id)


def publish(key: SessionKey, signals: VoiceSignals) -> None:
    with _lock:
        _signals[key] = signals


def clear(key: SessionKey) -> None:
    with _lock:
        _signals.pop(key, None)


def peek(key: SessionKey) -> VoiceSignals | None:
    """Direct lookup by key -- for the bridge's own teardown checks and tests."""
    with _lock:
        return _signals.get(key)


def lookup(tool_context) -> VoiceSignals | None:
    """Policy-side entry point: resolve the ADK tool context to this session's
    voice signals, or None when there are none (which is every text-chat call,
    and every voice call before the first utterance is verified).

    All ADK-shape knowledge lives here rather than in policy.py. Returns None on
    ANY unexpected context shape: the caller's absent-signals path defaults to
    HIGH, i.e. today's behaviour, so a shape change in a future ADK release
    degrades to "no identity modulation" instead of breaking tool calls."""
    try:
        session = getattr(tool_context, "session", None)
        if session is None:
            return None
        key = key_for(session.app_name, session.user_id, session.id)
    except Exception:
        logging.exception("voice_trust.lookup: unexpected tool_context shape")
        return None
    return peek(key)
