# Ses Kimliği — Pixel Doğruluğu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make voice identity actually correct on Kadir's Pixel 10 Pro: close the three gallery write paths that bypass the anti-spoofing CM, give the client a working enrollment path so the new device's channel can enter the gallery at all, and measure whether the phone's echo-cancellation chain is real.

**Architecture:** Three independent surfaces, executed in order. (A) Server: every write into the speaker gallery becomes conditional on a CM verdict — one rule, three sites. (B) Client: the audio chain gets the platform call it was missing (`setMode`) plus a self-test that reports which effects are *actually* active, turning two long-standing assumptions into measurements. (C) Client: `/api/voice/challenge` and `/api/voice/enroll` get a real caller, which is the only way a new device's channel can ever be added to the gallery.

**Tech Stack:** Python 3.12 / FastAPI / Google ADK / Firestore (brain); Kotlin / Compose / Retrofit 3 / OkHttp 5 (Android, compileSdk 36, minSdk 26); pytest; JUnit + Compose UI test.

## Global Constraints

- Turkish for all user-visible strings; English for code, identifiers, comments, commit messages.
- No mock or hardcoded "for now" data. If a stand-in is unavoidable, mark it `TODO(debt):` and name the real source.
- Every external call gets explicit error handling and a timeout.
- DATA-level diagnostic logging: per item, inputs → intermediate values → outputs with units, plus pass/fail.
- One name, one unit, end to end: the CM verdict is `cm_ok` (tri-state `True`/`False`/`None`) and the raw score is `cm_fake_prob` (float, "probability that this audio is fake") in every layer — server, wire, client, UI.
- Brain suite must stay green: `cd brain && .venv/bin/python -m pytest -q` (baseline: 863 passed, 2 skipped).
- Android: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:assembleDebug`. Check `free -g` before any Gradle call; refuse under 4 GB available.
- Branch: `feat/antispoof-cm` (this is the branch production is deployed from).
- No deploy without Kadir's explicit approval. No device driving — Kadir performs every on-device tap and reports the result.

## Evidence this plan rests on

Measured on production 2026-08-10/11, revision `jarvis-voice-00028-c44`, device `android-Pixel 10 Pro`:

| Utterance | `dur_in` | `dur_used` | `infer_ms` | `p_fake` | `score` | `anchor_score` | `verified` |
|---|---|---|---|---|---|---|---|
| 1 | 1.70 s | 1.70 s | 2189 | 0.0248 | 0.4407 | 0.4015 | true |
| 2 | 10.00 s | 4.00 s | 2333 | 0.0074 | 0.4764 | 0.4361 | true |
| 3 | 10.00 s | 4.00 s | 2240 | 0.4231 | 0.4935 | 0.4146 | true |
| 4 | 10.00 s | 4.00 s | 2265 | 0.0409 | 0.0349 | 0.0058 | **false** |

Thresholds in force: `accept=0.35`, `adapt=0.60`, `CM_REJECT_THRESHOLD=0.85`. Gallery: `anchors=7`, `adaptive=12`, all recorded on the sold Galaxy S23's channel.

Four conclusions drive the tasks below:
1. `anchor_score` never reaches the 0.60 adapt gate, so the gallery **cannot** learn this device by itself. Enrollment is the only path, and it has no client.
2. `enroll` writes *anchors* — the immutable reference every later decision is refereed against — with no CM check at all. Opening the client path without the gate would open the worst poisoning surface in the system.
3. Genuine speech produced `p_fake` up to 0.4231 where the 6 Aug benchmark's human fixtures produced 0.0004. The 0.85 threshold is not calibrated for this channel. **Deliberately out of scope here:** calibration needs a spoof distribution too, which needs the strong-clone (Chatterbox-class) test. That is a separate slice; this plan must not move the threshold.
4. Utterance 4 scored `anchor_score=0.0058` — near-orthogonal to the gallery — while `cm_ok=True` says it was live human audio. The leading hypothesis is that the assistant's own TTS reached the microphone and was scored. Task B2 measures this instead of assuming it.

---

## Task A1: CM gate on enrollment anchors

**Files:**
- Modify: `brain/app/main.py:576-640` (the `enroll` endpoint)
- Modify: `brain/app/config.py` (add `CM_ENROLL_REQUIRED`)
- Test: `brain/tests/test_enroll.py`

**Interfaces:**
- Consumes: `antispoof.is_bonafide(pcm: bytes) -> tuple[bool, float]`; `config.CM_ENABLED`, `config.CM_REJECT_THRESHOLD`.
- Produces: enrollment rejects with HTTP 422 and a Turkish detail when any clip fails the CM, and with HTTP 503 when the CM cannot produce a verdict. Later tasks (C1, C2) surface these to the user.

**Why fail-closed here specifically:** an anchor cannot be evicted by the diversity rule and is what `anchor_score` refereeing depends on (`brain/app/speaker.py:99-106`). A poisoned anchor is permanent and silently widens every later accept decision. The live-verify path fails *open* on purpose (Kadir must never be locked out mid-conversation); enrollment is the opposite case — it is a deliberate, repeatable, T3 action, so a missing verdict must block.

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_enroll.py`:

```python
def test_enroll_rejects_spoofed_clip(client, _grant):
    """A clip the CM calls spoof must never become an anchor: anchors are
    immutable and are what anchor_score refereeing depends on."""
    antispoof._score_fn = lambda pcm: 0.99  # >= CM_REJECT_THRESHOLD
    r = client.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 422
    assert "sahte" in r.json()["detail"].lower()


def test_enroll_rejects_when_cm_has_no_verdict(client, _grant):
    """No verdict is not a pass. Unlike the live path (which fails open so Kadir
    is never locked out mid-turn), enrollment is a deliberate repeatable action,
    so a CM failure blocks."""
    def _boom(pcm):
        raise RuntimeError("cm down")
    antispoof._score_fn = _boom
    r = client.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 503


def test_enroll_accepts_bonafide_clips(client, _grant, db):
    """The happy path still writes anchors."""
    antispoof._score_fn = lambda pcm: 0.01
    r = client.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 200
    assert r.json()["anchors"] >= 2


def test_enroll_checks_every_clip_not_just_the_first(client, _grant):
    """One bad clip in a batch poisons the gallery just as thoroughly as a batch
    of bad clips."""
    seen = []

    def _score(pcm):
        seen.append(pcm)
        return 0.01 if len(seen) == 1 else 0.99

    antispoof._score_fn = _score
    r = client.post("/api/voice/enroll", json={"clips": [_clip(), _clip()]})
    assert r.status_code == 422
    assert len(seen) == 2


def test_enroll_skips_cm_when_disabled(client, _grant, monkeypatch):
    """CM_ENABLED=0 is the operator's kill switch and must not brick enrollment."""
    monkeypatch.setattr(config, "CM_ENABLED", False)
    antispoof._score_fn = lambda pcm: 0.99
    r = client.post("/api/voice/enroll", json={"clips": [_clip()]})
    assert r.status_code == 200
```

