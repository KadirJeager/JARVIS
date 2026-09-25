"""Small HTTP handoff surface for the durable harness task ledger.

Run independently with ``uvicorn app.harness_control:app``. Credentials here
authenticate task delivery only; they are never model-provider credentials.
"""

import hmac
import os
from typing import Any, Callable, Literal, Mapping

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict

from . import harness_tasks as ledger
from . import hermes_model_api
from .chat_delivery import send_task_result


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewTask(_StrictBody):
    source: str
    event_id: str
    delivery_target: dict[str, Any]
    envelope: dict[str, Any]


class Outcome(_StrictBody):
    status: Literal["running", "waiting_user", "completed", "failed", "cancelled"]
    run_id: str | None = None
    session_id: str | None = None
    waiting_for: str | None = None
    result: dict[str, Any] | None = None


def _verify_chat_id_token(token: str, audience: str) -> Mapping[str, Any]:
    """Verify Google's signature, expiry, issuer and configured URL audience."""
    return id_token.verify_oauth2_token(token, GoogleAuthRequest(), audience)


def _chat_reply(text: str, *, workspace_addon: bool) -> dict[str, Any]:
    if workspace_addon:
        return {"hostAppDataAction": {"chatDataAction": {
            "createMessageAction": {"message": {"text": text}}
        }}}
    return {"text": text}


