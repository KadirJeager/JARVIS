"""C1 integration proof: does the trust level the voice bridge computes actually
reach the policy layer?

Every other speaker test fakes the session service with an object that returns
the SAME session from get_session() and create_session(), and fakes a runner
that ignores the session entirely -- so none of them can observe the real ADK
behaviour. This test uses the REAL InMemorySessionService and the REAL
policy_callback (the one app.agent.build_agent installs on the production
agent), and reproduces exactly what Runner.run_live does with the session,
verified against installed google-adk 1.36.2:

  * Runner.run_live(user_id=..., session_id=...) fetches its OWN session via
    _get_or_create_session -> session_service.get_session (runners.py:1049-1054
    and runners.py:401), and builds the invocation context on that object
    (runners.py:1055-1059).
  * InMemorySessionService.get_session returns a COPY, never the stored object
    (in_memory_session_service.py:186-202 -> _copy_session at :54-58, whose
    light-copy branch does `copied_session.state = copy.copy(session.state)`
    at :48-51).
  * ToolContext is Context (tools/tool_context.py:27) and Context.__init__
    binds State(value=invocation_context.session.state, ...)
    (agents/context.py:69-72) -- i.e. the RUNNER's copy.

So the fake runner below does the one thing that matters: it re-fetches its own
session copy from the real session service before building the ToolContext,
instead of reusing the bridge's object.

Scenario (spec section 7): presence=locked + a NON-matching voice must make the
YELLOW-zone tool (update_user_profile) ask for confirmation instead of running.
"""
import asyncio
import json

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext

from app import trust, voice_trust
from app.agent import build_agent
from app.speaker import SpeakerService
from app.speaker_store import enroll_anchors
from app.voice import VoiceBridge
from tests.fakes import FakeDB

APP_NAME = "jarvis"
USER = "kadir@example.com"
SESSION_ID = f"voice-{USER}"

KADIR_VEC = [1.0, 0.0, 0.0]
IMPOSTOR_VEC = [0.0, 1.0, 0.0]      # cosine 0.0 to the enrolled anchor


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


class _Tool:
    def __init__(self, name):
        self.name = name


class _WS:
    """Duck-type of the fastapi WebSocket surface VoiceBridge.run uses. receive()
    yields to the event loop first, the way a real ASGI socket read always does,
    so the concurrently running _pump_events task gets scheduled.

    Once the scripted messages run out it waits on `gate` and then reports a
    disconnect. That models a real client (which keeps the socket open while the
    turn is being served) and, crucially, makes the test deterministic: without
    it, run()'s teardown can cancel _pump_events while it is still awaiting the
    off-thread speaker inference, so the turn's tool call never happens."""

    def __init__(self, incoming, gate):
        self.incoming = list(incoming)
        self.gate = gate
        self.sent = []

    async def receive(self):
        await asyncio.sleep(0)
        if not self.incoming:
            await self.gate.wait()          # bounded by the caller's wait_for
            return {"type": "websocket.disconnect"}
        return self.incoming.pop(0)

    async def send_text(self, t):
        self.sent.append(("text", t))

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))


def _transcription_event(text, finished):
    class T:
        pass

    t = T()
    t.text = text
    t.finished = finished

    class E:
        pass

    e = E()
    e.content = None
    e.turn_complete = False
    e.input_transcription = t
    e.output_transcription = None
    return e


class _RunnerThatCallsATool:
    """Stands in for ADK's Runner ONLY in the two respects that matter here:
    it yields the user's finished input transcription, then obtains its own
    session copy from the session service the way Runner._get_or_create_session
    does (runners.py:401) and invokes the policy callback with a ToolContext
    built on it (runners.py:1055-1059 + agents/context.py:69-72)."""

    def __init__(self, sessions, agent, policy_cb, bridge_box, decisions, gate):
        self.sessions = sessions
        self.agent = agent
        self.policy_cb = policy_cb
        self.bridge_box = bridge_box
        self.decisions = decisions
        self.gate = gate

    def run_live(self, *, user_id, session_id, live_request_queue, run_config=None):
        async def events():
            bridge = self.bridge_box["bridge"]
            # Wait for the concurrently running mic loop to have buffered this
            # turn's audio. Bounded by a fixed number of event-loop turns (not
            # wall clock) so this is deterministic; if it never fills, the
            # caller's asyncio.wait_for turns it into a clean failure.
            for _ in range(200):
                if bridge._utterance:
                    break
                await asyncio.sleep(0)
            yield _transcription_event("profilimi guncelle", finished=True)
            # Resuming here means _pump_events already finished awaiting
            # _verify_utterance for the event above -- the trust level for this
            # turn is published by now.
            session = await self.sessions.get_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
            tool_context = ToolContext(
                InvocationContext(
                    session_service=self.sessions,
                    invocation_id="c1-invocation",
                    agent=self.agent,
                    session=session,
                )
            )
            self.decisions.append(
                self.policy_cb(_Tool("update_user_profile"), {"patch": {}}, tool_context)
            )
            self.gate.set()             # the turn is served; the client may go away

        return events()