Add the helpers at the top of the file if they are not already present:

```python
import base64

from app import antispoof, config


def _clip(seconds: float = 1.0) -> str:
    """Base64 PCM16 mono 16k, the wire shape EnrollRequest.clips expects."""
    return base64.b64encode(b"\x01\x02" * int(16000 * seconds)).decode()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_enroll.py -q -k "cm or spoof or bonafide"`
Expected: FAIL — the endpoint returns 200 because no CM check exists.

- [ ] **Step 3: Add the config flag**

In `brain/app/config.py`, immediately after `CM_WARMUP`:

```python
# Enrollment writes ANCHORS -- immutable, un-evictable, and the reference that
# anchor_score refereeing depends on (app/speaker.py:99-106). A poisoned anchor
# is permanent, so this path fails CLOSED, unlike the live verify path which
# fails open so Kadir is never locked out mid-conversation.
CM_ENROLL_REQUIRED = os.environ.get("JARVIS_CM_ENROLL_REQUIRED", "1") == "1"
```

- [ ] **Step 4: Gate the endpoint**

In `brain/app/main.py`, inside `enroll`, after the grant check and before the embedding block:

```python
    raw_clips = [base64.b64decode(clip) for clip in req.clips]

    if config.CM_ENABLED and config.CM_ENROLL_REQUIRED:
        try:
            verdicts = await asyncio.to_thread(
                lambda: [antispoof.is_bonafide(pcm) for pcm in raw_clips]
            )
        except Exception:
            logging.exception("enroll: CM evaluation failed for %s", email)
            raise HTTPException(
                status_code=503,
                detail="Ses doğrulaması şu anda yapılamıyor, birazdan tekrar dene",
            )
        for idx, (ok, fake_prob) in enumerate(verdicts):
            logging.info(
                "enroll CM: user=%s clip=%d/%d bytes=%d p_fake=%.4f verdict=%s",
                email, idx + 1, len(verdicts), len(raw_clips[idx]), fake_prob,
                "bonafide" if ok else "SPOOF",
            )
            if not ok:
                raise HTTPException(
                    status_code=422,
                    detail=f"{idx + 1}. ses klibi sahte olarak işaretlendi, kayıt yapılmadı",
                )
```

Then change the embedding lambda to reuse the decoded bytes instead of decoding twice:

```python
        vecs = await asyncio.to_thread(
            lambda: [speaker.embed(pcm) for pcm in raw_clips]
        )
```

Note the CM runs **before** the embedding: a rejected clip must not cost an ECAPA forward pass.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_enroll.py -q`
Expected: PASS, no pre-existing test broken.

- [ ] **Step 6: Run the full suite**

Run: `cd brain && .venv/bin/python -m pytest -q`
Expected: 868 passed, 2 skipped (863 + 5 new).

- [ ] **Step 7: Commit**

```bash
git add brain/app/main.py brain/app/config.py brain/tests/test_enroll.py
git commit -m "fix(voice): require a CM verdict before enrollment writes anchors

Anchors are immutable, un-evictable, and the reference anchor_score refereeing
depends on -- a poisoned one is permanent and silently widens every later
accept. The endpoint checked the liveness grant and nothing else.

Fails CLOSED (503) when the CM cannot answer, unlike the live verify path which
fails open so Kadir is never locked out mid-conversation. Every clip in a batch
is checked, and the CM runs before the ECAPA embedding so a rejected clip costs
no inference."
```

---

## Task A2: CM gate on the manual "Bu bendim" confirmation

**Files:**
- Modify: `brain/app/speaker.py:425-442` (`SpeakerService.confirm_history`)
- Test: `brain/tests/test_voice_manage.py`

**Interfaces:**
- Consumes: the history entry dict written by `SpeakerService.record_history`, which already carries `cm_fake_prob` (`brain/app/speaker.py:363`).
- Produces: `confirm_history` raises `speaker.RuleViolation` when the stored entry has no usable CM evidence. `voice_manage.confirm_history` already maps `RuleViolation` to HTTP 400, so no route change is needed.

**Why this is nearly free:** the verdict was already computed and persisted at utterance time. Nothing re-runs the model; the fix is to *read a field that is already there*.

- [ ] **Step 1: Write the failing tests**

```python
def test_confirm_rejects_entry_the_cm_called_spoof():
    """The utterance was flagged at the time it was spoken; confirming it must
    not launder it into the gallery."""
    svc, db = _service_with_history(cm_fake_prob=0.95)
    with pytest.raises(speaker.RuleViolation):
        svc.confirm_history("kadir@example.com", "entry-1")


def test_confirm_rejects_entry_with_no_cm_evidence():
    """cm_fake_prob=None means the CM never answered for this utterance (timeout,
    disabled, or an old record). Absence of evidence is not evidence of absence."""
    svc, db = _service_with_history(cm_fake_prob=None)
    with pytest.raises(speaker.RuleViolation):
        svc.confirm_history("kadir@example.com", "entry-1")


def test_confirm_accepts_bonafide_entry():
    svc, db = _service_with_history(cm_fake_prob=0.01)
    out = svc.confirm_history("kadir@example.com", "entry-1")
    assert out["already"] is False
    assert out["added_sample_id"]
