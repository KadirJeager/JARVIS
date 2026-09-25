"""Google Chat sender contract without outbound Google calls."""

import pytest

from app.chat_delivery import send_task_result


class FakeResponse:
    def __init__(self, status_code=200, result=None):
        self.status_code = status_code
        self.result = result or {"name": "spaces/room/messages/reply-1"}

    def json(self):
        return self.result


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.requests = []

    def post(self, url, *, params, json, timeout):
        self.requests.append((url, params, json, timeout))
        return self.response


def _record(space_type="SPACE", thread="spaces/room/threads/t1"):
    return {
        "task_id": "h1_stable-task",
        "delivery_target": {
            "channel": "google_chat", "space": "spaces/room",
            "space_type": space_type, "thread": thread,
        },
    }


def test_result_retries_use_the_same_chat_request_id_and_thread():
    session = FakeSession()
    record = _record()
    first = send_task_result(record, "Completed with evidence", session=session)
    second = send_task_result(record, "Completed with evidence", session=session)
    assert first == second == {"name": "spaces/room/messages/reply-1"}
    assert session.requests[0] == session.requests[1]
    url, params, body, timeout = session.requests[0]
    assert url == "https://chat.googleapis.com/v1/spaces/room/messages"
    assert body == {"text": "Completed with evidence",
                    "thread": {"name": "spaces/room/threads/t1"}}
    assert params["messageReplyOption"] == "REPLY_MESSAGE_OR_FAIL"
    assert params["requestId"]
    assert timeout == 15


def test_direct_message_omits_thread_reply_option():
    session = FakeSession()
    record = _record(space_type="DIRECT_MESSAGE")
    send_task_result(record, "Ready", session=session)
    _, params, body, _ = session.requests[0]
    assert body == {"text": "Ready"}
    assert "messageReplyOption" not in params


def test_invalid_route_or_response_never_claims_success():
    session = FakeSession()
    for record in (
        {"task_id": "x", "delivery_target": {"channel": "other", "space": "spaces/room"}},
        {"task_id": "x", "delivery_target": {"channel": "google_chat", "space": "spaces/room/other"}},
        _record(thread="spaces/another/threads/t1"),
    ):
        with pytest.raises(ValueError):
            send_task_result(record, "Result", session=session)
    assert session.requests == []

    with pytest.raises(RuntimeError, match="HTTP 503"):
        send_task_result(_record(), "Result", session=FakeSession(FakeResponse(503)))
    with pytest.raises(RuntimeError, match="invalid message identity"):
        send_task_result(_record(), "Result", session=FakeSession(FakeResponse(200, {"name": None})))