def _speaker_service(db, vec):
    return SpeakerService(
        db, embed_fn=lambda pcm: vec, now_fn=lambda: "t",
        accept=0.35, adapt=0.6, cap=20, top_k=3,
    )


def _voice_agent(audit):
    """The production voice agent: build_agent with the trust provider, exactly
    as main._init_voice wires it. The text runner passes no provider, which is
    what keeps /api/chat unaffected."""
    return build_agent(
        memory=object(), audit=audit, model="fake-live-model",
        trust_provider=voice_trust.lookup,
    )


async def _drive(sessions, agent, bridge, decisions, timeout=5):
    gate = asyncio.Event()
    bridge.runner = _RunnerThatCallsATool(
        sessions, agent, agent.before_tool_callback, {"bridge": bridge}, decisions, gate
    )
    ws = _WS([{"type": "websocket.receive", "bytes": b"\x00\x01" * 8}], gate)
    await asyncio.wait_for(bridge.run(ws, user_id=USER), timeout=timeout)
    return ws


@pytest.mark.asyncio
async def test_locked_nonmatching_voice_makes_yellow_tool_confirm():
    """THE C1 regression guard. Pre-fix this failed with
    `policy saw trust=HIGH, but the bridge computed LOW` -- the bridge wrote its
    own session copy and the runner read another."""
    db = FakeDB()
    enroll_anchors(db, USER, [KADIR_VEC])
    sessions = InMemorySessionService()
    audit = FakeAudit()
    agent = _voice_agent(audit)
    decisions = []
    bridge = VoiceBridge(
        runner=None, session_service=sessions,
        speaker_service=_speaker_service(db, IMPOSTOR_VEC),
        device_hint="phone", presence="locked",
    )
    ws = await _drive(sessions, agent, bridge, decisions)

    assert decisions, "the fake runner never reached the policy callback"
    assert ("text", json.dumps(
        {"type": "speaker", "role": "user", "verified": False, "score": 0.0})) in ws.sent
    entry = audit.entries[-1]
    assert entry["trust"] == trust.LOW, (
        f"policy saw trust={entry['trust']}, but the bridge computed LOW for this "
        "locked + non-matching-voice utterance"
    )
    assert entry["decision"] == "confirm"
    assert decisions[0] is not None and "onay" in decisions[0]["result"].lower()


@pytest.mark.asyncio
async def test_locked_matching_voice_still_confirms_but_foreground_does_not():
    """The other two cells of the same chain, so the test above cannot be
    satisfied by something that simply always returns LOW: a MATCHING voice
    while locked is MEDIUM (still confirm, spec §7), and an unlocked
    (foreground) connection is HIGH -- daily use is not restricted at all."""
    db = FakeDB()
    enroll_anchors(db, USER, [KADIR_VEC])

    for presence, expected_trust, expect_blocked in (
        ("locked", trust.MEDIUM, True),
        ("foreground", trust.HIGH, False),
    ):
        sessions = InMemorySessionService()
        audit = FakeAudit()
        agent = _voice_agent(audit)
        decisions = []
        bridge = VoiceBridge(
            runner=None, session_service=sessions,
            speaker_service=_speaker_service(db, KADIR_VEC),
            device_hint="phone", presence=presence,
        )
        await _drive(sessions, agent, bridge, decisions)
        entry = audit.entries[-1]
        assert entry["trust"] == expected_trust, f"{presence}: {entry}"
        assert (decisions[0] is not None) is expect_blocked, f"{presence}: {decisions}"