```

Helper, placed next to the other builders in the file:

```python
def _service_with_history(cm_fake_prob):
    """A SpeakerService whose history holds exactly one auto entry carrying the
    given CM score."""
    db = FakeDB()
    svc = speaker.SpeakerService(db)
    speaker_history.record(db, "kadir@example.com", {
        "id": "entry-1", "ts": "2026-08-11T00:00:00Z", "score": 0.72,
        "verified": True, "device_hint": "android-Pixel 10 Pro",
        "presence": "foreground", "trust_level": "HIGH",
        "adapted_sample_id": None, "correction": None,
        "cm_fake_prob": cm_fake_prob, "vec": [0.1] * 192,
    }, 50)
    return svc, db
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice_manage.py -q -k confirm`
Expected: FAIL — confirmation succeeds regardless of `cm_fake_prob`.

- [ ] **Step 3: Add the check**

In `brain/app/speaker.py`, inside `confirm_history`, immediately after the entry is located and before the `linked` branch:

```python
            # The CM verdict for this utterance was computed and stored when it
            # was spoken (record_history, cm_fake_prob). Confirming an utterance
            # promotes it into the gallery, so re-read that verdict rather than
            # trusting the user's tap: "Bu bendim" answers "is this Kadir", not
            # "is this a live human".
            fake_prob = entry.get("cm_fake_prob")
            if config.CM_ENABLED:
                if fake_prob is None:
                    raise RuleViolation(
                        "Bu söyleyiş için sahtelik kontrolü yapılamamış, galeriye eklenemez"
                    )
                if fake_prob >= config.CM_REJECT_THRESHOLD:
                    raise RuleViolation(
                        "Bu söyleyiş sahte olarak işaretlenmiş, galeriye eklenemez"
                    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_voice_manage.py -q`
Expected: PASS. If a pre-existing confirm test now fails, it is because its fixture has no `cm_fake_prob` — add `"cm_fake_prob": 0.01` to that fixture. Do **not** weaken the new rule.

- [ ] **Step 5: Run the full suite and commit**

```bash
cd brain && .venv/bin/python -m pytest -q
git add brain/app/speaker.py brain/tests/test_voice_manage.py
git commit -m "fix(voice): confirmation must read the CM verdict it already stored

record_history persists cm_fake_prob per utterance and confirm_history never
looked at it, so 'Bu bendim' could promote an utterance the CM had already
flagged. No model re-run: the field was there the whole time. A missing verdict
blocks too -- absence of evidence is not evidence of absence."
```

---

## Task A3: CM gate on the liveness challenge answer

**Files:**
- Modify: `brain/app/voice.py:250-277` (challenge-answer branch of the text-turn handler)
- Test: `brain/tests/test_antispoof.py`

**Interfaces:**
- Consumes: `voice_trust.peek(self._trust_key)` → `VoiceSignals` with `.cm_ok`, published by `_verify_utterance`, which runs immediately before this branch (`brain/app/voice.py:248`).
- Produces: a grant is only minted when the spoken code arrived on audio the CM called bonafide.

**Why:** the challenge exists to prove a live human is present. Granting on a synthetic voice that reads the digits correctly defeats the entire mechanism — and the grant it mints is what unlocks anchor enrollment.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_challenge_grant_requires_a_bonafide_utterance():
    """The code was spoken by something the CM flagged -- reading the digits
    correctly must not be enough. The grant this mints is what unlocks anchor
    enrollment, so it is the highest-value target in the system."""
    bridge, ws, db = _bridge_with_pending_challenge(code="4831")
    voice_trust.publish(bridge._trust_key, voice_trust.VoiceSignals(
        verified=True, voice_score=0.8, presence="foreground",
        device_hint="android-Pixel 10 Pro", cm_ok=False, cm_fake_prob=0.97,
    ))

    await bridge._on_final_transcript(ws, "dört sekiz üç bir")

    assert voice_challenge.has_valid_grant(db, bridge._user_id) is False
    assert "canlılık" in ws.sent[-3].lower() or "doğrulanamadı" in ws.sent[-3].lower()


@pytest.mark.asyncio
async def test_challenge_grant_blocked_when_cm_silent():
    """cm_ok=None (timeout/disabled) is not a pass on the one path whose entire
    purpose is proving liveness."""
    bridge, ws, db = _bridge_with_pending_challenge(code="4831")
    voice_trust.publish(bridge._trust_key, voice_trust.VoiceSignals(
        verified=True, voice_score=0.8, presence="foreground",
        device_hint="android-Pixel 10 Pro", cm_ok=None, cm_fake_prob=None,
    ))

    await bridge._on_final_transcript(ws, "dört sekiz üç bir")

    assert voice_challenge.has_valid_grant(db, bridge._user_id) is False


@pytest.mark.asyncio
async def test_challenge_grant_succeeds_on_bonafide_audio():
    bridge, ws, db = _bridge_with_pending_challenge(code="4831")
    voice_trust.publish(bridge._trust_key, voice_trust.VoiceSignals(
        verified=True, voice_score=0.8, presence="foreground",
        device_hint="android-Pixel 10 Pro", cm_ok=True, cm_fake_prob=0.01,
    ))

    await bridge._on_final_transcript(ws, "dört sekiz üç bir")

    assert voice_challenge.has_valid_grant(db, bridge._user_id) is True
```

Build `_bridge_with_pending_challenge` by following the existing bridge fixtures in this file (`VoiceBridge` + `_FakeWS` + `FakeDB`), setting `bridge._pending_challenge_code = code` and writing the matching pending doc via `voice_challenge.create_challenge`.

- [ ] **Step 2: Run to verify failure**

Run: `cd brain && .venv/bin/python -m pytest tests/test_antispoof.py -q -k challenge`
Expected: FAIL — the grant is minted on digit match alone.

- [ ] **Step 3: Condition the grant**

In `brain/app/voice.py`, replace the grant line:

```python
            granted = False
            if matched and db is not None:
                signals = voice_trust.peek(self._trust_key)
                cm_ok = signals.cm_ok if signals is not None else None
                if cm_ok is True:
                    granted = voice_challenge.verify_and_grant(db, self._user_id, pending_code)
                else:
                    logging.warning(
                        "voice challenge: code matched but CM did not clear the "
                        "utterance for %s (cm_ok=%s) -- no grant",
                        self._user_id, cm_ok,
                    )
```

and add a third reply branch so the user is told what actually happened:

```python
            self._pending_challenge_code = None
            if granted:
                reply = "Doğrulama kodu kabul edildi."
            elif matched:
                reply = "Kod doğru ama sesin canlılığı doğrulanamadı, tekrar dener misin?"
            else:
                reply = "Doğrulama kodu hatalı veya süresi dolmuş."
            await self._safe_send(ws, vp.evt_jarvis_text(reply))
            await self._safe_send(ws, vp.evt_transcript("jarvis", reply))
            await self._safe_send(ws, vp.evt_turn_complete())
            return
```

- [ ] **Step 4: Run tests, full suite, commit**

```bash
cd brain && .venv/bin/python -m pytest -q
git add brain/app/voice.py brain/tests/test_antispoof.py
git commit -m "fix(voice): the liveness challenge must clear the CM, not just match digits

The challenge exists to prove a live human is present, and the grant it mints is
what unlocks anchor enrollment -- the highest-value target in the system. It was
granting on digit match alone, so a synthetic voice reading the code aloud
passed. cm_ok must be True; None (timeout/disabled) is not a pass here.

A distinct Turkish reply separates 'wrong code' from 'code right, liveness not
proven' so a blocked user knows which."
```

---

## Task B1: `setMode(MODE_IN_COMMUNICATION)` and the permission it needs

**Files:**
- Modify: `android/app/src/main/AndroidManifest.xml:4-9`
- Modify: `android/app/src/main/java/com/jarvis/VoiceCallService.kt` (~line 70, before `routeVoiceToSpeaker()`; and `onDestroy`, ~line 138)
- Test: `android/app/src/androidTest/java/com/jarvis/VoiceCallServiceModeTest.kt` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the audio mode is `MODE_IN_COMMUNICATION` for the whole call and restored to `MODE_NORMAL` on teardown. Task B2 measures whether this changed the effect chain.

**The trap that makes this more than a one-liner:** `AudioManager.setMode` and `setSpeakerphoneOn` both require `MODIFY_AUDIO_SETTINGS`, and the manifest does not declare it (`AndroidManifest.xml:4-9` lists INTERNET, RECORD_AUDIO, FOREGROUND_SERVICE, FOREGROUND_SERVICE_MICROPHONE, POST_NOTIFICATIONS, WAKE_LOCK). Added without the permission, the call silently does nothing and the next round of debugging chases the wrong thing. This also fixes a latent bug: `VoiceCallService.routeVoiceToSpeaker()` already calls `audio.isSpeakerphoneOn = true` on the API < 31 branch, which needs the same permission.

- [ ] **Step 1: Declare the permission**

In `android/app/src/main/AndroidManifest.xml`, after the `WAKE_LOCK` line:

```xml
    <!-- setMode(MODE_IN_COMMUNICATION) and the pre-31 setSpeakerphoneOn fallback
         both require this. Without it both calls are silent no-ops. -->
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
```

It is a normal (install-time) permission — no runtime request, no change to the existing permission flow.

- [ ] **Step 2: Write the failing instrumented test**

Create `android/app/src/androidTest/java/com/jarvis/VoiceCallServiceModeTest.kt`:

```kotlin
package com.jarvis

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.ServiceTestRule
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VoiceCallServiceModeTest {
    @get:Rule val serviceRule = ServiceTestRule()

    @Test
    fun serviceSetsCommunicationModeAndRestoresIt() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

        serviceRule.startService(Intent(context, VoiceCallService::class.java))
        assertEquals(AudioManager.MODE_IN_COMMUNICATION, audio.mode)

        context.stopService(Intent(context, VoiceCallService::class.java))
        Thread.sleep(500) // onDestroy is asynchronous
        assertEquals(AudioManager.MODE_NORMAL, audio.mode)
    }
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:connectedDebugAndroidTest --tests '*VoiceCallServiceModeTest*'`
Expected: FAIL — mode stays `MODE_NORMAL`.

- [ ] **Step 4: Set and restore the mode**

In `VoiceCallService.onStartCommand`, immediately **before** `routeVoiceToSpeaker()`:

```kotlin
        // The switch that actually wakes the platform AEC chain. VOICE_COMMUNICATION
        // on AudioRecord and USAGE_VOICE_COMMUNICATION on the TTS output describe
        // intent; this is what puts the device into a communication routing state.
        // Order matters: set the mode first, then route -- routing decisions made in
        // MODE_NORMAL do not survive the transition.
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        previousAudioMode = audio.mode
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
        routeVoiceToSpeaker()
```

Add the field next to `wakeLock`:

```kotlin
    private var previousAudioMode: Int = AudioManager.MODE_NORMAL
```

In `onDestroy`, before `clearVoiceRoute()`:

```kotlin
        (getSystemService(Context.AUDIO_SERVICE) as AudioManager).mode = previousAudioMode
        clearVoiceRoute()
```

Restoring the *previous* mode rather than hardcoding `MODE_NORMAL` keeps a real phone call — which leaves the device in `MODE_IN_CALL` — untouched if a call somehow overlaps.

- [ ] **Step 5: Run the test, then the JVM suite, then commit**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:connectedDebugAndroidTest --tests '*VoiceCallServiceModeTest*'
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest
git add android/app/src/main/AndroidManifest.xml android/app/src/main/java/com/jarvis/VoiceCallService.kt android/app/src/androidTest/java/com/jarvis/VoiceCallServiceModeTest.kt
git commit -m "fix(voice): set MODE_IN_COMMUNICATION and declare the permission it needs

AudioRecord already used VOICE_COMMUNICATION and TTS already used
USAGE_VOICE_COMMUNICATION, but nothing ever called AudioManager.setMode -- the
switch that actually puts the device into a communication routing state.

MODIFY_AUDIO_SETTINGS was missing from the manifest, which would have made the
new call a silent no-op; it also fixes routeVoiceToSpeaker's pre-31
setSpeakerphoneOn branch, which needed the same permission. The previous mode is
restored rather than MODE_NORMAL hardcoded, so an overlapping real call survives."
```

⚠️ **Kadir must listen to one call after this lands.** On some OEMs `MODE_IN_COMMUNICATION` forces routing to the earpiece and binds volume to the call stream — the same class as the 4 Aug "replies are silent" regression. If the reply is quiet or comes out of the earpiece, say so and the `routeVoiceToSpeaker()` ordering is the first thing to revisit.

---

## Task B2: AEC self-test telemetry

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidMicSource.kt` (after `record.startRecording()`, ~line 56)
- Test: `android/app/src/test/java/com/jarvis/data/voice/session/AudioEffectReportTest.kt` (create)

**Interfaces:**
- Consumes: the `AudioRecord` created in `AndroidMicSource.start()`.
- Produces: `fun formatEffectReport(effects: List<String>, silenced: Boolean, sdkInt: Int): String` — a pure function returning the DATA log line. The impure callback wiring stays in `start()`; the formatting is unit-testable without a device.

**Why this task exists at all:** B1 and the `EXTRA_AUDIO_SOURCE` comment are both *assumptions* about the effect chain. `AudioRecordingConfiguration.getEffects()` reports which effects are actually attached, and `isClientSilenced()` reports whether this client's capture is being muted by the system — which is the direct test of the utterance-4 hypothesis (our own TTS reaching the mic). Without this, B1 stays a hypothesis forever.

- [ ] **Step 1: Write the failing unit test**

```kotlin
class AudioEffectReportTest {
    @Test
    fun reportNamesEffectsAndSilenceState() {
        val line = formatEffectReport(listOf("AEC", "NS"), silenced = false, sdkInt = 34)
        assertTrue(line.contains("effects=AEC,NS"))
        assertTrue(line.contains("silenced=false"))
        assertTrue(line.contains("aec=true"))
    }

    @Test
    fun reportFlagsMissingAec() {
        val line = formatEffectReport(listOf("NS"), silenced = false, sdkInt = 34)
        assertTrue(line.contains("aec=false"))
    }

    @Test
    fun reportIsHonestWhenTheApiIsUnavailable() {
        // getEffects()/isClientSilenced() are API 29+; minSdk is 26, so below that
        // the answer is "unknown", never a cheerful default.
        val line = formatEffectReport(emptyList(), silenced = false, sdkInt = 28)
        assertTrue(line.contains("effects=unknown"))
        assertFalse(line.contains("aec=true"))
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest --tests '*AudioEffectReportTest*'`
Expected: FAIL — `formatEffectReport` does not exist.

- [ ] **Step 3: Implement the formatter and wire the callback**

In `AndroidMicSource.kt`, at file scope:

```kotlin
/**
 * DATA line for the capture path: what the platform actually attached, not what we
 * asked for. `isClientSilenced` is the direct test of "is our own TTS reaching the
 * mic" -- the system mutes a capture client when another one wins the route.
 * Both APIs are 29+; below that the honest answer is "unknown".
 */
internal fun formatEffectReport(effects: List<String>, silenced: Boolean, sdkInt: Int): String =
    if (sdkInt < 29) {
        "mic effects=unknown silenced=unknown (API $sdkInt < 29)"
    } else {
        val names = if (effects.isEmpty()) "none" else effects.joinToString(",")
        "mic effects=$names aec=${effects.any { it.contains("AEC", ignoreCase = true) }} " +
            "silenced=$silenced"
    }
```

And after `record.startRecording()`:

```kotlin
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            recordingCallback = object : AudioManager.AudioRecordingCallback() {
                override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                    val mine = configs.firstOrNull { it.clientAudioSessionId == record.audioSessionId }
                        ?: return
                    Log.i(TAG, formatEffectReport(
                        mine.effects.map { it.name },
                        mine.isClientSilenced,
                        Build.VERSION.SDK_INT,
                    ))
                }
            }
            am.registerAudioRecordingCallback(recordingCallback!!, null)
        }
```

Unregister it in the existing teardown path alongside `record.release()`.

⚠️ **Honest limit, write it in the comment:** this reports only *our* capture client. The system recognizer (SODA) runs in another process, so its capture never appears here. Task B3 explains why that matters.

- [ ] **Step 4: Run tests and commit**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest
git add android/app/src/main/java/com/jarvis/data/voice/session/AndroidMicSource.kt android/app/src/test/java/com/jarvis/data/voice/session/AudioEffectReportTest.kt
git commit -m "feat(voice): report which audio effects are actually attached

VOICE_COMMUNICATION and setMode both describe intent. getEffects() reports what
the platform actually attached and isClientSilenced() reports whether our capture
is being muted -- the direct test of the 2026-08-10 utterance that scored 0.0058
against the gallery while the CM called it live human audio.

Reports only our own capture client; the system recognizer runs in another
process and never appears here."
```

---

## Task B3: Remove the dead `EXTRA_AUDIO_SOURCE` int and the false assurance around it

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidSpeechToText.kt:131-142`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing structural. This task removes code and replaces a comment; the behaviour on the device is unchanged because the removed code never did anything.

**What is actually wrong:** `RecognizerIntent.EXTRA_AUDIO_SOURCE` takes a `ParcelFileDescriptor` pointing at an already-open audio source for the recognizer to read from. The code passes `MediaRecorder.AudioSource.VOICE_COMMUNICATION`, an `Int`. The extra is silently ignored and the recognizer opens its own microphone with its own default source. The sibling constants in the installed `android-37.0` stub confirm the shape: `EXTRA_AUDIO_SOURCE_CHANNEL_COUNT`, `_ENCODING`, `_SAMPLING_RATE` — a bare `AudioSource` int would need none of those.

The comment above it currently claims this line *is* the AEC fix and cites a production report. That comment is the harmful part: it is a written assurance that a control exists when it does not, and it is why the STT path was recorded as "done" in the 5 Aug review.

**Deliberately NOT in this task:** building the real single-`AudioRecord` → `ParcelFileDescriptor.createPipe()` → recognizer path. It is 1–2 days and it rests on an unverified assumption (that Google's tr-TR recognizer accepts a PFD feed at all). Task B4 measures that first.

- [ ] **Step 1: Delete the dead call and correct the comment**

Replace the block at `AndroidSpeechToText.kt:131-142` with:

```kotlin
            // NOT setting RecognizerIntent.EXTRA_AUDIO_SOURCE here is deliberate.
            // That extra takes a ParcelFileDescriptor pointing at an already-open
            // audio source; we were passing MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            // an Int, which the framework silently ignores. The sibling constants
            // (EXTRA_AUDIO_SOURCE_CHANNEL_COUNT / _ENCODING / _SAMPLING_RATE) confirm
            // the shape -- a bare AudioSource int would need none of them.
            //
            // So the recognizer has always opened its own microphone with its own
            // default source, and the STT leg of the AEC chain was never established.
            // The comment removed here claimed the opposite and is why the 5 Aug
            // review recorded this as done. Feeding the recognizer from our single
            // AudioRecord via ParcelFileDescriptor.createPipe() is the real fix; it
            // is gated on confirming Google's tr-TR recognizer accepts a PFD feed
            // (docs/superpowers/plans/2026-08-11-ses-kimligi-pixel-dogrulugu.md, B4).
```

- [ ] **Step 2: Build and run the JVM suite**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:assembleDebug :app:testDebugUnitTest
```
Expected: green. No behaviour change is expected on the device — the removed line was inert.

- [ ] **Step 3: Commit**

```bash
git add android/app/src/main/java/com/jarvis/data/voice/session/AndroidSpeechToText.kt
git commit -m "fix(voice): drop the inert EXTRA_AUDIO_SOURCE int and the comment that lied about it

EXTRA_AUDIO_SOURCE takes a ParcelFileDescriptor; we passed an AudioSource Int, so
the framework ignored it and the recognizer has always opened its own microphone
with its own default source. The STT leg of the AEC chain was never established.

The comment claiming this line was the AEC fix is why the 5 Aug review recorded
the STT path as done. Removing a false assurance is worth more than the dead line."
```

---

## Task B4: Measure whether the recognizer accepts a piped audio source

**Files:**
- Create: `android/app/src/androidTest/java/com/jarvis/data/voice/session/RecognizerPipeProbeTest.kt`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a recorded yes/no answer to "does Google's tr-TR recognizer accept a `ParcelFileDescriptor` audio feed on this device", which decides whether the single-`AudioRecord` rework is viable.

This is a probe, not a feature. Its output is a ledger entry.

- [ ] **Step 1: Write the probe**

```kotlin
@RunWith(AndroidJUnit4::class)
class RecognizerPipeProbeTest {
    /**
     * Answers one question and writes it down: does the platform recognizer accept
     * a ParcelFileDescriptor audio feed for tr-TR on this device? If it does, one
     * AudioRecord can feed STT + speaker-ID + VAD from the same AEC-processed
     * signal. If it does not, the two-capture-client design stays and the mic
     * contention has to be answered another way.
     */
    @Test
    fun recognizerAcceptsPipedAudioSource() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val (readFd, writeFd) = ParcelFileDescriptor.createPipe()
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readFd)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, 16000)
            putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
            putExtra(
                RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING,
                AudioFormat.ENCODING_PCM_16BIT,
            )
        }

        val transcript = LinkedBlockingQueue<String>(1)
        val recognizer = SpeechRecognizer.createSpeechRecognizer(context)
        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onPartialResults(partial: Bundle) {
                partial.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()?.let { transcript.offer(it) }
            }
            override fun onResults(results: Bundle) {
                results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()?.let { transcript.offer(it) }
            }
            override fun onError(error: Int) { transcript.offer("ERROR:$error") }
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            recognizer.startListening(intent)
        }

        // Push the fixture into the write end, then close it so the recognizer sees EOF.
        Thread {
            ParcelFileDescriptor.AutoCloseOutputStream(writeFd).use { out ->
                context.assets.open("tr_probe_16k.pcm").use { it.copyTo(out) }
            }
        }.start()

        val result = transcript.poll(15, TimeUnit.SECONDS)
        Log.i("RecognizerPipeProbe", "PFD feed result=$result")

        // The probe's product is the recorded answer, so both outcomes are written
        // down rather than one of them failing the build.
        assertNotNull("recognizer never responded to a piped source", result)
        recognizer.destroy()
    }
}
```

Create the fixture from an existing 16 kHz mono PCM16 clip and commit it as `android/app/src/androidTest/assets/tr_probe_16k.pcm`. A result of `ERROR:7` (`ERROR_NO_MATCH`) or `ERROR:6` (`ERROR_SPEECH_TIMEOUT`) means the recognizer ignored the pipe and listened to a silent microphone — that is the negative answer, and it is just as valuable as the positive one.

- [ ] **Step 2: Run it on the Pixel and write down the answer**

```bash
adb -s 100.64.0.10:42051 shell true   # confirm the device is attached first
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:connectedDebugAndroidTest --tests '*RecognizerPipeProbeTest*'
```

Record the result in `~/.agents-shared/LESSONS.md` as a one-line lesson either way, and in this plan's task list. A negative result is a real finding, not a failure.

- [ ] **Step 3: Commit the probe and the answer**

```bash
git add android/app/src/androidTest/java/com/jarvis/data/voice/session/RecognizerPipeProbeTest.kt android/app/src/androidTest/assets/tr_probe_16k.pcm
git commit -m "test(voice): probe whether the tr-TR recognizer accepts a piped audio source

