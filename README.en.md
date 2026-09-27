# JARVIS

[Türkçe](README.md) · **English**

A personal autonomous assistant: a cloud "brain", an Android phone app, a
Wear OS watch app and a web client. Developed between July and September 2026.

> **Status: archived.** The project was closed in September 2026 and is no
> longer developed. Its Google Cloud project has been deleted; the code is
> left as it was, to be run with your own project.

Most code comments, documents and user-facing strings are in Turkish.

## What is in it

**Cloud brain (`brain/`, Python):** a FastAPI service on Cloud Run with an
orchestrator agent built on Google ADK.

- **Voice conversation:** WebSocket voice gateway, speech recognition with Vertex Gemini.
- **Speaker verification:** SpeechBrain ECAPA voiceprints with an adaptive voiceprint gallery.
- **Spoofed voice detection:** a countermeasure module that tells replayed or AI-generated speech apart from a live voice.
- **Risk-based trust:** combines identity signals into a trust level for each request.
- **Action authorization matrix and approval center:** every tool call passes through a policy layer; risky actions wait in a queue for the owner's approval.
- **Tiered memory:** profile, facts, lessons and session summaries stored in Firestore.
- **Guest gate:** exposes selected tools to external AI agents through an authenticated MCP endpoint.
- **Second opinion:** consults a different model family before important decisions.
- **Weekly retrospective and agent factory:** produces new task agents from templates.
- **Task handoff:** wakes a powered-off PC through a smart power card, hands the job to a manager agent on the PC, verifies the result and sends it back to the channel.

**Android (`android/app`, Kotlin):** Jetpack Compose chat and voice call
screens, voice identity enrollment, approval cards, Firebase Cloud Messaging
notifications.

**Wear OS (`android/wear`, Kotlin):** one-time pairing from the phone, token
storage encrypted with Android Keystore (AES/GCM), chat, quick commands and
voice commands.

## Branches

| Branch | Contents |
|---|---|
| `feat/antispoof-cm` (default) | The most complete version: brain, Android and Wear code, task handoff foundation |
| `feat/jarvis-core-k1` | The new cloud core: Pydantic AI and Pydantic AI Harness, Firestore, Cloud Tasks, PWA control panel |
| `main` | The main branch as of early August 2026: brain, Android and Wear plans |

The other branches are feature development branches.

## Technologies

- **Brain:** Python 3.12, FastAPI, Google ADK, Firestore, Cloud Run, Cloud Tasks, SpeechBrain / PyTorch
- **Mobile:** Kotlin 2.4, Jetpack Compose, Android Gradle Plugin 9.3, Retrofit, DataStore, Wear OS

The default branch has about 31 thousand lines of Python and 19 thousand lines
of Kotlin, with 991 Python and 404 Android tests.

## Running it

The repository contains no secrets, `google-services.json` or deployment
identities. Project, account and device identifiers have been replaced with
placeholders (such as `your-gcp-project` and `owner@example.com`). To run it,
set up your own Google Cloud and Firebase projects and fill in these values.
Setup and environment variables for the brain are described in
[`brain/README.md`](brain/README.md).

## Documents (Turkish)

- [`docs/JARVIS_Proje_Belgesi.md`](docs/JARVIS_Proje_Belgesi.md): vision, architecture, security and authorization matrix
- [`docs/superpowers/plans/`](docs/superpowers/plans/): layer-by-layer implementation plans
- [`docs/arastirma/`](docs/arastirma/): research on similar projects and existing solutions

## Note

The history was rewritten before the repository was made public. Personal
documents, local tool databases and the Firebase configuration were removed
from every commit, and personal identifiers were replaced with placeholders.
Commit hashes therefore differ from the original repository.

No license is specified; all rights reserved.
