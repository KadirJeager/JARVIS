"""Deliver turns to the worker endpoint through Cloud Tasks.

The first dispatch of a turn uses the turn id as the task name, so a
resubmitted message cannot create a second task. Cloud Tasks retries a
delivery until the endpoint answers 2xx, which is how "not runnable yet" and
transient failures are retried with backoff. The repair sweep re-dispatches
turns whose task was never created or ran out of retries, naming each repair
task by turn, attempt and minute so concurrent sweeps collapse into one.
"""

from __future__ import annotations

from datetime import datetime

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from google.protobuf import duration_pb2

from jarvis_core.config import DeploymentConfig

# Cloud Tasks allows at most 30 minutes for an HTTP target to answer.
_DISPATCH_DEADLINE_SECONDS = 1800


class CloudTasksDispatcher:
    def __init__(self, config: DeploymentConfig, client: tasks_v2.CloudTasksAsyncClient | None = None) -> None:
        self._config = config
        # Created on first dispatch: building it resolves credentials, which slows cold starts.
        self._client = client

    def turn_url(self, turn_id: str) -> str:
        return f'{self._config.service_url}/internal/turns/{turn_id}/run'

    async def dispatch(self, turn_id: str, *, repair_of: tuple[int, datetime] | None = None) -> None:
        """Create the delivery task; `repair_of` is `(attempt, now)` for a repair re-dispatch."""
        name = turn_id
        if repair_of is not None:
            attempt, now = repair_of
            name = f'{turn_id}-a{attempt}-{now.strftime("%Y%m%d%H%M")}'
        task = tasks_v2.Task(
            name=f'{self._config.tasks_queue}/tasks/{name}',
            dispatch_deadline=duration_pb2.Duration(seconds=_DISPATCH_DEADLINE_SECONDS),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=self.turn_url(turn_id),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._config.invoker_service_account,
                    audience=self._config.service_url,
                ),
            ),
        )
        if self._client is None:
            self._client = tasks_v2.CloudTasksAsyncClient()
        try:
            await self._client.create_task(parent=self._config.tasks_queue, task=task)
        except AlreadyExists:
            return