Decides whether one AudioRecord can feed STT + speaker-ID + VAD from the same
AEC-processed signal, or whether the two-capture-client design has to stay."
```

---

## Task C1: Client API for challenge and enrollment

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/data/net/VoiceApi.kt`
- Modify: `android/app/src/main/java/com/jarvis/data/net/VoiceModels.kt`
- Test: `android/app/src/test/java/com/jarvis/data/net/VoiceEnrollApiTest.kt` (create)

**Interfaces:**
- Consumes: server routes `POST /api/voice/challenge` (`brain/app/voice.py:598`) and `POST /api/voice/enroll` (`brain/app/main.py:576`), plus the status codes Task A1 introduced.
- Produces:
  - `suspend fun VoiceApi.challenge(): ChallengeResponse` where `ChallengeResponse(status: String, code_spoken: Boolean)`
  - `suspend fun VoiceApi.enroll(req: EnrollRequest): EnrollResponse` where `EnrollRequest(clips: List<String>, device_hint: String)` and `EnrollResponse(anchors: Int)`
  - Task C2 consumes both.

Field names are `snake_case` to match the server wire format exactly, following the existing `VoiceModels.kt` convention (`device_hint`, `cm_ok`).

- [ ] **Step 1: Write the failing tests**

```kotlin
class VoiceEnrollApiTest {
    @Test fun challengePostsAndParsesCodeSpoken() = runBlocking {
        server.enqueue(MockResponse.Builder().code(200)
            .body("""{"status":"challenge_created","code_spoken":true}""").build())
        val out = api.challenge()
        assertEquals("POST", server.takeRequest().method)
        assertTrue(out.code_spoken)
    }

    @Test fun enrollSendsClipsAndDeviceHint() = runBlocking {
        server.enqueue(MockResponse.Builder().code(200).body("""{"anchors":9}""").build())
        val out = api.enroll(EnrollRequest(listOf("AAEC", "AAED"), "android-Pixel 10 Pro"))
        val body = server.takeRequest().body!!.utf8()
        assertTrue(body.contains("device_hint"))
        assertTrue(body.contains("android-Pixel 10 Pro"))
        assertEquals(9, out.anchors)
    }

    @Test fun enrollSurfacesSpoofRejectionDistinctly() = runBlocking {
        // 422 is Task A1's "a clip was flagged as fake" -- the user must be told
        // this, not a generic failure, because retrying identically will not help.
        server.enqueue(MockResponse.Builder().code(422)
            .body("""{"detail":"1. ses klibi sahte olarak işaretlendi, kayıt yapılmadı"}""").build())
        val err = runCatching { api.enroll(EnrollRequest(listOf("AAEC"), "x")) }.exceptionOrNull()
        assertTrue(err is HttpException && err.code() == 422)
    }

    @Test fun enrollSurfacesMissingGrantDistinctly() = runBlocking {
        // 409 = no liveness grant. The fix is "do the challenge first", which is a
        // different user action from 422.
        server.enqueue(MockResponse.Builder().code(409).body("""{"detail":"..."}""").build())
        val err = runCatching { api.enroll(EnrollRequest(listOf("AAEC"), "x")) }.exceptionOrNull()
        assertTrue(err is HttpException && err.code() == 409)
    }
}
```

