"""Run one claimed turn to completion, continuing safely after a crash.

The dispatcher (Cloud Tasks in the cloud) calls `TurnWorker.run(turn_id)` and
retries later when the turn is not runnable yet. A new attempt never repeats
work blindly; it decides from the previous attempt's recorded facts:

- unresolved tool effects, or tool calls that finished after the last settled
  snapshot (a completed effect is not proof its result reached a
  checkpoint) -> the turn waits for reconciliation;
- the previous run already produced its final reply -> record that reply
  without calling the model again;
- a settled snapshot exists -> continue the run from it;
- otherwise -> start from the conversation head as a first attempt would.

Transient model failures return the turn to the queue for another attempt;
other failures settle it as failed. Error records keep the exception type,
HTTP status and the provider's machine-readable reason codes (for example
`RESOURCE_EXHAUSTED` or `VALIDATION_REQUIRED`), never response messages or
links, which can carry account-specific tokens.

While the model runs, each tool call and its outcome is written to the turn's
`activity` so the owner sees what the assistant is doing. The worker also
watches for the owner's cancel request: it stops the run and settles the turn
as cancelled, or as awaiting reconciliation when a tool call was cut off
before its outcome was recorded. When a turn settles with a reply or an error
the optional notifier tells the owner's devices.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, TypeVar

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.step_persistence.recovery import inspect_recovery

from jarvis_core.assistant import build_agent, turn_limits
from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.secrets import SecretResolutionError, SecretResolver
from jarvis_core.settings import FirestoreSettingsStore
from jarvis_core.turns import Claim, FirestoreTurnStore

Outcome = Literal['not_runnable', 'completed', 'failed', 'requeued', 'reconciling', 'cancelled']

_logger = logging.getLogger(__name__)
_T = TypeVar('_T')

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_REASON_CODE = re.compile(r'[A-Z][A-Z_]{2,63}')
# Tool arguments shown to the owner are shortened to this many characters.
_ARGS_PREVIEW_CHARS = 300
_USAGE_FIELDS = ('requests', 'tool_calls', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')


class TurnNotifier(Protocol):
    """Tells the owner's devices that a turn settled; failures must not raise."""

    async def turn_settled(self, turn: dict[str, Any], *, kind: Literal['reply', 'error'], text: str) -> None: ...


