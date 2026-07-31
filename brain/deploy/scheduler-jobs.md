# Cloud Scheduler Job Configurations

This document outlines the Cloud Scheduler job configurations for periodic tasks in the JARVIS backend.

## Weekly Retrospective Job (`weekly_retro`)

The weekly retrospective job triggers Phase Y1.4 (North Star §8.4) once a week. The job posts a `weekly_retro` event to `/api/jobs/event`.

```bash
gcloud scheduler jobs create http weekly-retro \
  --schedule="0 9 * * 0" \
  --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/event" \
  --http-method=POST \
  --message-body='{"source": "scheduler", "kind": "weekly_retro", "payload": {}}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```

### Parameters
- `--schedule="0 9 * * 0"`: Runs every Sunday at 09:00 AM Europe/Istanbul time.
- `--uri`: Points to the `/api/jobs/event` endpoint on the `jarvis-brain` service.
- `payload`: Contains event parameters (optionally `owner` email override).

## Workspace Poll Job (`workspace-poll`)

Polls Gmail and Calendar via the stored workspace refresh token (Faz Y2.2/Y2.3). Baseline rule applies on first run: current state is recorded, no events are generated.

```bash
gcloud scheduler jobs create http workspace-poll \
  --schedule="*/30 * * * *" \
  --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/workspace-poll" \
  --http-method=POST \
  --message-body='' \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```

## Reminders Tick Job (`reminders-tick`)

Dispatches due reminders to FCM (Faz Y2.4). Runs frequently so a reminder lands within a minute of its due time.

```bash
gcloud scheduler jobs create http reminders-tick \
  --schedule="* * * * *" \
  --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/reminders-tick" \
  --http-method=POST \
  --message-body='' \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```

## Task Tick Job (`task_tick`)

Steps every active task in the görev döngüsü (Faz Y1.3, North Star §7.6) once per wake.

```bash
gcloud scheduler jobs create http task-tick \
  --schedule="*/15 * * * *" \
  --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/event" \
  --http-method=POST \
  --message-body='{"source": "scheduler", "kind": "task_tick", "payload": {}}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```