Follow the MockWebServer setup already used in this module: `com.squareup.okhttp3:mockwebserver3:5.4.0`, `MockResponse.Builder()`, `server.close()`.

- [ ] **Step 2: Run to verify failure**

Run: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest --tests '*VoiceEnrollApiTest*'`
Expected: FAIL — the methods do not exist.

- [ ] **Step 3: Add the models and the routes**

In `VoiceModels.kt`:

```kotlin
@Serializable
data class ChallengeResponse(val status: String, val code_spoken: Boolean = false)

@Serializable
data class EnrollRequest(val clips: List<String>, val device_hint: String)

@Serializable
data class EnrollResponse(val anchors: Int)
```

In `VoiceApi.kt`:

```kotlin
    /**
     * Asks the server to mint a 4-digit liveness code. When a live voice bridge is
     * open for this user the server speaks it over that bridge, which is why the
     * enrollment flow has to run inside a voice call (see C2).
     */
    @POST("api/voice/challenge")
    suspend fun challenge(): ChallengeResponse

    /**
     * Writes anchors. Requires a liveness grant minted by [challenge] within the
     * last 5 minutes (409 otherwise) and clips the CM clears (422 otherwise).
     */
    @POST("api/voice/enroll")
    suspend fun enroll(@Body req: EnrollRequest): EnrollResponse
