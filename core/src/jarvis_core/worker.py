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
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai_harness.step_persistence.recovery import inspect_recovery

from jarvis_core.assistant import build_agent, turn_limits
from jarvis_core.firestore_stores import FirestoreMemoryStore, FirestoreStepStore
from jarvis_core.secrets import SecretResolutionError, SecretResolver
from jarvis_core.settings import FirestoreSettingsStore
from jarvis_core.turns import Claim, FirestoreTurnStore

Outcome = Literal['not_runnable', 'completed', 'failed', 'requeued', 'reconciling']

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_REASON_CODE = re.compile(r'[A-Z][A-Z_]{2,63}')


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
        if claim.attempt > self.max_attempts:
            await self.turns.fail(
                turn_id, claim.run_id, error_type='AttemptsExhausted', detail=f'{self.max_attempts} attempts'
            )
            return 'failed'
        plan = _Plan(prompt=turn['text'], history=[])
        if claim.previous_run_id is not None:
            uncertain, settled = await self._uncertain_tool_calls(claim.previous_run_id)
            if uncertain:
                await self.turns.require_reconciliation(turn_id, claim.run_id, unresolved=uncertain)
                return 'reconciling'
            if settled is not None:
                reply = final_reply(settled)
                if reply is not None:
                    await self.turns.complete(turn_id, claim.run_id, reply=reply, history_run_id=claim.previous_run_id)
                    return 'completed'
                plan = _Plan(prompt=None, history=settled)
        if plan.prompt is not None:
            try:
                plan = _Plan(prompt=turn['text'], history=await self._head_history(claim.head_run_id))
            except LookupError as exc:
                await self.turns.fail(turn_id, claim.run_id, error_type='HistoryMissing', detail=str(exc))
                return 'failed'
        return await self._execute(claim, plan)

    async def _execute(self, claim: Claim, plan: _Plan) -> Outcome:
        turn = claim.turn
        stored = await self.settings.get(turn['uid'])
        if stored is None:
            await self.turns.fail(turn['turn_id'], claim.run_id, error_type='SettingsMissing', detail='no settings saved')
            return 'failed'
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
            await self.turns.fail(turn['turn_id'], claim.run_id, error_type='SecretResolutionError', detail=str(exc))
            return 'failed'
        try:
            result = await agent.run(
                plan.prompt,
                message_history=plan.history,
                conversation_id=turn['conversation_id'],
                usage_limits=turn_limits(stored.settings),
            )
        except (UsageLimitExceeded, UnexpectedModelBehavior) as exc:
            # Messages only; `UnexpectedModelBehavior.body` may hold the provider response.
            await self.turns.fail(turn['turn_id'], claim.run_id, error_type=type(exc).__name__, detail=exc.message)
            return 'failed'
        except ModelAPIError as exc:
            status = exc.status_code if isinstance(exc, ModelHTTPError) else None
            reasons = provider_reasons(exc.body) if isinstance(exc, ModelHTTPError) else []
            detail = ' '.join([f'HTTP {status}' if status is not None else 'no HTTP response', *reasons])
            transient = status is None or status in _RETRYABLE_STATUS
            if transient and claim.attempt < self.max_attempts:
                await self.turns.release(turn['turn_id'], claim.run_id, error_type=type(exc).__name__, detail=detail)
                return 'requeued'
            await self.turns.fail(turn['turn_id'], claim.run_id, error_type=type(exc).__name__, detail=detail)
            return 'failed'
        await self.turns.complete(turn['turn_id'], claim.run_id, reply=result.output)
        return 'completed'