class _Cancelled(Exception):
    """The owner asked to cancel the running turn."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _preview(part: ToolCallPart) -> str:
    try:
        text = json.dumps(part.args_as_dict(), ensure_ascii=False, default=str)
    except ValueError:
        text = part.args_as_json_str()
    return text if len(text) <= _ARGS_PREVIEW_CHARS else text[: _ARGS_PREVIEW_CHARS - 1] + '…'


def usage_record(usage: RunUsage) -> dict[str, int]:
    """Counts the provider reported; zero means not reported, so zeros are left out."""
    return {name: value for name in _USAGE_FIELDS if (value := int(getattr(usage, name, 0) or 0))}


@dataclass
class _ActivityLog:
    """Mirror of a running attempt's tool calls in the turn record."""

    turns: FirestoreTurnStore
    turn_id: str
    run_id: str
    items: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    async def _save(self) -> None:
        try:
            await self.turns.record_activity(self.turn_id, self.run_id, self.items)
        except Exception:  # noqa: BLE001 - display state only; the run itself must not fail on it
            _logger.warning('could not record activity for turn %s', self.turn_id, exc_info=True)

    async def handle(self, ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                self.items.append({
                    'id': event.tool_call_id,
                    'tool': event.part.tool_name,
                    'args': _preview(event.part),
                    'state': 'running',
                    'started_at': _now(),
                    'attempt_run_id': self.run_id,
                })
                await self._save()
            elif isinstance(event, FunctionToolResultEvent):
                for item in self.items:
                    if item['id'] == event.tool_call_id and item['state'] == 'running':
                        item['state'] = 'retry' if isinstance(event.part, RetryPromptPart) else 'done'
                        item['ended_at'] = _now()
                await self._save()


def provider_reasons(body: object) -> list[str]:
    """Enum-like reason codes from a Google-style error body (`error.status`, `ErrorInfo.reason`).

    Only uppercase code tokens are returned, so messages and URLs in the body
    never reach the turn record.
    """
    if isinstance(body, (str, bytes)):
        try:
            body = json.loads(body)
        except ValueError:
            return []
    if isinstance(body, list) and body:
        body = body[0]
    error = body.get('error') if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return []
    details = error.get('details')
    candidates = [error.get('status')]
    candidates += [d.get('reason') for d in details if isinstance(d, dict)] if isinstance(details, list) else []
    return list(dict.fromkeys(c for c in candidates if isinstance(c, str) and _REASON_CODE.fullmatch(c)))


def final_reply(messages: list[ModelMessage]) -> str | None:
    """The reply text if `messages` end with a model response that requested no tools."""
    if not messages or not isinstance(messages[-1], ModelResponse):
        return None
    parts = messages[-1].parts
    if any(isinstance(part, ToolCallPart) for part in parts):
        return None
    return ''.join(part.content for part in parts if isinstance(part, TextPart))


@dataclass(frozen=True)
class _Plan:
    prompt: str | None
    history: list[ModelMessage]


@dataclass
class TurnWorker:
    turns: FirestoreTurnStore
    settings: FirestoreSettingsStore
    steps: FirestoreStepStore
    memory: FirestoreMemoryStore
    secrets: SecretResolver
    worker_id: str
    lease: timedelta = timedelta(minutes=15)
    max_attempts: int = 5
    notifier: TurnNotifier | None = None
    cancel_poll_seconds: float = 2.0

    async def _notify(self, turn: dict[str, Any], kind: Literal['reply', 'error'], text: str) -> None:
        if self.notifier is not None:
            await self.notifier.turn_settled(turn, kind=kind, text=text)

    async def _fail(self, turn: dict[str, Any], run_id: str, error_type: str, detail: str) -> Outcome:
        settled = await self.turns.fail(turn['turn_id'], run_id, error_type=error_type, detail=detail)
        await self._notify(settled, 'error', f'{error_type}: {detail}')
        return 'failed'

    async def _until_cancelled(self, turn_id: str, work: Awaitable[_T]) -> _T:
        """Await `work`, stopping it when the owner requests cancellation."""
        task = asyncio.ensure_future(work)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.cancel_poll_seconds)
                if task in done:
                    return task.result()
                if await self.turns.cancel_requested(turn_id):
                    task.cancel()
                    await asyncio.wait({task})
                    raise _Cancelled
        finally:
            if not task.done():
                task.cancel()

    async def _settle_cancelled(self, claim: Claim, run_id: str | None) -> Outcome:
        """Cancel the claimed attempt, unless a tool call of `run_id` was cut off before its outcome."""
        if run_id is not None:
            facts = await inspect_recovery(store=self.steps, run_id=run_id)
            if facts.unresolved:
                unresolved = [f'{effect.tool_name}:{effect.tool_call_id}' for effect in facts.unresolved]
                await self.turns.require_reconciliation(claim.turn['turn_id'], claim.run_id, unresolved=unresolved)
                return 'reconciling'
        await self.turns.cancel(claim.turn['turn_id'], claim.run_id)
        return 'cancelled'

    async def _head_history(self, head_run_id: str | None) -> list[ModelMessage]:
        if head_run_id is None:
            return []
        snapshot = await self.steps.latest_snapshot(run_id=head_run_id)
        if snapshot is None:
            raise LookupError(f'conversation head run {head_run_id!r} has no settled snapshot')
        return snapshot.messages

    async def _uncertain_tool_calls(self, run_id: str) -> tuple[list[str], list[ModelMessage] | None]:
        """Tool calls whose effect may not be reflected in the run's settled snapshot."""
        facts = await inspect_recovery(store=self.steps, run_id=run_id)
        settled = facts.settled.messages if facts.settled is not None else None
        covered = {
            part.tool_call_id
            for message in settled or []
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, (ToolReturnPart, RetryPromptPart))
        }
        uncertain = [f'{effect.tool_name}:{effect.tool_call_id}' for effect in facts.unresolved]
        uncertain += [
            f'{event.tool_name}:{event.tool_call_id}'
            for event in await self.steps.list_events(run_id=run_id)
            if event.kind in ('tool_call_completed', 'tool_call_failed') and event.tool_call_id not in covered
        ]
        return list(dict.fromkeys(uncertain)), settled

    async def run(self, turn_id: str) -> Outcome:
        claim = await self.turns.claim(turn_id, worker_id=self.worker_id, lease=self.lease)
        if claim is None:
            return 'not_runnable'
        turn = claim.turn
        if turn.get('cancel_requested_at') is not None:
            return await self._settle_cancelled(claim, claim.previous_run_id)
        if claim.attempt > self.max_attempts:
            return await self._fail(turn, claim.run_id, 'AttemptsExhausted', f'{self.max_attempts} attempts')
        plan = _Plan(prompt=turn['text'], history=[])
        if claim.previous_run_id is not None:
            uncertain, settled = await self._uncertain_tool_calls(claim.previous_run_id)
            if uncertain:
                settled_turn = await self.turns.require_reconciliation(turn_id, claim.run_id, unresolved=uncertain)
                await self._notify(settled_turn, 'error', 'unresolved_tool_effects')
                return 'reconciling'
            if settled is not None:
                reply = final_reply(settled)
                if reply is not None:
                    done = await self.turns.complete(
                        turn_id, claim.run_id, reply=reply, history_run_id=claim.previous_run_id
                    )
                    await self._notify(done, 'reply', reply)
                    return 'completed'
                plan = _Plan(prompt=None, history=settled)
        if plan.prompt is not None:
            try:
                plan = _Plan(prompt=turn['text'], history=await self._head_history(claim.head_run_id))
            except LookupError as exc:
                return await self._fail(turn, claim.run_id, 'HistoryMissing', str(exc))
        return await self._execute(claim, plan)

    async def _execute(self, claim: Claim, plan: _Plan) -> Outcome:
        turn = claim.turn
        stored = await self.settings.get(turn['uid'])
        if stored is None:
            return await self._fail(turn, claim.run_id, 'SettingsMissing', 'no settings saved')
        try:
            agent = await build_agent(
                stored.settings,
                uid=turn['uid'],
                run_id=claim.run_id,
                memory_store=self.memory,
                step_store=self.steps,
                secrets=self.secrets,
            )
        except SecretResolutionError as exc:
            return await self._fail(turn, claim.run_id, 'SecretResolutionError', str(exc))
        activity = _ActivityLog(self.turns, turn['turn_id'], claim.run_id, list(turn.get('activity') or []))
        try:
            result = await self._until_cancelled(
                turn['turn_id'],
                agent.run(
                    plan.prompt,
                    message_history=plan.history,
                    conversation_id=turn['conversation_id'],
                    usage_limits=turn_limits(stored.settings),
                    event_stream_handler=activity.handle,
                ),
            )
        except _Cancelled:
            return await self._settle_cancelled(claim, claim.run_id)
        except (UsageLimitExceeded, UnexpectedModelBehavior) as exc:
            # Messages only; `UnexpectedModelBehavior.body` may hold the provider response.
            return await self._fail(turn, claim.run_id, type(exc).__name__, exc.message)
        except ModelAPIError as exc:
            status = exc.status_code if isinstance(exc, ModelHTTPError) else None
            reasons = provider_reasons(exc.body) if isinstance(exc, ModelHTTPError) else []
            detail = ' '.join([f'HTTP {status}' if status is not None else 'no HTTP response', *reasons])
            transient = status is None or status in _RETRYABLE_STATUS
            if transient and claim.attempt < self.max_attempts:
                await self.turns.release(turn['turn_id'], claim.run_id, error_type=type(exc).__name__, detail=detail)
                return 'requeued'
            return await self._fail(turn, claim.run_id, type(exc).__name__, detail)
        done = await self.turns.complete(
            turn['turn_id'], claim.run_id, reply=result.output, usage=usage_record(result.usage)
        )
        await self._notify(done, 'reply', result.output)
        return 'completed'