```

- [ ] **Step 4: Run tests and commit**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest
git add android/app/src/main/java/com/jarvis/data/net/VoiceApi.kt android/app/src/main/java/com/jarvis/data/net/VoiceModels.kt android/app/src/test/java/com/jarvis/data/net/VoiceEnrollApiTest.kt
git commit -m "feat(voice): client routes for the liveness challenge and enrollment

Neither endpoint had any caller. /api/voice/enroll has required a liveness grant
since 6 Aug and the only way to obtain one is to repeat a spoken code over a live
voice bridge -- so enrollment has been impossible from any client since that day,
which is why a device change could not be recovered from.

409 (no grant) and 422 (clip flagged) are surfaced distinctly: they need
different user actions."
```

---

## Task C2: "Bu cihazı tanıt" flow on the voice screen

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/ui/voice/VoiceProfileScreen.kt` (the "Ses örneklerini yönet" section, ~line 615)
- Create: `android/app/src/main/java/com/jarvis/ui/voice/EnrollDeviceViewModel.kt`
- Test: `android/app/src/test/java/com/jarvis/ui/voice/EnrollDeviceViewModelTest.kt` (create)

**Interfaces:**
- Consumes: `VoiceApi.challenge()`, `VoiceApi.enroll()` (Task C1); `AndroidMicSource` for clip capture; `Build.MODEL` for `device_hint`, matching `JarvisApp.kt:81`'s `"android-" + Build.MODEL` exactly.
- Produces: a user-visible flow; no later task consumes it.

**Flow, and why it is shaped this way:** the server speaks the liveness code over an *open voice bridge* (`brain/app/voice.py:610` looks up `active_bridges[email]`). So the enrollment flow must run while a voice call is live. Sequence: user opens the voice screen → taps "Bu cihazı tanıt" → app ensures a voice call is connected → calls `challenge()` → Jarvis speaks four digits → user repeats them → server mints the grant (Task A3 requires the CM to clear that utterance) → app records 3 clips of ~2 s each from this device's microphone → `enroll()` → success shows the new anchor count.

**State machine** (`EnrollDeviceViewModel`), each state carrying its Turkish label:

```kotlin
sealed interface EnrollState {
    data object Idle : EnrollState
    data object RequestingCode : EnrollState                    // "Kod isteniyor…"
    data object WaitingForSpokenCode : EnrollState              // "Jarvis'in söylediği kodu tekrar et"
    data object Recording : EnrollState                         // "Konuş — örnek alınıyor (3/3)"
    data object Uploading : EnrollState                         // "Kaydediliyor…"
    data class Done(val anchors: Int) : EnrollState             // "Bu cihaz tanıtıldı (N örnek)"
    data class Failed(val message: String) : EnrollState
}
```

- [ ] **Step 1: Write the failing ViewModel tests**

```kotlin
class EnrollDeviceViewModelTest {
    @Test fun happyPathReachesDoneWithAnchorCount() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(anchors = 10), FakeRecorder(), "android-Pixel 10 Pro")
        vm.start()
        advanceUntilIdle()
        assertEquals(EnrollState.Done(10), vm.state.value)
    }

    @Test fun missingGrantTellsTheUserToRepeatTheCode() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(enrollStatus = 409), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        val failed = vm.state.value as EnrollState.Failed
        assertTrue(failed.message.contains("kod"))
    }

    @Test fun spoofRejectionSaysSoPlainly() = runTest(dispatcher) {
        // Retrying identically will not help, so the message must not read as a
        // transient error.
        val vm = EnrollDeviceViewModel(FakeVoiceApi(enrollStatus = 422), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        val failed = vm.state.value as EnrollState.Failed
        assertTrue(failed.message.contains("sahte"))
    }

    @Test fun deviceHintIsTheSameStringTheVoiceBridgeSends() = runTest(dispatcher) {
        // One name, one unit: the gallery's channel tag must match what the live
        // path writes (JarvisApp.kt: "android-" + Build.MODEL), or enrollment adds
        // anchors under a channel label no utterance will ever carry.
        val api = FakeVoiceApi(anchors = 1)
        EnrollDeviceViewModel(api, FakeRecorder(), "android-Pixel 10 Pro").apply { start() }
        advanceUntilIdle()
        assertEquals("android-Pixel 10 Pro", api.lastEnroll!!.device_hint)
    }

    @Test fun recorderFailureDoesNotStrandTheUiInRecording() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(), FailingRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        assertTrue(vm.state.value is EnrollState.Failed)
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest --tests '*EnrollDeviceViewModelTest*'`
Expected: FAIL — class does not exist.

- [ ] **Step 3: Implement the ViewModel**

Constructor `EnrollDeviceViewModel(private val api: VoiceApi, private val recorder: ClipRecorder, private val deviceHint: String)`, exposing `val state: StateFlow<EnrollState>`. `ClipRecorder` is a small interface (`suspend fun record(count: Int, seconds: Double): List<ByteArray>`) so the ViewModel stays JVM-testable — the same decoupling `ChatViewModel` uses for its repository.

Map failures explicitly:

```kotlin
        } catch (e: HttpException) {
            val message = when (e.code()) {
                409 -> "Kod doğrulanmadı. Jarvis'in söylediği dört haneli kodu tekrar et, sonra yeniden dene."
                422 -> "Alınan ses örneği sahte olarak işaretlendi, kayıt yapılmadı."
                503 -> "Ses doğrulaması şu anda yapılamıyor, birazdan tekrar dene."
                else -> "Kayıt tamamlanamadı (${e.code()})."
            }
            _state.value = EnrollState.Failed(message)
        }