def create_app(*, db=None, owner: str | None = None,
               worker_token: str | None = None,
               ingest_token: str | None = None,
               chat_audience: str | None = None,
               chat_allowed_user: str | None = None,
               chat_manager_kind: str | None = None,
               chat_event_format: str | None = None,
               chat_service_account: str | None = None,
               chat_token_verifier: Callable[[str, str], Mapping[str, Any]] | None = None,
               chat_result_sender: Callable[[dict, str], dict] | None = None,
               ) -> FastAPI:
    """Build a standalone app. Tests inject a FakeDB and synthetic tokens."""
    owner = owner if owner is not None else os.getenv("JARVIS_OWNER_ID")
    worker_token = worker_token if worker_token is not None else os.getenv("JARVIS_WORKER_TOKEN")
    ingest_token = ingest_token if ingest_token is not None else os.getenv("JARVIS_INGEST_TOKEN")
    chat_audience = chat_audience if chat_audience is not None else os.getenv("JARVIS_CHAT_AUDIENCE")
    chat_allowed_user = (chat_allowed_user if chat_allowed_user is not None
                         else os.getenv("JARVIS_CHAT_ALLOWED_USER"))
    chat_manager_kind = (chat_manager_kind if chat_manager_kind is not None
                         else os.getenv("JARVIS_CHAT_MANAGER_KIND"))
    chat_event_format = (chat_event_format if chat_event_format is not None
                         else os.getenv("JARVIS_CHAT_EVENT_FORMAT", "classic"))
    chat_service_account = (chat_service_account if chat_service_account is not None
                            else os.getenv("JARVIS_CHAT_SERVICE_ACCOUNT")
                            or ("chat@system.gserviceaccount.com"
                                if chat_event_format == "classic" else None))
    chat_token_verifier = chat_token_verifier or _verify_chat_id_token
    chat_result_sender = chat_result_sender or send_task_result
    api = FastAPI(title="JARVIS harness control", docs_url=None, redoc_url=None,
                  openapi_url=None)
    # The existing CLIProxyAPI sidecar can supply Hermes's OpenAI-compatible
    # model turns without starting the legacy ADK agent or voice stack.
    # The provider route has its own bearer token and fails closed if unset.
    api.include_router(hermes_model_api.router)
    api.state.db = db

    def configuration() -> None:
        if (not owner or not owner.strip() or not worker_token or not ingest_token
                or hmac.compare_digest(worker_token, ingest_token)):
            raise HTTPException(status_code=503, detail="control plane is not configured")

    def require(role: Literal["worker", "ingest"]):
        def authenticate(authorization: str | None = Header(default=None)) -> None:
            configuration()
            expected = worker_token if role == "worker" else ingest_token
            scheme, _, supplied = (authorization or "").partition(" ")
            if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(
                supplied, expected
            ):
                raise HTTPException(status_code=401, detail="invalid bearer token",
                                    headers={"WWW-Authenticate": "Bearer"})
        return authenticate

    worker_auth = require("worker")
    ingest_auth = require("ingest")

    def database():
        if api.state.db is None:
            api.state.db = firestore.Client()
        return api.state.db

    def owned(key: str) -> dict:
        record = ledger.get(database(), key)
        if record is None or record.get("owner") != owner:
            raise HTTPException(status_code=404, detail="task not found")
        return record

    def ledger_error(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail="task not found") from exc
        if isinstance(exc, ledger.LedgerBusy):
            raise HTTPException(status_code=503, detail=str(exc),
                                headers={"Retry-After": "1"}) from exc
        if isinstance(exc, (ledger.TaskConflict, ledger.InvalidTransition)):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @api.post("/v1/chat/events")
    async def chat_event(request: Request):
        """Receive one authenticated Chat message and durably queue it once.

        Google Chat expects a response within 30 seconds, so execution and
        result delivery are deliberately outside this request path.
        """
        if (not owner or not owner.strip() or not chat_audience
                or not chat_audience.startswith("https://")
                or not chat_allowed_user or not chat_allowed_user.startswith("users/")
                or chat_event_format not in {"classic", "workspace_addon"}
                or not chat_service_account):
            raise HTTPException(status_code=503, detail="Chat ingress is not configured")
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not bearer:
            raise HTTPException(status_code=401, detail="invalid Chat identity",
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = chat_token_verifier(bearer, chat_audience)
        except Exception:
            raise HTTPException(status_code=401, detail="invalid Chat identity",
                                headers={"WWW-Authenticate": "Bearer"}) from None
        if (not isinstance(claims, Mapping)
                or claims.get("email") != chat_service_account
                or claims.get("email_verified") is not True):
            raise HTTPException(status_code=401, detail="invalid Chat identity",
                                headers={"WWW-Authenticate": "Bearer"})

        try:
            event = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Chat event JSON") from None
        if not isinstance(event, dict):
            raise HTTPException(status_code=422, detail="Chat event must be an object")
        workspace_addon = chat_event_format == "workspace_addon"
        if workspace_addon:
            chat = event.get("chat")
            if not isinstance(chat, dict):
                raise HTTPException(status_code=422, detail="Chat add-on event is required")
            payload = chat.get("messagePayload")
            if payload is None:
                return {}
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="invalid Chat message payload")
            user = chat.get("user")
            message = payload.get("message")
            space = payload.get("space")
            thread = message.get("thread") if isinstance(message, dict) else None
        else:
            if event.get("type") != "MESSAGE":
                return {}
            user = event.get("user")
            message = event.get("message")
            space = event.get("space")
            thread = event.get("thread") or (message.get("thread") if isinstance(message, dict) else None)
        if not isinstance(user, dict) or user.get("name") != chat_allowed_user:
            raise HTTPException(status_code=403, detail="Chat user is not allowed")
        if not isinstance(message, dict) or not isinstance(space, dict):
            raise HTTPException(status_code=422, detail="Chat message and space are required")
        message_name = message.get("name")
        space_name = space.get("name")
        if (not isinstance(space_name, str) or not space_name.startswith("spaces/")
                or not isinstance(message_name, str)
                or not message_name.startswith(space_name + "/messages/")):
            raise HTTPException(status_code=422, detail="invalid Chat message identity")
        text = message.get("argumentText") or message.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=422, detail="Chat message text is required")
        if text.strip().casefold() in {"durum", "status"}:
            counts = {}
            for status in (ledger.QUEUED, ledger.CLAIMED, ledger.RUNNING, ledger.WAITING_USER):
                query = database().collection(ledger.COLLECTION).where(
                    filter=FieldFilter("status", "==", status)
                )
                counts[status] = sum(1 for snap in query.stream()
                                     if snap.to_dict().get("owner") == owner)
            return _chat_reply((
                f"Görevler: {counts[ledger.QUEUED]} bekleyen, "
                f"{counts[ledger.CLAIMED] + counts[ledger.RUNNING]} yürüyen, "
                f"{counts[ledger.WAITING_USER]} yanıt bekleyen. "
                "PC güç durumu henüz bağlı değil."
            ), workspace_addon=workspace_addon)
        if not chat_manager_kind or not chat_manager_kind.strip():
            raise HTTPException(status_code=503, detail="Chat manager is not configured")
        thread = thread or {}
        if not isinstance(thread, dict):
            raise HTTPException(status_code=422, detail="invalid Chat thread")
        thread_name = thread.get("name")
        if thread_name is not None and (
            not isinstance(thread_name, str)
            or not thread_name.startswith(space_name + "/threads/")
        ):
            raise HTTPException(status_code=422, detail="invalid Chat thread identity")

        delivery_target = {
            "kind": chat_manager_kind,
            "device": "pc",
            "channel": "google_chat",
            "space": space_name,
        }
        space_type = space.get("spaceType")
        if isinstance(space_type, str) and space_type:
            delivery_target["space_type"] = space_type
        if thread_name:
            delivery_target["thread"] = thread_name
        envelope = {
            "goal": text.strip(),
            "authority": "Kadir's direct Google Chat request; existing project and tool policies apply.",
            "success_criteria": "Complete the request and report a verified result or a concrete blocker.",
            "context": {"chat_user": chat_allowed_user, "chat_message": message_name},
        }
        try:
            record, _ = ledger.create_once(
                database(), owner=owner, source="google_chat",
                event_id="MESSAGE:" + message_name,
                delivery_target=delivery_target, envelope=envelope,
            )
        except (ledger.TaskConflict, ValueError) as exc:
            ledger_error(exc)
        return _chat_reply(f"Alındı. Görev sıraya alındı: {record['task_id']}",
                           workspace_addon=workspace_addon)

    @api.post("/v1/tasks", dependencies=[Depends(ingest_auth)])
    def create_task(body: NewTask, response: Response):
        try:
            record, created = ledger.create_once(
                database(), owner=owner, source=body.source, event_id=body.event_id,
                delivery_target=body.delivery_target, envelope=body.envelope,
            )
        except (ledger.TaskConflict, ValueError) as exc:
            ledger_error(exc)
        response.status_code = 201 if created else 200
        return {"task": record, "created": created}

    @api.get("/v1/tasks/queued", dependencies=[Depends(worker_auth)])
    def queued(limit: int = Query(default=20, ge=1, le=100)):
        # One indexed status query works without a project-specific composite
        # index. Filter owner before returning any payload to the worker.
        query = database().collection(ledger.COLLECTION).where(
            filter=FieldFilter("status", "==", ledger.QUEUED)
        )
        tasks = []
        for snap in query.stream():
            record = snap.to_dict()
            if record.get("owner") == owner:
                tasks.append(record)
                if len(tasks) == limit:
                    break
        return {"tasks": tasks}

    @api.get("/v1/tasks/{task_id}", dependencies=[Depends(worker_auth)])
    def get_task(task_id: str):
        return {"task": owned(task_id)}

    @api.post("/v1/tasks/{task_id}/claim", dependencies=[Depends(worker_auth)])
    def claim(task_id: str):
        owned(task_id)
        try:
            record, claimed = ledger.claim_dispatch(database(), task_id)
        except (KeyError, ValueError) as exc:
            ledger_error(exc)
        return {"task": record, "claimed": claimed}

    @api.post("/v1/tasks/{task_id}/outcome", dependencies=[Depends(worker_auth)])
    def outcome(task_id: str, body: Outcome):
        current = owned(task_id)
        if body.status in ledger.TERMINAL:
            if body.result is None or body.waiting_for is not None:
                raise HTTPException(status_code=422, detail="terminal outcome needs result only")
        elif body.result is not None:
            raise HTTPException(status_code=422, detail="result is only for terminal outcome")
        if body.status == ledger.WAITING_USER:
            if not body.waiting_for or not body.waiting_for.strip():
                raise HTTPException(status_code=422, detail="waiting_user needs waiting_for")
        elif body.waiting_for is not None:
            raise HTTPException(status_code=422, detail="waiting_for is only for waiting_user")
        if body.run_id is not None and not body.run_id.strip():
            raise HTTPException(status_code=422, detail="run_id must be nonempty")
        if body.session_id is not None and not body.session_id.strip():
            raise HTTPException(status_code=422, detail="session_id must be nonempty")
        if (current["harness_run_id"] and body.run_id is not None
                and body.run_id != current["harness_run_id"]):
            raise HTTPException(status_code=409, detail="harness run ID does not match")
        if current["harness_session_id"] and body.session_id is not None and body.session_id != current["harness_session_id"]:
            raise HTTPException(status_code=409, detail="harness session ID does not match")

        try:
            if body.status == ledger.RUNNING:
                if body.run_id is None:
                    raise ValueError("running outcome needs run_id")
                if current["status"] == ledger.CLAIMED:
                    current = ledger.record_harness_run(
                        database(), task_id, run_id=body.run_id,
                        session_id=body.session_id)
                elif current["status"] == ledger.RUNNING:
                    if body.session_id is not None:
                        current = ledger.record_harness_session(database(), task_id,
                                                                body.session_id)
                    else:
                        current = ledger.transition(database(), task_id, ledger.RUNNING)
                elif current["status"] == ledger.WAITING_USER:
                    if body.session_id is not None:
                        current = ledger.record_harness_session(database(), task_id,
                                                                body.session_id)
                    current = ledger.transition(database(), task_id, ledger.RUNNING)
                else:
                    raise ledger.InvalidTransition("cannot resume this task")
            else:
                if body.session_id is not None and current["status"] not in ledger.TERMINAL:
                    current = ledger.record_harness_session(database(), task_id,
                                                            body.session_id)
                current = ledger.transition(database(), task_id, body.status,
                                            result=body.result,
                                            waiting_for=body.waiting_for)
        except (KeyError, ValueError) as exc:
            ledger_error(exc)
        if current["status"] in ledger.TERMINAL and current["delivery_target"].get("channel") == "google_chat":
            summary = current["result"].get("summary") if current["result"] else None
            if not isinstance(summary, str) or not summary.strip():
                summary = current["status"]
            label = {
                ledger.COMPLETED: "Tamamlandı",
                ledger.FAILED: "Başarısız",
                ledger.CANCELLED: "İptal edildi",
            }[current["status"]]
            reply = f"{label}: {summary.strip()}"
            if len(reply.encode("utf-8")) > 29_000:
                reply = reply.encode("utf-8")[:28_990].decode("utf-8", "ignore") + "…"
            try:
                chat_result_sender(current, reply)
            except Exception:
                # The durable outcome is already written. Return a retryable
                # error so the PC connector keeps its journaled result; Chat's
                # stable requestId prevents a duplicate if delivery succeeded.
                raise HTTPException(status_code=503, detail="Chat result delivery unavailable") from None
        return {"task": current}

    return api


app = create_app()