@pytest.mark.asyncio
async def test_signals_are_cleared_on_teardown():
    """A dead connection must not leave trust behind for a later tool call on
    the same (per-user, process-lifetime) ADK session id."""
    db = FakeDB()
    enroll_anchors(db, USER, [KADIR_VEC])
    sessions = InMemorySessionService()
    agent = _voice_agent(FakeAudit())
    bridge = VoiceBridge(
        runner=None, session_service=sessions,
        speaker_service=_speaker_service(db, IMPOSTOR_VEC),
        device_hint="phone", presence="locked",
    )
    await _drive(sessions, agent, bridge, [])
    assert voice_trust.peek(voice_trust.key_for(APP_NAME, USER, SESSION_ID)) is None


@pytest.mark.asyncio
async def test_text_runner_agent_never_sees_voice_signals():
    """Structural guarantee for the text path: main._init builds the text agent
    WITHOUT a trust provider, so even with live voice signals published for the
    very same session key, /api/chat's policy callback still resolves HIGH."""
    key = voice_trust.key_for(APP_NAME, USER, SESSION_ID)
    voice_trust.publish(key, voice_trust.VoiceSignals(
        trust_level=trust.LOW, voice_score=0.0, presence="locked", device_hint="phone"))
    try:
        sessions = InMemorySessionService()
        session = await sessions.create_session(
            app_name=APP_NAME, user_id=USER, session_id=SESSION_ID)
        audit = FakeAudit()
        text_agent = build_agent(memory=object(), audit=audit)   # no trust_provider
        tool_context = ToolContext(InvocationContext(
            session_service=sessions, invocation_id="text-invocation",
            agent=text_agent, session=session,
        ))
        result = text_agent.before_tool_callback(
            _Tool("update_user_profile"), {"patch": {}}, tool_context)
        assert result is None                        # allowed, exactly as before
        assert audit.entries[-1]["trust"] == trust.HIGH
        assert audit.entries[-1]["voice_score"] is None
    finally:
        voice_trust.clear(key)


# --- voice_trust.lookup: the ADK-shape resolution it owns -------------------


def test_lookup_resolves_a_real_adk_tool_context():
    """Pins the PUBLIC surface this design depends on: ToolContext.session comes
    from ReadonlyContext (agents/readonly_context.py:59-62, inherited by Context
    at agents/context.py:42) and exposes app_name/user_id/id
    (sessions/session.py:39-45). If a future ADK release moves any of these,
    this test is what catches it."""
    from google.adk.sessions.session import Session

    key = voice_trust.key_for(APP_NAME, USER, SESSION_ID)
    signals = voice_trust.VoiceSignals(trust_level=trust.LOW, voice_score=0.1,
                                       presence="locked", device_hint="phone")
    voice_trust.publish(key, signals)
    sessions = InMemorySessionService()
    agent = build_agent(memory=object(), audit=FakeAudit(), model="m")
    tool_context = ToolContext(InvocationContext(
        session_service=sessions, invocation_id="i", agent=agent,
        session=Session(id=SESSION_ID, app_name=APP_NAME, user_id=USER),
    ))
    assert voice_trust.lookup(tool_context) is signals


def test_lookup_returns_none_for_a_different_session():
    """Keyed, not global: another session's tool call must not pick up this
    connection's trust."""
    voice_trust.publish(
        voice_trust.key_for(APP_NAME, USER, SESSION_ID),
        voice_trust.VoiceSignals(trust_level=trust.LOW),
    )
    from google.adk.sessions.session import Session

    agent = build_agent(memory=object(), audit=FakeAudit(), model="m")
    tool_context = ToolContext(InvocationContext(
        session_service=InMemorySessionService(), invocation_id="i", agent=agent,
        session=Session(id="chat-1", app_name=APP_NAME, user_id=USER),
    ))
    assert voice_trust.lookup(tool_context) is None


@pytest.mark.parametrize("ctx", [
    None,
    "not a context",
    type("NoSession", (), {"state": {}})(),
])
def test_lookup_degrades_to_none_on_unexpected_context_shapes(ctx):
    """Anything it cannot resolve means "no voice signals", which the policy
    layer turns into its HIGH default -- a future ADK shape change degrades to
    "no identity modulation", never to broken tool calls."""
    assert voice_trust.lookup(ctx) is None


def test_lookup_logs_when_a_session_object_is_malformed(caplog):
    """A session-like object missing the id fields is a real shape change, not
    an ordinary absence: swallow it, but loudly."""
    ctx = type("Ctx", (), {"session": object()})()
    with caplog.at_level("ERROR"):
        assert voice_trust.lookup(ctx) is None
    assert "unexpected tool_context shape" in caplog.text