```

Let `CancellationException` propagate unchanged — this branch has bitten this codebase three times (`JarvisApi`, `WatchPairing`, `ChatViewModel`); catch `HttpException` and `IOException` specifically, never bare `Exception`.

- [ ] **Step 4: Wire the UI**

In `VoiceProfileScreen.kt`, inside the existing collapsed "Ses örneklerini yönet" section, add a button labelled **"Bu cihazı tanıt"** with the supporting line *"Yeni telefonun mikrofonu farklı ses bırakır; tanıtmadan Jarvis seni bu cihazda daha zor tanır."* Render each `EnrollState` with its label above. Give the button `testTag("enroll_device")`.

- [ ] **Step 5: Run the suites, build, commit**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest :app:assembleDebug
git add android/app/src/main/java/com/jarvis/ui/voice/ android/app/src/test/java/com/jarvis/ui/voice/EnrollDeviceViewModelTest.kt
git commit -m "feat(voice): 'Bu cihazı tanıt' — the enrollment flow the server has been waiting for

Measured 2026-08-10 on the Pixel 10 Pro: anchor_score 0.40-0.44 against a gallery
recorded entirely on the sold S23, never reaching the 0.60 adapt gate, and one
utterance below the 0.35 accept threshold. The gallery cannot learn a new channel
on its own by design (the adapt gate scores against immutable anchors, which is
what stops poisoning ratchets), so enrollment is the only path -- and it had no
client at all.

device_hint is the same string the live bridge sends, or the anchors land under a
channel label no utterance will ever carry."
```

---

## Task C3: End-to-end proof on the real device (HITL)

**Files:** none — this is a verification task run with Kadir.

- [ ] **Step 1: Build and install**

```bash
cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:assembleDebug
adb -s 100.64.0.10:42051 install -r android/app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 2: Kadir performs, in order**

1. Open the voice screen → "Ses örneklerini yönet" → **"Bu cihazı tanıt"**
2. Jarvis speaks four digits — repeat them
3. Speak the three requested samples
4. Report what the screen shows

- [ ] **Step 3: Confirm from the logs, not from the screen**

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="jarvis-voice" AND timestamp>="<start>"' \
  --project your-gcp-project --limit 200 --format="value(timestamp,textPayload)" --order=asc \
  | grep -E "enroll CM|voice challenge|speaker.identify|voice trust"
```

Pass criteria:
- `enroll CM:` lines for every clip, all `verdict=bonafide`
- a grant minted only after a `cm_ok=True` utterance
- `anchors=` rises from 7
- **the decisive one:** on the next ordinary voice turn, `anchor_score` clears 0.60 and `adapted=True` appears for the first time on `device=android-Pixel 10 Pro`

- [ ] **Step 4: Record the outcome**

Write the before/after `anchor_score` figures into `.superpowers/sdd/progress.md` and add a lesson to `~/.agents-shared/LESSONS.md`.

---

## Blocking decision, not a task: the watch credential

`feat/wear-w1-core` is 12 commits, unmerged, and stores a device token **on the watch** (`android/wear/.../data/TokenCipher.kt`, Keystore + GCM; minted by the phone in W0 and pushed over `MessageClient`).

OpenClaw's shipped Wear companion does the opposite: the watch stores no credential and borrows the paired phone's gateway connection (ecosystem report §3.3(a), §1.10 D5(i), which marks it "directly applicable to the W1 branch"). Their watchOS node is also deliberately tiny — `device.info`, `device.status`, `system.notify` and nothing else — and uses signed HTTPS polling rather than a socket because of platform network limits (§1.5).

The trade-off is real in both directions: our design keeps the watch working when the phone is away; theirs keeps a credential off the easiest-to-lose device. **This was never decided — it was just built.** Decide it before the branch merges, because after the merge it becomes a migration instead of a choice.

---

## Out of scope, and why

- **CM threshold calibration.** Genuine speech measured `p_fake` from 0.0074 to 0.4231 against a benchmark that showed 0.0004 for human fixtures, so 0.85 is not calibrated for this channel. Calibration needs a spoof distribution as well, which needs the strong-clone (Chatterbox-class) test. Moving the threshold on bonafide data alone would be guessing with extra steps.
- **The single-`AudioRecord` → PFD rework.** Gated on Task B4's answer.
- **The 10-second utterance ceiling.** Three of four utterances reported `dur_in` of exactly 10.00 s, which is a buffer boundary rather than a coincidence. Worth finding, not worth blocking this slice.
- **`npx` missing from the image**, so `github_mcp` fails and retries on every turn. Real, unrelated, cheap: either add Node to the image or drop the toolset from the registry.
